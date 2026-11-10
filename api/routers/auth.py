"""User sign-in (SSO, email and password, dev login), sessions, password resets, and OAuth 2.0 client credentials
for source apps."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from api.deps import ROLES, UserPrincipal, current_user, effective_roles, get_db, session_revocation_id
from db.models import PasswordResetToken, RevokedToken, SourceApp, Tenant, User, utcnow
from services import audit, mailer, passwords, ratelimit, sso
from services.config import get_settings
from services.security import (
    constant_time_equal,
    decode_token,
    hash_secret,
    issue_token,
    oauth_fingerprint,
    session_expiry,
)

router = APIRouter(prefix="/v1", tags=["auth"])


def _user_out(u: User, db: Session) -> dict:
    tenant = db.get(Tenant, u.tenant_id)
    return {"id": u.id, "email": u.email, "name": u.name, "roles": u.roles, "team_ids": u.team_ids,
            "permissions": sorted(effective_roles(u.roles)), "password_set": bool(u.password_hash),
            "sso_linked": bool(u.oidc_subject), "email_notifications": u.email_notifications,
            "platform_admin": bool(u.is_platform_admin),
            "tenant": {"id": tenant.id, "name": tenant.name} if tenant else None}


def _expires_in(token: str) -> int:
    return max(0, int(decode_token(token)["exp"]) - int(time.time()))


def _login(db: Session, user: User, method: str, chan: str = "ui") -> dict:
    """Start a session. The web app's sign-in flows get chan=ui tokens; raw ID token sign-in (native clients) gets
    chan=api, which can never pass as the review workspace."""
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.active:
        raise HTTPException(403, "this organisation's account is not active")
    user.last_login_at = utcnow()
    audit.record(db, user.tenant_id, f"user:{user.id}", "user.signed_in", "user", user.id,
                 {"method": method, "channel": chan})
    token = issue_token(user.id, user.tenant_id, "user", user.roles, chan=chan)
    return {"access_token": token, "token_type": "bearer", "expires_in": _expires_in(token), "user": _user_out(user, db)}


def _choose_tenant(db: Session, users: list[User], extra: dict | None = None) -> HTTPException:
    tenants = [{"id": u.tenant_id, "name": t.name if (t := db.get(Tenant, u.tenant_id)) else u.tenant_id} for u in users]
    return HTTPException(409, {"code": "choose_tenant", "message": "this email has accounts in several organisations; "
                                                                   "choose one", "tenants": tenants, **(extra or {})})


class DevLoginIn(BaseModel):
    email: str
    tenant_id: str | None = None


@router.post("/auth/dev-login", dependencies=[Depends(ratelimit.auth_limit("dev_login"))])
def dev_login(body: DevLoginIn, db: Session = Depends(get_db)):
    """Local development only: sign in by email. Disabled when POR_DEV_LOGIN_ENABLED=false and always in prod."""
    if not get_settings().dev_login_enabled or get_settings().is_prod:
        raise HTTPException(404, "not found")
    q = select(User).where(User.email == body.email.strip().lower(), User.active.is_(True))
    if body.tenant_id:
        q = q.where(User.tenant_id == body.tenant_id)
    users = db.scalars(q).all()
    if not users:
        raise HTTPException(401, "unknown user")
    if len(users) > 1:
        raise _choose_tenant(db, users)
    return _login(db, users[0], "dev")


@router.get("/auth/methods")
def auth_methods(db: Session = Depends(get_db)):
    """Which sign-in methods are switched on, so the sign-in page shows the right options."""
    s = get_settings()
    return {"password": s.password_login_enabled,
            "password_reset": s.password_login_enabled,
            "dev_login": s.dev_login_enabled and not s.is_prod,
            "sso": sso.any_sso(db),
            # With only tenant-specific SSO, the page asks for the email first to find the organisation.
            "sso_needs_email": sso.platform_provider() is None}


class PasswordLoginIn(BaseModel):
    email: str
    password: str = Field(max_length=passwords.MAX_LENGTH)
    tenant_id: str | None = None


INVALID_LOGIN = "incorrect email or password, or the account is temporarily locked"


@router.post("/auth/login", dependencies=[Depends(ratelimit.auth_limit("login"))])
def password_login(body: PasswordLoginIn, db: Session = Depends(get_db)):
    """Sign in with email and password. One generic error for every failure, so accounts cannot be probed."""
    s = get_settings()
    if not s.password_login_enabled:
        raise HTTPException(404, "not found")
    q = select(User).where(User.email == body.email.strip().lower(), User.active.is_(True), User.password_hash.is_not(None))
    if body.tenant_id:
        q = q.where(User.tenant_id == body.tenant_id)
    candidates = db.scalars(q).all()
    if not candidates:
        passwords.burn_time(body.password)

    now = utcnow()
    matched: list[User] = []
    failed: list[User] = []
    for user in candidates:
        if user.locked_until and user.locked_until > now:
            passwords.burn_time(body.password)
            continue
        if passwords.verify_password(body.password, user.password_hash):
            matched.append(user)
        else:
            failed.append(user)

    # The same email can have accounts in several organisations with different passwords. A failure only counts
    # when the password matched none of them: the right password for one account must not lock, or write failed
    # sign-ins into, the person's accounts elsewhere.
    if not matched:
        for user in failed:
            user.failed_logins = (user.failed_logins or 0) + 1
            locked = user.failed_logins >= s.login_max_attempts
            if locked:
                user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
                user.failed_logins = 0
            audit.record(db, user.tenant_id, "anonymous", "user.sign_in_failed", "user", user.id,
                         {"method": "password", "locked": locked})

    if len(matched) > 1:
        db.commit()
        raise _choose_tenant(db, matched)
    if not matched:
        db.commit()  # keep the failed-attempt count; get_db would otherwise roll it back with the error
        raise HTTPException(401, INVALID_LOGIN)
    user = matched[0]
    user.failed_logins, user.locked_until = 0, None
    return _login(db, user, "password")


# --- Single sign-on (browser: authorization code + PKCE) --------------------------------


class SsoStartIn(BaseModel):
    redirect_uri: str
    code_challenge: str
    email: str | None = None


@router.post("/auth/sso/start", dependencies=[Depends(ratelimit.auth_limit("sso"))])
def sso_start(body: SsoStartIn, db: Session = Depends(get_db)):
    """Returns the identity provider URL to send the browser to. The email (optional) picks the organisation's
    own provider when it has one."""
    try:
        return sso.start(db, body.email, body.redirect_uri, body.code_challenge)
    except sso.SsoError as exc:
        raise HTTPException(exc.status, str(exc)) from None


class SsoCallbackIn(BaseModel):
    code: str
    state: str
    code_verifier: str = Field(min_length=43, max_length=128)


def _provision(db: Session, provider: sso.Provider, email: str, claims: dict) -> User | None:
    """Create an account on first sign-in when the organisation allows it (tenant providers only)."""
    roles = [r for r in provider.auto_provision_roles if r in ROLES and r != "admin"]
    if not (provider.tenant_id and roles):
        return None
    if db.scalars(select(User).where(User.tenant_id == provider.tenant_id, User.email == email)).first():
        return None  # an account exists (inactive, or linked to another identity): never create a second one
    u = User(tenant_id=provider.tenant_id, email=email, name=(claims.get("name") or "").strip()[:200], roles=roles,
             team_ids=[], active=True, oidc_subject=claims.get("sub"))
    db.add(u)
    db.flush()
    audit.record(db, provider.tenant_id, "system:sso", "user.created", "user", u.id,
                 {"email": email, "roles": roles, "team_ids": [], "active": True, "provisioned_by": "sso"})
    return u


def _sso_sign_in(db: Session, users: list[User], email: str, subject: str | None, chan: str = "ui") -> dict:
    if len(users) > 1:
        raise _choose_tenant(db, users, {"ticket": sso.ticket(email, subject, [u.tenant_id for u in users])})
    user = users[0]
    if user.oidc_subject and user.oidc_subject != subject:
        raise HTTPException(403, "this sign-in does not match the identity linked to the account")
    user.oidc_subject = subject
    return _login(db, user, "sso", chan)


def _no_account(db: Session, provider: sso.Provider, email: str, verified: bool) -> HTTPException:
    if sso.blocked_by_enforcement(db, provider, email):
        return HTTPException(403, "your organisation requires its own single sign-on: enter your work email on the "
                                  "sign-in page to use it")
    if not verified:
        return HTTPException(403, "the identity provider has not confirmed this email address, so it can't be linked "
                                  "to an account; ask your administrator")
    return HTTPException(403, f"there is no account for {email}; ask your administrator to add you")


@router.post("/auth/sso/callback", dependencies=[Depends(ratelimit.auth_limit("sso"))])
def sso_callback(body: SsoCallbackIn, db: Session = Depends(get_db)):
    try:
        provider, claims = sso.complete(db, body.code, body.state, body.code_verifier)
        email, verified = sso.identity(provider, claims)
    except sso.SsoError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    users = sso.candidates(db, provider, email, claims.get("sub"), verified)
    if not users:
        new = _provision(db, provider, email, claims)
        if new is None:
            raise _no_account(db, provider, email, verified)
        users = [new]
    return _sso_sign_in(db, users, email, claims.get("sub"))


class SsoChooseIn(BaseModel):
    ticket: str
    tenant_id: str


@router.post("/auth/sso/choose", dependencies=[Depends(ratelimit.auth_limit("sso"))])
def sso_choose(body: SsoChooseIn, db: Session = Depends(get_db)):
    """Finish an SSO sign-in for an email with accounts in several organisations."""
    try:
        t = sso.read_ticket(body.ticket)
    except sso.SsoError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    if body.tenant_id not in t["tids"]:
        raise HTTPException(403, "not one of the organisations offered")
    users = [u for u in db.scalars(select(User).where(User.email == t["email"], User.tenant_id == body.tenant_id,
                                                       User.active.is_(True))).all()]
    if not users:
        raise HTTPException(403, "the account is no longer active")
    return _sso_sign_in(db, users[:1], t["email"], t.get("sub"), t.get("chan") or "ui")


class OidcIn(BaseModel):
    id_token: str
    tenant_id: str | None = None


@router.post("/auth/oidc", dependencies=[Depends(ratelimit.auth_limit("sso"))])
def oidc_login(body: OidcIn, db: Session = Depends(get_db)):
    """For native and command-line clients that already hold an ID token from the platform's identity provider.
    The token is an api-channel token: it cannot record decisions as the web review workspace."""
    provider = sso.platform_provider()
    if provider is None:
        raise HTTPException(404, "single sign-on is not configured")
    try:
        claims = sso.verify_id_token(provider, body.id_token)
        email, verified = sso.identity(provider, claims)
    except sso.SsoError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    users = sso.candidates(db, provider, email, claims.get("sub"), verified)
    if body.tenant_id:
        users = [u for u in users if u.tenant_id == body.tenant_id]
    if not users:
        raise HTTPException(403, "no platform account for this identity")
    if len(users) > 1:
        raise _choose_tenant(db, users)
    return _sso_sign_in(db, users, email, claims.get("sub"), chan="api")


# --- Sessions ---------------------------------------------------------------------------


def _naive(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), UTC).replace(tzinfo=None)


def _revoke(db: Session, jti: str, principal: UserPrincipal, expires: int) -> None:
    if db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti, user_id=principal.user.id, tenant_id=principal.tenant_id, expires_at=_naive(expires)))


def _auth_time(claims: dict) -> int:
    return int(claims.get("auth_time") or claims.get("iat") or 0)


@router.post("/auth/refresh")
def refresh(request: Request, principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    """A fresh token for an active session, up to POR_SESSION_MAX_HOURS after the person signed in. The new token
    keeps the session id and channel (an mcp or api token stays one), and the token it replaces stops working."""
    claims = request.state.token_claims
    auth_time = _auth_time(claims)
    s = get_settings()
    if time.time() - auth_time > s.session_max_hours * 3600:
        raise HTTPException(401, "session has reached its maximum length; sign in again")
    u = principal.user
    token = issue_token(u.id, u.tenant_id, "user", u.roles, auth_time=auth_time, chan=claims.get("chan"),
                        sid=claims.get("sid"))
    if claims.get("jti"):
        _revoke(db, claims["jti"], principal, int(claims["exp"]))
    return {"access_token": token, "token_type": "bearer", "expires_in": _expires_in(token)}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    """End this session: this token, the tokens it was refreshed from and into, and tokens issued from it
    (POST /v1/me/tokens). Other sign-ins stay signed in."""
    claims = request.state.token_claims
    if claims.get("jti"):
        _revoke(db, claims["jti"], principal, int(claims["exp"]))
    if claims.get("sid"):
        _revoke(db, session_revocation_id(claims["sid"]), principal,
                max(int(claims["exp"]), session_expiry(_auth_time(claims))))
    audit.record(db, principal.tenant_id, f"user:{principal.user.id}", "user.signed_out", "user", principal.user.id, {})


@router.post("/auth/logout-all", status_code=204)
def logout_all(principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    """Sign out every session of the signed-in user, including this one."""
    principal.user.sessions_revoked_at = utcnow()
    audit.record(db, principal.tenant_id, f"user:{principal.user.id}", "user.sessions_revoked", "user",
                 principal.user.id, {"by": "self"})


class TokenIn(BaseModel):
    channel: Literal["mcp", "api"]
    ttl_minutes: int | None = Field(None, ge=1)


@router.post("/me/tokens", status_code=201)
def issue_client_token(body: TokenIn, request: Request, principal: UserPrincipal = Depends(current_user),
                       db: Session = Depends(get_db)):
    """A token for another client of yours, such as the MCP server in reviewer mode (channel mcp) or a script
    (channel api). It is part of this session: signing out, or signing out everywhere, ends it, and it lasts no longer
    than the session. Decisions made with it are always recorded as that channel. Only the web app (a ui token) can
    ask for one."""
    claims = request.state.token_claims
    if claims.get("chan") != "ui":
        raise HTTPException(403, "client tokens can only be created from the web app")
    s = get_settings()
    ttl = min(body.ttl_minutes or s.token_ttl_minutes, s.token_ttl_minutes)
    u = principal.user
    token = issue_token(u.id, u.tenant_id, "user", u.roles, ttl_minutes=ttl, auth_time=_auth_time(claims),
                        chan=body.channel, sid=claims.get("sid"))
    expires_in = _expires_in(token)
    audit.record(db, u.tenant_id, f"user:{u.id}", "user.token_issued", "user", u.id,
                 {"channel": body.channel, "expires_in": expires_in})
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "channel": body.channel}


# --- Your account -----------------------------------------------------------------------


@router.get("/me")
def me(principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    return _user_out(principal.user, db)


class MeUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    email_notifications: bool | None = None


@router.patch("/me")
def update_me(body: MeUpdate, principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    u = principal.user
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        u.name = changes["name"].strip()
    if changes.get("email_notifications") is not None:
        u.email_notifications = changes["email_notifications"]
    if changes:
        audit.record(db, u.tenant_id, f"user:{u.id}", "user.profile_updated", "user", u.id, changes)
    return _user_out(u, db)


class PasswordChangeIn(BaseModel):
    current_password: str | None = Field(None, max_length=passwords.MAX_LENGTH)
    new_password: str = Field(max_length=passwords.MAX_LENGTH)


@router.post("/me/password")
def change_own_password(body: PasswordChangeIn, request: Request, principal: UserPrincipal = Depends(current_user),
                        db: Session = Depends(get_db)):
    """Set or change your own password. Other sessions are signed out; the response carries a new token."""
    user = principal.user
    if user.password_hash and not passwords.verify_password(body.current_password or "", user.password_hash):
        raise HTTPException(403, "current password is incorrect")
    try:
        passwords.check_policy(body.new_password, user.email)
    except passwords.PasswordPolicyError as exc:
        raise HTTPException(422, str(exc)) from None
    passwords.set_password(db, user, body.new_password, f"user:{user.id}", "self")
    claims = request.state.token_claims
    token = issue_token(user.id, user.tenant_id, "user", user.roles, auth_time=_auth_time(claims),
                        chan=claims.get("chan"), sid=claims.get("sid"))
    return {"access_token": token, "token_type": "bearer", "expires_in": _expires_in(token)}


# --- Forgotten passwords ----------------------------------------------------------------


class ForgotIn(BaseModel):
    email: str = Field(max_length=320)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/auth/forgot-password", status_code=202, dependencies=[Depends(ratelimit.auth_limit("reset"))])
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Email a reset link to each account with this email that signs in with a password. The response is the
    same whether or not there is such an account."""
    s = get_settings()
    if not s.password_login_enabled:
        raise HTTPException(404, "not found")
    users = db.scalars(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True),
                                          User.password_hash.is_not(None))).all()
    for u in users:
        tenant = db.get(Tenant, u.tenant_id)
        if tenant is None or not tenant.active:
            continue
        token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(tenant_id=u.tenant_id, user_id=u.id, token_hash=_token_hash(token),
                                  expires_at=utcnow() + timedelta(minutes=s.password_reset_ttl_minutes)))
        audit.record(db, u.tenant_id, "anonymous", "user.password_reset_requested", "user", u.id, {})
        mailer.send(u.email, "Reset your Proof of Review password",
                    f"Someone asked to reset the password for your {tenant.name} account on Proof of Review.\n\n"
                    f"Open this link within {s.password_reset_ttl_minutes} minutes to choose a new password:\n"
                    f"{s.app_url}/reset-password?token={token}\n\n"
                    "If this wasn't you, ignore this email: your password stays the same.")
    return {"status": "if an account exists for this email, a reset link has been sent"}


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=passwords.MAX_LENGTH)


@router.post("/auth/reset-password", dependencies=[Depends(ratelimit.auth_limit("reset"))])
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    invalid = "this reset link is invalid or has expired; ask for a new one"
    row = db.scalars(select(PasswordResetToken).where(PasswordResetToken.token_hash == _token_hash(body.token))).first()
    if row is None or row.used_at or row.expires_at < utcnow():
        raise HTTPException(400, invalid)
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        raise HTTPException(400, invalid)
    try:
        passwords.check_policy(body.new_password, user.email)
    except passwords.PasswordPolicyError as exc:
        raise HTTPException(422, str(exc)) from None
    # Claim the link with one conditional update, so two uses at the same time can't both succeed.
    now = utcnow()
    claimed = db.execute(update(PasswordResetToken)
                         .where(PasswordResetToken.id == row.id, PasswordResetToken.used_at.is_(None),
                                PasswordResetToken.expires_at >= now)
                         .values(used_at=now).execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        raise HTTPException(400, invalid)
    db.refresh(row)
    passwords.set_password(db, user, body.new_password, "anonymous", "reset_link")
    return {"status": "password changed; sign in with your new password"}


# --- Source apps ------------------------------------------------------------------------


@router.post("/oauth/token", dependencies=[Depends(ratelimit.auth_limit("oauth"))])
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
    tenant = db.get(Tenant, app.tenant_id)
    if tenant is None or not tenant.active:
        raise HTTPException(401, "invalid_client")
    ttl = 60
    # Bound to this client id and secret: turning OAuth off or rotating the secret ends the token (see require_app).
    binding = {"oauth_client": app.oauth_client_id, "oauth_fp": oauth_fingerprint(app.oauth_client_secret_hash)}
    return {"access_token": issue_token(app.id, app.tenant_id, "app", ttl_minutes=ttl, extra=binding),
            "token_type": "bearer", "expires_in": ttl * 60}
