"""SharePoint / OneDrive connector (Microsoft Graph).

A monitored folder: documents dropped in are submitted; when decided, the decision is written back as
list-item metadata and the file moves to an Approved or Rejected folder.

Config keys: aad_tenant_id, client_id, client_secret (secret), drive_id, workflow_id,
             inbox_folder (default "Review/Inbox"), approved_folder ("Review/Approved"),
             rejected_folder ("Review/Rejected"), changes_folder ("Review/Changes requested"), risk_tier ("medium").
"""

from __future__ import annotations

import base64
import json
from urllib.parse import quote

from sqlalchemy.orm import Session

from connectors.base import check, config_of, http, por_client
from db.models import ConnectorConfig

GRAPH = "https://graph.microsoft.com/v1.0"
SUPPORTED = (".txt", ".docx", ".pptx", ".ppt", ".pdf")


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

    def poll(self) -> list[str]:
        """Submit new files from the inbox folder. Returns new submission ids."""
        state = dict(self.cfg.state or {})
        pending: dict = dict(state.get("pending", {}))
        seen: list = list(state.get("seen", []))
        created = []
        drive = self.c["drive_id"]
        inbox = self._folder("inbox_folder", "Review/Inbox")
        with self._graph() as g, por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            items = check(g.get(f"/drives/{drive}/root:/{quote(inbox)}:/children")).json().get("value", [])
            for item in items:
                if "file" not in item or not item["name"].lower().endswith(SUPPORTED):
                    continue
                marker = f"{item['id']}:{item.get('eTag', '')}"
                if marker in seen:
                    continue
                content = check(g.get(f"/drives/{drive}/items/{item['id']}/content", follow_redirects=True)).content
                body = {
                    "workflow_id": self.c["workflow_id"],
                    "file": {"filename": item["name"], "content_base64": base64.b64encode(content).decode()},
                    "external_ref": f"sharepoint:{item['id']}",
                    "risk_tier": self.c.get("risk_tier", "medium"),
                    "content_origin": self.c.get("content_origin", "mixed"),
                    "metadata": {"title": item["name"], "sharepoint_item_id": item["id"], "web_url": item.get("webUrl")},
                    "idempotency_key": f"sp:{marker}",
                }
                sub = check(api.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"})).json()
                pending[sub["id"]] = item["id"]
                seen.append(marker)
                created.append(sub["id"])
        state.update(pending=pending, seen=seen[-5000:])
        self.cfg.state = state
        return created

    def sync_decisions(self) -> list[str]:
        """Write decisions back as metadata and move decided files. Returns submission ids completed."""
        state = dict(self.cfg.state or {})
        pending: dict = dict(state.get("pending", {}))
        done = []
        drive = self.c["drive_id"]
        folders = {
            "approved": self._folder("approved_folder", "Review/Approved"),
            "rejected": self._folder("rejected_folder", "Review/Rejected"),
            "changes_requested": self._folder("changes_folder", "Review/Changes requested"),
        }
        with self._graph() as g, por_client(self.cfg.source_app_id, self.cfg.tenant_id) as api:
            for sub_id, item_id in list(pending.items()):
                sub = check(api.get(f"/v1/submissions/{sub_id}")).json()
                if sub["status"] not in ("approved", "rejected", "changes_requested", "deferred", "cancelled"):
                    continue
                decision = sub.get("decision") or {}
                score = (decision.get("assurance") or {}).get("score")
                check(g.patch(f"/drives/{drive}/items/{item_id}/listItem/fields", json={
                    "ReviewStatus": sub["status"], "ReviewAssuranceScore": score, "ReviewSubmissionId": sub_id,
                    "ReviewEvidenceUrl": decision.get("evidence_url"),
                }))
                folder = folders.get(sub["status"])
                if folder:
                    check(g.patch(f"/drives/{drive}/items/{item_id}",
                                  json={"parentReference": {"path": f"/drives/{drive}/root:/{folder}"}}))
                del pending[sub_id]
                done.append(sub_id)
        state["pending"] = pending
        self.cfg.state = state
        return done


def run_once(session: Session, cfg: ConnectorConfig) -> dict:
    conn = SharePointConnector(cfg)
    created = conn.poll()
    done = conn.sync_decisions()
    session.flush()
    return {"submitted": created, "completed": done}
