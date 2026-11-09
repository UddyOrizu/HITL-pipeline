"""Jira connector: tickets submitted for review; decisions comment on the ticket, move it, and attach the evidence pack.

Config keys: base_url, email, api_token (secret), workflow_id,
             transitions {"approved": id, "rejected": id, "changes_requested": id} (optional),
             submit_jql (optional): issues matching this JQL are submitted automatically, e.g.
             'project = LEGAL AND status = "Ready for review"'. Move them out of that status once submitted.
             risk_tier ("medium").
A submission is linked to a ticket when its metadata contains "jira_issue_key" (set automatically for tickets the
connector submits; API callers can set it themselves).
"""

from __future__ import annotations

import base64
import json
import logging

from sqlalchemy import select

from connectors.base import check, checked_url, config_of, http, por_client
from db.models import ConnectorConfig, Submission
from db.session import session_scope

log = logging.getLogger("por.connectors.jira")


class JiraConnector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = config_of(cfg)

    def _client(self):
        token = base64.b64encode(f"{self.c['email']}:{self.c['api_token']}".encode()).decode()
        return http(base_url=checked_url(self.c["base_url"].rstrip("/")),
                    headers={"Authorization": f"Basic {token}", "Accept": "application/json"})

    @staticmethod
    def _adf_text(node) -> str:
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            sep = "\n\n" if node.get("type") in ("doc",) else ("\n" if node.get("type") == "paragraph" else "")
            return sep.join(JiraConnector._adf_text(c) for c in node.get("content", [])) + ("\n" if node.get("type") == "paragraph" else "")
        if isinstance(node, list):
            return "".join(JiraConnector._adf_text(c) for c in node)
        return str(node or "")

    def submit_issue(self, issue_key: str, risk_tier: str | None = None) -> str:
        """Submit a ticket's description for review, linked to the ticket."""
        with self._client() as j:
            issue = check(j.get(f"/rest/api/3/issue/{issue_key}", params={"fields": "summary,description"})).json()
        fields = issue["fields"]
        desc = fields.get("description")
        text = self._adf_text(desc).strip() if isinstance(desc, dict | list) else (desc or "")
        body = {"workflow_id": self.c["workflow_id"], "text": text or fields.get("summary", ""), "external_ref": f"jira:{issue_key}",
                "risk_tier": risk_tier or self.c.get("risk_tier", "medium"),
                "metadata": {"title": fields.get("summary"), "jira_issue_key": issue_key},
                "idempotency_key": f"jira:{issue_key}:{issue.get('id')}"}
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"})).json()
        return sub["id"]

    def poll(self) -> list[str]:
        """Submit issues matching submit_jql that have not been submitted yet."""
        jql = self.c.get("submit_jql")
        if not jql:
            return []
        state = dict(self.cfg.state or {})
        seen: list = list(state.get("seen", []))
        created = []
        with self._client() as j:
            data = check(j.get("/rest/api/3/search/jql", params={"jql": jql, "fields": "summary", "maxResults": 50})).json()
        for issue in data.get("issues", []):
            key = issue.get("key")
            if not key or key in seen:
                continue
            created.append(self.submit_issue(key))
            seen.append(key)
        state["seen"] = seen[-5000:]
        self.cfg.state = state
        return created

    def on_decided(self, submission_id: str) -> None:
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.get(f"/v1/submissions/{submission_id}")).json()
            key = (sub.get("metadata") or {}).get("jira_issue_key")
            if not key:
                return
            pdf = check(api.get(f"/v1/submissions/{submission_id}/evidence", params={"format": "pdf"})).content
        decision = sub.get("decision") or {}
        score = (decision.get("assurance") or {}).get("score")
        text = f"Proof of Review decision: {sub['status']} (Review Assurance Score {score})."
        if decision.get("reason"):
            text += f" Reason: {decision['reason']}"
        with self._client() as j:
            check(j.post(f"/rest/api/3/issue/{key}/comment", json={"body": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}}))
            transition = (self.c.get("transitions") or {}).get(sub["status"])
            if transition:
                check(j.post(f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": str(transition)}}))
            check(j.post(f"/rest/api/3/issue/{key}/attachments", headers={"X-Atlassian-Token": "no-check"},
                         files={"file": (f"evidence-{submission_id}.pdf", pdf, "application/pdf")}))


def run_once(session, cfg: ConnectorConfig) -> dict:
    created = JiraConnector(cfg).poll()
    session.flush()
    return {"submitted": created}


def _configs(s, tenant_id: str, source_app_id: str) -> list[ConnectorConfig]:
    from connectors import kind_of

    rows = s.scalars(select(ConnectorConfig).where(
        ConnectorConfig.tenant_id == tenant_id, ConnectorConfig.kind == "jira", ConnectorConfig.active.is_(True),
        ConnectorConfig.source_app_id == source_app_id)).all()
    return [c for c in rows if kind_of(c) == "jira"]


def on_decided(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        sub = s.get(Submission, event["submission_id"])
        if sub is None:
            return
        for cfg in _configs(s, event["tenant_id"], sub.source_app_id):
            JiraConnector(cfg).on_decided(sub.id)
