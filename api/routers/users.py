"""User and team management.

Admins manage people; oversight leads can read the list (they need it for workflow settings and
capacity). Admin and reviewer are kept apart: an admin controls the review settings (canary rates,
caps, thresholds), so by default the same person cannot also be measured by them as a reviewer.
"""

from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import ROLES, UserPrincipal, get_db, require_roles
from db.models import AuditEvent, ReviewTask, Team, Tenant, User, utcnow
from services import assignment, audit, canary, passwords, workflow

router = APIRouter(prefix="/v1/admin", tags=["users"])

reader = require_roles("oversight_lead")
admin = require_roles("admin")

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLE_ORDER = ["admin", "reviewer", "submitter", "oversight_lead", "auditor", "integration_owner"]


def _actor(p: UserPrincipal) -> str:
    return f"user:{p.user.id}"


def _kind(roles: list[str]) -> str:
    """One headline label per user, for lists and filters."""
    have = set(roles or [])
    if "admin" in have:
        return "admin"
    if "reviewer" in have:
        return "reviewer"
    return "staff" if have else "none"


def _stats(db: Session, u: User) -> dict | None:
    if "reviewer" not in (u.roles or []):
        return None
    now = utcnow()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    open_tasks = db.scalar(select(func.count()).where(ReviewTask.reviewer_id == u.id, ReviewTask.status == "assigned"))
    completed = db.scalar(select(func.count()).where(
        ReviewTask.reviewer_id == u.id, ReviewTask.status == "completed", ReviewTask.completed_at >= now - timedelta(days=30)))
    rate, scored = canary.catch_rate(db, u.tenant_id, u.id)
    return {
        "open_tasks": open_tasks or 0,
        "completed_30d": completed or 0,
        "minutes_today": round(assignment.load_minutes(db, u.id, day), 1),
        "catch_rate": round(rate, 3) if scored else None,
        "canaries_scored": scored,
    }


def user_out(u: User, db: Session | None = None) -> dict:
    out = {
        "id": u.id, "email": u.email, "name": u.name, "roles": sorted(u.roles or [], key=ROLE_ORDER.index),
        "kind": _kind(u.roles), "team_ids": u.team_ids, "active": u.active,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "sso_linked": bool(u.oidc_subject),
        "password_set": bool(u.password_hash),
        "locked": bool(u.locked_until and u.locked_until > utcnow()),
        "hourly_rate": u.hourly_rate,
        "erased": bool(u.erased_at),
    }
    if db is not None:
        out["stats"] = _stats(db, u)
    return out


def _get_user(db: Session, p: UserPrincipal, user_id: str) -> User:
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise HTTPException(404, "user not found")
    return u


def _active_admins(db: Session, tenant_id: str) -> list[User]:
    users = db.scalars(select(User).where(User.tenant_id == tenant_id, User.active.is_(True))).all()
    return [u for u in users if "admin" in (u.roles or [])]


def _check_roles(db: Session, tenant_id: str, roles: list[str]) -> list[str]:
    roles = sorted(set(roles), key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99)
    bad = set(roles) - ROLES
    if bad:
        raise HTTPException(422, f"unknown roles {sorted(bad)}")
    if not roles:
        raise HTTPException(422, "a user needs at least one role")
    if {"admin", "reviewer"} <= set(roles):
        tenant = db.get(Tenant, tenant_id)
        if not (tenant.settings or {}).get("allow_admin_reviewers", False):
            raise HTTPException(422, "admin and reviewer are separate roles: an admin sets the review controls, so cannot "
                                     "also be measured by them. Use a separate account, or enable allow_admin_reviewers.")
    return roles


def _check_teams(db: Session, tenant_id: str, team_ids: list[str]) -> list[str]:
    team_ids = list(dict.fromkeys(team_ids))
    if team_ids:
        found = set(db.scalars(select(Team.id).where(Team.tenant_id == tenant_id, Team.id.in_(team_ids))).all())
        missing = [t for t in team_ids if t not in found]
        if missing:
            raise HTTPException(422, f"unknown teams {missing}")
    return team_ids


# --- Users ------------------------------------------------------------------------------


@router.get("/users")
def list_users(
    role: str | None = Query(None, description="admin | reviewer | oversight_lead | auditor | integration_owner"),
    status: str = Query("all", pattern="^(active|inactive|all)$"),
    q: str | None = None,
    p: UserPrincipal = Depends(reader),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).where(User.tenant_id == p.tenant_id).order_by(User.email)).all()
    out = []
    for u in users:
        if role and role not in (u.roles or []):
            continue
        if status != "all" and u.active != (status == "active"):
            continue
        if q and q.lower() not in f"{u.email} {u.name}".lower():
            continue
        out.append(user_out(u, db))
    return out


class UserIn(BaseModel):
    email: str
    name: str = ""
    roles: list[str] = Field(default_factory=lambda: ["reviewer"])
    team_ids: list[str] = Field(default_factory=list)
    active: bool = True
    # Optional initial password for sign-in without SSO. Leave out for SSO-only users.
    password: str | None = Field(None, max_length=passwords.MAX_LENGTH)
    hourly_rate: float | None = Field(None, ge=0)  # cost reports; falls back to the team, then the organisation


def _check_password(password: str, email: str) -> None:
    try:
        passwords.check_policy(password, email)
    except passwords.PasswordPolicyError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/users", status_code=201)
def create_user(body: UserIn, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "enter a valid email address")
    roles = _check_roles(db, p.tenant_id, body.roles)
    team_ids = _check_teams(db, p.tenant_id, body.team_ids)
    if body.password:
        _check_password(body.password, email)
    if db.scalars(select(User).where(User.tenant_id == p.tenant_id, User.email == email)).first():
        raise HTTPException(409, "a user with this email already exists")
    u = User(tenant_id=p.tenant_id, email=email, name=body.name.strip(), roles=roles, team_ids=team_ids, active=body.active,
             hourly_rate=body.hourly_rate)
    if body.password:
        u.password_hash, u.password_changed_at = passwords.hash_password(body.password), utcnow()
    db.add(u)
    db.flush()
    audit.record(db, p.tenant_id, _actor(p), "user.created", "user", u.id,
                 {"email": u.email, "roles": roles, "team_ids": team_ids, "active": u.active,
                  "password_set": bool(body.password)})
    return user_out(u, db)


class PasswordIn(BaseModel):
    password: str = Field(max_length=passwords.MAX_LENGTH)


@router.put("/users/{user_id}/password")
def set_user_password(user_id: str, body: PasswordIn, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    """Set or reset a user's password. Also unlocks the account and signs out their sessions."""
    u = _get_user(db, p, user_id)
    if u.id == p.user.id:
        raise HTTPException(409, "change your own password from your account (it needs your current password)")
    _check_password(body.password, u.email)
    passwords.set_password(db, u, body.password, _actor(p), "admin")
    return user_out(u, db)


@router.get("/users/{user_id}")
def get_user(user_id: str, p: UserPrincipal = Depends(reader), db: Session = Depends(get_db)):
    u = _get_user(db, p, user_id)
    out = user_out(u, db)
    tasks = db.scalars(select(ReviewTask).where(ReviewTask.reviewer_id == u.id, ReviewTask.status == "assigned")
                       .order_by(ReviewTask.assigned_at)).all()
    out["open_tasks"] = [{"task_id": t.id, "submission_id": t.submission_id, "stage": t.stage_name, "kind": t.kind,
                          "assigned_at": t.assigned_at.isoformat(), "due_at": t.due_at.isoformat() if t.due_at else None,
                          "effort_minutes": t.effort_minutes} for t in tasks]
    events = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == p.tenant_id, AuditEvent.entity == "user",
                                                 AuditEvent.entity_id == u.id).order_by(AuditEvent.seq.desc()).limit(25)).all()
    out["history"] = [{"at": e.created_at.isoformat(), "action": e.action, "actor": e.actor, "payload": e.payload} for e in events]
    return out


class UserUpdate(BaseModel):
    name: str | None = None
    roles: list[str] | None = None
    team_ids: list[str] | None = None
    active: bool | None = None
    email: str | None = None  # accepted for older clients; email is the sign-in identity and cannot change
    hourly_rate: float | None = Field(None, ge=0)


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    u = _get_user(db, p, user_id)
    if u.erased_at:
        raise HTTPException(409, "this user's personal data has been erased; the account cannot be changed")
    if body.email is not None and body.email.strip().lower() != u.email:
        raise HTTPException(422, "email is the sign-in identity and cannot be changed; create a new user instead")
    roles = _check_roles(db, p.tenant_id, body.roles) if body.roles is not None else list(u.roles or [])
    team_ids = _check_teams(db, p.tenant_id, body.team_ids) if body.team_ids is not None else list(u.team_ids or [])
    active = u.active if body.active is None else body.active

    losing_admin = "admin" in (u.roles or []) and u.active and ("admin" not in roles or not active)
    if u.id == p.user.id and losing_admin:
        raise HTTPException(409, "you cannot remove your own admin access or deactivate yourself")
    if losing_admin and len(_active_admins(db, p.tenant_id)) <= 1:
        raise HTTPException(409, "this is the last active admin; add another admin first")

    was_reviewer = u.active and "reviewer" in (u.roles or [])
    stops_reviewing = was_reviewer and ("reviewer" not in roles or not active)
    before = {"name": u.name, "roles": list(u.roles or []), "team_ids": list(u.team_ids or []), "active": u.active,
              "hourly_rate": u.hourly_rate}
    if body.name is not None:
        u.name = body.name.strip()
    if "hourly_rate" in body.model_fields_set:
        u.hourly_rate = body.hourly_rate
    u.roles, u.team_ids, u.active = roles, team_ids, active
    after = {"name": u.name, "roles": roles, "team_ids": team_ids, "active": active, "hourly_rate": u.hourly_rate}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    db.flush()

    action = "user.updated"
    if before["active"] and not active:
        action = "user.deactivated"
    elif not before["active"] and active:
        action = "user.reactivated"
    if changes:
        audit.record(db, p.tenant_id, _actor(p), action, "user", u.id, {"changes": changes})

    reassigned = []
    if stops_reviewing:
        reassigned = workflow.reassign_open_tasks(
            db, u.id, _actor(p), "reviewer_deactivated" if not active else "reviewer_role_removed")
    elif "reviewer" in roles and active and not was_reviewer:
        workflow.assign_pending(db, p.tenant_id)  # new capacity: pick up items waiting for a reviewer
    out = user_out(u, db)
    out["reassigned"] = reassigned
    return out


@router.post("/users/{user_id}/sign-out")
def sign_out_user(user_id: str, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    """End every session the user has (e.g. a lost laptop). They can sign in again straight away."""
    u = _get_user(db, p, user_id)
    u.sessions_revoked_at = utcnow()
    audit.record(db, p.tenant_id, _actor(p), "user.sessions_revoked", "user", u.id, {"by": "admin"})
    return user_out(u, db)


@router.post("/users/{user_id}/erase")
def erase_user(user_id: str, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    """Remove a deactivated user's personal data (name, email, sign-in details) for an erasure request. Their
    decisions stay, attributed to the anonymised account. Earlier audit entries are append-only and keep what they
    recorded at the time."""
    u = _get_user(db, p, user_id)
    if u.id == p.user.id:
        raise HTTPException(409, "you cannot erase your own account")
    if u.active:
        raise HTTPException(409, "deactivate the user first")
    if u.erased_at:
        return user_out(u, db)
    u.name = "Erased user"
    u.email = f"erased-{u.id}@erased.invalid"
    u.password_hash = None
    u.oidc_subject = None
    u.hourly_rate = None
    u.email_notifications = False
    u.sessions_revoked_at = utcnow()
    u.erased_at = utcnow()
    audit.record(db, p.tenant_id, _actor(p), "user.erased", "user", u.id, {})
    return user_out(u, db)


# --- Teams ------------------------------------------------------------------------------


class TeamIn(BaseModel):
    name: str
    daily_ceiling_minutes: int | None = Field(None, ge=1)
    hourly_rate: float | None = Field(None, ge=0)


def team_out(db: Session, t: Team) -> dict:
    members = [u for u in db.scalars(select(User).where(User.tenant_id == t.tenant_id, User.active.is_(True))).all()
               if t.id in (u.team_ids or [])]
    return {"id": t.id, "name": t.name, "daily_ceiling_minutes": t.daily_ceiling_minutes, "hourly_rate": t.hourly_rate,
            "members": len(members), "reviewers": sum(1 for u in members if "reviewer" in (u.roles or []))}


@router.get("/teams")
def list_teams(p: UserPrincipal = Depends(reader), db: Session = Depends(get_db)):
    return [team_out(db, t) for t in db.scalars(select(Team).where(Team.tenant_id == p.tenant_id).order_by(Team.name)).all()]


@router.post("/teams", status_code=201)
def create_team(body: TeamIn, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "team name is required")
    if db.scalars(select(Team).where(Team.tenant_id == p.tenant_id, Team.name == name)).first():
        raise HTTPException(409, "a team with this name already exists")
    t = Team(tenant_id=p.tenant_id, name=name, daily_ceiling_minutes=body.daily_ceiling_minutes, hourly_rate=body.hourly_rate)
    db.add(t)
    db.flush()
    audit.record(db, p.tenant_id, _actor(p), "team.created", "team", t.id,
                 {"name": t.name, "daily_ceiling_minutes": t.daily_ceiling_minutes, "hourly_rate": t.hourly_rate})
    return team_out(db, t)


@router.put("/teams/{team_id}")
def update_team(team_id: str, body: TeamIn, p: UserPrincipal = Depends(admin), db: Session = Depends(get_db)):
    t = db.get(Team, team_id)
    if t is None or t.tenant_id != p.tenant_id:
        raise HTTPException(404, "team not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "team name is required")
    clash = db.scalars(select(Team).where(Team.tenant_id == p.tenant_id, Team.name == name, Team.id != t.id)).first()
    if clash:
        raise HTTPException(409, "a team with this name already exists")
    t.name, t.daily_ceiling_minutes, t.hourly_rate = name, body.daily_ceiling_minutes, body.hourly_rate
    audit.record(db, p.tenant_id, _actor(p), "team.updated", "team", t.id,
                 {"name": t.name, "daily_ceiling_minutes": t.daily_ceiling_minutes, "hourly_rate": t.hourly_rate})
    return team_out(db, t)
