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

Apps: api (FastAPI on :8000), worker (background jobs), web (Vite on :5173), mcp (MCP over HTTP on :8001).
With no APP, every app is used. mcp starts only when it has a credential: POR_MCP_API_KEY or
POR_MCP_REVIEWER_TOKEN in the environment or .env, or the demo API key written by `seed`.

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

API_PORT, WEB_PORT, MCP_PORT = 8000, 5173, 8001


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
ORDER = ["api", "worker", "mcp", "web"]  # start order; stop runs in reverse

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


def migrate() -> None:
    url = base_env().get("POR_DATABASE_URL", "sqlite:///./var/por.db")
    if url.startswith("sqlite") and ":///" in url and not url.endswith(":memory:"):
        # Alembic opens SQLite directly and, unlike the app, does not create the folder.
        (ROOT / url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    info("Applying database migrations (alembic upgrade head)")
    run_step([venv_python(), "-m", "alembic", "upgrade", "head"])


def seed() -> None:
    if SEED_INFO.exists():
        info(f"Demo data already seeded ({SEED_INFO.relative_to(ROOT).as_posix()} exists), skipping")
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
    unknown = [n for n in names or [] if n not in APPS]
    if unknown:
        fail(f"unknown app: {', '.join(unknown)} (choose from {', '.join(ORDER)})")
    chosen = set(names or APPS)
    return [n for n in ORDER if n in chosen]


def cmd_up(args) -> None:
    names = selected(args.apps)
    explicit = set(args.apps or [])
    py_apps = [n for n in names if n != "web"]
    if not args.no_install:
        if py_apps or args.seed:
            ensure_python_env()
        if "web" in names:
            ensure_web_env()
    if not venv_python().exists() and (py_apps or args.seed):
        fail(".venv does not exist; run without --no-install first")
    if not args.no_migrate and any(APPS[n].needs_db for n in names):
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
        stop(name)


def cmd_restart(args) -> None:
    cmd_down(args)
    args.no_migrate = args.no_install = True
    args.seed = args.follow = False
    cmd_up(args)


def cmd_status(_args) -> None:
    print(f"{'APP':<8} {'STATE':<10} {'PID':<8} {'URL':<32} LOG")
    for name in ORDER:
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
    show_logs(selected(args.apps), args.follow, args.lines)


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
        epilog=f"Apps: {', '.join(f'{n} ({APPS[n].description})' for n in ORDER)}",
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
