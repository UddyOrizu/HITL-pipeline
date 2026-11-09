"""Azure DevOps connector: work items submitted for review; decisions add a history entry, move the work item and
attach the evidence pack.

Config keys: organization, project, pat (secret, needs Work Items read & write), workflow_id,
             states {"approved": "Done", "rejected": "Removed", "changes_requested": "Active"} (optional),
             submit_wiql (optional): work items matching this WIQL query are submitted automatically, e.g.
             "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Ready for review'",
             base_url (optional, default https://dev.azure.com/<organization>), risk_tier ("medium").
A submission is linked when its metadata contains "ado_work_item_id" (set automatically for work items the
connector submits).
"""

from __future__ import annotations

import base64
import html
import json
import re

from sqlalchemy import select

from connectors.base import check, checked_url, config_of, http, por_client
from db.models import ConnectorConfig, Submission
from db.session import session_scope

API = "api-version=7.1"


def _strip_html(value: str) -> str:
    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", value or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


class AzureDevOpsConnector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = config_of(cfg)

    def _base(self) -> str:
        base = (self.c.get("base_url") or f"https://dev.azure.com/{self.c['organization']}").rstrip("/")
        return checked_url(f"{base}/{self.c['project']}")

    def _client(self):
        token = base64.b64encode(f":{self.c['pat']}".encode()).decode()
        return http(base_url=self._base(), headers={"Authorization": f"Basic {token}", "Accept": "application/json"})

    def submit_work_item(self, item_id: int | str) -> str:
        with self._client() as c:
            item = check(c.get(f"/_apis/wit/workitems/{item_id}?{API}",
                               params={"fields": "System.Title,System.Description,System.Rev"})).json()
        fields = item.get("fields", {})
        title = fields.get("System.Title") or f"Work item {item_id}"
        text = _strip_html(fields.get("System.Description") or "") or title
        body = {"workflow_id": self.c["workflow_id"], "text": text, "external_ref": f"ado:{item_id}",
                "risk_tier": self.c.get("risk_tier", "medium"),
                "metadata": {"title": title, "ado_work_item_id": str(item_id)},
                "idempotency_key": f"ado:{item_id}:{fields.get('System.Rev', item.get('rev', ''))}"}
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"})).json()
        return sub["id"]

    def poll(self) -> list[str]:
        wiql = self.c.get("submit_wiql")
        if not wiql:
            return []
        state = dict(self.cfg.state or {})
        seen: list = list(state.get("seen", []))
        created = []
        with self._client() as c:
            data = check(c.post(f"/_apis/wit/wiql?{API}", json={"query": wiql})).json()
        for ref in data.get("workItems", [])[:50]:
            wid = str(ref.get("id"))
            if not wid or wid in seen:
                continue
            created.append(self.submit_work_item(wid))
            seen.append(wid)
        state["seen"] = seen[-5000:]
        self.cfg.state = state
        return created

    def on_decided(self, submission_id: str) -> None:
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.get(f"/v1/submissions/{submission_id}")).json()
            item = (sub.get("metadata") or {}).get("ado_work_item_id")
            if not item:
                return
            pdf = check(api.get(f"/v1/submissions/{submission_id}/evidence", params={"format": "pdf"})).content
        decision = sub.get("decision") or {}
        score = (decision.get("assurance") or {}).get("score")
        note = f"Proof of Review decision: {sub['status']} (Review Assurance Score {score})."
        if decision.get("reason"):
            note += f" Reason: {decision['reason']}"
        with self._client() as c:
            upload = check(c.post(f"/_apis/wit/attachments?fileName=evidence-{submission_id}.pdf&{API}", content=pdf,
                                  headers={"Content-Type": "application/octet-stream"})).json()
            ops = [
                {"op": "add", "path": "/fields/System.History", "value": html.escape(note)},
                {"op": "add", "path": "/relations/-", "value": {
                    "rel": "AttachedFile", "url": upload["url"],
                    "attributes": {"comment": "Proof of Review evidence pack"}}},
            ]
            state = (self.c.get("states") or {}).get(sub["status"])
            if state:
                ops.append({"op": "add", "path": "/fields/System.State", "value": state})
            check(c.patch(f"/_apis/wit/workitems/{item}?{API}", json=ops,
                          headers={"Content-Type": "application/json-patch+json"}))


def run_once(session, cfg: ConnectorConfig) -> dict:
    created = AzureDevOpsConnector(cfg).poll()
    session.flush()
    return {"submitted": created}


def on_decided(event: dict) -> None:
    from connectors import kind_of

    with session_scope(event["tenant_id"]) as s:
        sub = s.get(Submission, event["submission_id"])
        if sub is None:
            return
        rows = s.scalars(select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == event["tenant_id"], ConnectorConfig.kind.in_(["azure_devops", "jira"]),
            ConnectorConfig.active.is_(True), ConnectorConfig.source_app_id == sub.source_app_id)).all()
        for cfg in rows:
            if kind_of(cfg) == "azure_devops":
                AzureDevOpsConnector(cfg).on_decided(sub.id)
