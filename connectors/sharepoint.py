"""SharePoint / OneDrive connector (Microsoft Graph).

A monitored folder: documents dropped in are submitted; when decided, the decision is written back as
list-item metadata and the file moves to an Approved, Rejected, Changes requested, Deferred or Cancelled folder.

Config keys: aad_tenant_id, client_id, client_secret (secret), drive_id, workflow_id,
             inbox_folder (default "Review/Inbox"), approved_folder ("Review/Approved"),
             rejected_folder ("Review/Rejected"), changes_folder ("Review/Changes requested"),
             deferred_folder ("Review/Deferred"), cancelled_folder ("Review/Cancelled"), risk_tier ("medium"),
             max_items (500: files read from the inbox per run).
Set a folder to the inbox folder to leave those files where they are.

A file is submitted again only when its content changes (Graph cTag), not when its metadata does, so writing the
decision back never resubmits it. Deferred items stay tracked until a final decision, which is then written back.
Each item is written back on its own: one failing file is retried on the next run (up to MAX_SYNC_FAILURES times)
without holding up the others, and its error shows on the connector.
"""

from __future__ import annotations

import base64
import json
import logging
from urllib.parse import quote

from sqlalchemy.orm import Session

from connectors.base import check, config_of, http, por_client, save_state
from db.models import ConnectorConfig

log = logging.getLogger("por.connectors.sharepoint")

GRAPH = "https://graph.microsoft.com/v1.0"
SUPPORTED = (".txt", ".docx", ".pptx", ".ppt", ".pdf")
FINAL = ("approved", "rejected", "changes_requested", "cancelled")
WRITTEN_BACK = (*FINAL, "deferred")
MAX_SYNC_FAILURES = 20
MAX_PAGES = 50


def _content_marker(item: dict) -> str:
    """Changes when the file's content changes, not when we patch its metadata or move it (unlike eTag)."""
    hashes = (item.get("file") or {}).get("hashes") or {}
    return str(item.get("cTag") or hashes.get("quickXorHash") or hashes.get("sha256Hash") or item.get("eTag", ""))


def _pending_entry(value) -> dict:
    """Pending entries were plain item ids in older state."""
    return dict(value) if isinstance(value, dict) else {"item": value}


class SharePointConnector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.c = config_of(cfg)

    def _token(self) -> str:
        with http() as client:
            resp = check(client.post(
                f"https://login.microsoftonline.com/{self.c['aad_tenant_id']}/oauth2/v2.0/token",
                data={"grant_type": "client_credentials", "client_id": self.c["client_id"],
                      "client_secret": self.c["client_secret"], "scope": "https://graph.microsoft.com/.default"},
            ))
        return resp.json()["access_token"]

    def _graph(self):
        return http(base_url=GRAPH, headers={"Authorization": f"Bearer {self._token()}"})

    def _folder(self, key: str, default: str) -> str:
        return self.c.get(key, default).strip("/")

    def _children(self, g, path: str) -> list[dict]:
        """The items in a folder, following @odata.nextLink, up to max_items."""
        limit = int(self.c.get("max_items") or 500)
        items: list[dict] = []
        url = path
        for _ in range(MAX_PAGES):
            page = check(g.get(url)).json()
            items += page.get("value", [])
            url = page.get("@odata.nextLink")
            if not url or len(items) >= limit:
                break
        return items[:limit]

    def poll(self) -> tuple[list[str], list[str]]:
        """Submit new or changed files from the inbox folder. Returns (new submission ids, errors)."""
        state = dict(self.cfg.state or {})
        pending: dict = dict(state.get("pending", {}))
        known: dict = dict(state.get("items", {}))  # item id -> content marker last submitted
        legacy = set(state.get("seen", []))  # older state: "<item id>:<eTag>" markers
        created: list[str] = []
        errors: list[str] = []
        drive = self.c["drive_id"]
        inbox = self._folder("inbox_folder", "Review/Inbox")
        with self._graph() as g, por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            for item in self._children(g, f"/drives/{drive}/root:/{quote(inbox)}:/children"):
                if "file" not in item or not item["name"].lower().endswith(SUPPORTED):
                    continue
                marker = _content_marker(item)
                if known.get(item["id"]) == marker:
                    continue
                if f"{item['id']}:{item.get('eTag', '')}" in legacy:
                    known[item["id"]] = marker
                    continue
                try:
                    content = check(g.get(f"/drives/{drive}/items/{item['id']}/content", follow_redirects=True)).content
                    body = {
                        "workflow_id": self.c["workflow_id"],
                        "file": {"filename": item["name"], "content_base64": base64.b64encode(content).decode()},
                        "external_ref": f"sharepoint:{item['id']}",
                        "risk_tier": self.c.get("risk_tier", "medium"),
                        "content_origin": self.c.get("content_origin", "mixed"),
                        "metadata": {"title": item["name"], "sharepoint_item_id": item["id"], "web_url": item.get("webUrl")},
                        "idempotency_key": f"sp:{item['id']}:{marker}",
                    }
                    sub = check(api.post("/v1/submissions", content=json.dumps(body),
                                         headers={"Content-Type": "application/json"})).json()
                except Exception as exc:  # noqa: BLE001  (try this file again next run; carry on with the others)
                    log.warning("sharepoint item %s not submitted: %s", item["id"], exc)
                    errors.append(f"{item['name']}: {type(exc).__name__}: {exc}"[:300])
                    continue
                pending[sub["id"]] = {"item": item["id"]}
                known.pop(item["id"], None)
                known[item["id"]] = marker  # most recent last
                created.append(sub["id"])
        while len(known) > 5000:
            known.pop(next(iter(known)))
        save_state(self.cfg, pending=pending, items=known)
        return created, errors

    def sync_decisions(self) -> tuple[list[str], list[str]]:
        """Write decisions back as metadata and move decided files. Returns (submission ids completed, errors)."""
        state = dict(self.cfg.state or {})
        pending: dict = {k: _pending_entry(v) for k, v in (state.get("pending") or {}).items()}
        failed: list = list(state.get("failed", []))
        done: list[str] = []
        errors: list[str] = []
        if not pending:
            return done, errors
        drive = self.c["drive_id"]
        inbox = self._folder("inbox_folder", "Review/Inbox")
        folders = {
            "approved": self._folder("approved_folder", "Review/Approved"),
            "rejected": self._folder("rejected_folder", "Review/Rejected"),
            "changes_requested": self._folder("changes_folder", "Review/Changes requested"),
            "deferred": self._folder("deferred_folder", "Review/Deferred"),
            "cancelled": self._folder("cancelled_folder", "Review/Cancelled"),
        }
        with self._graph() as g, por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            for sub_id, entry in list(pending.items()):
                try:
                    sub = check(api.get(f"/v1/submissions/{sub_id}")).json()
                    status = sub["status"]
                    if status not in WRITTEN_BACK or entry.get("status") == status:
                        continue  # still in review, or this outcome (deferred) is already written back
                    self._write_back(g, drive, entry["item"], sub_id, sub, folders.get(status), inbox)
                except Exception as exc:  # noqa: BLE001  (retried next run; the other items carry on)
                    entry["failures"] = entry.get("failures", 0) + 1
                    entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
                    errors.append(f"submission {sub_id}: {entry['error']}")
                    if entry["failures"] >= MAX_SYNC_FAILURES:
                        log.error("sharepoint write-back for %s given up after %s attempts", sub_id, entry["failures"])
                        failed = [*failed, {"submission_id": sub_id, **entry}][-200:]
                        del pending[sub_id]
                    continue
                entry.pop("failures", None)
                entry.pop("error", None)
                if status in FINAL:
                    del pending[sub_id]
                    done.append(sub_id)
                else:
                    entry["status"] = status  # deferred: keep tracking until a final decision
        save_state(self.cfg, pending=pending, failed=failed)
        return done, errors

    def _write_back(self, g, drive: str, item_id: str, sub_id: str, sub: dict, folder: str | None, inbox: str) -> None:
        decision = sub.get("decision") or {}
        score = (decision.get("assurance") or {}).get("score")
        check(g.patch(f"/drives/{drive}/items/{item_id}/listItem/fields", json={
            "ReviewStatus": sub["status"], "ReviewAssuranceScore": score, "ReviewSubmissionId": sub_id,
            "ReviewEvidenceUrl": decision.get("evidence_url"),
        }))
        if folder and folder != inbox:
            check(g.patch(f"/drives/{drive}/items/{item_id}",
                          json={"parentReference": {"path": f"/drives/{drive}/root:/{folder}"}}))


def poll_step(session: Session, cfg: ConnectorConfig) -> dict:
    created, errors = SharePointConnector(cfg).poll()
    session.flush()
    return {"submitted": created, "errors": errors}


def sync_step(session: Session, cfg: ConnectorConfig) -> dict:
    done, errors = SharePointConnector(cfg).sync_decisions()
    session.flush()
    return {"completed": done, "errors": errors}


def run_once(session: Session, cfg: ConnectorConfig) -> dict:
    """Both steps in the caller's session (poll_one runs them as separate transactions instead)."""
    first, second = poll_step(session, cfg), sync_step(session, cfg)
    return {"submitted": first["submitted"], "completed": second["completed"], "errors": first["errors"] + second["errors"]}
