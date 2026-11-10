"""Proof of Review MCP server.

Submitter mode lets AI agents call the human approval gate as a tool:
    POR_MCP_API_URL=http://localhost:8000 POR_MCP_API_KEY=por_... python -m mcp_server.server
Reviewer mode lets a named human reviewer work their queue from an MCP client:
    POR_MCP_API_URL=http://localhost:8000 POR_MCP_REVIEWER_TOKEN=<token> python -m mcp_server.server
The reviewer token can be a channel-bound token minted for MCP (POST /v1/me/tokens with {"channel": "mcp"}), so it
can only be used through this server and is labelled as such.

Add --http to serve streamable HTTP (at /mcp) instead of stdio. A hosted server needs a bearer token of its own:
    POR_MCP_HTTP_TOKEN=<long random string> POR_MCP_HOST=0.0.0.0 python -m mcp_server.server --http
Every HTTP request must then send "Authorization: Bearer <POR_MCP_HTTP_TOKEN>". Without the token the server only
starts when bound to 127.0.0.1 / localhost (local development). In --http mode file_path is turned off, so remote
callers cannot read the server's own files: they send file_base64 with filename, or content.

A server runs in one mode only. If one server held both credentials, a single agent could submit work and then
approve it, which is exactly what a human-in-the-loop gate exists to prevent.
"""

from __future__ import annotations

import hmac
import os
import sys

from mcp.server.mcpserver import MCPServer

from mcp_server import tools

SUBMITTER_INSTRUCTIONS = (
    "Human-in-the-loop approval gate. Call submit_for_review before taking any action that needs human sign-off, "
    "then wait_for_decision. If the outcome is changes_requested, call get_review_feedback, revise, and resubmit "
    "with previous_submission_id. Only act on an 'approved' outcome."
)
REVIEWER_INSTRUCTIONS = (
    "Review tools for a named human reviewer. The reviewer makes every decision: show them the document_text from "
    "open_review_task, the risk flags and anything still blocking approval, and only call submit_review_decision with "
    "the outcome and reason the reviewer gives you. Never approve, reject or acknowledge a flag on your own judgement. "
    "Decisions are recorded under the reviewer's name, labelled as made through MCP."
)


def mode() -> str:
    has_key, has_token = bool(os.environ.get("POR_MCP_API_KEY")), bool(os.environ.get("POR_MCP_REVIEWER_TOKEN"))
    if has_key and has_token:
        raise SystemExit("Set POR_MCP_API_KEY (submitter) or POR_MCP_REVIEWER_TOKEN (reviewer), not both: one agent must "
                         "not be able to submit work and approve it. Run two servers if you need both.")
    return "reviewer" if has_token else "submitter"


def build(which: str, hosted: bool = False) -> MCPServer:
    """hosted: served over HTTP, so file_path (a path on this server's disk) is turned off."""
    tools.set_allow_file_paths(not hosted)
    srv = MCPServer(name=f"proof-of-review-{which}", instructions=REVIEWER_INSTRUCTIONS if which == "reviewer" else SUBMITTER_INSTRUCTIONS)
    for fn, description in (SUBMITTER_TOOLS if which == "submitter" else REVIEWER_TOOLS):
        if hosted and fn is submit_for_review:
            description = HOSTED_SUBMIT_DESCRIPTION
        srv.tool(description=description)(fn)
    return srv


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
    return await tools.submit_for_review(
        workflow_id, content, file_path, file_base64, filename, risk_tier, external_ref, ai_model, content_origin,
        metadata, previous_submission_id, idempotency_key,
    )


async def get_review_status(submission_id: str) -> dict:
    return await tools.get_review_status(submission_id)


async def wait_for_decision(submission_id: str, timeout_seconds: float = 300, poll_interval_seconds: float = 5) -> dict:
    return await tools.wait_for_decision(submission_id, timeout_seconds, poll_interval_seconds)


async def get_review_feedback(submission_id: str) -> dict:
    return await tools.get_review_feedback(submission_id)


async def list_review_tasks(kind: str | None = None) -> list[dict]:
    return await tools.list_review_tasks(kind)


async def open_review_task(task_id: str) -> dict:
    return await tools.open_review_task(task_id)


async def acknowledge_flag(task_id: str, flag_id: str) -> dict:
    return await tools.acknowledge_flag(task_id, flag_id)


async def add_review_comment(task_id: str, body: str, span_id: str | None = None, suggested_text: str | None = None) -> dict:
    return await tools.add_review_comment(task_id, body, span_id, suggested_text)


async def submit_review_decision(task_id: str, outcome: str, reason: str = "", cited_span_ids: list[str] | None = None,
                                 confirm_warnings: bool = False) -> dict:
    return await tools.submit_review_decision(task_id, outcome, reason, cited_span_ids, confirm_warnings)


SUBMITTER_TOOLS = [
    (submit_for_review, "Submit content (text, a local file path, or base64 file) for human review. Returns a submission ID."),
    (get_review_status, "Get the current review status of a submission and its decision if final."),
    (wait_for_decision, "Block until the submission is decided or the timeout passes. Use before acting on approved content."),
    (get_review_feedback, "Get reviewer comments for a request-changes outcome so the content can be revised and resubmitted."),
]
HOSTED_SUBMIT_DESCRIPTION = ("Submit content (text, or a base64 file with its filename) for human review. Returns a "
                             "submission ID. file_path is not available on a hosted server.")
REVIEWER_TOOLS = [
    (list_review_tasks, "List the reviewer's open review tasks. kind: primary or spot_check."),
    (open_review_task, "Open a task: the review copy as text with span ids, risk flags, comments and what blocks approval."),
    (acknowledge_flag, "Acknowledge a risk flag after the reviewer has read it. High-severity flags must be acknowledged before approving."),
    (add_review_comment, "Add the reviewer's comment, optionally on a span, optionally with suggested replacement text."),
    (submit_review_decision, "Record the reviewer's decision: approve, reject or request_changes (reject and request_changes need a reason)."),
]


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class BearerAuth:
    """ASGI middleware: every HTTP request must carry "Authorization: Bearer <token>" (compared in constant
    time). Lifespan events pass through so the MCP session manager still starts and stops."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            given = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, host: str, token: str | None):
    """The streamable HTTP app (at /mcp), behind the bearer token when one is set."""
    app = server.streamable_http_app(host=host)
    if token:
        return BearerAuth(app, token)
    if host not in LOCAL_HOSTS:
        raise SystemExit("POR_MCP_HTTP_TOKEN must be set to serve MCP over HTTP on a non-local address "
                         f"(POR_MCP_HOST={host}). Clients then send 'Authorization: Bearer <token>'.")
    return app


def main() -> None:
    hosted = "--http" in sys.argv
    server = build(mode(), hosted=hosted)
    if hosted:
        import uvicorn

        # POR_MCP_PORT lets the HTTP server run next to the API, which also defaults to 8000.
        # POR_MCP_HOST=0.0.0.0 in containers; the default only listens on this machine.
        host = os.environ.get("POR_MCP_HOST", "127.0.0.1")
        app = http_app(server, host, os.environ.get("POR_MCP_HTTP_TOKEN") or None)
        uvicorn.run(app, host=host, port=int(os.environ.get("POR_MCP_PORT", "8000")))
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
