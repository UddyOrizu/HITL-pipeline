"""Jira / Azure DevOps connector: submissions linked to a ticket; decisions update the ticket and attach the evidence pack.

Jira config keys: base_url, email, api_token, workflow_id,
                  transitions {"approved": id, "rejected": id, "changes_requested": id} (optional).
A submission is linked when its metadata contains "jira_issue_key".
"""

from __future__ import annotations

import base64
import json
import logging

from sqlalchemy import select

from connectors.base import check, http, por_client
from db.models import ConnectorConfig, Submission
from db.session import session_scope

log = logging.getLogger("por.connectors.jira")


class JiraConnector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = cfg.config or {}

    def _client(self):
        token = base64.b64encode(f"{self.c['email']}:{self.c['api_token']}".encode()).decode()
        return http(base_url=self.c["base_url"].rstrip("/"), headers={"Authorization": f"Basic {token}", "Accept": "application/json"})

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

    def submit_issue(self, issue_key: str, risk_tier: str = "medium") -> str:
        """Submit a ticket's description for review, linked to the ticket."""
        with self._client() as j:
            issue = check(j.get(f"/rest/api/3/issue/{issue_key}", params={"fields": "summary,description"})).json()
        fields = issue["fields"]
        desc = fields.get("description")
        text = self._adf_text(desc).strip() if isinstance(desc, dict | list) else (desc or "")
        body = {"workflow_id": self.c["workflow_id"], "text": text or fields.get("summary", ""), "external_ref": f"jira:{issue_key}",
                "risk_tier": risk_tier, "metadata": {"title": fields.get("summary"), "jira_issue_key": issue_key},
                "idempotency_key": f"jira:{issue_key}:{issue.get('id')}"}
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"})).json()
        return sub["id"]

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


class AzureDevOpsConnector:
    """Same contract for Azure DevOps work items. Config: organization, project, pat, workflow_id, states {status: state}."""

    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = cfg.config or {}

    def _client(self):
        token = base64.b64encode(f":{self.c['pat']}".encode()).decode()
        return http(base_url=f"https://dev.azure.com/{self.c['organization']}/{self.c['project']}",
                    headers={"Authorization": f"Basic {token}"})

    def on_decided(self, submission_id: str) -> None:
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.get(f"/v1/submissions/{submission_id}")).json()
        item = (sub.get("metadata") or {}).get("ado_work_item_id")
        if not item:
            return
        decision = sub.get("decision") or {}
        ops = [{"op": "add", "path": "/fields/System.History",
                "value": f"Proof of Review decision: {sub['status']} (score {(decision.get('assurance') or {}).get('score')}). "
                         f"Evidence: {decision.get('evidence_url')}"}]
        state = (self.c.get("states") or {}).get(sub["status"])
        if state:
            ops.append({"op": "add", "path": "/fields/System.State", "value": state})
        with self._client() as c:
            check(c.patch(f"/_apis/wit/workitems/{item}?api-version=7.1", json=ops,
                          headers={"Content-Type": "application/json-patch+json"}))


def on_decided(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        sub = s.get(Submission, event["submission_id"])
        if sub is None:
            return
        for cfg in s.scalars(select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == event["tenant_id"], ConnectorConfig.kind == "jira",
                ConnectorConfig.active.is_(True), ConnectorConfig.source_app_id == sub.source_app_id)).all():
            flavour = (cfg.config or {}).get("flavour", "jira")
            (AzureDevOpsConnector(cfg) if flavour == "azure_devops" else JiraConnector(cfg)).on_decided(sub.id)
