"""Evidence chain checks and anchoring, spot-check pending state, telemetry bounds and channel, history paging,
and the report exports."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from tests.conftest import auth, make_tenant, review, submit_text, task_for


def _ui(token):
    return {**auth(token), "X-PoR-Channel": "ui"}


def test_evidence_verifies_its_own_events_and_anchors_the_head(client, sink):
    from db.models import AuditEvent, EvidencePack, Submission
    from db.session import session_scope
    from services import audit, evidence

    world = make_tenant()
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve")
    with session_scope() as db:
        record = evidence.build_record(db, db.get(Submission, sub["id"]))
        chain = record["audit_chain"]
        assert chain["valid"] is True and chain["scope"] == "submission_events"
        head_seq, head_hash = audit.head(db, world["tenant_id"])
        assert chain["events_in_tenant_chain"] == chain["head_seq"] == head_seq and chain["head_hash"] == head_hash
        pack = db.scalars(select(EvidencePack).where(EvidencePack.submission_id == sub["id"])).one()
        assert pack.audit_head_seq and pack.audit_head_hash
        anchored = pack.audit_head_seq
        assert audit.verify_chain(db, world["tenant_id"])["valid"]
        last = db.scalar(select(AuditEvent.seq).where(AuditEvent.tenant_id == world["tenant_id"])
                         .order_by(AuditEvent.seq.desc()))
    # Tail truncation: remove every event from the anchored head on. The remaining chain still links up, but
    # it no longer reaches the head an evidence pack recorded.
    with session_scope() as db:
        try:
            db.execute(text("DROP TRIGGER IF EXISTS audit_events_no_delete"))
        except Exception:  # noqa: BLE001
            pass
        db.execute(text("DELETE FROM audit_events WHERE tenant_id = :t AND seq >= :s"),
                   {"t": world["tenant_id"], "s": anchored})
    with session_scope() as db:
        result = audit.verify_chain(db, world["tenant_id"])
        assert result["valid"] is False and result.get("truncated") is True
        assert last >= anchored


def test_evidence_record_detects_a_broken_link_in_its_own_events(client, sink):
    from db.models import AuditEvent, Submission
    from db.session import session_scope
    from services import evidence

    world = make_tenant()
    sub = submit_text(client, world)
    with session_scope() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == sub["id"]).order_by(AuditEvent.seq)).first()
        seq = ev.seq
    with session_scope() as db:
        db.execute(text("UPDATE audit_events SET action = 'tampered' WHERE tenant_id = :t AND seq = :s"),
                   {"t": world["tenant_id"], "s": seq})
    with session_scope() as db:
        assert evidence.build_record(db, db.get(Submission, sub["id"]))["audit_chain"]["valid"] is False


def test_spot_check_shows_pending_until_decided(client, sink):
    from db.models import Submission
    from db.session import session_scope
    from services import evidence

    world = make_tenant(spot_check_rate=1.0)
    sub = submit_text(client, world)
    with session_scope() as db:
        assert evidence.build_record(db, db.get(Submission, sub["id"]))["spot_check"] == {"sampled": False}
    review(client, world, sub["id"], "approve")
    with session_scope() as db:
        assert evidence.build_record(db, db.get(Submission, sub["id"]))["spot_check"] == {
            "sampled": True, "pending": True, "agreement": None, "spot_check_outcome": None}


def test_telemetry_only_from_the_web_workspace(client):
    world = make_tenant()
    sub = submit_text(client, world)
    who, item = task_for(client, world, sub["id"])
    token = world["users"][who]["token"]
    body = {"task_id": item["task_id"], "events": [{"type": "heartbeat"}]}
    r = client.post("/v1/telemetry", json=body, headers=auth(token))
    assert r.status_code == 403 and "web workspace" in r.text
    r = client.post("/v1/telemetry", json=body, headers={**auth(token), "X-PoR-Channel": "mcp"})
    assert r.status_code == 403
    assert client.post("/v1/telemetry", json=body, headers=_ui(token)).status_code == 202


def test_telemetry_cannot_claim_more_time_than_has_passed(client):
    world = make_tenant()
    sub = submit_text(client, world)
    who, item = task_for(client, world, sub["id"])
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    sections = [s["id"] for s in view["document"]["sections"]]
    now = datetime.now(UTC).replace(tzinfo=None)

    def batch(start, seconds, dwell):
        events = [{"type": "heartbeat", "client_ts": (start + timedelta(seconds=i)).isoformat()}
                  for i in range(0, seconds, 5)]
        events += [{"type": "section_in_view", "section_id": s, "value": dwell, "client_ts": start.isoformat()}
                   for s in sections]
        return {"task_id": item["task_id"], "events": events}

    assert client.post("/v1/telemetry", json=batch(now, 5, 1), headers=_ui(token)).status_code == 202
    # A second batch straight after claims 10 minutes of reading and 10 minutes of dwell per section, with
    # timestamps backdated and future-dated: the server has only seen seconds pass.
    fake = batch(now - timedelta(minutes=5), 600, 600)
    fake["events"].append({"type": "heartbeat", "client_ts": (now + timedelta(minutes=5)).isoformat()})
    client.post("/v1/telemetry", json=fake, headers=_ui(token))
    t = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()["telemetry"]
    assert t["active_seconds"] <= 90
    from db.models import TelemetryEvent
    from db.session import session_scope

    with session_scope() as db:
        values = db.scalars(select(TelemetryEvent.value).where(TelemetryEvent.review_task_id == item["task_id"],
                                                               TelemetryEvent.type == "section_in_view")).all()
        assert max(values) <= 20


def test_history_is_paged(client, sink):
    world = make_tenant(reviewers=1)
    for i in range(3):
        sub = submit_text(client, world, external_ref=f"H{i}")
        review(client, world, sub["id"], "approve")
    token = world["users"]["r1"]["token"]
    r = client.get("/v1/review/history?limit=2", headers=auth(token))
    assert r.status_code == 200 and len(r.json()) == 2
    assert r.headers["X-Total-Count"] == "3" and r.headers["X-Next-Offset"] == "2"
    r = client.get("/v1/review/history?offset=2&limit=2", headers=auth(token))
    assert len(r.json()) == 1 and "X-Next-Offset" not in r.headers
    assert client.get("/v1/review/history?limit=500", headers=auth(token)).status_code == 422


def test_sla_csv_and_workflow_list_headers(client, sink):
    from db.models import ReviewTask, utcnow
    from db.session import session_scope

    world = make_tenant()
    sub = submit_text(client, world)
    _, item = task_for(client, world, sub["id"])
    with session_scope() as db:
        db.get(ReviewTask, item["task_id"]).due_at = utcnow() - timedelta(hours=2)
    lead = auth(world["users"]["lead"]["token"])
    r = client.get("/v1/reports/sla?format=csv", headers=lead)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("task_id,submission_id,title") and item["task_id"] in lines[1]
    r = client.get("/v1/admin/workflows", headers=lead)
    assert isinstance(r.json(), list) and r.headers["X-Total-Count"] == "1"
