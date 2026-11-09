"""Platform administration: create and manage tenants (organisations). Needs a platform administrator
(users.is_platform_admin, set for the bootstrap admin, or with `python -m services.bootstrap --grant-platform-admin`)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, platform_admin
from db.models import ReviewTask, SourceApp, Submission, Tenant, User, utcnow
from services import audit, passwords

router = APIRouter(prefix="/v1/platform", tags=["platform"])

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def tenant_out(db: Session, t: Tenant) -> dict:
    users = db.scalar(select(func.count()).select_from(User).where(User.tenant_id == t.id, User.active.is_(True))) or 0
    subs = db.scalar(select(func.count()).select_from(Submission).where(Submission.tenant_id == t.id)) or 0
    open_tasks = db.scalar(select(func.count()).select_from(ReviewTask).where(
        ReviewTask.tenant_id == t.id, ReviewTask.status == "assigned")) or 0
    apps = db.scalar(select(func.count()).select_from(SourceApp).where(SourceApp.tenant_id == t.id, SourceApp.kind == "api")) or 0
    admins = [u.email for u in db.scalars(select(User).where(User.tenant_id == t.id, User.active.is_(True))).all()
              if "admin" in (u.roles or [])]
    return {"id": t.id, "name": t.name, "region": t.region, "active": t.active,
            "created_at": t.created_at.isoformat() + "Z", "active_users": users, "admins": admins,
            "submissions": subs, "open_tasks": open_tasks, "source_apps": apps,
            "sso": bool(((t.settings or {}).get("sso") or {}).get("issuer"))}


@router.get("/tenants")
def list_tenants(p: UserPrincipal = Depends(platform_admin), db: Session = Depends(get_db)):
    return [tenant_out(db, t) for t in db.scalars(select(Tenant).order_by(Tenant.name)).all()]


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    region: str = Field("uk", max_length=20)
    admin_email: str
    admin_name: str = ""
    # Optional: without a password the first admin signs in with SSO (or a reset link, once email is set up).
    admin_password: str | None = Field(None, max_length=passwords.MAX_LENGTH)


@router.post("/tenants", status_code=201)
def create_tenant(body: TenantIn, p: UserPrincipal = Depends(platform_admin), db: Session = Depends(get_db)):
    email = body.admin_email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "enter a valid admin email address")
    if body.admin_password:
        try:
            passwords.check_policy(body.admin_password, email)
        except passwords.PasswordPolicyError as exc:
            raise HTTPException(422, str(exc)) from None
    if db.scalars(select(Tenant).where(func.lower(Tenant.name) == body.name.strip().lower())).first():
        raise HTTPException(409, "an organisation with this name already exists")
    t = Tenant(name=body.name.strip(), region=body.region, settings={}, active=True)
    db.add(t)
    db.flush()
    audit.record(db, t.id, f"platform:{p.user.id}", "tenant.created", "tenant", t.id,
                 {"name": t.name, "region": t.region, "created_by": p.user.email})
    u = User(tenant_id=t.id, email=email, name=body.admin_name.strip(), roles=["admin"], team_ids=[], active=True)
    if body.admin_password:
        u.password_hash, u.password_changed_at = passwords.hash_password(body.admin_password), utcnow()
    db.add(u)
    db.flush()
    audit.record(db, t.id, f"platform:{p.user.id}", "user.created", "user", u.id,
                 {"email": email, "roles": ["admin"], "team_ids": [], "active": True, "password_set": bool(body.admin_password)})
    return tenant_out(db, t)


class TenantPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    active: bool | None = None


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: str, body: TenantPatch, p: UserPrincipal = Depends(platform_admin), db: Session = Depends(get_db)):
    """Rename, or deactivate (its users and source apps are refused at once; nothing is deleted)."""
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(404, "organisation not found")
    if body.active is False and t.id == p.tenant_id:
        raise HTTPException(409, "you cannot deactivate your own organisation")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name"):
        name = changes["name"].strip()
        clash = db.scalars(select(Tenant).where(func.lower(Tenant.name) == name.lower(), Tenant.id != t.id)).first()
        if clash:
            raise HTTPException(409, "an organisation with this name already exists")
        t.name = name
    if changes.get("active") is not None:
        t.active = changes["active"]
    audit.record(db, t.id, f"platform:{p.user.id}", "tenant.updated", "tenant", t.id, changes)
    return tenant_out(db, t)
