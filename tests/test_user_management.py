"""User management: admin vs reviewer separation, guards, reassignment on deactivation, teams."""

from sqlalchemy import select

from tests.conftest import auth, make_tenant, submit_text, task_for


def admin_h(world):
    return auth(world["users"]["admin"]["token"])


def users_by_email(client, world, **params):
    r = client.get("/v1/admin/users", params=params, headers=admin_h(world))
    assert r.status_code == 200, r.text
    return {u["email"]: u for u in r.json()}


def test_admin_cannot_review_and_reviewer_cannot_administer(client):
    world = make_tenant()
    admin, reviewer = admin_h(world), auth(world["users"]["r1"]["token"])
    # Admins run the platform: oversight, workflows, evidence, integrations, users.
    for path in ("/v1/admin/users", "/v1/oversight/dashboard", "/v1/admin/workflows", "/v1/audit/verify", "/v1/integrations/apps"):
        assert client.get(path, headers=admin).status_code == 200, path
    # ...but reviewing needs the reviewer role explicitly.
    assert client.get("/v1/review/queue", headers=admin).status_code == 403
    # Reviewers cannot see or change users.
    assert client.get("/v1/admin/users", headers=reviewer).status_code == 403
    assert client.post("/v1/admin/users", json={"email": "x@acme.test"}, headers=reviewer).status_code == 403
    me = client.get("/v1/me", headers=admin).json()
    assert "reviewer" not in me["permissions"] and "oversight_lead" in me["permissions"]


def test_admins_are_never_assigned_review_work(client):
    world = make_tenant(reviewers=1)
    sub = submit_text(client, world)
    who, _ = task_for(client, world, sub["id"])
    assert who == "r1"


def test_create_list_and_filter_users(client):
    world = make_tenant()
    h = admin_h(world)
    r = client.post("/v1/admin/users", json={"email": " New.Reviewer@Acme.test ", "name": "Nia", "roles": ["reviewer"],
                                             "team_ids": [world["team_id"]]}, headers=h)
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["email"] == "new.reviewer@acme.test" and u["kind"] == "reviewer" and u["stats"]["open_tasks"] == 0
    assert client.post("/v1/admin/users", json={"email": "new.reviewer@acme.test"}, headers=h).status_code == 409
    assert client.post("/v1/admin/users", json={"email": "not-an-email"}, headers=h).status_code == 422
    assert client.post("/v1/admin/users", json={"email": "a@acme.test", "roles": ["wizard"]}, headers=h).status_code == 422
    assert client.post("/v1/admin/users", json={"email": "b@acme.test", "roles": []}, headers=h).status_code == 422
    assert client.post("/v1/admin/users", json={"email": "c@acme.test", "team_ids": ["nope"]}, headers=h).status_code == 422

    reviewers = users_by_email(client, world, role="reviewer")
    assert "new.reviewer@acme.test" in reviewers and "admin@acme.test" not in reviewers
    admins = users_by_email(client, world, role="admin")
    assert list(admins) == ["admin@acme.test"] and admins["admin@acme.test"]["stats"] is None
    assert list(users_by_email(client, world, q="nia")) == ["new.reviewer@acme.test"]
    # Oversight leads can read the list but not change it.
    lead = auth(world["users"]["lead"]["token"])
    assert client.get("/v1/admin/users", headers=lead).status_code == 200
    assert client.put(f"/v1/admin/users/{u['id']}", json={"name": "x"}, headers=lead).status_code == 403


def test_admin_and_reviewer_roles_are_separate_by_default(client):
    world = make_tenant()
    h = admin_h(world)
    r = client.post("/v1/admin/users", json={"email": "both@acme.test", "roles": ["admin", "reviewer"]}, headers=h)
    assert r.status_code == 422 and "separate" in r.json()["detail"]
    r = client.put(f"/v1/admin/users/{world['users']['r1']['id']}", json={"roles": ["reviewer", "admin"]}, headers=h)
    assert r.status_code == 422
    # A tenant can opt in (e.g. a very small firm), and the combination is then allowed.
    from db.models import Tenant
    from db.session import session_scope

    with session_scope() as db:
        t = db.get(Tenant, world["tenant_id"])
        t.settings = {**(t.settings or {}), "allow_admin_reviewers": True}
    r = client.post("/v1/admin/users", json={"email": "both@acme.test", "roles": ["admin", "reviewer"]}, headers=h)
    assert r.status_code == 201 and r.json()["kind"] == "admin"


def test_email_cannot_change_and_updates_are_partial(client):
    world = make_tenant()
    h = admin_h(world)
    uid = world["users"]["r1"]["id"]
    assert client.put(f"/v1/admin/users/{uid}", json={"email": "other@acme.test"}, headers=h).status_code == 422
    r = client.put(f"/v1/admin/users/{uid}", json={"name": "Renamed"}, headers=h).json()
    assert r["name"] == "Renamed" and r["roles"] == ["reviewer"] and r["active"] is True


def test_admin_cannot_lock_themselves_or_the_tenant_out(client):
    world = make_tenant()
    h = admin_h(world)
    me = world["users"]["admin"]["id"]
    assert client.put(f"/v1/admin/users/{me}", json={"active": False}, headers=h).status_code == 409
    assert client.put(f"/v1/admin/users/{me}", json={"roles": ["auditor"]}, headers=h).status_code == 409
    second = client.post("/v1/admin/users", json={"email": "admin2@acme.test", "roles": ["admin"]}, headers=h).json()
    # Self-demotion is still refused even when another admin exists; the other admin can do it.
    assert client.put(f"/v1/admin/users/{me}", json={"roles": ["auditor"]}, headers=h).status_code == 409
    from services.security import issue_token

    h2 = auth(issue_token(second["id"], world["tenant_id"], "user", ["admin"]))
    assert client.put(f"/v1/admin/users/{me}", json={"roles": ["auditor"]}, headers=h2).status_code == 200
    # Now admin2 is the last admin and cannot be removed by anyone.
    assert client.put(f"/v1/admin/users/{second['id']}", json={"active": False}, headers=h2).status_code == 409


def test_deactivated_users_lose_access_immediately(client):
    world = make_tenant()
    token = world["users"]["r2"]["token"]
    assert client.get("/v1/review/queue", headers=auth(token)).status_code == 200
    r = client.put(f"/v1/admin/users/{world['users']['r2']['id']}", json={"active": False}, headers=admin_h(world))
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.get("/v1/review/queue", headers=auth(token)).status_code == 401
    assert client.post("/v1/auth/dev-login", json={"email": "r2@acme.test"}).status_code == 401
    # Role changes apply on the next request too (roles come from the database, not the token).
    client.put(f"/v1/admin/users/{world['users']['r1']['id']}", json={"roles": ["auditor"]}, headers=admin_h(world))
    assert client.get("/v1/review/queue", headers=auth(world["users"]["r1"]["token"])).status_code == 403


def test_deactivating_a_reviewer_reassigns_their_open_work(client):
    world = make_tenant(canary_rate=1.0, reviewers=2)
    sub = submit_text(client, world)
    who, item = task_for(client, world, sub["id"])
    other = "r2" if who == "r1" else "r1"
    r = client.put(f"/v1/admin/users/{world['users'][who]['id']}", json={"active": False}, headers=admin_h(world))
    body = r.json()
    assert body["reassigned"] == [{"task_id": item["task_id"], "submission_id": sub["id"],
                                   "new_reviewer_id": world["users"][other]["id"], "status": "reassigned"}]
    gone = world["users"].pop(who)  # a deactivated user's token no longer works
    new_who, new_item = task_for(client, world, sub["id"])
    assert new_who == other and new_item["task_id"] != item["task_id"]

    from db.models import AuditEvent, Canary
    from db.session import session_scope

    with session_scope() as db:
        c = db.scalars(select(Canary).where(Canary.submission_id == sub["id"])).one()
        assert c.scored_task_id == new_item["task_id"] and c.scored_reviewer_id == world["users"][other]["id"]
        assert c.scored_reviewer_id != gone["id"]
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == world["tenant_id"])).all()]
        assert "user.deactivated" in actions and "task.reassigned" in actions


def test_removing_the_last_reviewer_holds_work_until_someone_is_added(client):
    world = make_tenant(reviewers=1)
    h = admin_h(world)
    sub = submit_text(client, world)
    r = client.put(f"/v1/admin/users/{world['users']['r1']['id']}", json={"roles": ["auditor"]}, headers=h).json()
    assert r["reassigned"][0]["status"] == "awaiting_capacity"
    status = client.get(f"/v1/submissions/{sub['id']}", headers={"X-API-Key": world["api_key"]}).json()
    assert status["status"] == "in_review"
    # Adding a reviewer back picks up the queued item.
    new = client.post("/v1/admin/users", json={"email": "fresh@acme.test", "roles": ["auditor"], "team_ids": [world["team_id"]]},
                      headers=h).json()
    client.put(f"/v1/admin/users/{new['id']}", json={"roles": ["reviewer"]}, headers=h)
    detail = client.get(f"/v1/admin/users/{new['id']}", headers=h).json()
    assert [t["submission_id"] for t in detail["open_tasks"]] == [sub["id"]]
    assert [e["action"] for e in detail["history"]][:2] == ["user.updated", "user.created"]


def test_teams_can_be_created_renamed_and_counted(client):
    world = make_tenant()
    h = admin_h(world)
    t = client.post("/v1/admin/teams", json={"name": "Mortgages", "daily_ceiling_minutes": 600}, headers=h)
    assert t.status_code == 201
    assert client.post("/v1/admin/teams", json={"name": "Mortgages"}, headers=h).status_code == 409
    r = client.put(f"/v1/admin/teams/{t.json()['id']}", json={"name": "Mortgage advice", "daily_ceiling_minutes": None}, headers=h)
    assert r.json()["name"] == "Mortgage advice" and r.json()["daily_ceiling_minutes"] is None
    teams = {x["name"]: x for x in client.get("/v1/admin/teams", headers=h).json()}
    assert teams["Team A"]["reviewers"] == 3 and teams["Team A"]["members"] == 8
    assert teams["Mortgage advice"]["members"] == 0
    assert client.post("/v1/admin/teams", json={"name": "x"}, headers=auth(world["users"]["lead"]["token"])).status_code == 403


def test_sign_in_records_last_login(client):
    world = make_tenant()
    client.post("/v1/auth/dev-login", json={"email": "r3@acme.test"})
    users = users_by_email(client, world)
    assert users["r3@acme.test"]["last_login_at"] is not None
    assert users["r1@acme.test"]["last_login_at"] is None
