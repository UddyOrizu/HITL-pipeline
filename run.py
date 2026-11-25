#!/usr/bin/env python3
"""Run the Proof of Review stack locally: install, migrate, start, stop, status and logs.

Usage (from the repo root, with Python 3.11 or newer, which the app itself needs):

    python run.py up [APP ...] [--seed] [--no-migrate] [--no-install] [--follow]
    python run.py down [APP ...]
    python run.py restart [APP ...]
    python run.py status
    python run.py logs [APP ...] [-f] [-n LINES]
    python run.py migrate
    python run.py seed

Apps: db (Postgres in Docker on :5432), api (FastAPI on :8000), worker (background jobs), web (Vite on :5173),
mcp (MCP over HTTP on :8001). With no APP, every app is used. mcp starts only when it has a credential:
POR_MCP_API_KEY or POR_MCP_REVIEWER_TOKEN in the environment or .env, or the demo API key written by `seed`.

The database is the `postgres` service from infra/docker-compose.dev.yml, so Docker must be running. Migrations run
as its owner (por) and the apps connect as por_app, which is not a superuser, so row-level security applies.
Set POR_DATABASE_URL in .env to use another database instead (your own Postgres server, or a sqlite:/// file);
this script then leaves Docker alone. With your own Postgres, POR_MIGRATION_DATABASE_URL can name the owner
that migrations run as, and the apps' role in POR_DATABASE_URL is then created for you.

Apps run in the background and keep running after this script exits. Logs go to var/logs/<app>.log
(the previous run's log is kept as <app>.log.1) and process ids to var/run/<app>.json.
Uses only the standard library, so it runs before the virtualenv exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VAR = ROOT / "var"
LOG_DIR = VAR / "logs"
RUN_DIR = VAR / "run"
SEED_INFO = VAR / "seed" / "demo.json"
VENV = ROOT / ".venv"
WEB = ROOT / "web"
IS_WINDOWS = os.name == "nt"

API_PORT, WEB_PORT, MCP_PORT, DB_PORT = 8000, 5173, 8001, 5432

# The local database: the postgres service in infra/docker-compose.dev.yml, published on localhost.
# LOCAL_APP_DB_URL is also the default of POR_DATABASE_URL in services/config.py; keep the two the same.
COMPOSE_FILE = ROOT / "infra" / "docker-compose.dev.yml"
DB_SERVICE = "postgres"
LOCAL_APP_DB_URL = f"postgresql+psycopg://por_app:por_app@127.0.0.1:{DB_PORT}/por"
LOCAL_OWNER_DB_URL = f"postgresql+psycopg://por:por@127.0.0.1:{DB_PORT}/por"


@dataclass
class App:
    name: str
    description: str
    port: int | None = None
    url: str | None = None
    health_url: str | None = None  # HTTP check; otherwise a TCP check on port
    cwd: Path = ROOT
    needs_db: bool = False
    env: dict[str, str] = field(default_factory=dict)


APPS: dict[str, App] = {
    # The worker runs the periodic tasks, so the API does not run its own copy (they would race without Redis).
    "api": App("api", "FastAPI backend", API_PORT, f"http://localhost:{API_PORT}/docs",
               f"http://127.0.0.1:{API_PORT}/healthz", needs_db=True, env={"POR_SCHEDULER_IN_API": "false"}),
    "worker": App("worker", "background worker (SLAs, retries, queued assignments, polling, retention)", needs_db=True),
    "web": App("web", "reviewer web app (Vite)", WEB_PORT, f"http://localhost:{WEB_PORT}", cwd=WEB,
               env={"NO_COLOR": "1"}),  # keep colour codes out of the log file
    "mcp": App("mcp", "MCP server, streamable HTTP", MCP_PORT, f"http://localhost:{MCP_PORT}/mcp",
               env={"POR_MCP_PORT": str(MCP_PORT), "POR_MCP_API_URL": f"http://localhost:{API_PORT}"}),
}
DB = "db"  # not a process this script owns: the Postgres container, run through docker compose
ORDER = [DB, "api", "worker", "mcp", "web"]  # start order; stop runs in reverse

# --- Output -------------------------------------------------------------------------------

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
if COLOR and IS_WINDOWS:
    os.system("")  # turns on ANSI escape handling in the classic Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

APP_COLORS = {"api": "36", "worker": "35", "web": "32", "mcp": "33"}


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def info(msg: str) -> None:
    print(paint("==> ", "1;34") + msg, flush=True)


def warn(msg: str) -> None:
    print(paint("warning: ", "1;33") + msg, flush=True)


def fail(msg: str) -> None:
    print(paint("error: ", "1;31") + msg, file=sys.stderr, flush=True)
    sys.exit(1)


# --- Environment --------------------------------------------------------------------------


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def read_dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in ("'", '"') and value[0] in value[1:]:
            value = value[1:value.index(value[0], 1)]  # quoted: everything up to the closing quote
        else:
            # Unquoted: " # ..." starts a comment, as in python-dotenv (which the app's own settings use).
            hash_at = next((i for i in range(1, len(value)) if value[i] == "#" and value[i - 1] in " \t"), None)
            if hash_at is not None:
                value = value[:hash_at].rstrip()
        out[key] = value
    return out


def base_env() -> dict[str, str]:
    """.env values, overridden by the real environment (the same precedence the app's settings use)."""
    env = {**read_dotenv(), **os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_step(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print(paint("    $ " + " ".join(str(c) for c in cmd), "2"), flush=True)
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env or base_env())
    if result.returncode != 0:
        fail(f"command failed with exit code {result.returncode}")


def npm() -> str:
    exe = shutil.which("npm")
    if not exe:
        fail("npm was not found on PATH. Install Node.js 20+ to run the web app, or leave out 'web'.")
    return exe


def _deps_fingerprint() -> str:
    return hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()


def ensure_python_env() -> None:
    """Create .venv and install dependencies when missing or when pyproject.toml has changed."""
    marker = VENV / ".por-deps"
    fingerprint = _deps_fingerprint()
    if venv_python().exists() and marker.exists() and marker.read_text().strip() == fingerprint:
        return
    uv = shutil.which("uv")
    if uv:
        if not venv_python().exists():
            info("Creating .venv with uv")
            run_step([uv, "venv", str(VENV)])
        info("Installing Python dependencies")
        env = {**base_env(), "VIRTUAL_ENV": str(VENV)}
        run_step([uv, "pip", "install", "-e", ".[all]", "--group", "dev"], env=env)
    else:
        if not venv_python().exists():
            info("Creating .venv (uv not found, using python -m venv)")
            run_step([sys.executable, "-m", "venv", str(VENV)])
        info("Installing Python dependencies")
        run_step([venv_python(), "-m", "pip", "install", "-q", "-e", ".[all]", "pytest>=8.2", "pytest-asyncio>=0.23"])
    marker.write_text(fingerprint)


def ensure_web_env() -> None:
    """Run npm install when node_modules is missing or older than package-lock.json."""
    installed = WEB / "node_modules" / ".package-lock.json"
    lock = WEB / "package-lock.json"
    if installed.exists() and installed.stat().st_mtime >= lock.stat().st_mtime:
        return
    info("Installing web dependencies")
    run_step([npm(), "install", "--no-audit", "--no-fund"], cwd=WEB)


# --- Database -----------------------------------------------------------------------------


def database_url() -> str:
    """The database the apps connect to."""
    return base_env().get("POR_DATABASE_URL") or LOCAL_APP_DB_URL


def uses_local_container() -> bool:
    """True when the apps use the Postgres container this script manages (the default)."""
    return database_url() == LOCAL_APP_DB_URL


def migration_url() -> str:
    """The database URL migrations run with: the owner's, which may differ from the apps' role."""
    override = base_env().get("POR_MIGRATION_DATABASE_URL")
    return override or (LOCAL_OWNER_DB_URL if uses_local_container() else database_url())


def compose_cmd(*args: str) -> list[str] | None:
    docker = shutil.which("docker")
    return [docker, "compose", "-f", str(COMPOSE_FILE), *args] if docker else None


def database_running() -> bool:
    cmd = compose_cmd("ps", "--status", "running", "-q", DB_SERVICE)
    if cmd is None:
        return False
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def ensure_database() -> None:
    """Start the Postgres container and wait until it accepts connections. Does nothing for another database."""
    if not uses_local_container() or database_running():
        return
    other = "or set POR_DATABASE_URL in .env to use another database"
    docker = shutil.which("docker")
    if not docker:
        fail(f"Docker was not found on PATH. The local database runs in Docker: install Docker Desktop, {other}.")
    if subprocess.run([docker, "info"], capture_output=True).returncode != 0:
        fail(f"Docker is installed but not running. Start Docker Desktop and try again, {other}.")
    if port_open(DB_PORT):
        fail(f"port {DB_PORT} is already in use by another program (perhaps a PostgreSQL installed on this machine), "
             f"so the database container cannot start. Stop that program, {other}.")
    info(f"Starting the Postgres container on localhost:{DB_PORT}")
    run_step(compose_cmd("up", "-d", "--wait", DB_SERVICE))


def stop_database() -> None:
    if not uses_local_container():
        print(f"    {DB} is not managed by this script (POR_DATABASE_URL points elsewhere)")
        return
    if not database_running():
        print(f"    {DB} is not running")
        return
    subprocess.run(compose_cmd("stop", DB_SERVICE), capture_output=True)
    print(f"    stopped {DB} (the data is kept in the Docker volume)")


def migrate() -> None:
    url, app_url = migration_url(), database_url()
    if url.startswith("sqlite") and ":///" in url and not url.endswith(":memory:"):
        # Alembic opens SQLite directly and, unlike the app, does not create the folder.
        (ROOT / url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    ensure_database()
    info("Applying database migrations (alembic upgrade head)")
    env = {**base_env(), "POR_DATABASE_URL": url}
    run_step([venv_python(), "-m", "alembic", "upgrade", "head"], env=env)
    if app_url != url and app_url.startswith("postgresql"):
        # Migrations ran as the owner. The apps connect as their own role, which is not a superuser.
        parts = urllib.parse.urlsplit(app_url)
        if parts.username and parts.password:
            info(f"Checking the apps' database role ({parts.username})")
            run_step([venv_python(), "-m", "scripts.db_app_role"],
                     env={**env, "POR_APP_DB_USER": urllib.parse.unquote(parts.username),
                          "POR_APP_DB_PASSWORD": urllib.parse.unquote(parts.password)})


def demo_seeded() -> bool:
    """True when the demo tenant recorded in var/seed/demo.json exists in the database in use now."""
    try:
        tenant_id = json.loads(SEED_INFO.read_text()).get("tenant_id")
    except (OSError, ValueError):
        return False
    if not tenant_id:
        return False
    code = ("import sys\nfrom db.models import Tenant\nfrom db.session import session_scope\n"
            "with session_scope() as s:\n    sys.exit(0 if s.get(Tenant, sys.argv[1]) else 1)\n")
    result = subprocess.run([str(venv_python()), "-c", code, tenant_id], cwd=ROOT, env=base_env(), capture_output=True)
    return result.returncode == 0


def seed() -> None:
    if demo_seeded():
        info("Demo data already seeded in this database, skipping")
        return
    info("Seeding the demo tenant and submitting sample files")
    run_step([venv_python(), "-m", "scripts.seed", "--submit"])


def demo_api_key() -> str | None:
    try:
        return json.loads(SEED_INFO.read_text()).get("api_key")
    except (OSError, ValueError):
        return None


def mcp_credential_env() -> dict[str, str] | None:
    env = base_env()
    if env.get("POR_MCP_API_KEY") or env.get("POR_MCP_REVIEWER_TOKEN"):
        return {}
    key = demo_api_key()
    return {"POR_MCP_API_KEY": key} if key else None


def command_for(name: str) -> list[str]:
    py = str(venv_python())
    if name == "api":
        reload_dirs = [arg for d in ("api", "services", "agents", "db", "connectors") for arg in ("--reload-dir", d)]
        return [py, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(API_PORT), "--reload", *reload_dirs]
    if name == "worker":
        return [py, "-m", "services.worker"]
    if name == "mcp":
        return [py, "-m", "mcp_server.server", "--http"]
    if name == "web":
        return [npm(), "run", "dev", "--", "--port", str(WEB_PORT), "--strictPort"]
    raise KeyError(name)


# --- Processes ----------------------------------------------------------------------------


def _process_start_time(pid: int) -> float | None:
    """Epoch seconds when the process started, or None if it is not running."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return created / 1e7 - 11644473600  # FILETIME epoch is 1601-01-01
        finally:
            k32.CloseHandle(handle)
    try:
        out = subprocess.run(["ps", "-o", "etime=", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
    except OSError:
        return None
    if len(out) < 2 or out[1].startswith("Z"):
        return None
    days, _, clock = out[0].rpartition("-")
    seconds = 0
    for part in clock.split(":"):
        seconds = seconds * 60 + int(part)
    return time.time() - (seconds + int(days or 0) * 86400)


def read_state(name: str) -> dict | None:
    try:
        return json.loads((RUN_DIR / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None


def running_pid(name: str) -> int | None:
    """The app's pid if the recorded process is still the one this script started."""
    state = read_state(name)
    if not state:
        return None
    started = _process_start_time(state["pid"])
    # Comparing start times guards against a reused pid after a reboot or crash.
    if started is None or abs(started - state["started"]) > 30:
        (RUN_DIR / f"{name}.json").unlink(missing_ok=True)
        return None
    return state["pid"]


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=0.5):
            return True
    except OSError:
        return False


def healthy(app: App) -> bool:
    if app.health_url:
        try:
            with urllib.request.urlopen(app.health_url, timeout=1) as resp:
                return resp.status == 200
        except OSError:
            return False
    return port_open(app.port) if app.port else True


def log_path(name: str) -> Path:
    return LOG_DIR / f"{name}.log"


def start(name: str, extra_env: dict[str, str]) -> subprocess.Popen | None:
    app = APPS[name]
    if running_pid(name):
        info(f"{name} is already running (pid {running_pid(name)})")
        return None
    if app.port and port_open(app.port):
        fail(f"port {app.port} is already in use by another process, so {name} cannot start. Stop that process first.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log = log_path(name)
    if log.exists():
        log.replace(log.with_suffix(".log.1"))
    cmd = command_for(name)
    with open(log, "ab") as fh:
        fh.write(f"=== {name} started {time.strftime('%Y-%m-%d %H:%M:%S')}: {' '.join(cmd)}\n".encode())
        fh.flush()
        kwargs: dict = {}
        if IS_WINDOWS:
            # Own process group and a hidden console, so closing this terminal does not stop the app.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        started = time.time()
        env = {**base_env(), **app.env, **extra_env}
        if name == "api":
            # People open the Vite dev server, so SSO redirects and email links point there unless .env says otherwise.
            env.setdefault("POR_APP_BASE_URL", f"http://localhost:{WEB_PORT}")
        proc = subprocess.Popen(cmd, cwd=app.cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, **kwargs)
    (RUN_DIR / f"{name}.json").write_text(json.dumps({"pid": proc.pid, "started": started, "cmd": cmd}))
    info(f"started {name} (pid {proc.pid}), logging to {log.relative_to(ROOT).as_posix()}")
    return proc


def stop(name: str) -> None:
    pid = running_pid(name)
    if pid is None:
        print(f"    {name} is not running")
        return
    if IS_WINDOWS:
        # /T stops the whole tree (uvicorn's reloader and server, npm's cmd and node).
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + 10
        while time.time() < deadline and _process_start_time(pid) is not None:
            time.sleep(0.2)
        if _process_start_time(pid) is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    (RUN_DIR / f"{name}.json").unlink(missing_ok=True)
    print(f"    stopped {name} (pid {pid})")


def wait_until_ready(procs: dict[str, subprocess.Popen], timeout: float = 90) -> bool:
    pending = dict(procs)
    deadline = time.time() + timeout
    ok = True
    while pending and time.time() < deadline:
        for name, proc in list(pending.items()):
            if proc.poll() is not None:
                print(paint(f"    {name} exited with code {proc.returncode}. Last log lines:", "31"))
                for line in tail_lines(log_path(name), 20):
                    print("      " + line)
                (RUN_DIR / f"{name}.json").unlink(missing_ok=True)
                del pending[name]
                ok = False
            elif healthy(APPS[name]):
                print(f"    {name} is ready")
                del pending[name]
        time.sleep(0.5)
    for name in pending:
        warn(f"{name} did not report ready within {timeout:.0f}s; check `python run.py logs {name}`")
        ok = False
    return ok


# --- Logs ---------------------------------------------------------------------------------


def tail_lines(path: Path, n: int) -> list[str]:
    if not path.exists() or n <= 0:
        return []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 1_000_000))
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def prefix(name: str, width: int) -> str:
    return paint(f"{name:<{width}} |", APP_COLORS.get(name, "0")) + " "


def show_logs(names: list[str], follow: bool, lines: int) -> None:
    width = max(len(n) for n in names)
    multi = len(names) > 1
    for name in names:
        path = log_path(name)
        if not path.exists():
            print(paint(f"no log yet for {name} ({path.relative_to(ROOT).as_posix()})", "2"))
            continue
        for line in tail_lines(path, lines):
            print((prefix(name, width) if multi or follow else "") + line)
    if not follow:
        return
    print(paint(f"--- following {', '.join(names)} (Ctrl+C to stop) ---", "2"), flush=True)
    positions = {n: log_path(n).stat().st_size if log_path(n).exists() else 0 for n in names}
    partial = {n: b"" for n in names}
    try:
        while True:
            for name in names:
                path = log_path(name)
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size < positions[name]:  # the log was rotated by a restart
                    positions[name], partial[name] = 0, b""
                if size == positions[name]:
                    continue
                with open(path, "rb") as fh:
                    fh.seek(positions[name])
                    chunk = fh.read()
                positions[name] += len(chunk)
                data = partial[name] + chunk
                *complete, partial[name] = data.split(b"\n")
                for raw in complete:
                    print(prefix(name, width) + raw.decode("utf-8", errors="replace").rstrip("\r"), flush=True)
            time.sleep(0.25)
    except KeyboardInterrupt:
        print()


# --- Commands -----------------------------------------------------------------------------


def selected(names: list[str] | None) -> list[str]:
    unknown = [n for n in names or [] if n not in ORDER]
    if unknown:
        fail(f"unknown app: {', '.join(unknown)} (choose from {', '.join(ORDER)})")
    chosen = set(names or ORDER)
    return [n for n in ORDER if n in chosen]


def cmd_up(args) -> None:
    explicit = set(args.apps or [])
    names = [n for n in selected(args.apps) if n != DB]
    py_apps = [n for n in names if n != "web"]
    if not args.no_install:
        if py_apps or args.seed:
            ensure_python_env()
        if "web" in names:
            ensure_web_env()
    if not venv_python().exists() and (py_apps or args.seed):
        fail(".venv does not exist; run without --no-install first")
    needs_db = any(APPS[n].needs_db for n in names)
    if DB in explicit or needs_db or args.seed:
        ensure_database()
    if not args.no_migrate and needs_db:
        migrate()
    if args.seed:
        seed()

    mcp_env: dict[str, str] = {}
    if "mcp" in names:
        cred = mcp_credential_env()
        if cred is None:
            message = ("mcp needs a credential: set POR_MCP_API_KEY (submitter) or POR_MCP_REVIEWER_TOKEN (reviewer) "
                       "in .env, or run with --seed to use the demo source app's key")
            if "mcp" in explicit:
                fail(message)
            warn(message + ". Skipping mcp.")
            names.remove("mcp")
        else:
            mcp_env = cred

    procs = {}
    for name in names:
        proc = start(name, mcp_env if name == "mcp" else {})
        if proc:
            procs[name] = proc
    if procs:
        info("Waiting for apps to be ready")
        wait_until_ready(procs)
    print()
    cmd_status(args)
    if SEED_INFO.exists() and ("api" in names or "web" in names):
        print("\nSign in with a seeded email such as ada@demo.test (reviewer) or admin@demo.test (admin).")
        key = demo_api_key()
        if key:
            print(f"Demo source app API key: {key}")
    if args.follow:
        print()
        show_logs(names, follow=True, lines=0)


def cmd_down(args) -> None:
    info("Stopping apps")
    for name in reversed(selected(args.apps)):
        if name == DB:
            stop_database()
        else:
            stop(name)


def cmd_restart(args) -> None:
    if not args.apps:
        args.apps = [n for n in ORDER if n != DB]  # the database keeps running unless it is named
    cmd_down(args)
    args.no_migrate = args.no_install = True
    args.seed = args.follow = False
    cmd_up(args)


def cmd_status(_args) -> None:
    print(f"{'APP':<8} {'STATE':<10} {'PID':<8} {'URL':<32} LOG")
    if uses_local_container():
        state, code = ("running", "32") if database_running() else ("stopped", "2")
        where, log = f"127.0.0.1:{DB_PORT} (Docker)", f"python run.py logs {DB}"
    else:
        state, code = "external", "2"
        where, log = database_url().split(":", 1)[0] + ", from POR_DATABASE_URL", "-"
    print(f"{DB:<8} {paint(f'{state:<10}', code)} {'-':<8} {where:<32} {log}")
    for name in ORDER[1:]:
        app = APPS[name]
        pid = running_pid(name)
        if pid is None:
            state, code = "stopped", "2"
        elif app.port and not healthy(app):
            state, code = "starting", "33"
        else:
            state, code = "running", "32"
        log = log_path(name).relative_to(ROOT).as_posix()
        print(f"{name:<8} {paint(f'{state:<10}', code)} {pid or '-':<8} {app.url or '-':<32} {log}")


def cmd_logs(args) -> None:
    names = [n for n in selected(args.apps) if n != DB]
    if DB in (args.apps or []):
        # The container's log comes from Docker, and only when asked for by name.
        follow = ["-f"] if args.follow and not names else []
        cmd = compose_cmd("logs", "--tail", str(args.lines), *follow, DB_SERVICE)
        if cmd is None:
            fail("Docker was not found on PATH")
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print()
    if names:
        show_logs(names, args.follow, args.lines)


def cmd_migrate(args) -> None:
    if not args.no_install:
        ensure_python_env()
    migrate()


def cmd_seed(args) -> None:
    if not args.no_install:
        ensure_python_env()
    migrate()
    seed()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Proof of Review stack locally.",
        epilog=f"Apps: {DB} (Postgres container), {', '.join(f'{n} ({APPS[n].description})' for n in ORDER[1:])}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def apps_arg(p):
        p.add_argument("apps", nargs="*", metavar="APP", help=f"one or more of: {', '.join(ORDER)} (default: all)")

    up = sub.add_parser("up", help="install if needed, migrate, then start apps in the background")
    apps_arg(up)
    up.add_argument("--seed", action="store_true", help="seed the demo tenant and sample submissions (first run only)")
    up.add_argument("--no-migrate", action="store_true", help="skip alembic upgrade head")
    up.add_argument("--no-install", action="store_true", help="skip dependency checks")
    up.add_argument("-f", "--follow", action="store_true", help="follow the logs after starting")
    up.set_defaults(func=cmd_up)

    down = sub.add_parser("down", help="stop apps")
    apps_arg(down)
    down.set_defaults(func=cmd_down)

    restart = sub.add_parser("restart", help="stop then start apps (no install or migrate)")
    apps_arg(restart)
    restart.set_defaults(func=cmd_restart)

    sub.add_parser("status", help="show which apps are running").set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="show logs for all or some apps")
    apps_arg(logs)
    logs.add_argument("-f", "--follow", action="store_true", help="keep printing new lines (Ctrl+C to stop)")
    logs.add_argument("-n", "--lines", type=int, default=50, help="lines to show from the end of each log (default 50)")
    logs.set_defaults(func=cmd_logs)

    mig = sub.add_parser("migrate", help="apply database migrations only")
    mig.add_argument("--no-install", action="store_true", help="skip dependency checks")
    mig.set_defaults(func=cmd_migrate)

    sd = sub.add_parser("seed", help="migrate, then seed the demo tenant and sample submissions")
    sd.add_argument("--no-install", action="store_true", help="skip dependency checks")
    sd.set_defaults(func=cmd_seed)

    args = parser.parse_args()
    os.chdir(ROOT)
    args.func(args)


MIN_PYTHON = (3, 11)  # pyproject.toml requires-python; the app uses datetime.UTC and other 3.11 features

if __name__ == "__main__":
    if sys.version_info < MIN_PYTHON:
        sys.exit(f"run.py needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer (this is {sys.version.split()[0]}); "
                 "the .venv it creates uses the same interpreter when uv is not installed.")
    main()
