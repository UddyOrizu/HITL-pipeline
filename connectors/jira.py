"""Jira connector: tickets submitted for review; decisions comment on the ticket, move it, and attach the evidence pack.

Config keys: base_url, email, api_token (secret), workflow_id,
             transitions {"approved": id, "rejected": id, "changes_requested": id} (optional),
             submit_jql (optional): issues matching this JQL are submitted automatically, e.g.
             'project = LEGAL AND status = "Ready for review"'. Move them out of that status once submitted.
             risk_tier ("medium"), max_items (500: issues read per run).
A submission is linked to a ticket when its metadata contains "jira_issue_key" (set automatically for tickets the
connector submits; API callers can set it themselves).

Revisions: when a ticket whose last review asked for changes matches submit_jql again with a changed summary or
description, it is submitted again as a revision of that submission (previous_submission_id). Comments and
transitions alone (including our own write-back) never resubmit it.

Write-back is idempotent: each step (comment, transition, attachment) is recorded once done, so a retried event
only repeats the steps that failed.
"""

from __future__ import annotations

import base64
import json
import logging

from sqlalchemy import select

from connectors.base import (
    check,
    checked_url,
    config_of,
    content_hash,
    http,
    mark_writeback,
    por_client,
    save_state,
    writeback_done,
)
from db.models import ConnectorConfig, Submission
from db.session import session_scope

log = logging.getLogger("por.connectors.jira")

PAGE_SIZE = 100
MAX_PAGES = 50
RESUBMIT_AFTER = {"changes_requested"}
SETTLED = {"approved", "rejected", "cancelled"}


class JiraConnector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = config_of(cfg)
        self.errors: list[str] = []  # tickets that failed on their own during poll()

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

    @staticmethod
    def _fingerprint(fields: dict) -> str:
        return content_hash(fields.get("summary"), fields.get("description"))

    def submit_issue(self, issue_key: str, risk_tier: str | None = None, previous_submission_id: str | None = None) -> str:
        """Submit a ticket's description for review, linked to the ticket."""
        return self._submit(issue_key, risk_tier, previous_submission_id)[0]

    def _submit(self, issue_key: str, risk_tier: str | None = None,
                previous_submission_id: str | None = None) -> tuple[str, str]:
        """Returns (submission id, content fingerprint)."""
        with self._client() as j:
            issue = check(j.get(f"/rest/api/3/issue/{issue_key}", params={"fields": "summary,description"})).json()
        fields = issue["fields"]
        fingerprint = self._fingerprint(fields)
        desc = fields.get("description")
        text = self._adf_text(desc).strip() if isinstance(desc, dict | list) else (desc or "")
        body = {"workflow_id": self.c["workflow_id"], "text": text or fields.get("summary", ""), "external_ref": f"jira:{issue_key}",
                "risk_tier": risk_tier or self.c.get("risk_tier", "medium"),
                "metadata": {"title": fields.get("summary"), "jira_issue_key": issue_key},
                "previous_submission_id": previous_submission_id,
                "idempotency_key": f"jira:{issue_key}:{issue.get('id')}:{fingerprint[:16]}"}
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"})).json()
        return sub["id"], fingerprint

    def _search(self, j, jql: str) -> list[dict]:
        """Every issue matching the JQL, page by page, up to max_items. Handles both the enhanced search
        (nextPageToken / isLast) and the classic one (startAt / total)."""
        limit = int(self.c.get("max_items") or 500)
        issues: list[dict] = []
        params: dict = {"jql": jql, "fields": "summary,description,updated", "maxResults": PAGE_SIZE}
        for _ in range(MAX_PAGES):
            data = check(j.get("/rest/api/3/search/jql", params=params)).json()
            batch = data.get("issues", [])
            issues += batch
            if len(issues) >= limit or not batch:
                break
            if data.get("nextPageToken") and not data.get("isLast"):
                params = {**params, "nextPageToken": data["nextPageToken"]}
            elif "total" in data and data.get("startAt", 0) + len(batch) < data["total"]:
                params = {**params, "startAt": data.get("startAt", 0) + len(batch)}
            else:
                break
        return issues[:limit]

    def _status(self, api, submission_id: str) -> str | None:
        resp = api.get(f"/v1/submissions/{submission_id}")
        return resp.json().get("status") if resp.status_code == 200 else None

    def poll(self) -> list[str]:
        """Submit issues matching submit_jql that have not been submitted yet, and revised issues whose last review
        asked for changes."""
        jql = self.c.get("submit_jql")
        if not jql:
            return []
        state = dict(self.cfg.state or {})
        tracked: dict = dict(state.get("tracked") or {})  # key -> {"sub": id, "hash": fingerprint, "updated": ts}
        legacy = set(state.get("seen", []))  # older state: keys only
        created = []
        with self._client() as j:
            issues = self._search(j, jql)
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            for issue in issues:
                key = issue.get("key")
                if not key:
                    continue
                try:
                    self._poll_issue(api, tracked, legacy, key, issue.get("fields") or {}, created)
                except Exception as exc:  # noqa: BLE001  (try this ticket again next run; carry on with the others)
                    log.warning("jira issue %s not submitted: %s", key, exc)
                    self.errors.append(f"{key}: {type(exc).__name__}: {exc}"[:300])
        while len(tracked) > 5000:
            tracked.pop(next(iter(tracked)))
        save_state(self.cfg, tracked=tracked)
        return created

    def _poll_issue(self, api, tracked: dict, legacy: set, key: str, fields: dict, created: list) -> None:
        rec = tracked.get(key)
        if rec is None and key in legacy:
            tracked[key] = {"sub": None, "hash": self._fingerprint(fields) if fields else None,
                            "updated": fields.get("updated")}
            return
        if rec is None:
            sub_id, fingerprint = self._submit(key)
            tracked[key] = {"sub": sub_id, "hash": fingerprint, "updated": fields.get("updated")}
            created.append(sub_id)
            return
        if not fields or rec.get("updated") == fields.get("updated"):
            return  # untouched since we last looked
        fingerprint = self._fingerprint(fields)
        if fingerprint == rec.get("hash") or not rec.get("sub"):
            rec["updated"] = fields.get("updated")
            return  # only comments, status or other fields changed
        status = self._status(api, rec["sub"])
        if status in RESUBMIT_AFTER:
            sub_id, fingerprint = self._submit(key, previous_submission_id=rec["sub"])
            tracked[key] = {"sub": sub_id, "hash": fingerprint, "updated": fields.get("updated")}
            created.append(sub_id)
        elif status in SETTLED or status is None:
            tracked[key] = {**rec, "hash": fingerprint, "updated": fields.get("updated")}
        # Still in review: look again next run, the decision may yet ask for changes.

    def on_decided(self, submission_id: str) -> None:
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.get(f"/v1/submissions/{submission_id}")).json()
            key = (sub.get("metadata") or {}).get("jira_issue_key")
            if not key:
                return
            mark = f"{submission_id}:{sub['status']}"
            done = writeback_done(self.cfg, mark)
            pdf = None
            if not done.get("attachment"):
                pdf = check(api.get(f"/v1/submissions/{submission_id}/evidence", params={"format": "pdf"})).content
        decision = sub.get("decision") or {}
        score = (decision.get("assurance") or {}).get("score")
        text = f"Proof of Review decision: {sub['status']} (Review Assurance Score {score})."
        if decision.get("reason"):
            text += f" Reason: {decision['reason']}"
        with self._client() as j:
            if not done.get("comment"):
                check(j.post(f"/rest/api/3/issue/{key}/comment", json={"body": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}}))
                mark_writeback(self.cfg, mark, "comment")
            transition = (self.c.get("transitions") or {}).get(sub["status"])
            if transition and not done.get("transition"):
                check(j.post(f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": str(transition)}}))
                mark_writeback(self.cfg, mark, "transition")
            if pdf is not None:
                check(j.post(f"/rest/api/3/issue/{key}/attachments", headers={"X-Atlassian-Token": "no-check"},
                             files={"file": (f"evidence-{submission_id}.pdf", pdf, "application/pdf")}))
                mark_writeback(self.cfg, mark, "attachment")


def run_once(session, cfg: ConnectorConfig) -> dict:
    conn = JiraConnector(cfg)
    created = conn.poll()
    session.flush()
    return {"submitted": created, "errors": conn.errors}


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
