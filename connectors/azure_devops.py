"""Azure DevOps connector: work items submitted for review; decisions add a history entry, move the work item and
attach the evidence pack.

Config keys: organization, project, pat (secret, needs Work Items read & write), workflow_id,
             states {"approved": "Done", "rejected": "Removed", "changes_requested": "Active"} (optional),
             submit_wiql (optional): work items matching this WIQL query are submitted automatically, e.g.
             "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Ready for review'",
             base_url (optional, default https://dev.azure.com/<organization>), risk_tier ("medium"),
             max_items (500: work items read per run).
A submission is linked when its metadata contains "ado_work_item_id" (set automatically for work items the
connector submits).

Revisions: when a work item whose last review asked for changes matches submit_wiql again with a changed title or
description (System.Rev moved on and the content differs), it is submitted again as a revision of that submission.
Our own write-back also moves System.Rev on, so the content is compared before resubmitting.

Write-back is idempotent: the uploaded attachment and the work item update are recorded once done, so a retried
event does not upload or add the history entry twice.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re

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

log = logging.getLogger("por.connectors.azure_devops")

API = "api-version=7.1"
BATCH = 200  # the work items batch endpoint takes at most 200 ids
FIELDS = "System.Title,System.Description,System.Rev"
RESUBMIT_AFTER = {"changes_requested"}
SETTLED = {"approved", "rejected", "cancelled"}


def _strip_html(value: str) -> str:
    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", value or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _fingerprint(fields: dict) -> str:
    return content_hash(fields.get("System.Title"), fields.get("System.Description"))


class AzureDevOpsConnector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = config_of(cfg)
        self.errors: list[str] = []  # work items that failed on their own during poll()

    def _base(self) -> str:
        base = (self.c.get("base_url") or f"https://dev.azure.com/{self.c['organization']}").rstrip("/")
        return checked_url(f"{base}/{self.c['project']}")

    def _client(self):
        token = base64.b64encode(f":{self.c['pat']}".encode()).decode()
        return http(base_url=self._base(), headers={"Authorization": f"Basic {token}", "Accept": "application/json"})

    def submit_work_item(self, item_id: int | str, item: dict | None = None, previous_submission_id: str | None = None) -> str:
        """Submit a work item (fetched unless given) for review, linked to it."""
        if item is None:
            with self._client() as c:
                item = check(c.get(f"/_apis/wit/workitems/{item_id}?{API}", params={"fields": FIELDS})).json()
        fields = item.get("fields", {})
        title = fields.get("System.Title") or f"Work item {item_id}"
        text = _strip_html(fields.get("System.Description") or "") or title
        body = {"workflow_id": self.c["workflow_id"], "text": text, "external_ref": f"ado:{item_id}",
                "risk_tier": self.c.get("risk_tier", "medium"),
                "metadata": {"title": title, "ado_work_item_id": str(item_id)},
                "previous_submission_id": previous_submission_id,
                "idempotency_key": f"ado:{item_id}:{fields.get('System.Rev', item.get('rev', ''))}"}
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"})).json()
        return sub["id"]

    def _query(self, c, wiql: str) -> list[dict]:
        """Run the WIQL query and fetch the matching work items in batches of 200, up to max_items."""
        limit = int(self.c.get("max_items") or 500)
        data = check(c.post(f"/_apis/wit/wiql?{API}&$top={limit}", json={"query": wiql})).json()
        ids = [str(ref["id"]) for ref in data.get("workItems", []) if ref.get("id") is not None][:limit]
        items: list[dict] = []
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            page = check(c.get(f"/_apis/wit/workitems?{API}",
                               params={"ids": ",".join(chunk), "fields": FIELDS, "errorPolicy": "omit"})).json()
            items += [it for it in page.get("value", []) if it]
        return items

    def poll(self) -> list[str]:
        wiql = self.c.get("submit_wiql")
        if not wiql:
            return []
        state = dict(self.cfg.state or {})
        tracked: dict = dict(state.get("tracked") or {})  # id -> {"sub": id, "hash": fingerprint, "rev": n}
        legacy = set(state.get("seen", []))  # older state: ids only
        created: list[str] = []
        with self._client() as c:
            items = self._query(c, wiql)
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            for item in items:
                wid = str(item.get("id") or "")
                if not wid:
                    continue
                try:
                    self._poll_item(api, tracked, legacy, wid, item, created)
                except Exception as exc:  # noqa: BLE001  (try this work item again next run; carry on)
                    log.warning("azure devops work item %s not submitted: %s", wid, exc)
                    self.errors.append(f"{wid}: {type(exc).__name__}: {exc}"[:300])
        while len(tracked) > 5000:
            tracked.pop(next(iter(tracked)))
        save_state(self.cfg, tracked=tracked)
        return created

    def _poll_item(self, api, tracked: dict, legacy: set, wid: str, item: dict, created: list) -> None:
        fields = item.get("fields") or {}
        rev = fields.get("System.Rev", item.get("rev"))
        rec = tracked.get(wid)
        if rec is None and wid in legacy:
            tracked[wid] = {"sub": None, "hash": _fingerprint(fields), "rev": rev}
            return
        if rec is None:
            sub_id = self.submit_work_item(wid, item)
            tracked[wid] = {"sub": sub_id, "hash": _fingerprint(fields), "rev": rev}
            created.append(sub_id)
            return
        if rec.get("rev") == rev:
            return  # unchanged since we last looked
        fingerprint = _fingerprint(fields)
        if fingerprint == rec.get("hash") or not rec.get("sub"):
            rec["rev"] = rev
            return  # only state, history or other fields changed (our own write-back among them)
        resp = api.get(f"/v1/submissions/{rec['sub']}")
        status = resp.json().get("status") if resp.status_code == 200 else None
        if status in RESUBMIT_AFTER:
            sub_id = self.submit_work_item(wid, item, previous_submission_id=rec["sub"])
            tracked[wid] = {"sub": sub_id, "hash": fingerprint, "rev": rev}
            created.append(sub_id)
        elif status in SETTLED or status is None:
            tracked[wid] = {**rec, "hash": fingerprint, "rev": rev}
        # Still in review: look again next run, the decision may yet ask for changes.

    def on_decided(self, submission_id: str) -> None:
        with por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            sub = check(api.get(f"/v1/submissions/{submission_id}")).json()
            item = (sub.get("metadata") or {}).get("ado_work_item_id")
            if not item:
                return
            mark = f"{submission_id}:{sub['status']}"
            done = writeback_done(self.cfg, mark)
            if done.get("update"):
                return
            pdf = None
            if not done.get("attachment_url"):
                pdf = check(api.get(f"/v1/submissions/{submission_id}/evidence", params={"format": "pdf"})).content
        decision = sub.get("decision") or {}
        score = (decision.get("assurance") or {}).get("score")
        note = f"Proof of Review decision: {sub['status']} (Review Assurance Score {score})."
        if decision.get("reason"):
            note += f" Reason: {decision['reason']}"
        with self._client() as c:
            attachment_url = done.get("attachment_url")
            if pdf is not None:
                upload = check(c.post(f"/_apis/wit/attachments?fileName=evidence-{submission_id}.pdf&{API}", content=pdf,
                                      headers={"Content-Type": "application/octet-stream"})).json()
                attachment_url = upload["url"]
                mark_writeback(self.cfg, mark, "attachment_url", attachment_url)
            ops = [
                {"op": "add", "path": "/fields/System.History", "value": html.escape(note)},
                {"op": "add", "path": "/relations/-", "value": {
                    "rel": "AttachedFile", "url": attachment_url,
                    "attributes": {"comment": "Proof of Review evidence pack"}}},
            ]
            state = (self.c.get("states") or {}).get(sub["status"])
            if state:
                ops.append({"op": "add", "path": "/fields/System.State", "value": state})
            # One PATCH: history, attachment link and state change apply together or not at all.
            check(c.patch(f"/_apis/wit/workitems/{item}?{API}", json=ops,
                          headers={"Content-Type": "application/json-patch+json"}))
            mark_writeback(self.cfg, mark, "update")


def run_once(session, cfg: ConnectorConfig) -> dict:
    conn = AzureDevOpsConnector(cfg)
    created = conn.poll()
    session.flush()
    return {"submitted": created, "errors": conn.errors}


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
