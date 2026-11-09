"""User sign-in (SSO, email and password, dev login), sessions, password resets, and OAuth 2.0 client credentials
for source apps."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import ROLES, UserPrincipal, current_user, effective_roles, get_db
from db.models import PasswordResetToken, RevokedToken, SourceApp, Tenant, User, utcnow
from services import audit, mailer, passwords, ratelimit, sso
from services.config import get_settings
from services.security import constant_time_equal, hash_secret, issue_token

router = APIRouter(prefix="/v1", tags=["auth"])


def _user_out(u: User, db: Session) -> dict:
    tenant = db.get(Tenant, u.tenant_id)
    return {"id": u.id, "email": u.email, "name": u.name, "roles": u.roles, "team_ids": u.team_ids,
            "permissions": sorted(effective_roles(u.roles)), "password_set": bool(u.password_hash),
            "sso_linked": bool(u.oidc_subject), "email_notifications": u.email_notifications,
            "platform_admin": bool(u.is_platform_admin),
            "tenant": {"id": tenant.id, "name": tenant.name} if tenant else None}


def _login(db: Session, user: User, method: str) -> dict:
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.active:
        raise HTTPException(403, "this organisation's account is not active")
    user.last_login_at = utcnow()
    audit.record(db, user.tenant_id, f"user:{user.id}", "user.signed_in", "user", user.id, {"method": method})
    return {"access_token": issue_token(user.id, user.tenant_id, "user", user.roles), "token_type": "bearer",
            "expires_in": get_settings().token_ttl_minutes * 60, "user": _user_out(user, db)}


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
    for user in candidates:
        if user.locked_until and user.locked_until > now:
            passwords.burn_time(body.password)
            continue
        if passwords.verify_password(body.password, user.password_hash):
            matched.append(user)
            continue
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
    u = User(tenant_id=provider.tenant_id, email=email, name=(claims.get("name") or "").strip()[:200], roles=roles,
             team_ids=[], active=True, oidc_subject=claims.get("sub"))
    db.add(u)
    db.flush()
    audit.record(db, provider.tenant_id, "system:sso", "user.created", "user", u.id,
                 {"email": email, "roles": roles, "team_ids": [], "active": True, "provisioned_by": "sso"})
    return u


def _sso_sign_in(db: Session, users: list[User], email: str, subject: str | None) -> dict:
    if len(users) > 1:
        raise _choose_tenant(db, users, {"ticket": sso.ticket(email, subject, [u.tenant_id for u in users])})
    user = users[0]
    if user.oidc_subject and user.oidc_subject != subject:
        raise HTTPException(403, "this sign-in does not match the identity linked to the account")
    user.oidc_subject = subject
    return _login(db, user, "sso")


@router.post("/auth/sso/callback", dependencies=[Depends(ratelimit.auth_limit("sso"))])
def sso_callback(body: SsoCallbackIn, db: Session = Depends(get_db)):
    try:
        provider, claims = sso.complete(db, body.code, body.state, body.code_verifier)
        email = sso.identity_email(claims)
    except sso.SsoError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    users = sso.candidates(db, provider, email, claims.get("sub"))
    if not users:
        new = _provision(db, provider, email, claims)
        if new is None:
            raise HTTPException(403, f"there is no account for {email}; ask your administrator to add you")
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
    return _sso_sign_in(db, users[:1], t["email"], t.get("sub"))


class OidcIn(BaseModel):
    id_token: str
    tenant_id: str | None = None


@router.post("/auth/oidc", dependencies=[Depends(ratelimit.auth_limit("sso"))])
def oidc_login(body: OidcIn, db: Session = Depends(get_db)):
    """For native and command-line clients that already hold an ID token from the platform's identity provider."""
    provider = sso.platform_provider()
    if provider is None:
        raise HTTPException(404, "single sign-on is not configured")
    try:
        claims = sso.verify_id_token(provider, body.id_token)
        email = sso.identity_email(claims)
    except sso.SsoError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    users = sso.candidates(db, provider, email, claims.get("sub"))
    if body.tenant_id:
        users = [u for u in users if u.tenant_id == body.tenant_id]
    if not users:
        raise HTTPException(403, "no platform account for this identity")
    if len(users) > 1:
        raise _choose_tenant(db, users)
    return _sso_sign_in(db, users, email, claims.get("sub"))


# --- Sessions ---------------------------------------------------------------------------


@router.post("/auth/refresh")
def refresh(request: Request, principal: UserPrincipal = Depends(current_user)):
    """A fresh token for an active session, up to POR_SESSION_MAX_HOURS after the person signed in."""
    claims = request.state.token_claims
    auth_time = int(claims.get("auth_time") or claims.get("iat") or 0)
    s = get_settings()
    if time.time() - auth_time > s.session_max_hours * 3600:
        raise HTTPException(401, "session has reached its maximum length; sign in again")
    u = principal.user
    return {"access_token": issue_token(u.id, u.tenant_id, "user", u.roles, auth_time=auth_time), "token_type": "bearer",
            "expires_in": s.token_ttl_minutes * 60}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    """Revoke this session's token."""
    claims = request.state.token_claims
    if claims.get("jti") and db.get(RevokedToken, claims["jti"]) is None:
        from datetime import UTC, datetime

        db.add(RevokedToken(jti=claims["jti"], user_id=principal.user.id, tenant_id=principal.tenant_id,
                            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC).replace(tzinfo=None)))
    audit.record(db, principal.tenant_id, f"user:{principal.user.id}", "user.signed_out", "user", principal.user.id, {})


@router.post("/auth/logout-all", status_code=204)
def logout_all(principal: UserPrincipal = Depends(current_user), db: Session = Depends(get_db)):
    """Sign out every session of the signed-in user, including this one."""
    principal.user.sessions_revoked_at = utcnow()
    audit.record(db, principal.tenant_id, f"user:{principal.user.id}", "user.sessions_revoked", "user",
                 principal.user.id, {"by": "self"})


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
def change_own_password(body: PasswordChangeIn, principal: UserPrincipal = Depends(current_user),
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
    return {"access_token": issue_token(user.id, user.tenant_id, "user", user.roles), "token_type": "bearer"}


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
    row = db.scalars(select(PasswordResetToken).where(PasswordResetToken.token_hash == _token_hash(body.token))).first()
    if row is None or row.used_at or row.expires_at < utcnow():
        raise HTTPException(400, "this reset link is invalid or has expired; ask for a new one")
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        raise HTTPException(400, "this reset link is invalid or has expired; ask for a new one")
    try:
        passwords.check_policy(body.new_password, user.email)
    except passwords.PasswordPolicyError as exc:
        raise HTTPException(422, str(exc)) from None
    row.used_at = utcnow()
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
    return {"access_token": issue_token(app.id, app.tenant_id, "app", ttl_minutes=ttl), "token_type": "bearer",
            "expires_in": ttl * 60}
