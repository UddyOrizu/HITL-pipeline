"""Milestone 6: effort estimation, caps, overflow, rotation, conflict rules and blind spot-checks."""

from tests.conftest import auth, make_tenant, review, submit_text, task_for


def sstatus(client, world, sid):
    return client.get(f"/v1/submissions/{sid}", headers={"X-API-Key": world["api_key"]}).json()


def test_effort_scales_with_length_and_risk():
    from services.assignment import estimate_effort

    assert estimate_effort(200, "low") == 3.0
    assert estimate_effort(200, "high") > estimate_effort(200, "low")
    assert estimate_effort(8000, "medium") > 10 * estimate_effort(200, "medium") / 2


def test_cap_overflow_queues_then_assigns_when_capacity_appears(client, sink):
    world = make_tenant(cap_policy={"daily_minutes": 4, "hourly_minutes": 4, "overflow": "queue"}, reviewers=2)
    subs = [submit_text(client, world, external_ref=f"S{i}") for i in range(3)]
    queues = {k: client.get("/v1/review/queue", headers=auth(u["token"])).json() for k, u in world["users"].items() if k.startswith("r")}
    assert sorted(len(q) for q in queues.values()) == [1, 1]  # one each, then caps are full
    lead = auth(world["users"]["lead"]["token"])
    held = client.get("/v1/oversight/submissions?held=true", headers=lead).json()
    assert [h["id"] for h in held] == [subs[2]["id"]] and held[0]["hold_reason"] == "awaiting_capacity"
    assert sstatus(client, world, subs[2]["id"])["status"] == "in_review"  # never a silent approval

    admin = auth(world["users"]["admin"]["token"])
    client.post("/v1/admin/users", json={"email": "new@acme.test", "roles": ["reviewer"], "team_ids": [world["team_id"]]}, headers=admin)
    from db.session import session_scope
    from services import workflow

    with session_scope() as db:
        assert workflow.assign_pending(db) == 1
    assert client.get("/v1/oversight/submissions?held=true", headers=lead).json() == []


def test_overflow_defer_and_escalate(client, sink):
    world = make_tenant(name="D", cap_policy={"daily_minutes": 4, "overflow": "defer"}, reviewers=1)
    submit_text(client, world)
    deferred = submit_text(client, world)
    assert deferred["status"] == "deferred"
    assert any(e["event"] == "submission.deferred" and e["data"]["reason"] == "capacity" for e in sink.events())

    world2 = make_tenant(name="E", cap_policy={"daily_minutes": 4, "overflow": "escalate"}, reviewers=1)
    submit_text(client, world2)
    esc = submit_text(client, world2)
    assert esc["status"] == "in_review" and esc["escalated"] is True
    assert any(e["event"] == "submission.escalated" and e["data"]["reason"] == "awaiting_capacity" for e in sink.events())


def test_team_ceiling(client):
    world = make_tenant(cap_policy={"team_daily_minutes": 4}, reviewers=3)
    submit_text(client, world)
    second = submit_text(client, world)
    lead = auth(world["users"]["lead"]["token"])
    assert [h["id"] for h in client.get("/v1/oversight/submissions?held=true", headers=lead).json()] == [second["id"]]


def test_round_robin_and_rotation(client):
    world = make_tenant(reviewers=2, cap_policy={"max_consecutive_same_source": 1})
    owners = []
    for i in range(4):
        sub = submit_text(client, world, external_ref=f"R{i}", metadata={"client": "Client A"})
        owners.append(task_for(client, world, sub["id"])[0])
    assert owners[0] != owners[1] and owners[1] != owners[2] and owners[2] != owners[3]


def test_conflict_rule_author_never_reviews_own_work(client):
    world = make_tenant(reviewers=2)
    for _ in range(3):
        sub = submit_text(client, world, metadata={"author_email": "r1@acme.test"})
        assert task_for(client, world, sub["id"])[0] == "r2"


def test_spot_check_is_blind_and_agreement_releases(client, sink):
    world = make_tenant(spot_check_rate=1.0)
    sub = submit_text(client, world)
    first, item = task_for(client, world, sub["id"])
    token = world["users"][first]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    span = view["document"]["sections"][0]["spans"][0]["id"]
    client.post(f"/v1/review/tasks/{item['task_id']}/comments", json={"span_id": span, "body": "first reviewer note"}, headers=auth(token))
    review(client, world, sub["id"], "approve")
    assert sstatus(client, world, sub["id"])["status"] == "spot_check"
    assert not [e for e in sink.events() if e["event"] == "submission.decided"]  # held until second review

    second, sitem = task_for(client, world, sub["id"], kind="spot_check")
    assert second != first
    sview = client.get(f"/v1/review/tasks/{sitem['task_id']}", headers=auth(world["users"][second]["token"])).json()
    assert sview["comments"] == [] and sview["prior_stages"] == []
    assert "first reviewer note" not in str(sview)
    review(client, world, sub["id"], "approve", kind="spot_check")
    s = sstatus(client, world, sub["id"])
    assert s["status"] == "approved"
    assert s["decision"]["assurance"]["components"]["independent_check"]["value"] == 1.0
    assert len(s["decision"]["reviewer_ids"]) == 2


def test_spot_check_disagreement_escalates_and_oversight_resolves(client, sink):
    world = make_tenant(spot_check_rate=1.0)
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve")
    review(client, world, sub["id"], "reject", kind="spot_check", reason="Ratio is wrong")
    s = sstatus(client, world, sub["id"])
    assert s["status"] == "spot_check" and s["escalated"] and s["decision"] is None
    assert any(e["event"] == "submission.escalated" and e["data"]["reason"] == "spot_check_disagreement" for e in sink.events())

    lead = auth(world["users"]["lead"]["token"])
    r = client.post(f"/v1/oversight/submissions/{sub['id']}/resolve", json={"outcome": "reject", "reason": "Agree with second reviewer"}, headers=lead)
    assert r.status_code == 200
    s = sstatus(client, world, sub["id"])
    assert s["status"] == "rejected" and s["decision"]["assurance"]["components"]["independent_check"]["value"] == 0.0
    assert client.post(f"/v1/oversight/submissions/{sub['id']}/resolve", json={"outcome": "approve"}, headers=lead).status_code == 409


def test_spot_check_sampling_is_weighted(client):
    import random

    from db.models import Submission, Workflow
    from services import workflow

    workflow.set_rng(random.Random(0))
    wf = Workflow(spot_check_rate=0.3)
    low = Submission(risk_tier="low", tenant_id="t")
    high = Submission(risk_tier="high", tenant_id="t")

    class FakeSession:
        def scalars(self, *_a, **_k):
            class R:
                def all(self):
                    return []

            return R()

    n = 2000
    low_hits = sum(workflow._sample_for_spot_check(FakeSession(), low, wf, "r") for _ in range(n))
    high_hits = sum(workflow._sample_for_spot_check(FakeSession(), high, wf, "r") for _ in range(n))
    assert 0.25 < low_hits / n < 0.35 and 0.55 < high_hits / n < 0.65
