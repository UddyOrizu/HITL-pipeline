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

[run.py](run.py) runs the whole local stack. It works on Windows, macOS and Linux and only needs Python 3.10+ (plus Node.js for the web app):

```bash
python run.py up --seed     # create .venv, install deps, migrate, seed demo data (first run only), start everything
python run.py status        # what is running, with URLs
python run.py logs          # last 50 lines of every app's log; add -f to follow
python run.py logs api -f   # follow one app (api, worker, mcp, web); several can be listed
python run.py restart api   # restart one app
python run.py down          # stop everything (or name apps: python run.py down web)
python run.py migrate       # migrations only
```

Apps run in the background on API :8000, web :5173 and MCP :8001, and keep running after the command returns. Logs are in `var/logs/<app>.log` (the previous run's log is kept as `.log.1`). The MCP server starts when it has a credential: `POR_MCP_API_KEY` or `POR_MCP_REVIEWER_TOKEN` in `.env`, or the demo key written by `--seed`. `up` re-installs Python dependencies only when `pyproject.toml` changes, and web dependencies only when `package-lock.json` changes.

The same steps by hand:

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

This runs:

- Postgres + TimescaleDB, with row-level security and the append-only audit trigger applied by migrations
- Redis Streams
- MinIO
- ClamAV
- Mailpit, which catches outgoing email (inbox at http://localhost:8025)
- the LibreOffice worker for `.ppt`
- the API, which serves the built web app at http://localhost:8000
- a background worker, which handles jobs, retries, SLAs, queued assignments, connector polling and retention

Add `--profile mcp` with `POR_MCP_API_KEY` set to run a hosted MCP server too.

Terraform for Azure UK South is in [infra/terraform](infra/terraform). It sets up:

- Container Apps: the API with a ClamAV sidecar, the worker, LibreOffice, and an optional MCP server
- Postgres Flexible Server with TimescaleDB
- Redis
- Blob storage, reached through the apps' managed identity
- Key Vault, which holds every secret and which the apps read through the same identity

Build the image with `--build-arg EXTRAS=all,azure` for Blob storage (add `observability` for Sentry).

## Users and roles

| Role | Can do |
| --- | --- |
| **Admin** | Manage users and teams, plus everything an oversight lead, auditor and integration owner can do. **Never reviews.** |
| **Reviewer** | Review assigned items and spot-checks. Measured by canaries, telemetry and spot-checks. |
| Submitter | Send work for review from the web app, track it, read feedback, download the evidence pack. Never assigned their own work. |
| Oversight lead | Assurance dashboard, held items, workflow settings, reports and costs. Can read the user list. |
| Auditor | Read-only evidence explorer, audit log and reports (not reviewer-level detail or costs). |
| Integration owner | Source apps, API keys, OAuth clients, webhooks, connectors. |

**Platform administrators** (a flag on an account, not a tenant role) create and manage organisations on the *Organisations* page (`/v1/platform/tenants`): create one with its first admin, rename, or deactivate it (its users and source apps are refused at once; nothing is deleted). The bootstrap admin is a platform administrator; grant it to another account with `python -m services.bootstrap --grant-platform-admin EMAIL`.

Admins set organisation-wide options on **Organisation settings** (`/v1/admin/tenant`): name, whether admins may also review, data retention, currency and reviewer hourly rates, model prices, email notifications, and the organisation's own SSO provider.

Admin and reviewer are kept apart on purpose. An admin controls the review settings (canary rates, caps, thresholds), so they should not also be scored by them. Admins never get review work and cannot open the review queue, and a single account cannot hold both roles. A very small firm can opt out by setting `allow_admin_reviewers: true` in the tenant settings.

Admins manage people on the **Users & teams** page (`/v1/admin/users`, `/v1/admin/teams`):

- Add users by email, set their account type and access, and assign them to teams. Email is the sign-in identity, so it cannot be changed.
- Deactivate rather than delete, so the audit trail keeps every name. A deactivated user is signed out on their next request.
- Changes to roles take effect on the user's next request.
- When a reviewer is deactivated or loses the reviewer role, their open tasks go to other reviewers. Any unscored canaries move with the tasks. If nobody is free, the item waits for capacity.
- Admins cannot deactivate themselves or remove their own admin role. The last active admin cannot be removed by anyone.
- Every change is written to the audit log with before and after values.

### Signing in

Users sign in with SSO (OIDC), with their email and a password, or both. Passwords work in every environment; dev login (email only, no password) is for local development and is off in prod.

- **Single sign-on.** The sign-in page has a *Sign in with single sign-on* button (authorization code flow with PKCE; register `<app URL>/login/sso` as a single-page-app redirect URI). There are two kinds of provider. The platform provider comes from `POR_OIDC_ISSUER` and `POR_OIDC_CLIENT_ID` (endpoints are read from the issuer's discovery document). Each organisation can also set its own provider in Organisation settings, with the email domains that use it, so different firms can use their own Entra ID tenants. A provider must confirm the email (`email_verified` is checked when present). An account linked to one identity can't be taken over by another. An organisation can let new people in on first sign-in with chosen non-admin roles (`auto_provision_roles`). If an email exists in several organisations, the person picks one. `POST /v1/auth/oidc` still accepts an ID token directly for native clients.
- **Sessions.** Sessions refresh themselves while in use, for up to `POR_SESSION_MAX_HOURS` (24 by default) after signing in, so a long review isn't cut short. When a session does end, the app keeps the reviewer's drafts and returns them to the same page after they sign in again. *Sign out* revokes that session's token on the server. *Sign out everywhere* (or an admin's *Sign out of all sessions*) ends every session.
- **Forgotten passwords.** *Forgotten your password?* emails a single-use reset link (valid for `POR_PASSWORD_RESET_TTL_MINUTES`). The response is the same whether or not the account exists.
- **Rate limits.** Sign-in, SSO, password reset and the OAuth token endpoint are limited per client IP (`POR_RATE_LIMIT_AUTH_PER_MINUTE`), and submissions per source app (`POR_RATE_LIMIT_SUBMISSIONS_PER_MINUTE`). The counters are shared through Redis when it is configured.
- **Passwords.** An admin can give a user a password when adding them, and set or reset it later (`PUT /v1/admin/users/{id}/password`). Leave it blank for SSO-only users. Anyone signed in can set or change their own password from "Change password" in the sidebar (`POST /v1/me/password`, which needs the current password if one is set). Passwords are at least 12 characters (`POR_PASSWORD_MIN_LENGTH`), stored as salted scrypt hashes, and never written to the audit log.
- **Sessions.** Setting or changing a password signs out every session that started before it.
- **Lockout.** After 5 failed attempts (`POR_LOGIN_MAX_ATTEMPTS`) the account locks for 15 minutes (`POR_LOGIN_LOCKOUT_MINUTES`). An admin setting a new password unlocks it. Every failure returns the same message, so the endpoint does not reveal which emails have accounts.
- **Initial admin.** For a new installation, set `POR_BOOTSTRAP_ADMIN_EMAIL` and `POR_BOOTSTRAP_ADMIN_PASSWORD` (the email is the username). When the API starts it creates the tenant named by `POR_BOOTSTRAP_TENANT_NAME` (default `Default`) if missing, then that admin if no user with the email exists. An existing user is never changed, so after the first sign-in, change the password in the app and remove `POR_BOOTSTRAP_ADMIN_PASSWORD` from the environment. Bad values stop the API from starting. `python -m services.bootstrap` runs the same step by hand.

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
| `GET /v1/submissions` | This app's submissions, newest first (`status`, `external_ref`, `offset`, `limit`; total in `X-Total-Count`) |
| `GET /v1/submissions/{id}` | Status, current stage, decision when final |
| `GET /v1/submissions/{id}/feedback` | Reviewer comments for a request-changes outcome |
| `GET /v1/submissions/{id}/evidence` | Evidence record as JSON, or `?format=pdf` for the evidence pack |
| `POST /v1/submissions/{id}/cancel` | Withdraw a submission still in review |
| `GET /v1/workflows` | Workflows available to the calling source app |
| `GET /v1/assurance/summary` | Aggregate metrics for the customer's own dashboards |
| `POST /v1/oauth/token` | OAuth 2.0 client credentials for enterprise source apps |

Interactive docs are at `/docs`.

**Webhooks**: `submission.received`, `submission.in_review`, `submission.decided`, `submission.deferred`, `submission.escalated`, `submission.cancelled`. Each one is signed in the `X-PoR-Signature: t=<unix>,v1=<hmac>` header with HMAC-SHA256 over `"{t}.{body}"` using the app's webhook secret ([services/webhooks.py](services/webhooks.py) has `verify()`). Failed deliveries retry with exponential backoff for 24 hours and are shown in the delivery log. In prod, webhook and connector URLs must be public `https` addresses: private, loopback and cloud-metadata addresses are refused when saved and again at delivery (`POR_ALLOW_PRIVATE_TARGETS` overrides).

**Keys**: rotating an API key can keep the old key working for a grace period (up to 7 days) while the app is redeployed. OAuth client credentials can be switched on or off, and their secret rotated, at any time. Lists return a plain JSON array with the total in `X-Total-Count` and the next page's offset in `X-Next-Offset`.

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
- **Jira**: tickets matching a JQL query (`submit_jql`) are submitted automatically. The decision comments on the ticket, moves it (optional transitions) and attaches the evidence pack.
- **Azure DevOps**: work items matching a WIQL query (`submit_wiql`) are submitted automatically. The decision adds a history entry, moves the work item (optional states) and attaches the evidence pack.

Integration owners create, edit, switch off, delete and run connectors in the Integrations console. Required settings are checked per kind. Secrets (client secrets, tokens, PATs, Teams webhook URLs) are encrypted at rest and never returned. Each connector shows its last run and last error. Polling connectors run every minute, each on its own, so one broken configuration doesn't stop the others.

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

**When things go wrong:**

- **Processing failures.** Temporary failures (document converter or model provider unavailable, network errors) are retried with backoff (`POR_PROCESSING_MAX_ATTEMPTS`). A file that can't be read is deferred straight away.
- **Retrying deferred items.** An oversight lead can retry a deferred item, sending it back to processing or into review.
- **Held items.** The oversight lead sees both decisions, the comments, the flags, the score and the original text. They settle a hold with a decision, by assigning a named reviewer past the caps, or by deferring or cancelling it.
- **SLAs.** Each stage's `on_sla_breach` either escalates (the default) or also reassigns the task to another reviewer.
- **Cancelling** sends `submission.cancelled` and frees the reviewer's capacity for queued items.

## Reports and costs

**Reports** (`/v1/reports/*`; oversight leads, and auditors for the overview) cover:

- **Overview:** volumes received and decided, outcomes and approval rate, median and 90th-percentile turnaround, SLA breaches, escalations, held items, and scores below threshold.
- **Breakdowns:** a time series by day, week or month, and breakdowns by workflow, source app, risk tier, content origin and decision channel.
- **Reviewers:** a per-reviewer table with tasks, reading time and coverage, flag engagement, confirmed warnings, canary catch rate, spot-check disagreements, SLA breaches and open work.
- **SLA:** a list of overdue tasks.

**Costs** (`/v1/reports/costs`; oversight leads) breaks spend down by source app, workflow, team, reviewer, model, submission, day, week or month:

- **Model spend:** every LLM call through the model gateway is stored with its input, output and cache tokens. Spend is tokens × the price per million tokens. Defaults are Anthropic list prices (as of October 2026); an organisation can override them or add other models in Organisation settings, and models without a price are listed so nothing is silently counted as free.
- **Reviewer time:** the active reading time measured in the workspace × an hourly rate (the user's own, else their team's, else the organisation's default). API and MCP decisions have no reading telemetry, so their effort estimate is used and reported separately. Minutes with no rate are flagged.
- **Currency:** everything is shown in the organisation's currency; USD model prices are converted with its exchange rate.

Every report has a CSV export (`?format=csv`). The *Reports* and *Costs* pages in the web app show the same data with charts.

## Operations

- **Background work.** When Redis jobs are on, events and jobs go only to Redis Streams, and the worker runs each handler once. Failed jobs and handlers are retried with backoff (`POR_JOB_MAX_ATTEMPTS`) and then moved to the `por:dead` stream. Messages left by a crashed worker are reclaimed. Periodic tasks run under a Redis lock, so several worker replicas are safe:
  - webhook retries
  - SLA checks
  - queued assignments
  - re-queueing stuck submissions
  - connector polling
  - retention
  - token clean-up

  Without a worker (inline mode), the API runs the periodic tasks itself.
- **Retention and erasure.** An organisation sets `retention_days` (30 or more; empty keeps everything). Finished submissions older than that have their content purged:
  - removed: stored files, extracted text, claim text, canary text, comment text and reading telemetry;
  - kept: decisions, scores, the audit log and the evidence pack.

  Admins can purge one submission on request (`DELETE /v1/admin/submissions/{id}/content`). They can also erase a deactivated user's personal data (`POST /v1/admin/users/{id}/erase`). The audit log is append-only, so earlier entries keep what they recorded at the time.
- **Virus scanning** fails closed: only an explicit ClamAV "OK" lets a file in. In prod the API won't start without `POR_CLAMAV_HOST` (set `POR_REQUIRE_VIRUS_SCAN=false` to opt out). Docker Compose and Terraform include ClamAV.
- **Observability.** `GET /metrics` serves Prometheus metrics: requests by route, latency, jobs, webhooks, model calls, rate limiting, and submissions and tasks by status. Protect it with `POR_METRICS_TOKEN`. `GET /readyz` checks the database. Every response carries `X-Request-ID`, which also appears in the logs (`POR_LOG_FORMAT=json` for JSON lines). `POR_SENTRY_DSN` turns on error reporting.
- **CORS** comes from `POR_CORS_ORIGINS`. It is empty by default, because the web app is same-origin.

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

`POR_ENV=prod` refuses to start with the default secret key, without a virus scanner, or with `*` in `POR_CORS_ORIGINS`, and turns off dev login. Set `POR_ENCRYPTION_KEY` (for stored connector and SSO secrets) separately from `POR_SECRET_KEY`. Without SSO, create the first admin with `POR_BOOTSTRAP_ADMIN_EMAIL` and `POR_BOOTSTRAP_ADMIN_PASSWORD` (see [Signing in](#signing-in)); set `POR_PASSWORD_LOGIN_ENABLED=false` to allow SSO only. Configure `POR_OIDC_ISSUER`, `POR_OIDC_AUDIENCE` and `POR_OIDC_JWKS_URL` for SSO (Entra ID, Google). For confidential client material, set `POR_MODEL_PROVIDER` to a zero-data-retention provider or point `POR_MODEL_BASE_URL` at the customer's own model endpoint.
