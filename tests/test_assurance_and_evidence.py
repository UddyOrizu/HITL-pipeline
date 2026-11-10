"""Milestone 7: Review Assurance Score, thresholds, dashboard and evidence packs."""

import pymupdf

from tests.conftest import ack_all_flags, auth, make_tenant, review, submit_text, task_for


def sstatus(client, world, sid):
    return client.get(f"/v1/submissions/{sid}", headers={"X-API-Key": world["api_key"]}).json()


def skim_approve(client, world, sid):
    who, item = task_for(client, world, sid)
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    ack_all_flags(client, token, item["task_id"], view)
    return client.post(f"/v1/review/tasks/{item['task_id']}/decision", json={"outcome": "approve", "confirm_warnings": True},
                       headers=auth(token))


def test_score_components_and_weight_redistribution(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    review(client, world, sub["id"])
    a = sstatus(client, world, sub["id"])["decision"]["assurance"]
    comps = a["components"]
    assert comps["independent_check"]["sampled"] is False and comps["independent_check"]["weight"] == 0
    weights = sum(v["weight"] for v in comps.values())
    assert abs(weights - 1.0) < 1e-3
    assert abs(sum(v["points"] for v in comps.values()) - a["score"]) < 0.5
    assert comps["reading_coverage"]["value"] > 0.9 and comps["risk_engagement"]["value"] == 1.0
    assert comps["reviewer_reliability"]["value"] == 0.5  # no canary history yet: neutral prior


def test_thorough_review_scores_higher_than_a_skim(client, sink):
    world = make_tenant()
    thorough = submit_text(client, world, external_ref="T")
    review(client, world, thorough["id"])
    skim = submit_text(client, world, external_ref="S")
    assert skim_approve(client, world, skim["id"]).status_code == 200
    t = sstatus(client, world, thorough["id"])["decision"]["assurance"]["score"]
    s = sstatus(client, world, skim["id"])["decision"]["assurance"]["score"]
    assert t > s + 15


def test_below_threshold_block_holds_for_oversight(client, sink):
    world = make_tenant(assurance_policy={"min_score": 90, "below_action": "block"})
    sub = submit_text(client, world)
    skim_approve(client, world, sub["id"])
    s = sstatus(client, world, sub["id"])
    assert s["status"] == "in_review" and s["decision"] is None and s["escalated"]
    lead = auth(world["users"]["lead"]["token"])
    held = client.get("/v1/oversight/submissions?held=true", headers=lead).json()
    assert held[0]["hold_reason"] == "below_threshold"
    client.post(f"/v1/oversight/submissions/{sub['id']}/resolve", json={"outcome": "approve", "reason": "Checked personally"}, headers=lead)
    s = sstatus(client, world, sub["id"])
    assert s["status"] == "approved" and s["decision"]["assurance"]["below_threshold"] is True


def test_below_threshold_second_review_forces_spot_check(client, sink):
    world = make_tenant(assurance_policy={"min_score": 90, "below_action": "second_review"}, spot_check_rate=0.0)
    sub = submit_text(client, world)
    skim_approve(client, world, sub["id"])
    assert sstatus(client, world, sub["id"])["status"] == "spot_check"


def test_release_with_flag(client, sink):
    world = make_tenant(assurance_policy={"min_score": 90, "below_action": "release_flag"})
    sub = submit_text(client, world)
    skim_approve(client, world, sub["id"])
    s = sstatus(client, world, sub["id"])
    assert s["status"] == "approved" and s["decision"]["assurance"]["below_threshold"] is True
    dash = client.get("/v1/oversight/dashboard", headers=auth(world["users"]["lead"]["token"])).json()
    assert dash["scores"]["released_below_threshold"] == 1


def test_dashboard_reports_catch_rates_and_fast_approvals(client, sink):
    world = make_tenant(canary_rate=1.0)
    caught = submit_text(client, world, external_ref="caught")
    who, item = task_for(client, world, caught["id"])
    token = world["users"][who]["token"]
    from sqlalchemy import select

    from db.models import Canary
    from db.session import session_scope

    with session_scope() as db:
        cspan = db.scalars(select(Canary.span_id).where(Canary.submission_id == caught["id"])).one()
    client.post(f"/v1/review/tasks/{item['task_id']}/comments", json={"span_id": cspan, "body": "error"}, headers=auth(token))
    review(client, world, caught["id"])
    missed = submit_text(client, world, external_ref="missed")
    skim_approve(client, world, missed["id"])

    dash = client.get("/v1/oversight/dashboard", headers=auth(world["users"]["lead"]["token"])).json()
    assert dash["catch_rate"]["overall"] == 0.5
    assert sum(r["total"] for r in dash["catch_rate"]["by_reviewer"]) == 2
    assert dash["review_time"]["fast_approvals"] == 1
    assert dash["capacity"][0]["used_minutes"] > 0
    summary = client.get("/v1/assurance/summary", headers={"X-API-Key": world["api_key"]}).json()
    assert summary["catch_rate"]["overall"] == 0.5 and "by_reviewer" not in summary["catch_rate"]


def test_evidence_pack_contents(client, sink):
    world = make_tenant(canary_rate=1.0, spot_check_rate=1.0)
    sub = submit_text(client, world)
    review(client, world, sub["id"])
    review(client, world, sub["id"], kind="spot_check")
    h = {"X-API-Key": world["api_key"]}
    ev = client.get(f"/v1/submissions/{sub['id']}/evidence", headers=h).json()
    assert ev["documents"][0]["sha256"] and ev["content_hash"]
    assert ev["workflow"]["version"] == 1
    assert ev["risk_flags"] and all("opened" in f for f in ev["risk_flags"])
    assert ev["canary"]["present"] is True and ev["canary"]["items"][0]["caught"] is False
    assert "injected_text" not in str(ev) and "canary_text" not in str(ev)
    assert ev["spot_check"] == {"sampled": True, "pending": False, "agreement": True, "spot_check_outcome": "approve"}
    assert ev["assurance"]["score"] > 0
    assert ev["audit_chain"]["valid"] is True
    actions = [e["action"] for e in ev["audit_trail"]]
    for needed in ("submission.received", "risk.flagged", "canary.injected", "task.assigned", "decision.recorded",
                   "canary.missed", "spot_check.agreed", "submission.released"):
        assert needed in actions, needed
    pdf = client.get(f"/v1/submissions/{sub['id']}/evidence?format=pdf", headers=h).content
    text = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    assert "evidence pack" in text and "canary was present" in text and "Tenant chain valid" in text
    auditor = auth(world["users"]["auditor"]["token"])
    assert client.get(f"/v1/audit/submissions/{sub['id']}/evidence.pdf", headers=auditor).status_code == 200
    assert client.get("/v1/audit/verify", headers=auditor).json()["valid"] is True
    listed = client.get("/v1/audit/submissions?q=EXT", headers=auditor).json()
    assert listed[0]["has_pack"] is True
