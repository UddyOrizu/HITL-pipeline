"""Admin-side fixes: platform admin accounts, erasure, admin reviewers, auditor redaction, pagination headers and
tenant settings audit."""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import auth, make_tenant, review, submit_text


def _make_platform_admin(user_id: str) -> None:
    from db.models import User
    from db.session import session_scope

    with session_scope() as db:
        db.get(User, user_id).is_platform_admin = True


def test_tenant_admin_cannot_take_over_a_platform_admin(client):
    world = make_tenant()
    _make_platform_admin(world["users"]["lead"]["id"])
    admin = auth(world["users"]["admin"]["token"])
    pid = world["users"]["lead"]["id"]
    assert client.put(f"/v1/admin/users/{pid}/password", json={"password": "a-takeover-password-1"}, headers=admin).status_code == 403
    assert client.put(f"/v1/admin/users/{pid}", json={"roles": ["auditor"]}, headers=admin).status_code == 403
    assert client.put(f"/v1/admin/users/{pid}", json={"active": False}, headers=admin).status_code == 403
    assert client.post(f"/v1/admin/users/{pid}/sign-out", headers=admin).status_code == 403
    assert client.post(f"/v1/admin/users/{pid}/erase", headers=admin).status_code == 403
    # Harmless edits still work, and another platform admin can make the changes.
    assert client.put(f"/v1/admin/users/{pid}", json={"name": "Lead"}, headers=admin).status_code == 200
    _make_platform_admin(world["users"]["admin"]["id"])
    assert client.put(f"/v1/admin/users/{pid}/password", json={"password": "a-legit-password-12"}, headers=admin).status_code == 200


def test_erasure_scrubs_submission_metadata_and_reset_links(client):
    from db.models import PasswordResetToken, Submission
    from db.session import session_scope

    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    sub_user = world["users"]["sub"]
    portal = client.post("/v1/portal/submissions", json={"workflow_id": world["workflow_id"], "text": "Some text here."},
                         headers=auth(sub_user["token"])).json()
    api = submit_text(client, world, metadata={"author_email": "SUB@acme.test", "author_name": "sub", "team": "x"})
    client.put(f"/v1/admin/users/{sub_user['id']}/password", json={"password": "a-reset-password-12"}, headers=admin)
    assert client.post("/v1/auth/forgot-password", json={"email": "sub@acme.test"}).status_code == 202
    assert client.put(f"/v1/admin/users/{sub_user['id']}", json={"active": False}, headers=admin).status_code == 200
    r = client.post(f"/v1/admin/users/{sub_user['id']}/erase", headers=admin)
    assert r.status_code == 200 and r.json()["erased"]
    with session_scope() as db:
        p = db.get(Submission, portal["id"]).meta
        a = db.get(Submission, api["id"]).meta
        assert p["submitted_by"] == sub_user["id"] and p["author_email"].endswith("@erased.invalid")
        assert a["author_email"].endswith("@erased.invalid") and a["author_name"] == "Erased user" and a["team"] == "x"
        assert not db.scalars(select(PasswordResetToken).where(PasswordResetToken.user_id == sub_user["id"])).all()


def test_turning_off_admin_reviewers_lists_accounts_that_hold_both(client):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    assert client.put("/v1/admin/tenant", json={"allow_admin_reviewers": True}, headers=admin).status_code == 200
    r2 = world["users"]["r2"]["id"]
    assert client.put(f"/v1/admin/users/{r2}", json={"roles": ["admin", "reviewer"]}, headers=admin).status_code == 200
    r = client.put("/v1/admin/tenant", json={"allow_admin_reviewers": False}, headers=admin)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] and [u["id"] for u in detail["users"]] == [r2]
    assert client.put(f"/v1/admin/users/{r2}", json={"roles": ["reviewer"]}, headers=admin).status_code == 200
    assert client.put("/v1/admin/tenant", json={"allow_admin_reviewers": False}, headers=admin).status_code == 200


def test_tenant_settings_audit_has_before_and_after(client):
    from db.models import AuditEvent
    from db.session import session_scope

    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    assert client.put("/v1/admin/tenant", json={"default_hourly_rate": 50}, headers=admin).status_code == 200
    assert client.put("/v1/admin/tenant", json={"default_hourly_rate": 70}, headers=admin).status_code == 200
    with session_scope() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == world["tenant_id"],
                                                 AuditEvent.action == "tenant.settings_updated")
                        .order_by(AuditEvent.seq.desc())).first()
        assert ev.payload["before"] == {"default_hourly_rate": 50} and ev.payload["settings"] == {"default_hourly_rate": 70}


def test_auditors_see_reviewers_pseudonymised_without_emails(client, sink):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    created = client.post("/v1/admin/users", json={"email": "newrev@acme.test", "roles": ["reviewer"]}, headers=admin).json()
    sid = submit_text(client, world)["id"]
    who, _ = review(client, world, sid, "approve", read=False)
    rid = world["users"][who]["id"]

    aud = client.get("/v1/audit/events", params={"limit": 1000}, headers=auth(world["users"]["auditor"]["token"]))
    assert aud.status_code == 200
    text = aud.text
    assert rid not in text and created["id"] not in text and "newrev@acme.test" not in text
    decided = [e for e in aud.json() if e["action"].startswith("review.") or e["action"].startswith("decision")]
    pseudo = next(e["actor"] for e in aud.json() if e["actor"].startswith("reviewer:"))
    assert decided or pseudo
    # Stable pseudonyms, usable as a filter.
    again = client.get("/v1/audit/events", params={"actor": pseudo}, headers=auth(world["users"]["auditor"]["token"])).json()
    assert again and all(e["actor"] == pseudo for e in again)
    # Oversight leads see the stored log.
    lead = client.get("/v1/audit/events", params={"limit": 1000}, headers=auth(world["users"]["lead"]["token"])).text
    assert rid in lead and "newrev@acme.test" in lead


def test_list_pagination_headers(client):
    world = make_tenant()
    lead = auth(world["users"]["lead"]["token"])
    r = client.get("/v1/admin/users", headers=lead)
    total = len(r.json())
    assert r.headers["X-Total-Count"] == str(total) and "X-Next-Offset" not in r.headers
    r = client.get("/v1/admin/users", params={"limit": 2}, headers=lead)
    assert len(r.json()) == 2 and r.headers["X-Next-Offset"] == "2" and r.headers["X-Total-Count"] == str(total)
    r = client.get("/v1/admin/teams", headers=lead)
    assert r.headers["X-Total-Count"] == "1"
    admin = auth(world["users"]["admin"]["token"])
    for rate in (10, 20, 30):
        client.put("/v1/admin/tenant", json={"default_hourly_rate": rate}, headers=admin)
    ev = client.get("/v1/audit/events", params={"limit": 1}, headers=lead)
    assert ev.headers["X-Total-Count"] == "3" and ev.headers["X-Next-Offset"] == "1"
    ev = client.get("/v1/audit/events", params={"limit": 1, "offset": 2}, headers=lead)
    assert len(ev.json()) == 1 and "X-Next-Offset" not in ev.headers
