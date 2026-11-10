"""Password sign-in across organisations, portal idempotency and rate limits, client IPs, production checks and
single-use reset links."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant

PASSWORD_A = "alpha-password-123"
PASSWORD_B = "bravo-password-456"


def _shared_accounts():
    from db.models import User
    from db.session import session_scope
    from services import passwords

    a, b = make_tenant("Alpha"), make_tenant("Bravo")
    ids = {}
    with session_scope() as db:
        for world, pw in ((a, PASSWORD_A), (b, PASSWORD_B)):
            u = User(tenant_id=world["tenant_id"], email="pat@shared.test", name="Pat", roles=["reviewer"], team_ids=[],
                     password_hash=passwords.hash_password(pw))
            db.add(u)
            db.flush()
            ids[world["tenant_id"]] = u.id
    return a, b, ids


def test_right_password_for_one_org_does_not_count_against_the_other(client, monkeypatch):
    from db.models import AuditEvent, User
    from db.session import session_scope
    from services.config import get_settings

    monkeypatch.setenv("POR_LOGIN_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("POR_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    a, b, ids = _shared_accounts()
    for _ in range(3):
        r = client.post("/v1/auth/login", json={"email": "pat@shared.test", "password": PASSWORD_A})
        assert r.status_code == 200 and r.json()["user"]["tenant"]["id"] == a["tenant_id"]
    with session_scope() as db:
        other = db.get(User, ids[b["tenant_id"]])
        assert not other.failed_logins and other.locked_until is None
        assert not db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == b["tenant_id"],
                                                       AuditEvent.action == "user.sign_in_failed")).all()
    # A wrong password still counts against both.
    client.post("/v1/auth/login", json={"email": "pat@shared.test", "password": "wrong-password-000"})
    with session_scope() as db:
        assert db.get(User, ids[b["tenant_id"]]).failed_logins == 1
        assert db.get(User, ids[a["tenant_id"]]).failed_logins == 1


def test_portal_idempotency_keys_are_per_person(client):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    other = client.post("/v1/admin/users", json={"email": "sub2@acme.test", "roles": ["submitter"]}, headers=admin).json()
    from services.security import issue_token

    mine = auth(world["users"]["sub"]["token"])
    theirs = auth(issue_token(other["id"], world["tenant_id"], "user", ["submitter"]))
    body = {"workflow_id": world["workflow_id"], "text": "Private draft for the board.", "idempotency_key": "k-1"}
    first = client.post("/v1/portal/submissions", json=body, headers=mine).json()
    assert client.post("/v1/portal/submissions", json=body, headers=mine).json()["id"] == first["id"]
    r = client.post("/v1/portal/submissions", json={**body, "text": "Something else entirely."}, headers=theirs)
    assert r.status_code == 201 and r.json()["id"] != first["id"]
    # The same key from a source app is its own namespace too.
    from tests.conftest import submit_text

    assert submit_text(client, world, idempotency_key="k-1")["id"] not in (first["id"], r.json()["id"])


def test_portal_submissions_are_rate_limited_per_person(client, monkeypatch):
    from services.config import get_settings

    monkeypatch.setenv("POR_RATE_LIMIT_SUBMISSIONS_PER_MINUTE", "2")
    get_settings.cache_clear()
    world = make_tenant()
    h = auth(world["users"]["sub"]["token"])
    codes = [client.post("/v1/portal/submissions", json={"workflow_id": world["workflow_id"], "text": f"Note {i}."},
                         headers=h).status_code for i in range(3)]
    assert codes == [201, 201, 429]


def test_forwarded_for_uses_the_address_the_proxy_appended(env, monkeypatch):
    from fastapi import Request

    from services import ratelimit
    from services.config import get_settings

    monkeypatch.setenv("POR_TRUST_FORWARDED_FOR", "true")
    get_settings.cache_clear()
    req = Request({"type": "http", "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")], "client": ("10.0.0.1", 1)})
    assert ratelimit.client_ip(req) == "203.0.113.9"


def test_reset_link_cannot_be_used_twice_at_once(client, monkeypatch):
    from db.models import PasswordResetToken, utcnow
    from db.session import session_scope
    from services import mailer, passwords

    world = make_tenant()
    client.put(f"/v1/admin/users/{world['users']['r1']['id']}/password", json={"password": "first-password-123"},
               headers=auth(world["users"]["admin"]["token"]))
    mailer.outbox().clear()
    client.post("/v1/auth/forgot-password", json={"email": "r1@acme.test"})
    token = mailer.outbox()[-1].get_content().split("token=")[1].split()[0]
    real = passwords.check_policy

    def racing(password, email=None):
        real(password, email)
        with session_scope() as db:  # another request uses the link in the meantime
            for row in db.scalars(select(PasswordResetToken)).all():
                row.used_at = utcnow()

    monkeypatch.setattr(passwords, "check_policy", racing)
    r = client.post("/v1/auth/reset-password", json={"token": token, "new_password": "brand-new-password-9"})
    assert r.status_code == 400


def test_env_must_be_known():
    from pydantic import ValidationError

    from services.config import Settings

    assert Settings(_env_file=None, env="Production").is_prod
    assert Settings(_env_file=None, env="PROD").env == "prod"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="staging")


def test_production_start_up_checks(env, monkeypatch):
    from api.main import check_production_settings
    from services.config import get_settings

    good = {"POR_ENV": "prod", "POR_SECRET_KEY": "k" * 48, "POR_ENCRYPTION_KEY": "e" * 48,
            "POR_METRICS_TOKEN": "m" * 32, "POR_CLAMAV_HOST": "clamav"}

    def check(**overrides):
        for k, v in {**good, **overrides}.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        try:
            check_production_settings()
        finally:
            monkeypatch.setenv("POR_ENV", "test")
            get_settings.cache_clear()

    check()
    for bad in ({"POR_SECRET_KEY": None}, {"POR_SECRET_KEY": "short-but-set"}, {"POR_ENCRYPTION_KEY": None},
                {"POR_METRICS_TOKEN": None}):
        with pytest.raises(RuntimeError):
            check(**bad)
    check(POR_METRICS_TOKEN=None, POR_METRICS_ENABLED="false")
