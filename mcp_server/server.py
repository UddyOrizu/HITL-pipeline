"""Proof of Review MCP server.

Submitter mode lets AI agents call the human approval gate as a tool:
    POR_MCP_API_URL=http://localhost:8000 POR_MCP_API_KEY=por_... python -m mcp_server.server
Reviewer mode lets a named human reviewer work their queue from an MCP client:
    POR_MCP_API_URL=http://localhost:8000 POR_MCP_REVIEWER_TOKEN=<token> python -m mcp_server.server
Add --http to serve streamable HTTP instead of stdio.

A server runs in one mode only. If one server held both credentials, a single agent could submit work and then
approve it, which is exactly what a human-in-the-loop gate exists to prevent.
"""

from __future__ import annotations

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


def build(which: str) -> MCPServer:
    srv = MCPServer(name=f"proof-of-review-{which}", instructions=REVIEWER_INSTRUCTIONS if which == "reviewer" else SUBMITTER_INSTRUCTIONS)
    for fn, description in (SUBMITTER_TOOLS if which == "submitter" else REVIEWER_TOOLS):
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
REVIEWER_TOOLS = [
    (list_review_tasks, "List the reviewer's open review tasks. kind: primary or spot_check."),
    (open_review_task, "Open a task: the review copy as text with span ids, risk flags, comments and what blocks approval."),
    (acknowledge_flag, "Acknowledge a risk flag after the reviewer has read it. High-severity flags must be acknowledged before approving."),
    (add_review_comment, "Add the reviewer's comment, optionally on a span, optionally with suggested replacement text."),
    (submit_review_decision, "Record the reviewer's decision: approve, reject or request_changes (reject and request_changes need a reason)."),
]


def main() -> None:
    server = build(mode())
    if "--http" in sys.argv:
        # POR_MCP_PORT lets the HTTP server run next to the API, which also defaults to 8000.
        # POR_MCP_HOST=0.0.0.0 in containers; the default only listens on this machine.
        server.run("streamable-http", host=os.environ.get("POR_MCP_HOST", "127.0.0.1"),
                   port=int(os.environ.get("POR_MCP_PORT", "8000")))
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
