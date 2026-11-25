#!/usr/bin/env sh
# Start the whole Proof of Review stack locally on macOS or Linux (API, worker, MCP server, web app).
#
# Checks that Python 3.11+ and Node.js are installed, then hands over to run.py, which creates the
# virtualenv, installs dependencies, starts the Postgres container, applies migrations, seeds the demo
# data and starts every app in the background. Docker must be running for the database.
#
#   ./start-local.sh                 install if needed, migrate, seed, start everything
#   ./start-local.sh status          what is running, with URLs
#   ./start-local.sh logs api -f     follow the API log
#   ./start-local.sh down            stop everything
#
# With no arguments it runs `python run.py migrate`, then `python run.py up --seed`, so the database is
# always brought up to date before the apps start. Any arguments are passed straight to run.py.
# Full guide: docs/running-locally.md

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MIN_NODE=20

# The first interpreter on PATH that is Python 3.11 or newer.
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        PYTHON=$candidate
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "error: Python 3.11 or newer was not found on PATH." >&2
    echo "Install it from https://www.python.org/downloads/ or your package manager, then run this again." >&2
    exit 1
fi

has_node() {
    command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || return 1
    major=$(node --version | sed 's/^v\([0-9]*\).*/\1/')
    [ "$major" -ge "$MIN_NODE" ]
}

if [ "$#" -gt 0 ]; then
    exec "$PYTHON" "$ROOT/run.py" "$@"
fi

# Always bring the database up to date before anything starts. If a migration fails, nothing is started
# (set -e stops the script).
"$PYTHON" "$ROOT/run.py" migrate

# --no-migrate: the migrations have just been applied above.
if has_node; then
    exec "$PYTHON" "$ROOT/run.py" up --seed --no-migrate
else
    echo "warning: Node.js $MIN_NODE+ (with npm) was not found, so the web app will not be started." >&2
    echo "Install it from https://nodejs.org/ and run this again to get the web app on http://localhost:5173." >&2
    exec "$PYTHON" "$ROOT/run.py" up api worker mcp --seed --no-migrate
fi
