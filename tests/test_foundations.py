"""Milestone 1: audit chain, append-only log, auth, and tenant isolation on every endpoint."""

import pytest
from sqlalchemy import select, text

from tests.conftest import auth, make_tenant, review, submit_text, task_for


def test_audit_chain_is_valid_and_detects_tampering(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    review(client, world, sub["id"])

    from db.models import AuditEvent
    from db.session import session_scope
    from services import audit

    with session_scope() as db:
        result = audit.verify_chain(db, world["tenant_id"])
        assert result["valid"] and result["count"] > 10
        victim = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == world["tenant_id"]).order_by(AuditEvent.seq).offset(3)).first()
        seq = victim.seq
    # Bypass the ORM guard to simulate a direct database edit.
    with session_scope() as db:
        db.execute(text("UPDATE audit_events SET action = 'tampered' WHERE tenant_id = :t AND seq = :s"),
                   {"t": world["tenant_id"], "s": seq})
    with session_scope() as db:
        result = audit.verify_chain(db, world["tenant_id"])
        assert result == {"valid": False, "count": result["count"], "broken_at": seq}


def test_audit_rows_cannot_be_updated_or_deleted_through_the_orm(client):
    world = make_tenant()
    from db.models import AuditEvent, AuditImmutableError
    from db.session import new_session

    db = new_session()
    ev = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == world["tenant_id"])).first()
    if ev is None:
        from services import audit

        ev = audit.record(db, world["tenant_id"], "test", "x", "tenant", world["tenant_id"])
        db.commit()
    ev.action = "changed"
    with pytest.raises(AuditImmutableError):
        db.flush()
    db.rollback()
    db.delete(db.get(AuditEvent, ev.id))
    with pytest.raises(AuditImmutableError):
        db.flush()
    db.rollback()
    db.close()


def test_api_key_and_oauth_client_credentials(client):
    world = make_tenant()
    assert client.get("/v1/workflows").status_code == 401
    assert client.get("/v1/workflows", headers={"X-API-Key": "por_wrongwrongwrong"}).status_code == 401
    assert client.get("/v1/workflows", headers={"Authorization": f"Bearer {world['api_key']}"}).status_code == 200

    owner = world["users"]["owner"]["token"]
    created = client.post("/v1/integrations/apps", json={"name": "Enterprise", "enable_oauth": True}, headers=auth(owner)).json()
    r = client.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": created["oauth_client_id"],
                                             "client_secret": created["oauth_client_secret"]})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert client.get("/v1/workflows", headers=auth(token)).status_code == 200
    bad = client.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": created["oauth_client_id"],
                                               "client_secret": "nope"})
    assert bad.status_code == 401
    # A user token is not a source-app credential, and vice versa.
    assert client.get("/v1/workflows", headers=auth(owner)).status_code == 401
    assert client.get("/v1/me", headers=auth(token)).status_code == 401


def test_dev_login_and_roles(client):
    make_tenant()
    r = client.post("/v1/auth/dev-login", json={"email": "r1@acme.test"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert client.get("/v1/me", headers=auth(token)).json()["roles"] == ["reviewer"]
    assert client.get("/v1/oversight/dashboard", headers=auth(token)).status_code == 403
    assert client.get("/v1/integrations/apps", headers=auth(token)).status_code == 403


# --- Tenant isolation -------------------------------------------------------------------


@pytest.fixture()
def two_tenants(client, sink):
    a = make_tenant("Alpha", canary_rate=1.0)
    b = make_tenant("Bravo")
    done = submit_text(client, a, external_ref="A-DONE")
    review(client, a, done["id"], "approve")
    open_sub = submit_text(client, a, external_ref="A-OPEN")
    who, item = task_for(client, a, open_sub["id"])
    token = a["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    span = view["document"]["sections"][0]["spans"][0]["id"]
    comment = client.post(f"/v1/review/tasks/{item['task_id']}/comments", json={"span_id": span, "body": "hm"}, headers=auth(token)).json()

    from db.models import WebhookDelivery
    from db.session import session_scope

    with session_scope() as db:
        delivery = db.scalars(select(WebhookDelivery).where(WebhookDelivery.tenant_id == a["tenant_id"])).first().id
    connector = client.post("/v1/integrations/connectors", headers=auth(a["users"]["owner"]["token"]), json={
        "source_app_id": a["app_id"], "kind": "teams", "config": {"webhook_url": "https://teams.example.test/hook"}}).json()
    return {
        "connector": connector["id"],
        "a": a, "b": b, "done": done["id"], "open": open_sub["id"], "task": item["task_id"],
        "flag": view["flags"][0]["id"] if view["flags"] else "none", "comment": comment["id"], "delivery": delivery,
        "user": a["users"]["r1"]["id"],
        "portal": client.post("/v1/portal/submissions", json={"workflow_id": a["workflow_id"], "text": "Portal item.\n\nSecond part."},
                              headers=auth(a["users"]["sub"]["token"])).json()["id"],
    }


# (method, path, credential kind for tenant B, json body) - every route must appear here.
def _cases(t):
    return [
        ("GET", f"/v1/submissions/{t['done']}", "app", None),
        ("GET", f"/v1/submissions/{t['done']}/feedback", "app", None),
        ("GET", f"/v1/submissions/{t['done']}/evidence", "app", None),
        ("POST", f"/v1/submissions/{t['open']}/cancel", "app", None),
        ("POST", "/v1/submissions", "app", {"workflow_id": t["a"]["workflow_id"], "text": "hello world text"}),
        ("GET", f"/v1/review/tasks/{t['task']}", "r1", None),
        ("POST", f"/v1/review/tasks/{t['task']}/flags/{t['flag']}/open", "r1", None),
        ("POST", f"/v1/review/tasks/{t['task']}/flags/{t['flag']}/acknowledge", "r1", None),
        ("POST", f"/v1/review/tasks/{t['task']}/comments", "r1", {"body": "x"}),
        ("DELETE", f"/v1/review/tasks/{t['task']}/comments/{t['comment']}", "r1", None),
        ("POST", f"/v1/review/tasks/{t['task']}/decision", "r1", {"outcome": "approve"}),
        ("POST", "/v1/telemetry", "r1", {"task_id": t["task"], "events": [{"type": "heartbeat"}]}),
        ("POST", f"/v1/oversight/submissions/{t['open']}/resolve", "lead", {"outcome": "approve"}),
        ("PUT", f"/v1/admin/workflows/{t['a']['workflow_id']}", "lead", {"name": "hijack"}),
        ("PUT", f"/v1/admin/users/{t['user']}", "admin", {"roles": ["reviewer"], "active": False}),
        ("GET", f"/v1/admin/users/{t['user']}", "admin", None),
        ("PUT", f"/v1/admin/users/{t['user']}/password", "admin", {"password": "hijack-password-123"}),
        ("PUT", f"/v1/admin/teams/{t['a']['team_id']}", "admin", {"name": "hijack"}),
        ("POST", "/v1/admin/workflows", "lead", {"name": "x", "stages": [{"name": "s", "team_id": t["a"]["team_id"]}]}),
        ("GET", f"/v1/audit/submissions/{t['done']}/evidence", "auditor", None),
        ("GET", f"/v1/audit/submissions/{t['done']}/evidence.pdf", "auditor", None),
        ("PATCH", f"/v1/integrations/apps/{t['a']['app_id']}", "owner", {"name": "hijack"}),
        ("POST", f"/v1/integrations/apps/{t['a']['app_id']}/rotate-key", "owner", None),
        ("POST", f"/v1/integrations/apps/{t['a']['app_id']}/rotate-webhook-secret", "owner", None),
        ("POST", f"/v1/integrations/deliveries/{t['delivery']}/redeliver", "owner", None),
        ("POST", "/v1/integrations/connectors", "owner", {"source_app_id": t["a"]["app_id"], "kind": "teams",
                                                          "config": {"webhook_url": "https://x.example.test"}}),
        ("PATCH", f"/v1/integrations/connectors/{t['connector']}", "owner", {"active": False}),
        ("DELETE", f"/v1/integrations/connectors/{t['connector']}", "owner", None),
        ("POST", f"/v1/integrations/connectors/{t['connector']}/run", "owner", None),
        ("POST", f"/v1/integrations/apps/{t['a']['app_id']}/revoke-previous-key", "owner", None),
        ("PUT", f"/v1/integrations/apps/{t['a']['app_id']}/oauth", "owner", {"enabled": True}),
        ("POST", f"/v1/integrations/apps/{t['a']['app_id']}/rotate-oauth-secret", "owner", None),
        ("GET", f"/v1/review/tasks/{t['task']}/references", "r1", None),
        ("GET", f"/v1/review/tasks/{t['task']}/references/{t['done']}/file", "r1", None),
        ("GET", f"/v1/oversight/submissions/{t['open']}", "lead", None),
        ("POST", f"/v1/oversight/submissions/{t['open']}/action", "lead", {"action": "cancel"}),
        ("POST", f"/v1/admin/users/{t['user']}/sign-out", "admin", None),
        ("POST", f"/v1/admin/users/{t['user']}/erase", "admin", None),
        ("DELETE", f"/v1/admin/submissions/{t['done']}/content", "admin", None),
        ("GET", f"/v1/portal/submissions/{t['portal']}", "sub", None),
        ("GET", f"/v1/portal/submissions/{t['portal']}/evidence.pdf", "sub", None),
        ("POST", f"/v1/portal/submissions/{t['portal']}/cancel", "sub", None),
        ("POST", "/v1/portal/submissions", "sub", {"workflow_id": t["a"]["workflow_id"], "text": "hello world text"}),
    ]


# List endpoints: tenant B must see none of tenant A's data.
LIST_CASES = [
    ("GET", "/v1/workflows", "app"),
    ("GET", "/v1/assurance/summary", "app"),
    ("GET", "/v1/review/queue", "r1"),
    ("GET", "/v1/review/history", "r1"),
    ("GET", "/v1/oversight/dashboard", "lead"),
    ("GET", "/v1/oversight/submissions", "lead"),
    ("GET", "/v1/admin/workflows", "lead"),
    ("GET", "/v1/admin/users", "lead"),
    ("GET", "/v1/admin/teams", "lead"),
    ("GET", "/v1/audit/submissions", "auditor"),
    ("GET", "/v1/audit/events", "auditor"),
    ("GET", "/v1/audit/verify", "auditor"),
    ("GET", "/v1/integrations/apps", "owner"),
    ("GET", "/v1/integrations/deliveries", "owner"),
    ("GET", "/v1/integrations/connectors", "owner"),
    ("GET", "/v1/me", "r1"),
    ("GET", "/v1/portal/workflows", "sub"),
    ("GET", "/v1/portal/submissions", "sub"),
    ("GET", "/v1/submissions", "app"),
    ("GET", "/v1/admin/source-apps", "lead"),
    ("GET", "/v1/admin/tenant", "admin"),
    ("GET", "/v1/audit/actions", "auditor"),
    ("GET", "/v1/reports/overview", "lead"),
    ("GET", "/v1/reports/reviewers", "lead"),
    ("GET", "/v1/reports/sla", "lead"),
    ("GET", "/v1/reports/costs", "lead"),
    ("GET", "/v1/integrations/connector-kinds", "owner"),
    ("GET", "/v1/integrations/workflows", "owner"),
    ("GET", "/v1/reports/filters", "auditor"),
]

# Routes that are tenant-agnostic by design, or create only inside the caller's tenant.
EXEMPT = {
    ("GET", "/healthz"), ("POST", "/v1/auth/dev-login"), ("POST", "/v1/auth/oidc"), ("POST", "/v1/oauth/token"),
    ("GET", "/v1/files/{key}"), ("POST", "/v1/admin/users"), ("POST", "/v1/admin/teams"), ("POST", "/v1/integrations/apps"),
    ("POST", "/v1/auth/login"), ("GET", "/v1/auth/methods"),
    ("POST", "/v1/me/password"), ("PATCH", "/v1/me"), ("POST", "/v1/me/tokens"),  # act only on the signed-in user
    ("POST", "/v1/auth/refresh"), ("POST", "/v1/auth/logout"), ("POST", "/v1/auth/logout-all"),
    ("POST", "/v1/auth/sso/start"), ("POST", "/v1/auth/sso/callback"), ("POST", "/v1/auth/sso/choose"),
    ("POST", "/v1/auth/forgot-password"), ("POST", "/v1/auth/reset-password"),
    ("PUT", "/v1/admin/tenant"),  # acts only on the caller's own tenant
    ("GET", "/readyz"), ("GET", "/metrics"),
    # Platform administration is cross-tenant by design; test_platform.py checks only platform admins get in.
    ("GET", "/v1/platform/tenants"), ("POST", "/v1/platform/tenants"), ("PATCH", "/v1/platform/tenants/{tenant_id}"),
}


def _creds(world, kind):
    return {"X-API-Key": world["api_key"]} if kind == "app" else auth(world["users"][kind]["token"])


def test_every_endpoint_is_tenant_isolated(client, two_tenants):
    t = two_tenants
    b = t["b"]
    for method, path, kind, body in _cases(t):
        r = client.request(method, path, json=body, headers=_creds(b, kind))
        assert r.status_code in (403, 404, 422), f"{method} {path} returned {r.status_code}: {r.text}"
    a_markers = [t["done"], t["open"], t["task"], t["portal"], t["a"]["tenant_id"], t["a"]["app_id"], t["a"]["workflow_id"]]
    for method, path, kind in LIST_CASES:
        r = client.request(method, path, headers=_creds(b, kind))
        assert r.status_code == 200, f"{method} {path}: {r.text}"
        for marker in a_markers:
            assert marker not in r.text, f"{method} {path} leaked tenant A data"
    # And tenant A's own resources are still intact.
    assert client.get(f"/v1/submissions/{t['open']}", headers={"X-API-Key": t["a"]["api_key"]}).json()["status"] == "in_review"


def test_cross_tenant_cases_cover_every_route(client, two_tenants):
    import re

    from api.main import app

    # Read routes from the OpenAPI schema: newer FastAPI wraps included routers, so app.routes
    # only lists top-level routes and a walk over it would silently check nothing.
    paths = app.openapi()["paths"]
    assert len(paths) > 30

    def template(path):
        return re.sub(r"/[0-9a-f-]{36}", "/{}", path)

    case_templates = {(m, template(p)) for m, p, _, _ in _cases(two_tenants)} | {(m, p) for m, p, _ in LIST_CASES}
    for path, ops in paths.items():
        for method in (m.upper() for m in ops):
            if (method, path) in EXEMPT:
                continue
            key = (method, re.sub(r"\{[^}]+\}", "{}", path))
            assert key in case_templates, f"no cross-tenant test for {method} {path}"
