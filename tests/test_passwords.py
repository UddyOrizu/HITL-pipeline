"""Local passwords: sign-in without SSO, admin-managed passwords, lockout, sign-out on change, initial admin."""

import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant

GOOD = "correct-horse-battery"


def admin_h(world):
    return auth(world["users"]["admin"]["token"])


def login(client, email, password, **extra):
    return client.post("/v1/auth/login", json={"email": email, "password": password, **extra})


def add_user(client, world, email="pat@acme.test", password=GOOD, roles=("reviewer",)):
    r = client.post("/v1/admin/users", json={"email": email, "name": "Pat", "roles": list(roles), "password": password},
                    headers=admin_h(world))
    assert r.status_code == 201, r.text
    return r.json()


def test_hashes_are_salted_and_verify():
    from services import passwords

    a, b = passwords.hash_password(GOOD), passwords.hash_password(GOOD)
    assert a != b and a.startswith("scrypt$") and GOOD not in a
    assert passwords.verify_password(GOOD, a)
    assert not passwords.verify_password(GOOD + "x", a)
    assert not passwords.verify_password(GOOD, None) and not passwords.verify_password(GOOD, "garbage")


def test_admin_adds_user_with_password_and_they_sign_in(client):
    world = make_tenant()
    created = add_user(client, world)
    assert created["password_set"] is True and "password_hash" not in created
    r = login(client, "PAT@acme.test", GOOD)
    assert r.status_code == 200, r.text
    me = client.get("/v1/me", headers=auth(r.json()["access_token"])).json()
    assert me["email"] == "pat@acme.test" and me["roles"] == ["reviewer"] and me["password_set"] is True
    listed = {u["email"]: u for u in client.get("/v1/admin/users", headers=admin_h(world)).json()}
    assert listed["pat@acme.test"]["last_login_at"] and listed["r1@acme.test"]["password_set"] is False


def test_failures_share_one_message_and_sso_only_users_cannot_use_passwords(client):
    world = make_tenant()
    add_user(client, world)
    wrong = login(client, "pat@acme.test", "not-the-password")
    nobody = login(client, "nobody@acme.test", GOOD)
    sso_only = login(client, "r1@acme.test", GOOD)  # exists, but has no password
    assert wrong.status_code == nobody.status_code == sso_only.status_code == 401
    assert wrong.json() == nobody.json() == sso_only.json()


def test_password_policy(client):
    world = make_tenant()
    for bad in ("short", "aaaaaaaaaaaaaaaa", "pat@acme.test"):
        r = client.post("/v1/admin/users", json={"email": "pat@acme.test", "roles": ["reviewer"], "password": bad},
                        headers=admin_h(world))
        assert r.status_code == 422, bad


def test_lockout_after_repeated_failures_and_admin_reset_unlocks(client):
    world = make_tenant()
    user = add_user(client, world)
    for _ in range(5):
        assert login(client, "pat@acme.test", "wrong-password-1").status_code == 401
    assert login(client, "pat@acme.test", GOOD).status_code == 401  # locked, even with the right password
    listed = {u["email"]: u for u in client.get("/v1/admin/users", headers=admin_h(world)).json()}
    assert listed["pat@acme.test"]["locked"] is True

    r = client.put(f"/v1/admin/users/{user['id']}/password", json={"password": "a-brand-new-secret"}, headers=admin_h(world))
    assert r.status_code == 200 and r.json()["locked"] is False
    assert login(client, "pat@acme.test", GOOD).status_code == 401
    assert login(client, "pat@acme.test", "a-brand-new-secret").status_code == 200


def test_changing_a_password_signs_out_older_sessions(client):
    world = make_tenant()
    add_user(client, world)
    old = login(client, "pat@acme.test", GOOD).json()["access_token"]

    import time

    time.sleep(1.1)  # token issue times have one-second resolution
    assert client.post("/v1/me/password", json={"current_password": "wrong", "new_password": "another-good-one"},
                       headers=auth(old)).status_code == 403
    r = client.post("/v1/me/password", json={"current_password": GOOD, "new_password": "another-good-one"}, headers=auth(old))
    assert r.status_code == 200, r.text
    assert client.get("/v1/me", headers=auth(old)).status_code == 401
    assert client.get("/v1/me", headers=auth(r.json()["access_token"])).status_code == 200
    assert login(client, "pat@acme.test", "another-good-one").status_code == 200


def test_sso_user_can_add_a_password_without_a_current_one(client):
    world = make_tenant()
    r = client.post("/v1/me/password", json={"new_password": "first-password-ok"}, headers=auth(world["users"]["r1"]["token"]))
    assert r.status_code == 200, r.text
    assert login(client, "r1@acme.test", "first-password-ok").status_code == 200


def test_admin_cannot_reset_own_password_or_other_tenants(client):
    world, other = make_tenant(), make_tenant("Other")
    me = client.get("/v1/me", headers=admin_h(world)).json()
    assert client.put(f"/v1/admin/users/{me['id']}/password", json={"password": GOOD},
                      headers=admin_h(world)).status_code == 409
    assert client.put(f"/v1/admin/users/{other['users']['r1']['id']}/password", json={"password": GOOD},
                      headers=admin_h(world)).status_code == 404
    assert client.put(f"/v1/admin/users/{world['users']['r2']['id']}/password", json={"password": GOOD},
                      headers=auth(world["users"]["lead"]["token"])).status_code == 403


def test_passwords_never_reach_the_audit_log(client):
    world = make_tenant()
    user = add_user(client, world)
    client.put(f"/v1/admin/users/{user['id']}/password", json={"password": "audit-check-secret"}, headers=admin_h(world))
    login(client, "pat@acme.test", "wrong-password-xyz")
    events = client.get("/v1/audit/events", headers=admin_h(world)).text
    for secret in (GOOD, "audit-check-secret", "wrong-password-xyz", "scrypt$"):
        assert secret not in events
    assert "user.password_set" in events and "user.sign_in_failed" in events


def test_password_login_works_in_prod_where_dev_login_is_off(client, monkeypatch):
    world = make_tenant()
    add_user(client, world)
    from services.config import get_settings

    monkeypatch.setenv("POR_ENV", "prod")
    get_settings.cache_clear()
    assert client.post("/v1/auth/dev-login", json={"email": "pat@acme.test"}).status_code == 404
    assert login(client, "pat@acme.test", GOOD).status_code == 200
    assert client.get("/v1/auth/methods").json() == {"password": True, "password_reset": True, "dev_login": False,
                                                     "sso": False, "sso_needs_email": True}

    monkeypatch.setenv("POR_PASSWORD_LOGIN_ENABLED", "false")
    get_settings.cache_clear()
    assert login(client, "pat@acme.test", GOOD).status_code == 404


# --- Initial admin from settings --------------------------------------------------------


@pytest.fixture()
def bootstrap_env(env, monkeypatch):
    monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_EMAIL", "Root@Firm.test")
    monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-secret-1")
    monkeypatch.setenv("POR_BOOTSTRAP_TENANT_NAME", "Firm LLP")
    from services.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_bootstrap_creates_tenant_and_admin_once_on_startup(bootstrap_env):
    from fastapi.testclient import TestClient

    from api.main import app
    from services import bootstrap

    with TestClient(app) as client:  # runs the start-up hook
        r = login(client, "root@firm.test", "bootstrap-secret-1")
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        me = client.get("/v1/me", headers=auth(token)).json()
        assert me["roles"] == ["admin"] and me["tenant"]["name"] == "Firm LLP"
        # The admin can manage people straight away.
        created = client.post("/v1/admin/users", json={"email": "rev@firm.test", "roles": ["reviewer"], "password": GOOD},
                              headers=auth(token))
        assert created.status_code == 201, created.text
        # Change the password, then restart: the bootstrap must not put the .env password back.
        new = client.post("/v1/me/password", json={"current_password": "bootstrap-secret-1", "new_password": "changed-after-setup"},
                          headers=auth(token))
        assert new.status_code == 200
    assert bootstrap.ensure_initial_admin() == "exists"

    from db.models import Tenant, User
    from db.session import session_scope

    with session_scope() as db:
        assert len(db.scalars(select(Tenant).where(Tenant.name == "Firm LLP")).all()) == 1
        assert len(db.scalars(select(User).where(User.email == "root@firm.test")).all()) == 1
    with TestClient(app) as client:
        assert login(client, "root@firm.test", "bootstrap-secret-1").status_code == 401
        assert login(client, "root@firm.test", "changed-after-setup").status_code == 200


def test_bootstrap_rejects_bad_settings(env, monkeypatch):
    from services import bootstrap
    from services.config import get_settings

    assert bootstrap.ensure_initial_admin() == "not_configured"
    for email, password in (("admin@firm.test", ""), ("not-an-email", "long-enough-pass"), ("admin@firm.test", "short")):
        monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_EMAIL", email)
        monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_PASSWORD", password)
        get_settings.cache_clear()
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap.ensure_initial_admin()
    get_settings.cache_clear()


def test_bootstrap_never_changes_an_existing_user(env, monkeypatch):
    world = make_tenant("Firm")
    from services import bootstrap
    from services.config import get_settings

    # A reviewer already uses this email: the bootstrap must not make them admin or give them a password.
    monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_EMAIL", "r1@firm.test")
    monkeypatch.setenv("POR_BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-secret-1")
    monkeypatch.setenv("POR_BOOTSTRAP_TENANT_NAME", "Firm")
    get_settings.cache_clear()
    assert bootstrap.ensure_initial_admin() == "exists"

    from db.models import User
    from db.session import session_scope

    with session_scope() as db:
        u = db.get(User, world["users"]["r1"]["id"])
        assert u.roles == ["reviewer"] and u.password_hash is None
    get_settings.cache_clear()
