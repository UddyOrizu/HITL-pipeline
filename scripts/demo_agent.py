"""A test Agno agent that plays a source application: it drafts a short document and sends it for human review.

It submits the same way a real agent would, through either door (or both, one after the other):

    --via api   the REST API, with plain Python function tools that call POST /v1/submissions
    --via mcp   the MCP server's submit_for_review tool (submitter mode)

Usage, from the repo root with the stack running (python run.py up --seed):

    .venv/bin/python -m scripts.demo_agent                      # both, one submission each
    .venv/bin/python -m scripts.demo_agent --via api
    .venv/bin/python -m scripts.demo_agent --via mcp --topic "a late-payment reminder"
    .venv/bin/python -m scripts.demo_agent --via mcp --mcp-url http://127.0.0.1:8001/mcp

After each run the script asks the API for the submission by its external reference, so it exits 0 only when
the work really arrived. The agent never waits for the decision: a human makes that in the review workspace.

Needs a real model: POR_MODEL_PROVIDER=anthropic or openai, with POR_MODEL_API_KEY (the offline heuristic engine
cannot run an agent). The model comes from services/model_gateway.py, like every other agent here. The source
app key is --api-key, else POR_API_KEY, else the demo key that the seed step saved in var/seed/demo.json.

For MCP the script starts its own MCP server over stdio, in submitter mode, unless --mcp-url names a running one
(for example the one run.py starts on :8001; set POR_MCP_HTTP_TOKEN if that server requires a bearer token).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    # Run as a file (python scripts/demo_agent.py, or an editor's Run button) rather than with -m: put the repo
    # root on the import path and work from it, so the project's packages and .env are found.
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

from services.model_gateway import get_gateway  # noqa: E402
SEED_INFO = ROOT / "var" / "seed" / "demo.json"
TIMEOUT = 180  # seconds. With inline jobs the API flags risks before it answers, which can take a minute.

INSTRUCTIONS = (
    "You are a drafting assistant inside a client application. Nothing you write may reach a client until a human "
    "has reviewed it, so every draft goes through the review tools you are given. Draft what is asked, submit it "
    "for review exactly once with the workflow id and external reference you are told to use, then check its "
    "status once. Do not wait for the decision and never treat a draft as approved. Finish with one line that "
    "gives the submission id and its status."
)


def task(topic: str, workflow_id: str, external_ref: str) -> str:
    return (f"Draft a short client letter (about 100 words, plain text) about: {topic}.\n"
            f"Submit it for human review with workflow_id {workflow_id!r}, external_ref {external_ref!r}, "
            f"risk_tier 'medium' and a short title, then report the submission id and status.")


# --- The REST API door: plain function tools ----------------------------------------------


def api_tools(client: httpx.Client, ai_model: str) -> list:
    def submit_for_review(workflow_id: str, title: str, content: str, external_ref: str, risk_tier: str = "medium") -> str:
        """Submit drafted text for human review through the REST API. Returns the submission id and status.

        Args:
            workflow_id: The review workflow to use.
            title: A short title for the document.
            content: The full text of the draft.
            external_ref: The caller's own reference for this piece of work.
            risk_tier: low, medium or high.
        """
        r = client.post("/v1/submissions", json={
            "workflow_id": workflow_id, "text": content, "risk_tier": risk_tier, "external_ref": external_ref,
            "ai_model": ai_model, "content_origin": "ai", "metadata": {"title": title, "submitted_by": "demo_agent"},
        })
        if r.status_code >= 400:
            return f"API error {r.status_code}: {r.text[:500]}"
        sub = r.json()
        return json.dumps({"submission_id": sub["id"], "status": sub["status"]})

    def get_review_status(submission_id: str) -> str:
        """Get the current review status of a submission, and its decision once a human has made one.

        Args:
            submission_id: The id returned by submit_for_review.
        """
        r = client.get(f"/v1/submissions/{submission_id}")
        if r.status_code >= 400:
            return f"API error {r.status_code}: {r.text[:500]}"
        sub = r.json()
        return json.dumps({"submission_id": sub["id"], "status": sub["status"], "decision": sub.get("decision")})

    return [submit_for_review, get_review_status]


# --- The MCP door: the server's own tools -------------------------------------------------


@asynccontextmanager
async def mcp_session(api_url: str, api_key: str, mcp_url: str | None):
    """An initialised MCP client session: to a running HTTP server, or to a server started here over stdio."""
    from mcp import ClientSession

    if mcp_url:
        from mcp.client.streamable_http import streamable_http_client

        token = os.environ.get("POR_MCP_HTTP_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT) as http:
            async with streamable_http_client(mcp_url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=TIMEOUT) as session:
                    yield session
        return

    from mcp import StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client

    # Submitter mode only: the child gets the source app key and never a reviewer token, whatever is in this
    # process's environment, so the agent cannot approve what it submits.
    env = {**get_default_environment(), "POR_MCP_API_URL": api_url, "POR_MCP_API_KEY": api_key}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=TIMEOUT) as session:
            yield session


# --- Running ------------------------------------------------------------------------------

_reasoning_off = False


async def run_agent(tools: list, prompt: str) -> str:
    from agno.agent import Agent

    global _reasoning_off
    model = get_gateway().agno_model()
    for attempt in (1, 2):
        if _reasoning_off:
            model.reasoning_effort = "none"
        agent = Agent(name="demo_submitter", model=model, instructions=INSTRUCTIONS, tools=tools,
                      tool_call_limit=4, markdown=False, telemetry=False)
        result = await agent.arun(prompt)
        reply = str(result.content or "").strip()
        failed = str(getattr(result, "status", "")).upper().endswith("ERROR")  # Agno reports a failed run this way
        # Some OpenAI reasoning models refuse function tools on chat completions unless reasoning is switched off.
        if failed and attempt == 1 and "reasoning_effort" in reply and hasattr(model, "reasoning_effort"):
            print("This model needs reasoning switched off to use tools; retrying that way.", flush=True)
            _reasoning_off = True  # remembered for the next door, so the refusal happens once
            continue
        break
    if failed:
        raise RuntimeError(f"the model call failed: {reply}")
    return reply


async def submit_once(via: str, args, client: httpx.Client, workflow_id: str) -> bool:
    external_ref = f"agent-{via}-{uuid.uuid4().hex[:8]}"
    prompt = task(args.topic, workflow_id, external_ref)
    print(f"\n=== Submitting via {via.upper()} (external_ref {external_ref}) ===", flush=True)
    try:
        if via == "api":
            reply = await run_agent(api_tools(client, get_gateway().model_id), prompt)
        else:
            from agno.tools.mcp import MCPTools

            async with mcp_session(args.api_url, args.api_key, args.mcp_url) as session:
                toolkit = MCPTools(session=session, include_tools=["submit_for_review", "get_review_status"],
                                   timeout_seconds=TIMEOUT)
                await toolkit.initialize()
                reply = await run_agent([toolkit], prompt)
    except RuntimeError as exc:
        print(f"FAILED: {exc}")
        return False
    print(f"Agent: {reply}")

    # Do not take the agent's word for it: ask the API whether the work arrived.
    found = client.get("/v1/submissions", params={"external_ref": external_ref}).json()
    if not found:
        print(f"FAILED: no submission with external_ref {external_ref} reached the API")
        return False
    sub = found[0]
    print(f"OK: submission {sub['id']} is {sub['status']} (find it in a reviewer's queue in the web app)")
    return True


def resolve_workflow(client: httpx.Client, wanted: str | None) -> str:
    r = client.get("/v1/workflows")
    if r.status_code == 401:
        sys.exit("The API rejected the app key: it has been replaced, revoked or expired, or it belongs to a different "
                 "database. Create a new one in the web app (Settings > Connected apps > Create API key), then pass "
                 "it with --api-key or set POR_API_KEY.")
    r.raise_for_status()
    workflows = r.json()
    if not workflows:
        sys.exit("This source app has no workflows. Create one in Workflow settings, or seed the demo data.")
    if wanted is None:
        return workflows[0]["id"]
    for wf in workflows:
        if wanted in (wf["id"], wf.get("name")):
            return wf["id"]
    sys.exit(f"No workflow {wanted!r}. Available: " + ", ".join(f"{w.get('name')} ({w['id']})" for w in workflows))


def demo_api_key() -> str | None:
    try:
        return json.loads(SEED_INFO.read_text()).get("api_key")
    except (OSError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Agno agent: drafts a document and submits it for human review.")
    parser.add_argument("--via", choices=["api", "mcp", "both"], default="both", help="how to submit (default: both)")
    parser.add_argument("--topic", default="confirming receipt of the client's documents and the next steps",
                        help="what the agent should draft")
    parser.add_argument("--workflow", help="workflow id or name (default: the first one the source app can use)")
    parser.add_argument("--api-url", default=os.environ.get("POR_MCP_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.environ.get("POR_API_KEY") or demo_api_key(),
                        help="source app key (default: POR_API_KEY, else the seeded demo key)")
    parser.add_argument("--mcp-url", help="use a running MCP server over HTTP instead of starting one over stdio")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if not get_gateway().enabled:
        sys.exit("An agent needs a real model. Set POR_MODEL_PROVIDER to anthropic or openai, with POR_MODEL_API_KEY, "
                 "in .env.")
    if not args.api_key:
        sys.exit("No source app key. Pass --api-key, set POR_API_KEY, or seed the demo data (python run.py seed).")

    with httpx.Client(base_url=args.api_url, headers={"X-API-Key": args.api_key}, timeout=TIMEOUT) as client:
        try:
            workflow_id = resolve_workflow(client, args.workflow)
        except httpx.ConnectError:
            sys.exit(f"Cannot reach the API at {args.api_url}. Start the stack first: python run.py up")
        doors = ["api", "mcp"] if args.via == "both" else [args.via]

        async def run_all() -> list[bool]:  # one event loop for every door: the model's HTTP client lives in it
            return [await submit_once(via, args, client, workflow_id) for via in doors]

        results = asyncio.run(run_all())
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
