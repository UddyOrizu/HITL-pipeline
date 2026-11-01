"""Request dependencies: database session, source-app auth and user auth with roles."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import SourceApp, User
from db.session import new_session, set_tenant
from services.security import constant_time_equal, decode_token, hash_secret

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
        for candidate in db.scalars(select(SourceApp).where(SourceApp.api_key_prefix == token[:12])).all():
            if constant_time_equal(candidate.api_key_hash, hash_secret(token)):
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
    set_tenant(db, user.tenant_id)
    request.state.tenant_id = user.tenant_id
    return UserPrincipal(user)


def require_roles(*roles: str):
    def dep(principal: UserPrincipal = Depends(current_user)) -> UserPrincipal:
        if not principal.has(*roles):
            raise HTTPException(403, f"requires role: {' or '.join(roles)}")
        return principal

    return dep
