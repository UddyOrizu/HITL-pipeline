"""Create the first admin from settings, so a new installation can be signed in to without SSO.

Set POR_BOOTSTRAP_ADMIN_EMAIL and POR_BOOTSTRAP_ADMIN_PASSWORD (the email is the username). On API start-up,
the tenant named POR_BOOTSTRAP_TENANT_NAME is created if missing, then the admin if no user with that email
exists in it. An existing user is never changed: editing .env cannot reset a password or grant admin rights,
and the password can be removed from .env once the admin has signed in and changed it.

The bootstrap admin is also a platform administrator (can create and manage tenants at /v1/platform).

Run by hand with: python -m services.bootstrap
Grant platform administration to an existing user: python -m services.bootstrap --grant-platform-admin EMAIL
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select, text

from db.models import Tenant, User, utcnow
from db.session import is_postgres, session_scope
from services import audit, passwords
from services.config import get_settings

log = logging.getLogger("por.bootstrap")

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BootstrapError(RuntimeError):
    pass


def ensure_initial_admin() -> str:
    """Returns what happened: 'not_configured', 'created' or 'exists'. Raises BootstrapError on bad settings."""
    s = get_settings()
    email = (s.bootstrap_admin_email or "").strip().lower()
    password = s.bootstrap_admin_password or ""
    if not email and not password:
        return "not_configured"
    if not email or not password:
        raise BootstrapError("set both POR_BOOTSTRAP_ADMIN_EMAIL and POR_BOOTSTRAP_ADMIN_PASSWORD, or neither")
    if not EMAIL.match(email):
        raise BootstrapError("POR_BOOTSTRAP_ADMIN_EMAIL must be an email address (it is the admin's username)")
    try:
        passwords.check_policy(password, email)
    except passwords.PasswordPolicyError as exc:
        raise BootstrapError(f"POR_BOOTSTRAP_ADMIN_PASSWORD is not acceptable: {exc}") from None
    tenant_name = s.bootstrap_tenant_name.strip() or "Default"

    with session_scope() as db:
        if is_postgres(db):
            # Several API replicas can start at once; only one may create the tenant and admin.
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('por:bootstrap'))"))
        tenant = db.scalars(select(Tenant).where(Tenant.name == tenant_name).order_by(Tenant.created_at)).first()
        if tenant is None:
            tenant = Tenant(name=tenant_name)
            db.add(tenant)
            db.flush()
            audit.record(db, tenant.id, "system:bootstrap", "tenant.created", "tenant", tenant.id, {"name": tenant_name})
            log.info("created tenant %r (%s)", tenant_name, tenant.id)

        existing = db.scalars(select(User).where(User.tenant_id == tenant.id, User.email == email)).first()
        if existing is not None:
            if "admin" not in (existing.roles or []) or not existing.active:
                log.warning("bootstrap user %s exists in %r but is not an active admin; leaving it unchanged",
                            email, tenant_name)
            else:
                log.info("bootstrap admin %s already exists in %r; leaving it unchanged", email, tenant_name)
            return "exists"

        user = User(tenant_id=tenant.id, email=email, name=s.bootstrap_admin_name.strip() or "Administrator",
                    roles=["admin"], team_ids=[], password_hash=passwords.hash_password(password),
                    password_changed_at=utcnow(), is_platform_admin=True)
        db.add(user)
        db.flush()
        audit.record(db, tenant.id, "system:bootstrap", "user.created", "user", user.id,
                     {"email": email, "roles": ["admin"], "team_ids": [], "active": True, "password_set": True})
        log.info("created bootstrap admin %s in tenant %r", email, tenant_name)
        return "created"


def grant_platform_admin(email: str) -> int:
    """Make every active account with this email a platform administrator. Returns how many changed."""
    email = email.strip().lower()
    n = 0
    with session_scope() as db:
        for u in db.scalars(select(User).where(User.email == email, User.active.is_(True))).all():
            if not u.is_platform_admin:
                u.is_platform_admin = True
                audit.record(db, u.tenant_id, "system:bootstrap", "user.platform_admin_granted", "user", u.id, {})
                n += 1
    return n


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if len(sys.argv) == 3 and sys.argv[1] == "--grant-platform-admin":
        print(f"granted to {grant_platform_admin(sys.argv[2])} account(s)")
    else:
        print(ensure_initial_admin())
