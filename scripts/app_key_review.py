"""Let a source app's API key review as a named reviewer account, or turn that off again.

With it on, one key can submit work and decide it, which is what lets a single MCP server offer both the
submit tools and the review tools (POR_MCP_API_KEY with POR_MCP_REVIEW_WITH_API_KEY=true). It is off by default:
whoever holds the key can then approve the work it submitted.

Usage, from the repo root:

    python -m scripts.app_key_review --reviewer ada@demo.test             # the organisation's only source app
    python -m scripts.app_key_review --reviewer ada@demo.test --app "Demo drafting assistant"
    python -m scripts.app_key_review --off --app "Demo drafting assistant"
    python -m scripts.app_key_review --list

The same switch is review_as_user_id on PATCH /v1/integrations/apps/{id} (integration owners). Every change is
written to the audit log.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from db.models import SourceApp, User
from db.session import session_scope
from services import audit

ACTOR = "system:app_key_review"


def find_apps(db, wanted: str | None, tenant_id: str | None) -> list[SourceApp]:
    q = select(SourceApp).where(SourceApp.kind == "api")
    if tenant_id:
        q = q.where(SourceApp.tenant_id == tenant_id)
    apps = db.scalars(q.order_by(SourceApp.name)).all()
    return [a for a in apps if wanted in (a.id, a.name)] if wanted else list(apps)


def one_app(db, wanted: str | None, tenant_id: str | None) -> SourceApp:
    apps = find_apps(db, wanted, tenant_id)
    if len(apps) != 1:
        names = ", ".join(f"{a.name} ({a.id})" for a in find_apps(db, None, tenant_id)) or "none"
        sys.exit(f"Name one source app with --app (found {len(apps)}). Available: {names}")
    return apps[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Let a source app's API key review as a reviewer account.")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--reviewer", help="email of the reviewer account the key reviews as")
    what.add_argument("--off", action="store_true", help="stop the key reviewing")
    what.add_argument("--list", action="store_true", help="show each source app and who its key reviews as")
    parser.add_argument("--app", help="source app name or id (not needed when there is only one)")
    args = parser.parse_args()

    with session_scope() as db:
        if args.list:
            for a in find_apps(db, args.app, None):
                user = db.get(User, a.review_as_user_id) if a.review_as_user_id else None
                print(f"{a.name} ({a.id}): {'reviews as ' + user.email if user else 'cannot review'}")
            return
        if args.off:
            app = one_app(db, args.app, None)
            app.review_as_user_id = None
            audit.record(db, app.tenant_id, ACTOR, "source_app.updated", "source_app", app.id, {"review_as_user_id": None})
            print(f"{app.name}: its API key can no longer review.")
            return
        users = db.scalars(select(User).where(func.lower(User.email) == args.reviewer.lower(), User.active.is_(True))).all()
        users = [u for u in users if "reviewer" in (u.roles or [])]
        if len(users) != 1:
            sys.exit(f"Expected one active user with the reviewer role and email {args.reviewer}, found {len(users)}.")
        user = users[0]
        app = one_app(db, args.app, user.tenant_id)
        app.review_as_user_id = user.id
        audit.record(db, app.tenant_id, ACTOR, "source_app.updated", "source_app", app.id, {"review_as_user_id": user.id})
        print(f"{app.name}: its API key can now review as {user.email}.")
        print("Set POR_MCP_REVIEW_WITH_API_KEY=true (with POR_MCP_API_KEY, and no POR_MCP_REVIEWER_TOKEN) so the MCP "
              "server offers the review tools too.")


if __name__ == "__main__":
    main()
