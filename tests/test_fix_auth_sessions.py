"""Sessions and channels: sid/chan claims, refresh revocation, whole-session sign-out, client tokens
(POST /v1/me/tokens), channel binding, and OAuth client token binding."""

from __future__ import annotations

import time

import jwt

from tests.conftest import auth, make_tenant

TEXT = ("Revenue rose to £4.2m in 2025.\n\nWe recommend the client relies on this to meet its FCA obligations.\n\n"
        "The board will review the plan next quarter.")


def claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def login(client, email="r1@acme.test") -> str:
    r = client.post("/v1/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_sign_in_tokens_carry_ui_channel_session_and_capped_expiry(client, monkeypatch):
    make_tenant()
    c = claims(login(client))
    assert c["chan"] == "ui" and c["sid"] and c["exp"] <= c["auth_time"] + 24 * 3600
    monkeypatch.setenv("POR_SESSION_MAX_HOURS", "1")
    from services.config import get_settings

    get_settings.cache_clear()
    c = claims(login(client))
    assert c["exp"] == c["auth_time"] + 3600  # 12h token TTL, capped at the 1h session maximum


def test_refresh_revokes_the_old_token_and_keeps_the_session(client):
    make_tenant()
    old = login(client)
    r = client.post("/v1/auth/refresh", headers=auth(old))
    assert r.status_code == 200
    new = r.json()["access_token"]
    assert claims(new)["sid"] == claims(old)["sid"] and claims(new)["chan"] == "ui"
    assert client.get("/v1/me", headers=auth(old)).status_code == 401
    assert client.get("/v1/me", headers=auth(new)).status_code == 200


def test_logout_ends_the_whole_session_including_refreshed_and_client_tokens(client):
    make_tenant()
    first = login(client)
    other_session = login(client)
    refreshed = client.post("/v1/auth/refresh", headers=auth(first)).json()["access_token"]
    mcp = client.post("/v1/me/tokens", json={"channel": "mcp"}, headers=auth(refreshed)).json()["access_token"]
    assert client.get("/v1/me", headers=auth(mcp)).status_code == 200
    assert client.post("/v1/auth/logout", headers=auth(refreshed)).status_code == 204
    assert client.get("/v1/me", headers=auth(refreshed)).status_code == 401
    assert client.get("/v1/me", headers=auth(mcp)).status_code == 401
    assert client.get("/v1/me", headers=auth(other_session)).status_code == 200


def test_sign_out_everywhere_ends_client_tokens(client):
    make_tenant()
    ui = login(client)
    mcp = client.post("/v1/me/tokens", json={"channel": "mcp"}, headers=auth(ui)).json()["access_token"]
    time.sleep(1.1)
    assert client.post("/v1/auth/logout-all", headers=auth(ui)).status_code == 204
    assert client.get("/v1/me", headers=auth(mcp)).status_code == 401


def test_client_tokens_contract_and_audit(client):
    world = make_tenant()
    ui = login(client)
    r = client.post("/v1/me/tokens", json={"channel": "mcp", "ttl_minutes": 99999}, headers=auth(ui))
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "channel"}
    assert body["token_type"] == "bearer" and body["channel"] == "mcp" and body["expires_in"] <= 12 * 3600
    c = claims(body["access_token"])
    assert c["chan"] == "mcp" and c["sid"] == claims(ui)["sid"] and c["auth_time"] == claims(ui)["auth_time"]
    # Only a ui token can mint client tokens; refreshing an mcp token keeps it mcp.
    assert client.post("/v1/me/tokens", json={"channel": "api"}, headers=auth(body["access_token"])).status_code == 403
    assert client.post("/v1/me/tokens", json={"channel": "ui"}, headers=auth(ui)).status_code == 422
    refreshed = client.post("/v1/auth/refresh", headers=auth(body["access_token"])).json()["access_token"]
    assert claims(refreshed)["chan"] == "mcp"

    from sqlalchemy import select

    from db.models import AuditEvent
    from db.session import session_scope

    with session_scope() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == world["tenant_id"],
                                                 AuditEvent.action == "user.token_issued")).first()
        assert ev.payload["channel"] == "mcp" and body["access_token"] not in str(ev.payload)


def test_channel_comes_from_the_token_not_the_header(client):
    from fastapi import Request

    from api.deps import request_channel

    def chan(token_claims, header=None):
        scope = {"type": "http", "headers": [(b"x-por-channel", header.encode())] if header else []}
        req = Request(scope)
        if token_claims is not None:
            req.state.token_claims = token_claims
        return request_channel(req)

    user = {"typ": "user"}
    assert chan({**user, "chan": "mcp"}, "ui") == "mcp"
    assert chan({**user, "chan": "api"}, "ui") == "api"
    assert chan({**user, "chan": "ui"}, "ui") == "ui"
    assert chan({**user, "chan": "ui"}, "mcp") == "mcp"
    assert chan({**user, "chan": "ui"}) == "api"
    assert chan(user, "ui") == "api"  # issued before channels: never the workspace
    assert chan(user, "mcp") == "mcp"
    assert chan(None, "mcp") == "mcp" and chan(None) == "api"  # source apps


def test_mcp_token_cannot_decide_where_only_the_workspace_may(client):
    from tests.conftest import submit_text, task_for

    world = make_tenant()
    lead = auth(world["users"]["lead"]["token"])
    wf = next(w for w in client.get("/v1/admin/workflows", headers=lead).json() if w["id"] == world["workflow_id"])
    body = {k: wf[k] for k in ("name", "stages", "canary_rate", "canary_domain", "cap_policy", "spot_check_rate",
                               "risk_policy", "assurance_policy", "source_app_ids")}
    body["telemetry_policy"] = {**wf["telemetry_policy"], "review_channels": ["ui"]}
    assert client.put(f"/v1/admin/workflows/{world['workflow_id']}", json=body, headers=lead).status_code == 200
    sid = submit_text(client, world, text=TEXT)["id"]
    who, item = task_for(client, world, sid)
    ui = login(client, f"{who}@acme.test")
    mcp = client.post("/v1/me/tokens", json={"channel": "mcp"}, headers=auth(ui)).json()["access_token"]
    r = client.post(f"/v1/review/tasks/{item['task_id']}/decision", json={"outcome": "reject", "reason": "no"},
                    headers={**auth(mcp), "X-PoR-Channel": "ui"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "channel_not_allowed"


def _oauth_app(client, world):
    owner = auth(world["users"]["owner"]["token"])
    app = client.post("/v1/integrations/apps", json={"name": "Enterprise", "enable_oauth": True}, headers=owner).json()
    return owner, app


def _oauth_token(client, app, secret):
    r = client.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": app["oauth_client_id"],
                                             "client_secret": secret})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_oauth_tokens_end_when_the_secret_rotates_or_oauth_is_turned_off(client):
    world = make_tenant()
    owner, app = _oauth_app(client, world)
    token = _oauth_token(client, app, app["oauth_client_secret"])
    assert client.get("/v1/workflows", headers=auth(token)).status_code == 200
    rotated = client.post(f"/v1/integrations/apps/{app['id']}/rotate-oauth-secret", headers=owner).json()
    assert client.get("/v1/workflows", headers=auth(token)).status_code == 401
    token = _oauth_token(client, app, rotated["oauth_client_secret"])
    assert client.get("/v1/workflows", headers=auth(token)).status_code == 200
    assert client.put(f"/v1/integrations/apps/{app['id']}/oauth", json={"enabled": False}, headers=owner).status_code == 200
    assert client.get("/v1/workflows", headers=auth(token)).status_code == 401


def test_connector_app_tokens_still_work(client):
    from services.security import issue_token

    world = make_tenant()
    token = issue_token(world["app_id"], world["tenant_id"], "app", ttl_minutes=10)
    assert client.get("/v1/workflows", headers=auth(token)).status_code == 200
