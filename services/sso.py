"""Single sign-on with OpenID Connect: the authorization code flow with PKCE, for the browser.

Providers:
- the platform provider from POR_OIDC_* settings, used by any tenant;
- per-tenant providers in tenant.settings["sso"] (issuer, client_id, client_secret, domains,
  auto_provision_roles, and optional endpoint overrides), found by the email domain the person types.

Flow: the web app makes a PKCE verifier, calls start() for the authorize URL and a signed state, sends the person
to their identity provider, and posts the returned code with the verifier to complete(). The ID token is checked
against the provider's keys, issuer, audience and the nonce in the state.
"""

from __future__ import annotations

import hashlib
import base64
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Tenant, User
from services import secretbox
from services.config import get_settings
from services.security import read_payload, sign_payload

STATE_TTL_SECONDS = 600
_transport: httpx.BaseTransport | None = None
_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}
CACHE_SECONDS = 3600


class SsoError(ValueError):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests inject an httpx.MockTransport that plays the identity provider."""
    global _transport
    _transport = transport
    _discovery_cache.clear()
    _jwks_cache.clear()


def _http() -> httpx.Client:
    return httpx.Client(transport=_transport, timeout=15)


@dataclass
class Provider:
    tenant_id: str | None  # None: the platform provider
    issuer: str
    client_id: str
    client_secret: str | None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks_uri: str | None = None
    scopes: str = "openid email profile"
    auto_provision_roles: tuple[str, ...] = ()

    def _discovered(self) -> dict:
        key = self.issuer.rstrip("/")
        hit = _discovery_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        with _http() as c:
            resp = c.get(f"{key}/.well-known/openid-configuration")
        if resp.status_code != 200:
            raise SsoError(f"cannot read the identity provider's configuration ({resp.status_code})", 502)
        data = resp.json()
        _discovery_cache[key] = (time.time() + CACHE_SECONDS, data)
        return data

    def endpoint(self, name: str) -> str:
        explicit = getattr(self, name)
        if explicit:
            return explicit
        value = self._discovered().get(name)
        if not value:
            raise SsoError(f"identity provider does not publish {name}", 502)
        return value


def platform_provider() -> Provider | None:
    s = get_settings()
    if not s.global_sso_configured:
        return None
    return Provider(None, s.oidc_issuer, s.oidc_client, s.oidc_client_secret, s.oidc_authorization_endpoint,
                    s.oidc_token_endpoint, s.oidc_jwks_url, s.oidc_scopes)


def tenant_provider(tenant: Tenant) -> Provider | None:
    cfg = (tenant.settings or {}).get("sso") or {}
    if not (cfg.get("enabled", True) and cfg.get("issuer") and cfg.get("client_id")):
        return None
    return Provider(tenant.id, cfg["issuer"], cfg["client_id"], secretbox.decrypt(cfg.get("client_secret")) or None,
                    cfg.get("authorization_endpoint"), cfg.get("token_endpoint"), cfg.get("jwks_uri"),
                    cfg.get("scopes") or "openid email profile", tuple(cfg.get("auto_provision_roles") or ()))


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def find_provider(db: Session, email: str | None) -> Provider | None:
    """The tenant whose SSO domains include the email's domain, otherwise the platform provider."""
    domain = _domain(email or "")
    if domain:
        for tenant in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
            cfg = (tenant.settings or {}).get("sso") or {}
            if domain in [d.lower() for d in cfg.get("domains") or []]:
                p = tenant_provider(tenant)
                if p:
                    return p
    return platform_provider()


def any_sso(db: Session) -> bool:
    if platform_provider():
        return True
    return any(tenant_provider(t) for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all())


def allowed_redirects() -> set[str]:
    s = get_settings()
    bases = {s.app_url, *s.cors_origin_list}
    return {f"{b.rstrip('/')}/login/sso" for b in bases}


def start(db: Session, email: str | None, redirect_uri: str, code_challenge: str) -> dict:
    if redirect_uri not in allowed_redirects():
        raise SsoError("redirect_uri is not an allowed sign-in address for this platform", 400)
    if not code_challenge or len(code_challenge) < 43:
        raise SsoError("a PKCE code_challenge (S256) is required", 400)
    provider = find_provider(db, email)
    if provider is None:
        raise SsoError("single sign-on is not set up for this email address", 404)
    nonce = secrets.token_urlsafe(24)
    state = sign_payload("sso_state", {"tid": provider.tenant_id, "nonce": nonce, "ru": redirect_uri,
                                       "hint": (email or "").lower() or None}, STATE_TTL_SECONDS)
    params = {"response_type": "code", "client_id": provider.client_id, "redirect_uri": redirect_uri,
              "scope": provider.scopes, "state": state, "nonce": nonce, "code_challenge": code_challenge,
              "code_challenge_method": "S256"}
    if email:
        params["login_hint"] = email
    return {"authorize_url": f"{provider.endpoint('authorization_endpoint')}?{urlencode(params)}", "state": state}


def _provider_for_state(db: Session, claims: dict) -> Provider:
    tid = claims.get("tid")
    if tid is None:
        p = platform_provider()
    else:
        tenant = db.get(Tenant, tid)
        p = tenant_provider(tenant) if tenant and tenant.active else None
    if p is None:
        raise SsoError("single sign-on is no longer set up for this organisation", 400)
    return p


def _jwks(uri: str) -> dict:
    hit = _jwks_cache.get(uri)
    if hit and hit[0] > time.time():
        return hit[1]
    with _http() as c:
        resp = c.get(uri)
    if resp.status_code != 200:
        raise SsoError("cannot read the identity provider's signing keys", 502)
    data = resp.json()
    _jwks_cache[uri] = (time.time() + CACHE_SECONDS, data)
    return data


def verify_id_token(provider: Provider, id_token: str, nonce: str | None = None) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise SsoError(f"invalid ID token: {exc}") from None
    uri = provider.endpoint("jwks_uri")
    keys = jwt.PyJWKSet.from_dict(_jwks(uri)).keys
    key = next((k for k in keys if k.key_id == header.get("kid")), None)
    if key is None:
        _jwks_cache.pop(uri, None)  # the provider may have rotated keys: fetch again once
        keys = jwt.PyJWKSet.from_dict(_jwks(uri)).keys
        key = next((k for k in keys if k.key_id == header.get("kid")), keys[0] if len(keys) == 1 else None)
    if key is None:
        raise SsoError("ID token is signed with an unknown key")
    try:
        claims = jwt.decode(id_token, key.key, algorithms=["RS256", "ES256", "PS256"], audience=provider.client_id,
                            issuer=provider.issuer, leeway=60)
    except jwt.PyJWTError as exc:
        raise SsoError(f"invalid ID token: {exc}") from None
    if nonce is not None and claims.get("nonce") != nonce:
        raise SsoError("ID token nonce does not match this sign-in")
    return claims


def identity_email(claims: dict) -> str:
    """The verified email. An explicit email_verified=false is refused; Entra ID tokens carry the sign-in name
    (preferred_username / upn) instead, which the directory owns."""
    if claims.get("email") and claims.get("email_verified") is False:
        raise SsoError("the identity provider has not verified this email address", 403)
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    if "@" not in email:
        raise SsoError("the identity provider did not return an email address", 403)
    return email


def complete(db: Session, code: str, state: str, code_verifier: str) -> tuple[Provider, dict]:
    """Exchange the code, verify the ID token. Returns (provider, claims)."""
    try:
        st = read_payload("sso_state", state)
    except jwt.PyJWTError:
        raise SsoError("this sign-in link has expired; start again", 400) from None
    provider = _provider_for_state(db, st)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": st["ru"], "client_id": provider.client_id,
            "code_verifier": code_verifier}
    if provider.client_secret:
        data["client_secret"] = provider.client_secret
    with _http() as c:
        resp = c.post(provider.endpoint("token_endpoint"), data=data, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        raise SsoError(f"the identity provider refused the sign-in code ({resp.status_code})")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise SsoError("the identity provider returned no ID token")
    return provider, verify_id_token(provider, id_token, st["nonce"])


def candidates(db: Session, provider: Provider, email: str, subject: str | None) -> list[User]:
    """Accounts this identity may sign in to. A tenant provider only reaches its own tenant."""
    q = select(User).where(User.email == email, User.active.is_(True))
    if provider.tenant_id:
        q = q.where(User.tenant_id == provider.tenant_id)
    users = [u for u in db.scalars(q).all() if (t := db.get(Tenant, u.tenant_id)) is not None and t.active]
    # A linked account only accepts the identity it was linked to.
    return [u for u in users if not u.oidc_subject or u.oidc_subject == subject]


def ticket(email: str, subject: str | None, tenant_ids: list[str]) -> str:
    """Proof that this person already signed in with SSO, used to pick one of several organisations."""
    return sign_payload("sso_ticket", {"email": email, "sub": subject, "tids": tenant_ids}, 300)


def read_ticket(value: str) -> dict:
    try:
        return read_payload("sso_ticket", value)
    except jwt.PyJWTError:
        raise SsoError("the organisation choice has expired; sign in again", 400) from None


def pkce_challenge(verifier: str) -> str:
    """S256 challenge for a verifier (used by tests and non-browser clients)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
