"""Browser SSO (authorization code + PKCE), sessions, password resets, rate limits, tenants and tenant settings."""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import auth, make_tenant

ISSUER = "https://idp.example.test"
CLIENT = "por-web"
VERIFIER = "v" * 64
REDIRECT = "http://testserver/login/sso"


class FakeIdP:
    """Plays an OpenID provider: discovery, JWKS and the token endpoint."""

    def __init__(self, issuer=ISSUER, client=CLIENT):
        self.issuer, self.client = issuer, client
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict = {}
        self.token_requests: list[dict] = []

    def jwk(self) -> dict:
        data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**data, "kid": "k1", "use": "sig", "alg": "RS256"}

    def id_token(self, nonce: str, **claims) -> str:
        now = int(time.time())
        body = {"iss": self.issuer, "aud": self.client, "iat": now, "exp": now + 300, "nonce": nonce,
                "sub": "subject-1", **claims}
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": "k1"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/authorize",
                                             "token_endpoint": f"{self.issuer}/token", "jwks_uri": f"{self.issuer}/jwks"})
        if url.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if url.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            nonce = form["code"]  # the test passes the nonce as the code, so the IdP can echo it
            return httpx.Response(200, json={"id_token": self.id_token(nonce, **self.claims), "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture()
def idp(env, monkeypatch):
    from services import sso
    from services.config import get_settings

    monkeypatch.setenv("POR_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("POR_OIDC_CLIENT_ID", CLIENT)
    monkeypatch.setenv("POR_APP_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    fake = FakeIdP()
    sso.set_transport(httpx.MockTransport(fake))
    yield fake
    sso.set_transport(None)


def challenge(verifier=VERIFIER) -> str:
    from services.sso import pkce_challenge

    return pkce_challenge(verifier)


def sso_sign_in(client, email: str | None = None, claims: dict | None = None, idp: FakeIdP | None = None):
    r = client.post("/v1/auth/sso/start", json={"redirect_uri": REDIRECT, "code_challenge": challenge(), "email": email})
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["authorize_url"]).query)
    assert q["code_challenge"] == [challenge()] and q["code_challenge_method"] == ["S256"]
    if idp is not None and claims is not None:
        idp.claims = claims
    return client.post("/v1/auth/sso/callback", json={"code": q["nonce"][0], "state": r.json()["state"],
                                                      "code_verifier": VERIFIER})


def test_browser_sso_signs_in_and_links_the_identity(client, idp):
    make_tenant()
    assert client.get("/v1/auth/methods").json()["sso"] is True
    r = sso_sign_in(client, claims={"email": "r1@acme.test", "email_verified": True}, idp=idp)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "r1@acme.test" and r.json()["user"]["sso_linked"]
    assert idp.token_requests[0]["code_verifier"] == VERIFIER
    # Another identity with the same email cannot take over the linked account.
    r = sso_sign_in(client, claims={"email": "r1@acme.test", "sub": "someone-else"}, idp=idp)
    assert r.status_code == 403


def test_sso_refuses_unverified_email_unknown_people_and_bad_redirects(client, idp):
    make_tenant()
    assert sso_sign_in(client, claims={"email": "r1@acme.test", "email_verified": False}, idp=idp).status_code == 403
    assert sso_sign_in(client, claims={"email": "nobody@acme.test"}, idp=idp).status_code == 403
    r = client.post("/v1/auth/sso/start", json={"redirect_uri": "https://evil.example/login/sso", "code_challenge": challenge()})
    assert r.status_code == 400


def test_sso_state_nonce_must_match(client, idp):
    make_tenant()
    r = client.post("/v1/auth/sso/start", json={"redirect_uri": REDIRECT, "code_challenge": challenge()})
    idp.claims = {"email": "r1@acme.test"}
    bad = client.post("/v1/auth/sso/callback", json={"code": "not-the-nonce", "state": r.json()["state"], "code_verifier": VERIFIER})
    assert bad.status_code == 401 and "nonce" in bad.json()["detail"]


def test_sso_email_in_two_tenants_chooses_an_organisation(client, idp):
    a = make_tenant("Alpha")
    b = make_tenant("Bravo")
    from db.models import User
    from db.session import session_scope

    with session_scope() as db:
        db.add(User(tenant_id=b["tenant_id"], email="shared@alpha.test", name="S", roles=["reviewer"], team_ids=[]))
        db.add(User(tenant_id=a["tenant_id"], email="shared@alpha.test", name="S", roles=["reviewer"], team_ids=[]))
    r = sso_sign_in(client, claims={"email": "shared@alpha.test"}, idp=idp)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert {t["name"] for t in detail["tenants"]} == {"Alpha", "Bravo"}
    chosen = client.post("/v1/auth/sso/choose", json={"ticket": detail["ticket"], "tenant_id": b["tenant_id"]})
    assert chosen.status_code == 200 and chosen.json()["user"]["tenant"]["name"] == "Bravo"


def test_tenant_sso_by_email_domain_with_auto_provisioning(client, env, monkeypatch):
    from services import sso
    from services.config import get_settings

    monkeypatch.setenv("POR_APP_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    world = make_tenant()
    tenant_idp = FakeIdP(issuer="https://login.acme.example", client="acme-app")
    sso.set_transport(httpx.MockTransport(tenant_idp))
    admin = auth(world["users"]["admin"]["token"])
    r = client.put("/v1/admin/tenant", headers=admin, json={"sso": {
        "issuer": "https://login.acme.example", "client_id": "acme-app", "client_secret": "s3cret",
        "domains": ["acme.test"], "auto_provision_roles": ["submitter"]}})
    assert r.status_code == 200, r.text
    assert r.json()["sso"]["client_secret"] == "***"
    methods = client.get("/v1/auth/methods").json()
    assert methods["sso"] and methods["sso_needs_email"]
    # Unknown domain: no provider.
    assert client.post("/v1/auth/sso/start", json={"redirect_uri": REDIRECT, "code_challenge": challenge(),
                                                   "email": "x@other.test"}).status_code == 404
    r = sso_sign_in(client, "new.person@acme.test", claims={"email": "new.person@acme.test", "name": "New Person"}, idp=tenant_idp)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["roles"] == ["submitter"]
    assert tenant_idp.token_requests[0]["client_secret"] == "s3cret"
    # The client secret is encrypted at rest.
    from db.models import Tenant
    from db.session import session_scope

    with session_scope() as db:
        assert db.get(Tenant, world["tenant_id"]).settings["sso"]["client_secret"].startswith("enc:v1:")
    # A domain belongs to one organisation only.
    other = make_tenant("Other")
    clash = client.put("/v1/admin/tenant", headers=auth(other["users"]["admin"]["token"]), json={"sso": {
        "issuer": "https://login.other.example", "client_id": "x", "domains": ["acme.test"]}})
    assert clash.status_code == 409


# --- Sessions ---------------------------------------------------------------------------


def login(client, email="r1@acme.test"):
    r = client.post("/v1/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_tokens_use_real_unix_time(client):
    make_tenant()
    claims = jwt.decode(login(client), options={"verify_signature": False})
    assert abs(claims["iat"] - time.time()) < 10 and claims["jti"] and claims["auth_time"] == claims["iat"]


def test_refresh_keeps_the_sign_in_time_and_stops_at_the_maximum(client, monkeypatch):
    make_tenant()
    token = login(client)
    first = jwt.decode(token, options={"verify_signature": False})
    r = client.post("/v1/auth/refresh", headers=auth(token))
    assert r.status_code == 200
    refreshed = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert refreshed["auth_time"] == first["auth_time"] and refreshed["jti"] != first["jti"]
    monkeypatch.setenv("POR_SESSION_MAX_HOURS", "0")
    from services.config import get_settings

    get_settings.cache_clear()
    time.sleep(1.1)
    assert client.post("/v1/auth/refresh", headers=auth(token)).status_code == 401


def test_logout_revokes_only_that_session(client):
    make_tenant()
    one, two = login(client), login(client)
    assert client.post("/v1/auth/logout", headers=auth(one)).status_code == 204
    assert client.get("/v1/me", headers=auth(one)).status_code == 401
    assert client.get("/v1/me", headers=auth(two)).status_code == 200


def test_sign_out_everywhere_by_user_or_admin(client):
    world = make_tenant()
    one = login(client)
    time.sleep(1.1)
    assert client.post("/v1/auth/logout-all", headers=auth(one)).status_code == 204
    assert client.get("/v1/me", headers=auth(one)).status_code == 401
    time.sleep(1.1)
    fresh = login(client)
    assert client.get("/v1/me", headers=auth(fresh)).status_code == 200
    time.sleep(1.1)
    r = client.post(f"/v1/admin/users/{world['users']['r1']['id']}/sign-out", headers=auth(world["users"]["admin"]["token"]))
    assert r.status_code == 200
    assert client.get("/v1/me", headers=auth(fresh)).status_code == 401


def test_profile_update(client):
    make_tenant()
    token = login(client)
    r = client.patch("/v1/me", headers=auth(token), json={"name": "Reviewer One", "email_notifications": False})
    assert r.json()["name"] == "Reviewer One" and r.json()["email_notifications"] is False


# --- Password reset and rate limits -----------------------------------------------------


def test_forgotten_password_reset_by_email(client):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    client.put(f"/v1/admin/users/{world['users']['r1']['id']}/password", headers=admin, json={"password": "first-password-123"})
    from services import mailer

    mailer.outbox().clear()
    assert client.post("/v1/auth/forgot-password", json={"email": "nobody@acme.test"}).status_code == 202
    assert not mailer.outbox()
    assert client.post("/v1/auth/forgot-password", json={"email": "R1@acme.test"}).status_code == 202
    body = mailer.outbox()[-1].get_content()
    token = body.split("token=")[1].split()[0]
    r = client.post("/v1/auth/reset-password", json={"token": token, "new_password": "brand-new-password-9"})
    assert r.status_code == 200, r.text
    assert client.post("/v1/auth/login", json={"email": "r1@acme.test", "password": "brand-new-password-9"}).status_code == 200
    # Single use.
    again = client.post("/v1/auth/reset-password", json={"token": token, "new_password": "another-password-77"})
    assert again.status_code == 400


def test_sign_in_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("POR_RATE_LIMIT_AUTH_PER_MINUTE", "3")
    from services.config import get_settings

    get_settings.cache_clear()
    make_tenant()
    codes = [client.post("/v1/auth/login", json={"email": "r1@acme.test", "password": "wrong-password-1"}).status_code
             for _ in range(4)]
    assert codes[:3] == [401, 401, 401] and codes[3] == 429


def test_tokens_use_real_time_for_password_change_checks(client):
    world = make_tenant()
    token = login(client)
    r = client.post("/v1/me/password", headers=auth(token), json={"new_password": "a-fresh-password-42"})
    assert r.status_code == 200
    assert client.get("/v1/me", headers=auth(r.json()["access_token"])).status_code == 200
    assert world


# --- Platform administration and tenant settings ----------------------------------------


def test_platform_admin_creates_and_deactivates_tenants(client, monkeypatch):
    from services import bootstrap
    from services.config import get_settings

    monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_EMAIL", "root@platform.test")
    monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_PASSWORD", "platform-root-password")
    get_settings.cache_clear()
    assert bootstrap.ensure_initial_admin() == "created"
    root = client.post("/v1/auth/login", json={"email": "root@platform.test", "password": "platform-root-password"}).json()
    assert root["user"]["platform_admin"] is True
    headers = auth(root["access_token"])

    world = make_tenant()
    assert client.get("/v1/platform/tenants", headers=auth(world["users"]["admin"]["token"])).status_code == 403

    r = client.post("/v1/platform/tenants", headers=headers, json={
        "name": "Newco", "admin_email": "boss@newco.test", "admin_password": "newco-admin-password"})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["admins"] == ["boss@newco.test"]
    boss = client.post("/v1/auth/login", json={"email": "boss@newco.test", "password": "newco-admin-password"})
    assert boss.status_code == 200
    names = {t["name"] for t in client.get("/v1/platform/tenants", headers=headers).json()}
    assert {"Default", "Acme", "Newco"} <= names

    # Deactivating an organisation locks out its users and source apps.
    assert client.patch(f"/v1/platform/tenants/{world['tenant_id']}", headers=headers, json={"active": False}).status_code == 200
    assert client.get("/v1/me", headers=auth(world["users"]["r1"]["token"])).status_code == 401
    assert client.get("/v1/workflows", headers={"X-API-Key": world["api_key"]}).status_code == 401
    assert client.post("/v1/auth/dev-login", json={"email": "r1@acme.test"}).status_code == 403


def test_tenant_settings_validation_and_admin_reviewers(client):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    assert client.put("/v1/admin/tenant", headers=admin, json={"retention_days": 7}).status_code == 422
    assert client.put("/v1/admin/tenant", headers=admin, json={"currency": "GBP"}).status_code == 422
    r = client.put("/v1/admin/tenant", headers=admin, json={
        "currency": "GBP", "usd_to_currency": 0.8, "default_hourly_rate": 60, "retention_days": 365,
        "allow_admin_reviewers": True, "model_prices": {"my-model": {"input": 1, "output": 2}},
        "notifications": {"assigned": True}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["currency"] == "GBP" and out["retention_days"] == 365 and out["notifications"]["assigned"]
    assert "claude-opus-5-5" in out["default_model_prices"]
    # allow_admin_reviewers, set through the API, now lets one account hold both roles.
    r = client.put(f"/v1/admin/users/{world['users']['r2']['id']}", headers=admin, json={"roles": ["admin", "reviewer"]})
    assert r.status_code == 200
    # Only admins.
    assert client.get("/v1/admin/tenant", headers=auth(world["users"]["lead"]["token"])).status_code == 403


def test_email_notifications_when_switched_on(client, sink):
    from services import mailer

    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    client.put("/v1/admin/tenant", headers=admin, json={"notifications": {"assigned": True}})
    mailer.outbox().clear()
    from tests.conftest import submit_text

    submit_text(client, world)
    assert any("New review task" in m["Subject"] for m in mailer.outbox())


def test_cors_origins_come_from_settings(monkeypatch):
    from services.config import Settings

    s = Settings(_env_file=None, cors_origins="https://app.example.com/, https://other.example.com")
    assert s.cors_origin_list == ["https://app.example.com", "https://other.example.com"]
    assert Settings(_env_file=None).cors_origin_list == []


def test_default_build_sends_no_cors_headers(client):
    r = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_pkce_helper_matches_rfc7636_example():
    from services.sso import pkce_challenge

    assert pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert base64  # imported for readers comparing with the RFC
