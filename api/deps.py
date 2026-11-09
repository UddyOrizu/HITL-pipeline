"""Request dependencies: database session, source-app auth and user auth with roles."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import RevokedToken, SourceApp, Tenant, User, utcnow
from db.session import new_session, set_tenant
from services.security import constant_time_equal, decode_token, epoch, hash_secret

ROLES = {"reviewer", "submitter", "oversight_lead", "auditor", "integration_owner", "admin"}
# Admins run the platform (people, teams, workflows, integrations, evidence) but do not review.
# Reviewing needs the reviewer role explicitly, so admin access never doubles as review work.
ADMIN_IMPLIES = ROLES - {"reviewer"}


CHANNELS = ("ui", "api", "mcp")


def request_channel(request: Request) -> str:
    """Which client made the call. The web app sends ui, the MCP server sends mcp, anything else is api.
    It labels the evidence; it is not a security boundary (telemetry is what measures reading)."""
    ch = (request.headers.get("x-por-channel") or "api").lower()
    return ch if ch in CHANNELS else "api"


def effective_roles(roles: list[str] | None) -> set[str]:
    have = set(roles or [])
    return have | ADMIN_IMPLIES if "admin" in have else have


def get_db() -> Iterator[Session]:
    session = new_session("*")
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@dataclass
class AppPrincipal:
    app: SourceApp

    @property
    def tenant_id(self) -> str:
        return self.app.tenant_id


def require_app(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> AppPrincipal:
    """API key per source app (X-API-Key or Bearer por_...), or an OAuth client-credentials token."""
    token = x_api_key or _bearer(authorization)
    if not token:
        raise HTTPException(401, "missing API key or bearer token", headers={"WWW-Authenticate": "Bearer"})
    app = None
    if token.startswith("por_"):
        hashed = hash_secret(token)
        for candidate in db.scalars(select(SourceApp).where(SourceApp.api_key_prefix == token[:12])).all():
            if constant_time_equal(candidate.api_key_hash, hashed):
                app = candidate
                break
        if app is None:
            # The previous key keeps working during the grace period after a rotation.
            for candidate in db.scalars(select(SourceApp).where(SourceApp.previous_key_prefix == token[:12])).all():
                if (candidate.previous_key_hash and candidate.previous_key_expires_at
                        and candidate.previous_key_expires_at > utcnow()
                        and constant_time_equal(candidate.previous_key_hash, hashed)):
                    app = candidate
                    break
    else:
        try:
            claims = decode_token(token)
        except jwt.PyJWTError:
            raise HTTPException(401, "invalid token") from None
        if claims.get("typ") == "app":
            app = db.get(SourceApp, claims["sub"])
    if app is None or not app.active:
        raise HTTPException(401, "invalid credentials")
    tenant = db.get(Tenant, app.tenant_id)
    if tenant is None or not tenant.active:
        raise HTTPException(401, "invalid credentials")
    set_tenant(db, app.tenant_id)
    request.state.tenant_id = app.tenant_id
    return AppPrincipal(app)


@dataclass
class UserPrincipal:
    user: User

    @property
    def tenant_id(self) -> str:
        return self.user.tenant_id

    def has(self, *roles: str) -> bool:
        return bool(effective_roles(self.user.roles) & set(roles))


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> UserPrincipal:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None
    if claims.get("typ") != "user":
        raise HTTPException(401, "user token required")
    user = db.get(User, claims["sub"])
    if user is None or not user.active or user.tenant_id != claims.get("tid"):
        raise HTTPException(401, "user not found")
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.active:
        raise HTTPException(401, "this organisation's account is not active")
    iat = int(claims.get("iat", 0))
    # A password change, or "sign out everywhere", ends every session that started before it.
    if user.password_changed_at and iat < epoch(user.password_changed_at):
        raise HTTPException(401, "session ended because the password changed; sign in again")
    if user.sessions_revoked_at and iat < epoch(user.sessions_revoked_at):
        raise HTTPException(401, "session ended; sign in again")
    if claims.get("jti") and db.get(RevokedToken, claims["jti"]) is not None:
        raise HTTPException(401, "session ended; sign in again")
    set_tenant(db, user.tenant_id)
    request.state.tenant_id = user.tenant_id
    request.state.token_claims = claims
    return UserPrincipal(user)


def platform_admin(principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)) -> UserPrincipal:
    """Platform operators manage tenants. These routes work across tenants, so the session is not tenant-scoped."""
    if not principal.user.is_platform_admin:
        raise HTTPException(403, "requires platform administrator access")
    set_tenant(db, "*")
    return principal


def require_roles(*roles: str):
    def dep(principal: UserPrincipal = Depends(current_user)) -> UserPrincipal:
        if not principal.has(*roles):
            raise HTTPException(403, f"requires role: {' or '.join(roles)}")
        return principal

    return dep
