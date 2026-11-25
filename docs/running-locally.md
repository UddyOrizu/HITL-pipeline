# Running Proof of Review locally

This guide gets the whole stack running on your own machine: a Postgres database in a Docker container, and the API, the background worker, the MCP server and the web app running directly on your machine. A first run takes five to ten minutes, most of it downloading dependencies and the database image.

## What you need

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | 3.11 or newer | API, worker, MCP server |
| Node.js with npm | 20 or newer | Web app only |
| Docker Desktop (or Docker Engine with Compose) | any recent | The Postgres database. It must be running before you start |
| [uv](https://docs.astral.sh/uv/) | any | Optional. Makes installing faster; plain `pip` is used when it is missing |

Nothing else is required. The database container is started for you, files are stored on disk and risks are flagged by an offline rule-based engine, so there is no database server to install and no Redis or model API key to set up.

## Start everything

From the repository root:

**Windows (PowerShell)**

```powershell
.\start-local.ps1
```

**macOS or Linux**

```bash
./start-local.sh
```

The launcher checks that Python and Node.js are installed and new enough, applies any pending database migrations (`python run.py migrate`), then runs `python run.py up --seed`. Migrations are applied on every start, and if one fails nothing is started. On a first run the launcher:

1. creates a virtualenv in `.venv` and installs the Python dependencies
2. installs the web app's dependencies (`web/node_modules`)
3. starts the Postgres container and waits until it accepts connections
4. applies migrations as the database owner, then creates the role the apps connect as
5. seeds a demo organisation with users, two workflows and sample submissions
6. starts the four apps in the background and waits until each one answers

Later runs skip any step that is already done, so starting again takes a few seconds. The apps keep running after the command returns and after you close the terminal.

If Node.js is missing, the launcher warns you and starts everything except the web app.

## What is running

| App | Address | What it is |
| --- | --- | --- |
| web | http://localhost:5173 | The web app. Start here |
| api | http://localhost:8000/docs | REST API, with interactive documentation |
| mcp | http://localhost:8001/mcp | MCP server, so AI agents can submit work for review |
| worker | (no address) | Background tasks: SLA checks, retries, queued assignments, connector polling |
| db | 127.0.0.1:5432 | Postgres with TimescaleDB, in the Docker container `proof-of-review-dev-postgres-1` |

## Sign in

Open http://localhost:5173 and enter one of the seeded email addresses. No password is needed locally.

| Email | Role | What you will see |
| --- | --- | --- |
| `ada@demo.test`, `ben@demo.test`, `cara@demo.test` | Reviewer | Review queue with the sample documents |
| `sam@demo.test` | Submitter | Submit for review, My submissions |
| `olu@demo.test` | Oversight lead | Assurance dashboard, held items, workflow settings, reports, costs |
| `aud@demo.test` | Auditor | Evidence explorer, audit log |
| `ian@demo.test` | Integration owner | Source apps, API keys, webhooks, connectors |
| `admin@demo.test` | Admin | Users and teams, organisation settings |

A good first walkthrough: sign in as `sam@demo.test` and submit a document, sign in as a reviewer and approve it, then sign in as `olu@demo.test` to see its score on the dashboard.

## Everyday commands

The launcher passes any arguments to `run.py`, so both forms below do the same thing. Use whichever you prefer.

| Task | Command |
| --- | --- |
| See what is running | `python run.py status` |
| Stop everything, including the database container | `python run.py down` |
| Stop one app | `python run.py down web` |
| Restart one app | `python run.py restart api` |
| Restart the apps (the database keeps running) | `python run.py restart` |
| Show recent logs for every app | `python run.py logs` |
| Follow one app's log | `python run.py logs api -f` |
| Show the database's log | `python run.py logs db` |
| Start only some apps | `python run.py up api web` |
| Apply migrations only | `python run.py migrate` |

On Windows, `.\start-local.ps1 status` is the same as `python run.py status`, and so on.

The API restarts itself when you edit Python files, and the web app reloads when you edit files under `web/src`. Restart the worker and MCP server by hand after changing their code.

Logs are written to `var/logs/<app>.log`. The log from the previous run is kept as `<app>.log.1`. The database's log is kept by Docker.

Stopping the database container does not delete anything: the data lives in a Docker volume and is there again on the next start.

## Submit something through the API

The seed step creates a source app and saves its API key in `var/seed/demo.json`. `python run.py up` prints the key each time it starts.

```bash
# List the workflows this key can use, and copy an id
curl http://localhost:8000/v1/workflows -H "X-API-Key: <demo key>"

# Submit a sample file for review
curl -X POST http://localhost:8000/v1/submissions \
  -H "X-API-Key: <demo key>" \
  -F workflow_id=<workflow id> \
  -F file=@var/seed/letter.txt \
  -F 'metadata={"title":"Test letter"}'
```

In PowerShell, use `curl.exe` instead of `curl`, and put the command on one line.

The new item appears in a reviewer's queue in the web app.

## Submit with the test agent

[scripts/demo_agent.py](../scripts/demo_agent.py) is a small Agno agent that plays a source application. It drafts a short letter and sends it for human review, the way a real agent would, through the REST API, the MCP server, or both:

```bash
# Windows: .venv\Scripts\python.exe   macOS or Linux: .venv/bin/python
.venv/bin/python -m scripts.demo_agent                  # one submission through each
.venv/bin/python -m scripts.demo_agent --via api        # REST API only
.venv/bin/python -m scripts.demo_agent --via mcp        # MCP only
.venv/bin/python -m scripts.demo_agent --topic "a late-payment reminder"
```

| Option | What it does |
| --- | --- |
| `--via api` | The agent calls `POST /v1/submissions` through Python function tools |
| `--via mcp` | The agent calls the MCP server's `submit_for_review` tool. The script starts its own MCP server for this, so the `mcp` app does not need to be running |
| `--mcp-url http://127.0.0.1:8001/mcp` | Use an MCP server that is already running instead, such as the one `run.py` starts |
| `--workflow <id or name>` | Pick the review workflow. The default is the first one the source app can use |
| `--api-key <key>` | The source app key. The default is `POR_API_KEY`, then the seeded demo key |

After each submission the script asks the API for the item by its reference and prints `OK` or `FAILED`, and it exits with an error if anything did not arrive. The agent never waits for a decision: a person makes that in the web app, where the new items appear in a reviewer's queue.

The API must be running (`python run.py up`), and the agent needs a real model: set `POR_MODEL_PROVIDER` to `anthropic` or `openai` with `POR_MODEL_API_KEY` in `.env`. Each submission costs a few model calls for the agent, plus the calls the platform makes to flag risks in the new document.

## Submit and review on one MCP server

By default the MCP server either submits (with a source app key) or reviews (with a reviewer's token). One server can do both with a single key, the source app key:

1. Choose the reviewer account the key reviews as. In the demo data:

   ```
   .venv\Scripts\python.exe -m scripts.app_key_review --reviewer ada@demo.test
   ```

   (`.venv/bin/python` on macOS and Linux. `--list` shows the current setting and `--off` removes it.)

2. In `.env`, keep `POR_MCP_API_KEY`, remove `POR_MCP_REVIEWER_TOKEN` and add:

   ```
   POR_MCP_REVIEW_WITH_API_KEY=true
   ```

3. Restart it: `python run.py restart mcp`.

The server at http://localhost:8001/mcp now offers all nine tools. The key sees only that reviewer's queue, so work assigned to another reviewer does not appear. Decisions are recorded under that reviewer's name, and the audit log notes that the source app key made them.

This is off by default for a reason: an agent holding the key can approve the work it submitted, so no person is guaranteed to have looked at it. Use it for testing, or where one trusted client really must do both.

## The database

Local runs use Postgres, the same database as the Docker and Azure deployments, so row-level security between organisations and the append-only audit log behave as they do in production.

| | |
| --- | --- |
| Address | `127.0.0.1:5432`, database `por` |
| Role the apps use | `por_app` (password `por_app`). Not a superuser, so row-level security applies to it |
| Owner, used for migrations | `por` (password `por`) |
| Defined in | [infra/docker-compose.dev.yml](../infra/docker-compose.dev.yml) |

These passwords are for local development only. To look inside the database:

```bash
docker exec -it proof-of-review-dev-postgres-1 psql -U por -d por
```

**Using your own Postgres server instead.** Set these in `.env`, and `run.py` leaves Docker alone:

```
POR_DATABASE_URL=postgresql+psycopg://por_app:<password>@<host>:<port>/<database>
POR_MIGRATION_DATABASE_URL=postgresql+psycopg://<owner>:<password>@<host>:<port>/<database>
```

The database must already exist. `run.py` applies migrations as the owner and creates the `por_app` role with the password you gave. Use `127.0.0.1` rather than `localhost` for a server on your own machine: on Windows, `localhost` is tried over IPv6 first, which can add a long delay to every new connection.

**SQLite.** `POR_DATABASE_URL=sqlite:///./var/por.db` still works for a quick run with no database server, but without row-level security. The test suite uses SQLite by default.

## Changing settings

Settings are optional. To change any, copy `.env.example` to `.env` and edit it; every setting starts with `POR_`. Restart the apps afterwards (`python run.py restart`).

Common local changes:

- **Use a real model for risk flags.** Set `POR_MODEL_PROVIDER=anthropic` (or `openai`) and `POR_MODEL_API_KEY`.
- **Create your own admin with a password.** Set `POR_BOOTSTRAP_ADMIN_EMAIL` and `POR_BOOTSTRAP_ADMIN_PASSWORD`. The account is created the next time the API starts.
- **Run the MCP server as a reviewer** instead of a submitter. Set `POR_MCP_REVIEWER_TOKEN` to a token from the Account page in the web app, and remove `POR_MCP_API_KEY`.
- **Submit and review on one MCP server with one key.** See [below](#submit-and-review-on-one-mcp-server).

Leave `POR_DATABASE_URL` out of `.env` (or keep the value from `.env.example`) to use the database container.

Set `POR_SECRET_KEY` before you seed, and then leave it alone. API keys are stored as hashes made with this key, so changing it afterwards makes every existing API key stop working (see Troubleshooting).

## Starting again from scratch

Local data lives in two places: the database is in a Docker volume, and uploaded files, seeded samples and logs are in the `var` folder. To wipe both and start clean:

```bash
python run.py down
docker compose -f infra/docker-compose.dev.yml down -v
# delete the var folder:
#   PowerShell:   Remove-Item -Recurse -Force var
#   macOS/Linux:  rm -rf var
python run.py up --seed
```

This deletes every local submission, user and review. It does not touch `.venv`, `web/node_modules` or `.env`.

## Running the tests

```bash
# Windows
.venv\Scripts\python.exe -m pytest

# macOS or Linux
.venv/bin/python -m pytest

# Web app type check
cd web && npm run typecheck
```

The tests create their own SQLite database for each test, so they need neither Docker nor the apps to be running. To run them against Postgres instead, with row-level security live, create an empty database and name it in `POR_TEST_DATABASE_URL`. Every test wipes and rebuilds that database, so never point it at one you want to keep:

```bash
docker exec proof-of-review-dev-postgres-1 createdb -U por por_test
# then run pytest with this set:
POR_TEST_DATABASE_URL=postgresql+psycopg://por:por@127.0.0.1:5432/por_test
```

## Troubleshooting

**"Docker is installed but not running."** Start Docker Desktop, wait until it says it is running, and start again.

**"port 5432 is already in use by another program."** Another database is using the port, usually a PostgreSQL installed on the machine or a container from another project. Stop it (`docker ps` lists running containers), or point `POR_DATABASE_URL` at a server of your own (see The database).

**The apps start slowly or the seed step seems to hang.** Check that `POR_DATABASE_URL` in `.env` uses `127.0.0.1`, not `localhost`.

**My old data is missing after switching from SQLite.** Earlier versions kept the database in `var/por.db`. That file is not read any more and is not deleted; the Postgres database starts empty and is seeded afresh.

**The MCP server exits with "Set POR_MCP_API_KEY or POR_MCP_REVIEWER_TOKEN, not both".** Both are set in `.env`. Remove one, then run `python run.py up mcp`. To submit and review on the same server, keep the API key and follow [Submit and review on one MCP server](#submit-and-review-on-one-mcp-server).

**A review tool answers "this source app key is not allowed to review".** The source app has no reviewer account to review as. Run `python -m scripts.app_key_review --reviewer ada@demo.test`.

**"port 8000 is already in use by another process"** (or 5173, 8001). Something else is using the port. Run `python run.py status`; if the app shows as stopped, another program has the port and has to be closed first.

**An app shows as "exited" during start-up.** The last 20 lines of its log are printed. For more, run `python run.py logs <app> -n 200`.

**The demo API key answers `401 invalid credentials`.** `POR_SECRET_KEY` in `.env` was changed after the demo data was seeded, so the saved key no longer matches its stored hash. The MCP server is affected too, because it uses the same key. Either:

- start again from scratch (see above), which seeds a fresh key, or
- sign in as `ian@demo.test`, rotate the source app's key in the Integrations console, and set the new key as `POR_MCP_API_KEY` in `.env`.

**"mcp needs a credential ... Skipping mcp."** The MCP server needs an API key or reviewer token. Run with `--seed` (the launcher does this) to use the demo key, or set `POR_MCP_API_KEY` in `.env`.

**"Python 3.11 or newer was not found on PATH."** Install Python from https://www.python.org/downloads/. On Windows, tick *Add python.exe to PATH* in the installer and open a new terminal.

**PowerShell refuses to run `start-local.ps1`.** Script execution is restricted on that machine. Run `python run.py up --seed` instead; it does the same thing.

**Dependencies seem out of date after pulling changes.** `run.py up` reinstalls Python dependencies when `pyproject.toml` changes, and web dependencies when `web/package-lock.json` changes. To force it, delete `.venv` or `web/node_modules` and start again.
