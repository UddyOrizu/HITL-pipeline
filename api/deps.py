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
from services.security import constant_time_equal, decode_token, epoch, hash_secret, oauth_fingerprint

ROLES = {"reviewer", "submitter", "oversight_lead", "auditor", "integration_owner", "admin"}
# Admins run the platform (people, teams, workflows, integrations, evidence) but do not review.
# Reviewing needs the reviewer role explicitly, so admin access never doubles as review work.
ADMIN_IMPLIES = ROLES - {"reviewer"}


CHANNELS = ("ui", "api", "mcp")


def request_channel(request: Request) -> str:
    """Which client made the call: ui (the web app), mcp (the MCP server) or api (anything else).

    For people, the sign-in token decides. Tokens from the web app's sign-in flows carry chan=ui; tokens made for
    other clients (POST /v1/me/tokens, or POST /v1/auth/oidc with a raw ID token) carry chan=mcp or chan=api and
    always report that channel, whatever X-PoR-Channel says, so they can never pass as the review workspace.
    A ui token reports the X-PoR-Channel header (the web app sends ui, the MCP server mcp), defaulting to api.
    Tokens issued before channels existed (no chan claim) count as api, and may only lower themselves to mcp.
    Source apps report the header (defaulting to api)."""
    header = (request.headers.get("x-por-channel") or "").strip().lower()
    claims = getattr(request.state, "token_claims", None)
    if not claims or claims.get("typ") != "user":
        return header if header in CHANNELS else "api"
    chan = claims.get("chan")
    if chan in ("api", "mcp"):
        return chan
    if chan == "ui":
        return header if header in CHANNELS else "api"
    return header if header in ("api", "mcp") else "api"


def session_revocation_id(sid: str) -> str:
    """RevokedToken.jti for a whole revoked session."""
    return f"sid:{sid}"


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
            # Tokens from the OAuth client credentials grant are bound to the client id and secret they were issued
            # for: turning OAuth off or rotating the secret ends them. (Connector tokens carry no such binding.)
            if app is not None and "oauth_client" in claims and not (
                    app.oauth_client_id and app.oauth_client_secret_hash
                    and constant_time_equal(app.oauth_client_id, str(claims["oauth_client"]))
                    and constant_time_equal(oauth_fingerprint(app.oauth_client_secret_hash), str(claims.get("oauth_fp", "")))):
                app = None
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
    # Signing out ends the whole session: every token refreshed or issued from it carries the same sid.
    if claims.get("sid") and db.get(RevokedToken, session_revocation_id(claims["sid"])) is not None:
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
