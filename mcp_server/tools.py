"""MCP tool implementations: a thin layer over the REST API (one source of truth for logic).

Two modes, never both in one server:
- submitter (POR_MCP_API_KEY, a source app key): submit work and wait for the decision. The agent only ever sees
  the decision, reasons and comments: never canaries, telemetry or reviewer identities.
- reviewer (POR_MCP_REVIEWER_TOKEN, a reviewer's sign-in token): a named human reviewer works their queue from an
  MCP client. They see the review copy, exactly as in the web workspace.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from mcp.server.mcpserver.exceptions import ToolError

FINAL = {"approved", "rejected", "changes_requested", "deferred", "cancelled"}

ClientFactory = Callable[[], httpx.AsyncClient]


CHANNEL = {"X-PoR-Channel": "mcp"}


def default_client() -> httpx.AsyncClient:
    base = os.environ.get("POR_MCP_API_URL", "http://localhost:8000")
    key = os.environ.get("POR_MCP_API_KEY")
    if not key:
        raise RuntimeError("POR_MCP_API_KEY must be set to a source app API key")
    return httpx.AsyncClient(base_url=base, headers={"X-API-Key": key, **CHANNEL}, timeout=60)


def default_reviewer_client() -> httpx.AsyncClient:
    base = os.environ.get("POR_MCP_API_URL", "http://localhost:8000")
    token = os.environ.get("POR_MCP_REVIEWER_TOKEN")
    if not token:
        raise RuntimeError("POR_MCP_REVIEWER_TOKEN must be set to a reviewer's sign-in token")
    return httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}", **CHANNEL}, timeout=60)


_factory: ClientFactory = default_client
_reviewer_factory: ClientFactory = default_reviewer_client

# Reading file_path from this machine's disk only makes sense when the MCP client runs on the same machine
# (stdio). A hosted (--http) server turns it off, so a remote caller cannot read the server's own files.
_allow_file_paths = True


def set_allow_file_paths(allowed: bool) -> None:
    global _allow_file_paths
    _allow_file_paths = allowed


def set_client_factory(factory: ClientFactory) -> None:
    global _factory
    _factory = factory


def set_reviewer_client_factory(factory: ClientFactory) -> None:
    global _reviewer_factory
    _reviewer_factory = factory


class ToolFailure(ToolError):
    """An expected failure. Subclassing the SDK's ToolError means the client sees the reason (for example which
    flags still need acknowledging); any other exception is reported only as "Error executing tool"."""


def _check(resp: httpx.Response) -> dict:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = resp.text
        if isinstance(detail, dict):  # review errors carry a code and details, e.g. which flags are still open
            detail = json.dumps(detail)
        raise ToolFailure(f"Proof of Review API error {resp.status_code}: {detail}")
    return resp.json() if resp.content else {}


def _agent_view(sub: dict) -> dict:
    """Strip everything an agent must not see."""
    decision = sub.get("decision") or None
    out = {
        "submission_id": sub["id"],
        "status": sub["status"],
        "is_final": sub["status"] in FINAL,
        "external_ref": sub.get("external_ref"),
        "current_stage": sub.get("current_stage"),
    }
    if decision:
        out["decision"] = {
            "outcome": decision.get("outcome"),
            "reason": decision.get("reason"),
            "comments": [
                {"span_id": c.get("span_id"), "comment": c.get("body"), "suggested_text": c.get("suggested_text")}
                for c in decision.get("comments") or []
            ],
        }
    return out


async def submit_for_review(
    workflow_id: str,
    content: str | None = None,
    file_path: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
    risk_tier: str = "medium",
    external_ref: str | None = None,
    ai_model: str | None = None,
    content_origin: str = "ai",
    metadata: dict | None = None,
    previous_submission_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    body: dict = {
        "workflow_id": workflow_id, "risk_tier": risk_tier, "external_ref": external_ref, "ai_model": ai_model,
        "content_origin": content_origin, "metadata": metadata or {}, "previous_submission_id": previous_submission_id,
        "idempotency_key": idempotency_key,
    }
    given = [x is not None for x in (content, file_path, file_base64)]
    if sum(given) != 1:
        raise ToolFailure("provide exactly one of content, file_path or file_base64")
    if content is not None:
        body["text"] = content
    elif file_path is not None:
        if not _allow_file_paths:
            raise ToolFailure("file_path is not available on a hosted (HTTP) MCP server: send the file as "
                              "file_base64 with filename, or the text as content")
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise ToolFailure(f"file not found: {file_path}")
        body["file"] = {"filename": path.name, "content_base64": base64.b64encode(path.read_bytes()).decode()}
    else:
        if not filename:
            raise ToolFailure("filename is required with file_base64")
        body["file"] = {"filename": filename, "content_base64": file_base64}
    async with _factory() as client:
        sub = _check(await client.post("/v1/submissions", content=json.dumps(body), headers={"Content-Type": "application/json"}))
    return _agent_view(sub)


async def get_review_status(submission_id: str) -> dict:
    async with _factory() as client:
        return _agent_view(_check(await client.get(f"/v1/submissions/{submission_id}")))


async def wait_for_decision(submission_id: str, timeout_seconds: float = 300, poll_interval_seconds: float = 5) -> dict:
    """Long-poll until the submission reaches a final status or the timeout passes."""
    deadline = time.monotonic() + max(0.0, min(timeout_seconds, 3600))
    while True:
        view = await get_review_status(submission_id)
        if view["is_final"] or time.monotonic() >= deadline:
            view["timed_out"] = not view["is_final"]
            return view
        await asyncio.sleep(max(0.1, poll_interval_seconds))


async def get_review_feedback(submission_id: str) -> dict:
    async with _factory() as client:
        fb = _check(await client.get(f"/v1/submissions/{submission_id}/feedback"))
    return {
        "submission_id": fb["submission_id"],
        "status": fb["status"],
        "outcome": fb["outcome"],
        "reason": fb["reason"],
        "comments": [
            {"span_id": c.get("span_id"), "comment": c.get("body"), "suggested_text": c.get("suggested_text")}
            for c in fb.get("comments") or []
        ],
        "next_step": (
            "Revise the content using these comments and call submit_for_review again with previous_submission_id."
            if fb["outcome"] == "changes_requested" else None
        ),
    }


# --- Reviewer tools ---------------------------------------------------------------------


async def list_review_tasks(kind: str | None = None) -> list[dict]:
    """The reviewer's open tasks (primary reviews and blind spot-checks)."""
    async with _reviewer_factory() as client:
        return _check(await client.get("/v1/review/queue", params={"kind": kind} if kind else None))


def _as_text(view: dict) -> str:
    """The review copy as numbered text, so a reviewer can read it in any MCP client and cite span ids."""
    flags_by_span: dict[str, list[dict]] = {}
    for f in view["flags"]:
        flags_by_span.setdefault(f["span_id"], []).append(f)
    lines = [f"# {view['submission']['title']}", ""]
    for sec in view["document"]["sections"]:
        if sec.get("title"):
            lines += [f"## {sec['title']}", ""]
        for sp in sec["spans"]:
            marks = "".join(f" [FLAG {f['severity'].upper()} {f['category']}]" for f in flags_by_span.get(sp["id"], []))
            lines.append(f"[{sp['id']}]{marks} {sp['text']}")
        lines.append("")
    return "\n".join(lines)


async def open_review_task(task_id: str) -> dict:
    """The review copy with span ids, risk flags, comments and what still blocks approval."""
    async with _reviewer_factory() as client:
        view = _check(await client.get(f"/v1/review/tasks/{task_id}"))
    pending = [f["id"] for f in view["flags"] if f["mandatory"] and not f["acknowledged"]]
    return {
        "task": view["task"],
        "submission": view["submission"],
        "document_text": _as_text(view),
        "flags": view["flags"],
        "comments": view["comments"],
        "prior_stages": view.get("prior_stages", []),
        "must_acknowledge_before_approving": pending,
        "note": ("Reading time is not measured in MCP clients, so this decision is recorded as an MCP decision with no "
                 "reading telemetry. The workflow's reading standard may ask you to confirm (confirm_warnings) or may "
                 "require the web workspace."),
    }


async def acknowledge_flag(task_id: str, flag_id: str) -> dict:
    async with _reviewer_factory() as client:
        _check(await client.post(f"/v1/review/tasks/{task_id}/flags/{flag_id}/open"))
        return _check(await client.post(f"/v1/review/tasks/{task_id}/flags/{flag_id}/acknowledge"))


async def add_review_comment(task_id: str, body: str, span_id: str | None = None, suggested_text: str | None = None) -> dict:
    payload = {"body": body, "span_id": span_id, "kind": "edit" if suggested_text else "comment",
               "suggested_text": suggested_text}
    async with _reviewer_factory() as client:
        return _check(await client.post(f"/v1/review/tasks/{task_id}/comments", json=payload))


async def submit_review_decision(task_id: str, outcome: str, reason: str = "", cited_span_ids: list[str] | None = None,
                                 confirm_warnings: bool = False) -> dict:
    """approve | reject | request_changes. Rejecting and requesting changes need a reason."""
    payload = {"outcome": outcome, "reason": reason, "cited_span_ids": cited_span_ids or [], "confirm_warnings": confirm_warnings}
    async with _reviewer_factory() as client:
        return _check(await client.post(f"/v1/review/tasks/{task_id}/decision", json=payload))
