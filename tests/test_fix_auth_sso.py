"""SSO hardening: SSRF checks on an organisation's provider, verified emails, and enforcing the organisation's own
provider."""

from __future__ import annotations

import socket

import httpx
import pytest

from tests.conftest import auth, make_tenant
from tests.test_sso_sessions import FakeIdP, challenge, sso_sign_in

TENANT_ISSUER = "https://login.acme.example"


class InternalTokenEndpointIdP(FakeIdP):
    """A provider whose discovery document points the token endpoint at the cloud metadata service."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/authorize",
                                             "token_endpoint": "https://169.254.169.254/latest/meta-data/token",
                                             "jwks_uri": f"{self.issuer}/jwks"})
        if "169.254.169.254" in str(request.url):
            self.token_requests.append({"metadata": True})
            return httpx.Response(200, json={"id_token": "x"})
        return super().__call__(request)


@pytest.fixture()
def public_dns(monkeypatch):
    """Names resolve to a public address, as in production."""
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host.endswith((".example", ".example.test")):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _tenant_sso(client, world, idp, monkeypatch, **extra):
    from services import sso
    from services.config import get_settings

    monkeypatch.setenv("POR_APP_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    sso.set_transport(httpx.MockTransport(idp))
    r = client.put("/v1/admin/tenant", headers=auth(world["users"]["admin"]["token"]), json={"sso": {
        "issuer": idp.issuer, "client_id": idp.client, "domains": ["acme.test"],
        "auto_provision_roles": ["submitter"], **extra}})
    assert r.status_code == 200, r.text
    return r.json()


def test_discovered_token_endpoint_on_a_private_address_is_refused(client, env, monkeypatch, public_dns):
    world = make_tenant()
    idp = InternalTokenEndpointIdP(issuer=TENANT_ISSUER, client="acme-app")
    _tenant_sso(client, world, idp, monkeypatch)
    monkeypatch.setenv("POR_ALLOW_PRIVATE_TARGETS", "false")  # as in production
    from services.config import get_settings

    get_settings.cache_clear()
    r = sso_sign_in(client, "new.person@acme.test", claims={"email": "new.person@acme.test"}, idp=idp)
    assert r.status_code == 502
    assert "169.254" not in r.text and not idp.token_requests


def test_private_issuer_is_refused_at_call_time(client, env, monkeypatch):
    world = make_tenant()
    idp = FakeIdP(issuer="https://idp.corp.example", client="acme-app")
    _tenant_sso(client, world, idp, monkeypatch)
    monkeypatch.setenv("POR_ALLOW_PRIVATE_TARGETS", "false")
    from services.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))])
    r = client.post("/v1/auth/sso/start", json={"redirect_uri": "http://testserver/login/sso",
                                                "code_challenge": challenge(), "email": "a@acme.test"})
    assert r.status_code == 502 and "10.0.0.5" not in r.text


def test_upstream_errors_are_not_echoed(client, env, monkeypatch):
    world = make_tenant()

    class Broken(FakeIdP):
        def __call__(self, request):
            if str(request.url).endswith("/token"):
                return httpx.Response(418, text="secret upstream body")
            return super().__call__(request)

    idp = Broken(issuer=TENANT_ISSUER, client="acme-app")
    _tenant_sso(client, world, idp, monkeypatch)
    r = sso_sign_in(client, "r1@acme.test", claims={}, idp=idp)
    assert r.status_code == 401 and "418" not in r.text and "secret upstream" not in r.text


@pytest.fixture()
def platform_idp(env, monkeypatch):
    from services import sso
    from services.config import get_settings

    monkeypatch.setenv("POR_OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("POR_OIDC_CLIENT_ID", "por-web")
    monkeypatch.setenv("POR_APP_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    fake = FakeIdP()
    sso.set_transport(httpx.MockTransport(fake))
    yield fake
    sso.set_transport(None)


def test_platform_provider_needs_a_verified_email_to_link(client, platform_idp):
    make_tenant()
    assert sso_sign_in(client, claims={"email": "r1@acme.test"}, idp=platform_idp).status_code == 403
    # The sign-in name alone (no email claim) cannot link an existing account either.
    assert sso_sign_in(client, claims={"preferred_username": "r1@acme.test"}, idp=platform_idp).status_code == 403
    ok = sso_sign_in(client, claims={"email": "r1@acme.test", "email_verified": True}, idp=platform_idp)
    assert ok.status_code == 200 and ok.json()["user"]["sso_linked"]
    # Once linked, the same identity gets in by its sign-in name.
    again = sso_sign_in(client, claims={"preferred_username": "r1@acme.test"}, idp=platform_idp)
    assert again.status_code == 200


def test_identity_rules():
    from services.sso import Provider, SsoError, identity

    platform, own = Provider(None, "i", "c", None), Provider("t1", "i", "c", None)
    assert identity(platform, {"email": "A@x.test", "email_verified": True}) == ("a@x.test", True)
    assert identity(platform, {"email": "a@x.test", "email_verified": "true"}) == ("a@x.test", True)
    assert identity(platform, {"email": "a@x.test"}) == ("a@x.test", False)
    assert identity(own, {"email": "a@x.test"}) == ("a@x.test", True)
    assert identity(own, {"upn": "a@x.test"}) == ("a@x.test", False)
    with pytest.raises(SsoError):
        identity(own, {"email": "a@x.test", "email_verified": False})


def test_tenant_can_enforce_its_own_sso(client, platform_idp, monkeypatch):
    from services import sso

    world = make_tenant()
    tenant_idp = FakeIdP(issuer=TENANT_ISSUER, client="acme-app")

    def router(request):
        return (tenant_idp if request.url.host == "login.acme.example" else platform_idp)(request)

    r = client.put("/v1/admin/tenant", headers=auth(world["users"]["admin"]["token"]), json={"sso": {
        "issuer": TENANT_ISSUER, "client_id": "acme-app", "domains": ["acme.test"], "enforce": True}})
    assert r.status_code == 200 and r.json()["sso"]["enforce"] is True
    sso.set_transport(httpx.MockTransport(router))
    # Without an email, the sign-in page uses the platform provider: refused for this organisation.
    blocked = sso_sign_in(client, claims={"email": "r1@acme.test", "email_verified": True}, idp=platform_idp)
    assert blocked.status_code == 403 and "own single sign-on" in blocked.json()["detail"]
    # The organisation's own provider still works.
    ok = sso_sign_in(client, "r1@acme.test", claims={"email": "r1@acme.test"}, idp=tenant_idp)
    assert ok.status_code == 200, ok.text
    # Turning enforcement off lets the platform provider in again.
    client.put("/v1/admin/tenant", headers=auth(world["users"]["admin"]["token"]), json={"sso": {
        "issuer": TENANT_ISSUER, "client_id": "acme-app", "domains": ["acme.test"], "enforce": False}})
    r2 = sso_sign_in(client, claims={"email": "r2@acme.test", "email_verified": True}, idp=platform_idp)
    assert r2.status_code == 200
