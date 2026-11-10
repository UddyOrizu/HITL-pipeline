"""Single sign-on with OpenID Connect: the authorization code flow with PKCE, for the browser.

Providers:
- the platform provider from POR_OIDC_* settings, used by any tenant;
- per-tenant providers in tenant.settings["sso"] (issuer, client_id, client_secret, domains,
  auto_provision_roles, enforce, and optional endpoint overrides), found by the email domain the person types.
  With enforce=true the platform provider cannot sign that organisation's people in.

Flow: the web app makes a PKCE verifier, calls start() for the authorize URL and a signed state, sends the person
to their identity provider, and posts the returned code with the verifier to complete(). The ID token is checked
against the provider's keys, issuer, audience and the nonce in the state.

Calls to an organisation's own provider (discovery, signing keys, the token endpoint, including endpoints read from
the discovery document) are checked with services.netguard when they are made, with DNS resolution, and never
follow redirects, so SSO settings cannot point the platform at internal services. The platform provider comes from
the operator's own settings and is trusted.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Tenant, User
from services import netguard, secretbox
from services.config import get_settings
from services.security import read_payload, sign_payload

STATE_TTL_SECONDS = 600
_transport: httpx.BaseTransport | None = None
_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}
CACHE_SECONDS = 3600
UNREACHABLE = "cannot reach the identity provider; try again, or ask your administrator to check the SSO settings"


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
    return httpx.Client(transport=_transport, timeout=15, follow_redirects=False)


def _check_target(tenant_id: str | None, url: str, resolve: bool = True) -> None:
    """Refuse URLs on private or reserved networks for an organisation's own provider (unless
    POR_ALLOW_PRIVATE_TARGETS allows them, as netguard decides)."""
    if tenant_id is None:
        return
    try:
        netguard.check_url(url, resolve=resolve)
    except netguard.UnsafeTarget:
        raise SsoError("the identity provider's address is not allowed; ask your administrator to check the SSO "
                       "settings", 502) from None


def _call(tenant_id: str | None, method: str, url: str, **kwargs) -> dict:
    """One request to the identity provider. Upstream status codes and bodies are never passed on to the person."""
    _check_target(tenant_id, url)
    try:
        with _http() as c:
            resp = c.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise SsoError(UNREACHABLE, 502) from None
    if resp.status_code != 200:
        if method == "POST":
            raise SsoError("the identity provider refused the sign-in; start again")
        raise SsoError(UNREACHABLE, 502)
    try:
        data = resp.json()
    except ValueError:
        raise SsoError(UNREACHABLE, 502) from None
    if not isinstance(data, dict):
        raise SsoError(UNREACHABLE, 502)
    return data


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
        data = _call(self.tenant_id, "GET", f"{key}/.well-known/openid-configuration")
        _discovery_cache[key] = (time.time() + CACHE_SECONDS, data)
        return data

    def endpoint(self, name: str) -> str:
        explicit = getattr(self, name)
        if explicit:
            return explicit
        value = self._discovered().get(name)
        if not value or not isinstance(value, str):
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


def enforces_own_sso(tenant: Tenant | None) -> bool:
    """The organisation requires its own provider (settings.sso.enforce with a working provider), so the platform
    provider can't sign its people in."""
    if tenant is None:
        return False
    cfg = (tenant.settings or {}).get("sso") or {}
    return bool(cfg.get("enforce")) and tenant_provider(tenant) is not None


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
    authorize = provider.endpoint("authorization_endpoint")
    _check_target(provider.tenant_id, authorize, resolve=False)  # the browser goes there, not the platform
    return {"authorize_url": f"{authorize}?{urlencode(params)}", "state": state}


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


def _jwks(uri: str, tenant_id: str | None = None) -> dict:
    hit = _jwks_cache.get(uri)
    if hit and hit[0] > time.time():
        return hit[1]
    data = _call(tenant_id, "GET", uri)
    _jwks_cache[uri] = (time.time() + CACHE_SECONDS, data)
    return data


def _keys(uri: str, tenant_id: str | None) -> list:
    try:
        return jwt.PyJWKSet.from_dict(_jwks(uri, tenant_id)).keys
    except jwt.PyJWTError:
        raise SsoError("cannot read the identity provider's signing keys", 502) from None


def verify_id_token(provider: Provider, id_token: str, nonce: str | None = None) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise SsoError(f"invalid ID token: {exc}") from None
    uri = provider.endpoint("jwks_uri")
    keys = _keys(uri, provider.tenant_id)
    key = next((k for k in keys if k.key_id == header.get("kid")), None)
    if key is None:
        _jwks_cache.pop(uri, None)  # the provider may have rotated keys: fetch again once
        keys = _keys(uri, provider.tenant_id)
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


def _verified_flag(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def identity(provider: Provider, claims: dict) -> tuple[str, bool]:
    """(email, verified). The email counts as verified when the provider says email_verified=true, or when the claim
    is absent and the provider is the organisation's own (its directory owns the address). email_verified=false is
    refused. Without an email claim, the sign-in name (preferred_username / upn, which Entra ID sends) is used, but
    never counts as verified.

    An unverified email only reaches an account already linked to this identity, or creates one through an
    organisation's auto-provisioning: it never links an existing account."""
    flag = _verified_flag(claims.get("email_verified"))
    email = (claims.get("email") or "").strip().lower()
    if email:
        if flag is False:
            raise SsoError("the identity provider has not verified this email address", 403)
        verified = flag is True or (flag is None and provider.tenant_id is not None)
    else:
        email = (claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
        verified = False
    if "@" not in email:
        raise SsoError("the identity provider did not return an email address", 403)
    return email, verified


def identity_email(claims: dict) -> str:
    """The email without its verification state (as seen from the platform provider). Prefer identity()."""
    return identity(Provider(None, "", "", None), claims)[0]


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
    tokens = _call(provider.tenant_id, "POST", provider.endpoint("token_endpoint"), data=data,
                   headers={"Accept": "application/json"})
    id_token = tokens.get("id_token")
    if not id_token or not isinstance(id_token, str):
        raise SsoError("the identity provider returned no ID token")
    return provider, verify_id_token(provider, id_token, st["nonce"])


def candidates(db: Session, provider: Provider, email: str, subject: str | None, verified: bool = True) -> list[User]:
    """Accounts this identity may sign in to. A tenant provider only reaches its own tenant; the platform provider
    doesn't reach organisations that enforce their own SSO. An unverified email only reaches accounts already
    linked to this identity."""
    q = select(User).where(User.email == email, User.active.is_(True))
    if provider.tenant_id:
        q = q.where(User.tenant_id == provider.tenant_id)
    users = []
    for u in db.scalars(q).all():
        t = db.get(Tenant, u.tenant_id)
        if t is None or not t.active:
            continue
        if provider.tenant_id is None and enforces_own_sso(t):
            continue
        users.append(u)
    if not verified:
        return [u for u in users if subject and u.oidc_subject == subject]
    # A linked account only accepts the identity it was linked to.
    return [u for u in users if not u.oidc_subject or u.oidc_subject == subject]


def blocked_by_enforcement(db: Session, provider: Provider, email: str) -> bool:
    """True when the platform provider was used and the email's accounts are all in organisations that require
    their own SSO."""
    if provider.tenant_id is not None:
        return False
    tenants = [db.get(Tenant, u.tenant_id)
               for u in db.scalars(select(User).where(User.email == email, User.active.is_(True))).all()]
    return bool(tenants) and all(enforces_own_sso(t) for t in tenants)


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
