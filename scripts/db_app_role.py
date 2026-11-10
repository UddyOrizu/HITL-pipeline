"""Create (or update) the database role the API and worker connect as, on Postgres.

Row-level security does not apply to superusers, and the official Postgres image makes POSTGRES_USER one. So the
migrations run as the owner (POR_DATABASE_URL here) and the apps connect as a separate role that is neither a
superuser nor allowed to bypass row-level security. This script runs after `alembic upgrade head`, with the owner's
URL, and is safe to run again:

    POR_APP_DB_USER=por_app POR_APP_DB_PASSWORD=... python -m scripts.db_app_role

It grants the role read/write access to every table and sequence in the public schema, now and for tables the
owner creates later. It does nothing on SQLite or when POR_APP_DB_USER is not set.
"""

from __future__ import annotations

import os
import re
import sys

from sqlalchemy import create_engine, text

from services.config import get_settings


def ensure_app_role(url: str, user: str, password: str) -> bool:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", user):
        raise SystemExit(f"POR_APP_DB_USER must be a plain lower-case identifier, got {user!r}")
    engine = create_engine(url)
    if engine.dialect.name != "postgresql":
        return False
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :u"), {"u": user}).scalar()
        verb = "ALTER" if exists else "CREATE"
        # Identifiers can't be bound parameters; the name is validated above and the password is quoted by Postgres.
        quoted = conn.execute(text("SELECT quote_literal(:p)"), {"p": password}).scalar()
        conn.execute(text(f"{verb} ROLE {user} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
                          f"PASSWORD {quoted}"))
        db = conn.execute(text("SELECT current_database()")).scalar()
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO {user}'))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {user}"))
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {user}"))
        conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {user}"))
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES "
                          f"TO {user}"))
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {user}"))
    return True


def main() -> None:
    user = os.environ.get("POR_APP_DB_USER")
    password = os.environ.get("POR_APP_DB_PASSWORD")
    if not user:
        print("POR_APP_DB_USER is not set: nothing to do")
        return
    if not password:
        sys.exit("POR_APP_DB_PASSWORD is required with POR_APP_DB_USER")
    if ensure_app_role(get_settings().database_url, user, password):
        print(f"database role {user} is ready (no superuser, no BYPASSRLS)")
    else:
        print("not Postgres: no database role needed")


if __name__ == "__main__":
    main()
