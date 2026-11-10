"""Milestone 4: reviewer workspace API, telemetry and the gates on Approve."""

from datetime import UTC, datetime, timedelta

from tests.conftest import ack_all_flags, auth, make_tenant, read_fully, submit_text, task_for


def open_task(client, world, sid):
    who, item = task_for(client, world, sid)
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    return who, token, item["task_id"], view


def decide(client, token, task_id, outcome="approve", **kw):
    return client.post(f"/v1/review/tasks/{task_id}/decision", json={"outcome": outcome, **kw}, headers=auth(token))


def test_task_view_has_sections_flags_and_policy(client):
    world = make_tenant()
    sub = submit_text(client, world)
    _, _, _, view = open_task(client, world, sub["id"])
    assert view["document"]["render_kind"] == "text"
    assert len(view["document"]["sections"][0]["spans"]) == 7
    cats = {f["category"] for f in view["flags"]}
    assert {"unsupported_figure", "regulated_advice"} <= cats
    assert any(f["mandatory"] for f in view["flags"])
    assert view["policy"]["min_reading_ratio"] == 0.3


def test_telemetry_summary(client):
    world = make_tenant()
    sub = submit_text(client, world)
    _, token, task_id, view = open_task(client, world, sub["id"])
    read_fully(client, token, task_id, view, seconds=40)
    view = client.get(f"/v1/review/tasks/{task_id}", headers=auth(token)).json()
    t = view["telemetry"]
    assert 30 <= t["active_seconds"] <= 41
    assert t["coverage"] == 1.0 and t["max_scroll_depth"] == 1.0
    assert t["expected_seconds"] > 0

    # Focus lost: time away from the tab is not counted.
    now = datetime.now(UTC).replace(tzinfo=None)
    events = [{"type": "focus_lost", "client_ts": (now - timedelta(seconds=3)).isoformat()},
              {"type": "heartbeat", "client_ts": (now - timedelta(seconds=2)).isoformat()},
              {"type": "focus_gained", "client_ts": now.isoformat()}]
    client.post("/v1/telemetry", json={"task_id": task_id, "events": events}, headers={**auth(token), "X-PoR-Channel": "ui"})
    after = client.get(f"/v1/review/tasks/{task_id}", headers=auth(token)).json()["telemetry"]
    assert after["focus_lost_count"] == 1
    assert after["active_seconds"] <= t["active_seconds"] + 1


def test_mandatory_flags_gate_approval(client):
    world = make_tenant()
    sub = submit_text(client, world)
    _, token, task_id, view = open_task(client, world, sub["id"])
    read_fully(client, token, task_id, view)
    r = decide(client, token, task_id, confirm_warnings=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "mandatory_flags"
    ack_all_flags(client, token, task_id, view)
    assert decide(client, token, task_id, confirm_warnings=True).status_code == 200


def test_skim_approval_is_blocked_warned_or_flagged(client):
    for action, expected in (("block", 409), ("warn", 409), ("flag", 200)):
        world = make_tenant(name=f"T{action}", telemetry_policy={"min_reading_ratio": 0.3, "min_coverage": 0.6, "action": action})
        sub = submit_text(client, world)
        _, token, task_id, view = open_task(client, world, sub["id"])
        ack_all_flags(client, token, task_id, view)
        r = decide(client, token, task_id)
        assert r.status_code == expected, (action, r.text)
        if action == "block":
            assert r.json()["detail"]["code"] == "below_review_standard"
            assert decide(client, token, task_id, confirm_warnings=True).status_code == 409
        if action == "warn":
            assert r.json()["detail"]["code"] == "confirm_required"
            ok = decide(client, token, task_id, confirm_warnings=True)
            assert ok.status_code == 200 and ok.json()["decision"]["warnings"]
        if action == "flag":
            assert r.json()["decision"]["warnings"]


def test_reject_requires_reason_and_valid_spans(client):
    world = make_tenant()
    sub = submit_text(client, world)
    _, token, task_id, view = open_task(client, world, sub["id"])
    assert decide(client, token, task_id, "reject").status_code == 422
    assert decide(client, token, task_id, "reject", reason="bad", cited_span_ids=["not-a-span"]).status_code == 422
    span = view["document"]["sections"][0]["spans"][2]["id"]
    assert decide(client, token, task_id, "reject", reason="Wrong ratio", cited_span_ids=[span]).status_code == 200
    assert decide(client, token, task_id, "reject", reason="again").status_code == 409


def test_comments_and_history(client):
    world = make_tenant()
    sub = submit_text(client, world)
    who, token, task_id, view = open_task(client, world, sub["id"])
    span = view["document"]["sections"][0]["spans"][0]["id"]
    c = client.post(f"/v1/review/tasks/{task_id}/comments", json={"span_id": span, "body": "Check"}, headers=auth(token))
    assert c.status_code == 201
    assert client.post(f"/v1/review/tasks/{task_id}/comments", json={"span_id": "nope", "body": "x"}, headers=auth(token)).status_code == 404
    assert client.post(f"/v1/review/tasks/{task_id}/comments", json={"kind": "edit", "body": "x"}, headers=auth(token)).status_code == 422
    assert client.delete(f"/v1/review/tasks/{task_id}/comments/{c.json()['id']}", headers=auth(token)).status_code == 204
    read_fully(client, token, task_id, view)
    ack_all_flags(client, token, task_id, view)
    decide(client, token, task_id, confirm_warnings=True)
    hist = client.get("/v1/review/history", headers=auth(token)).json()
    assert hist[0]["submission_id"] == sub["id"] and hist[0]["telemetry"]["coverage"] == 1.0
    # Telemetry for a closed task is ignored.
    r = client.post("/v1/telemetry", json={"task_id": task_id, "events": [{"type": "heartbeat"}]},
                    headers={**auth(token), "X-PoR-Channel": "ui"})
    assert r.json()["stored"] == 0


def test_only_reviewers_use_the_workspace(client):
    world = make_tenant()
    for role in ("lead", "auditor", "owner"):
        assert client.get("/v1/review/queue", headers=auth(world["users"][role]["token"])).status_code == 403
    # Another reviewer in the same tenant cannot open someone else's task.
    sub = submit_text(client, world)
    who, item = task_for(client, world, sub["id"])
    other = next(k for k in world["users"] if k.startswith("r") and k != who)
    assert client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(world["users"][other]["token"])).status_code == 404
