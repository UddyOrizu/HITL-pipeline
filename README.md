# Proof of Review

A multi-tenant human-in-the-loop (HITL) assurance platform. Any application submits content for review (AI-generated, human-drafted or mixed) through the REST API, a connector or an MCP tool call. The platform routes it to reviewers, measures how it was reviewed, and returns a decision with a **Review Assurance Score** and an evidence trail.

Most approval tools record that someone clicked Approve. This one records whether that click was credible, using five controls:

| Control | What it does | Where |
| --- | --- | --- |
| Canaries | Known errors seeded into the review copy (never the original) to measure catch rates, revealed after each decision | [services/canary.py](services/canary.py) |
| Behavioural telemetry | Active reading time, sections viewed, scroll depth, flags opened, focus. Review session only | [web/src/telemetry.ts](web/src/telemetry.ts), [services/telemetry.py](services/telemetry.py) |
| Volume caps | Per-reviewer daily and hourly budgets in review minutes, team ceilings, queue / escalate / defer on overflow | [services/assignment.py](services/assignment.py) |
| Risk-pointing | Claim extraction and risk flags per span; high-risk flags must be acknowledged before Approve | [services/risk.py](services/risk.py), [agents/](agents/) |
| Rotation and spot-checks | Round-robin, rotation and conflict rules; a weighted sample goes to a blind second reviewer, and disagreements are held for oversight | [services/workflow.py](services/workflow.py) |

## Quick start (no Docker)

```bash
uv venv && uv pip install -e ".[all]" --group dev
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed --submit      # demo tenant, 3 reviewers, 2 workflows, sample files in every format
.venv/bin/uvicorn api.main:app --reload         # API on :8000

cd web && npm install && npm run dev            # reviewer app on :5173 (proxies /v1 to :8000)
```

Sign in at http://localhost:5173 with a seeded email: `admin@demo.test` (admin), `ada@demo.test`, `ben@demo.test` or `cara@demo.test` (reviewers), `sam@demo.test` (submitter), `olu@demo.test` (oversight lead), `aud@demo.test` (auditor), `ian@demo.test` (integration owner). The seed script prints the demo source app's API key.

By default everything runs in one process with SQLite, local file storage and the offline `heuristic` risk engine. Set `POR_*` variables (see [.env.example](.env.example)) to switch on Postgres, Redis workers, S3/MinIO, LibreOffice and an LLM provider.

## Full stack (Docker)

```bash
docker compose -f infra/docker-compose.yml up --build
docker compose -f infra/docker-compose.yml exec api python -m scripts.seed --submit
```

This runs Postgres + TimescaleDB (with row-level security and the append-only audit trigger applied by migrations), Redis Streams, MinIO, the LibreOffice worker for `.ppt`, the API (which serves the built web app at http://localhost:8000) and a background worker. The worker handles jobs, webhook retries, SLA escalation, queued assignments and SharePoint polling.

Terraform for Azure UK South is in [infra/terraform](infra/terraform): Container Apps, Postgres Flexible Server with TimescaleDB, Redis and Key Vault.

## Users and roles

| Role | Can do |
| --- | --- |
| **Admin** | Manage users and teams, plus everything an oversight lead, auditor and integration owner can do. **Never reviews.** |
| **Reviewer** | Review assigned items and spot-checks. Measured by canaries, telemetry and spot-checks. |
| Submitter | Send work for review from the web app, track it, read feedback, download the evidence pack. Never assigned their own work. |
| Oversight lead | Assurance dashboard, held items, workflow settings. Can read the user list. |
| Auditor | Read-only evidence explorer and audit log. |
| Integration owner | Source apps, API keys, webhooks, connectors. |

Admin and reviewer are kept apart on purpose. An admin controls the review settings (canary rates, caps, thresholds), so they should not also be scored by them. Admins never get review work and cannot open the review queue, and a single account cannot hold both roles. A very small firm can opt out by setting `allow_admin_reviewers: true` in the tenant settings.

Admins manage people on the **Users & teams** page (`/v1/admin/users`, `/v1/admin/teams`):

- Add users by email, set their account type and access, and assign them to teams. Email is the sign-in identity, so it cannot be changed.
- Deactivate rather than delete, so the audit trail keeps every name. A deactivated user is signed out on their next request.
- Changes to roles take effect on the user's next request.
- When a reviewer is deactivated or loses the reviewer role, their open tasks go to other reviewers. Any unscored canaries move with the tasks. If nobody is free, the item waits for capacity.
- Admins cannot deactivate themselves or remove their own admin role. The last active admin cannot be removed by anyone.
- Every change is written to the audit log with before and after values.

## Three ways in, three ways to review

Work can be submitted and reviewed through the web app, the REST API or MCP. Whichever way it comes in, it goes through the same pipeline and the same controls: risk flags, canaries, caps, rotation, spot-checks and the release check.

| | Web app | REST API | MCP |
| --- | --- | --- | --- |
| **Submit** | *Submit for review* page (submitter role) | `POST /v1/submissions` (source app key) | `submit_for_review` (submitter mode) |
| **Review and decide** | Review workspace (reviewer role) | `/v1/review/...` with the reviewer's sign-in token | `open_review_task`, `submit_review_decision` (reviewer mode) |

Things that differ by channel:

- **Web submissions** go through a built-in "Web portal" source app (one per tenant, managed by the platform). The submitter's email becomes the author, so the conflict rule keeps them off their own work. Submitters only ever see their own items. They see what a source app sees (status, decision, feedback, evidence pack), never canaries or reviewer names.
- **Decisions record their channel** (`ui`, `api` or `mcp`), which appears in the evidence record and PDF. Clients set it with the `X-PoR-Channel` header. The header is only a label: what proves reading is the telemetry.
- **Only the web workspace measures reading.** API and MCP reviews send no reading telemetry, so the workflow's reading standard applies to them. With `warn`, the reviewer has to resend with `confirm_warnings: true`, and the warning is recorded. With `block`, approval has to happen in the workspace. Either way, reading coverage in the assurance score is zero.
- **A workflow can limit which channels may decide** with `telemetry_policy.review_channels` (for example `["ui"]`). This is set on the Workflow settings page.

## Submitting content

```bash
curl -X POST localhost:8000/v1/submissions \
  -H "X-API-Key: $POR_API_KEY" -H "Idempotency-Key: letter-123" \
  -F workflow_id=$WORKFLOW_ID -F file=@letter.docx \
  -F risk_tier=high -F ai_model=claude-sonnet-5-5 -F 'metadata={"title":"Client letter"}' \
  -F reference_files=@client-file.pdf
```

JSON bodies work too (`text`, or `file: {filename, content_base64}`). Supported inputs: plain text, `.txt`, `.docx`, `.pptx`, `.ppt` (converted with LibreOffice) and `.pdf` (text layer). Every format is normalised into ordered sections and spans with stable ids, which flags, canaries, telemetry and comments all reference.

| Method and path | Purpose |
| --- | --- |
| `POST /v1/submissions` | Submit text or a file with workflow, risk tier, model, metadata, optional reference documents |
| `GET /v1/submissions/{id}` | Status, current stage, decision when final |
| `GET /v1/submissions/{id}/feedback` | Reviewer comments for a request-changes outcome |
| `GET /v1/submissions/{id}/evidence` | Evidence record as JSON, or `?format=pdf` for the evidence pack |
| `POST /v1/submissions/{id}/cancel` | Withdraw a submission still in review |
| `GET /v1/workflows` | Workflows available to the calling source app |
| `GET /v1/assurance/summary` | Aggregate metrics for the customer's own dashboards |
| `POST /v1/oauth/token` | OAuth 2.0 client credentials for enterprise source apps |

Interactive docs are at `/docs`.

**Webhooks**: `submission.received`, `submission.in_review`, `submission.decided`, `submission.deferred`, `submission.escalated`. Each one is signed in the `X-PoR-Signature: t=<unix>,v1=<hmac>` header with HMAC-SHA256 over `"{t}.{body}"` using the app's webhook secret ([services/webhooks.py](services/webhooks.py) has `verify()`). Failed deliveries retry with exponential backoff for 24 hours and are shown in the delivery log.

**MCP server**: runs in one of two modes.

```bash
# Submitter mode: agents call the approval gate as a tool (source app API key)
POR_MCP_API_URL=http://localhost:8000 POR_MCP_API_KEY=por_... python -m mcp_server.server          # stdio
POR_MCP_API_URL=http://localhost:8000 POR_MCP_API_KEY=por_... python -m mcp_server.server --http   # streamable HTTP

# Reviewer mode: a named reviewer works their queue from an MCP client (the reviewer's sign-in token)
POR_MCP_API_URL=http://localhost:8000 POR_MCP_REVIEWER_TOKEN=<token> python -m mcp_server.server
```

- **Submitter tools:** `submit_for_review`, `get_review_status`, `wait_for_decision` and `get_review_feedback`. Agents only ever see the decision, reasons and comments: never canaries, telemetry or reviewer identities.
- **Reviewer tools:** `list_review_tasks`, `open_review_task` (the review copy as text with span ids and flags), `acknowledge_flag`, `add_review_comment` and `submit_review_decision`. Decisions are recorded under the reviewer's name, with channel `mcp`. The server's instructions tell the client that the human makes every decision.
- **One mode per server:** it refuses to start with both a key and a token, so a single agent cannot submit work and then approve it.
- **Token expiry:** a reviewer token comes from signing in (OIDC, or dev login locally) and expires after `POR_TOKEN_TTL_MINUTES` (12 hours by default).

**Connectors** ([connectors/](connectors/)) sit on top of the same API:

- **SharePoint / OneDrive**: a monitored folder. Decisions are written back as metadata and the file moves to an Approved, Rejected or Changes requested folder.
- **Microsoft Teams**: reviewer nudges and escalation cards. Decisions are always made in the review workspace.
- **Jira / Azure DevOps**: the decision updates the ticket and the evidence pack is attached.

## How a submission flows

1. The API validates, virus-scans (ClamAV when `POR_CLAMAV_HOST` is set) and stores the original.
2. The pipeline extracts the normalised document and records the content hash.
3. The risk engine flags spans.
4. The canary service may seed the review copy.
5. The assignment service picks a reviewer who is under their cap, applying rotation rules.
6. The reviewer reads in the workspace while telemetry is batched every 5 s. High-risk flags must be acknowledged before approving, and the telemetry policy can warn, block or flag a skim.
7. Canaries are scored deterministically and revealed to the reviewer. If a reviewer rejects only because of the canary, the canary is removed and they decide again on the genuine content.
8. Sampled items go to a blind second reviewer, and disagreements are held for the oversight lead.
9. Release runs a hash check: the approved content must match the original byte for byte and span for span. Then the score is computed, the evidence pack (PDF + JSON) generated and the `submission.decided` webhook sent.

**Review Assurance Score**: reading coverage 30%, risk engagement 25%, reviewer reliability (rolling canary catch rate) 20%, load against cap 10%, independent check 15%. When an item isn't spot-checked, the independent-check weight is spread across the other four. Each workflow sets a minimum score and what happens below it: release with a flag, require a second review, or block.

## Engineering rules (enforced by tests)

- Every state change writes to a per-tenant, hash-chained, append-only audit log. Updates and deletes are refused by an ORM guard and a Postgres trigger. `GET /v1/audit/verify` recomputes the chain.
- Canary text never appears in any outbound payload. Payloads are built without canary data, reviewer free text that quotes a canary is redacted, and every response and webhook passes through a leak guard that fails closed. [tests/test_canary_leakage.py](tests/test_canary_leakage.py) drives reviewers into quoting canaries everywhere and scans every webhook, API response, PDF and MCP output.
- All LLM calls go through [services/model_gateway.py](services/model_gateway.py), which handles the provider setting and per-call logging. A test fails if a provider SDK is imported anywhere else.
- Every endpoint is tenant-scoped. [tests/test_foundations.py](tests/test_foundations.py) runs a cross-tenant check on every route and fails if a new route has no case.
- Canary scoring and telemetry are deterministic, not AI-driven, so scores stay process-quality measures (see the EU AI Act note in the product doc).

## Tests

```bash
.venv/bin/python -m pytest                                    # SQLite, offline
POR_TEST_DATABASE_URL=postgresql+psycopg://por:por@localhost:5432/por_test .venv/bin/python -m pytest   # real Postgres, via migrations with RLS
cd web && npm run typecheck
```

There is a test file per milestone, plus [tests/test_definition_of_done.py](tests/test_definition_of_done.py), which runs the MVP definition of done end to end on the seeded tenant. A `.docx`, a `.pptx` and a `.pdf` go through the API and one file through MCP; one canary is caught and one missed; one spot-check disagrees; the test checks the signed webhooks with scores and downloads an evidence pack for each item.

## Repository layout

```
api/          FastAPI app, routers (source apps, web portal, reviewer, oversight, users, audit, integrations), auth
services/     ingestion, risk, canary, assignment, workflow, telemetry, assurance, evidence, webhooks, outbound guard, worker
agents/       Agno agents: claim_extractor, risk_flagger, canary_generator
prompts/      prompt files, loaded by file name
mcp_server/   MCP tools over the REST API
connectors/   sharepoint, teams, jira (+ Azure DevOps)
web/          React + TypeScript reviewer app
db/           SQLAlchemy models, Alembic migrations (0002 adds RLS, audit trigger, Timescale hypertable)
scripts/      seed script and sample-file generator
tests/        pytest suites per milestone
infra/        docker-compose, LibreOffice worker, Terraform (Azure)
```

## Settings that matter in production

`POR_ENV=prod` refuses to start with the default secret key and turns off dev login. Configure `POR_OIDC_ISSUER`, `POR_OIDC_AUDIENCE` and `POR_OIDC_JWKS_URL` for SSO (Entra ID, Google). For confidential client material, set `POR_MODEL_PROVIDER` to a zero-data-retention provider or point `POR_MODEL_BASE_URL` at the customer's own model endpoint.
