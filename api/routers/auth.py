"""User sign-in (OIDC, plus dev login) and OAuth 2.0 client credentials for source apps."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, current_user, get_db
from db.models import SourceApp, Tenant, User, utcnow
from services import audit
from services.config import get_settings
from services.security import constant_time_equal, hash_secret, issue_token, verify_oidc_id_token

router = APIRouter(prefix="/v1", tags=["auth"])


def _user_out(u: User, db: Session) -> dict:
    tenant = db.get(Tenant, u.tenant_id)
    from api.deps import effective_roles

    return {"id": u.id, "email": u.email, "name": u.name, "roles": u.roles, "team_ids": u.team_ids,
            "permissions": sorted(effective_roles(u.roles)),
            "tenant": {"id": tenant.id, "name": tenant.name} if tenant else None}


def _login(db: Session, user: User, method: str) -> dict:
    user.last_login_at = utcnow()
    audit.record(db, user.tenant_id, f"user:{user.id}", "user.signed_in", "user", user.id, {"method": method})
    return {"access_token": issue_token(user.id, user.tenant_id, "user", user.roles), "token_type": "bearer",
            "user": _user_out(user, db)}


class DevLoginIn(BaseModel):
    email: str
    tenant_id: str | None = None


@router.post("/auth/dev-login")
def dev_login(body: DevLoginIn, db: Session = Depends(get_db)):
    """Local development only: sign in by email. Disabled when POR_DEV_LOGIN_ENABLED=false."""
    if not get_settings().dev_login_enabled or get_settings().env == "prod":
        raise HTTPException(404, "not found")
    q = select(User).where(User.email == body.email.lower(), User.active.is_(True))
    if body.tenant_id:
        q = q.where(User.tenant_id == body.tenant_id)
    users = db.scalars(q).all()
    if len(users) != 1:
        raise HTTPException(401, "unknown user" if not users else "email exists in several tenants; pass tenant_id")
    return _login(db, users[0], "dev")


class OidcIn(BaseModel):
    id_token: str


@router.post("/auth/oidc")
def oidc_login(body: OidcIn, db: Session = Depends(get_db)):
    """Exchange an ID token from the configured OIDC provider (Entra ID, Google) for a platform token."""
    try:
        claims = verify_oidc_id_token(body.id_token)
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(401, f"invalid ID token: {exc}") from None
    email = (claims.get("email") or claims.get("preferred_username") or "").lower()
    users = db.scalars(select(User).where(User.email == email, User.active.is_(True))).all()
    if len(users) != 1:
        raise HTTPException(403, "no platform account for this identity")
    user = users[0]
    if user.oidc_subject and user.oidc_subject != claims.get("sub"):
        raise HTTPException(403, "identity does not match the linked account")
    user.oidc_subject = claims.get("sub")
    return _login(db, user, "oidc")


@router.get("/me")
def me(principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    return _user_out(principal.user, db)


@router.post("/oauth/token")
def oauth_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    db: Session = Depends(get_db),
):
    """OAuth 2.0 client credentials grant for enterprise source apps."""
    if grant_type != "client_credentials":
        raise HTTPException(400, "unsupported_grant_type")
    app = db.scalars(select(SourceApp).where(SourceApp.oauth_client_id == client_id)).first()
    if app is None or not app.active or not app.oauth_client_secret_hash or not constant_time_equal(
        app.oauth_client_secret_hash, hash_secret(client_secret)
    ):
        raise HTTPException(401, "invalid_client")
    ttl = 60
    return {"access_token": issue_token(app.id, app.tenant_id, "app", ttl_minutes=ttl), "token_type": "bearer",
            "expires_in": ttl * 60}
