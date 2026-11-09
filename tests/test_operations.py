"""Operations: job routing and retries, scheduler recovery, holds and SLAs, cancellation, retention, scanning,
outbound URL checks, connectors, metrics and pagination."""

from __future__ import annotations

import json
import socket
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant, review, submit_text, task_for

# --- Jobs and the worker ----------------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.streams: dict[str, list[dict]] = {}
        self.zset: dict[str, float] = {}
        self.acked = []

    def xadd(self, stream, fields, maxlen=None):
        self.streams.setdefault(stream, []).append(dict(fields))
        return f"{len(self.streams[stream])}-0"

    def xack(self, stream, group, msg_id):
        self.acked.append((stream, msg_id))

    def zadd(self, key, mapping):
        self.zset.update(mapping)

    def zrangebyscore(self, key, lo, hi, start=0, num=100):
        return [m for m, score in self.zset.items() if score <= hi][:num]

    def zrem(self, key, member):
        return self.zset.pop(member, None) is not None


@pytest.fixture()
def redis_mode(env, monkeypatch):
    from services import jobs
    from services.config import get_settings

    monkeypatch.setenv("POR_REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setenv("POR_INLINE_JOBS", "false")
    get_settings.cache_clear()
    fake = FakeRedis()
    monkeypatch.setattr(jobs, "_redis", lambda: fake)
    return fake


def test_events_go_to_the_stream_only_when_redis_jobs_are_on(redis_mode):
    from services import jobs

    seen = []
    jobs.subscribe("test.event", seen.append)
    jobs.publish("test.event", {"x": 1})
    assert seen == []  # not also run in-process: the worker runs it once
    assert redis_mode.streams[jobs.EVENT_STREAM][0]["event"] == "test.event"


def test_events_run_in_process_without_redis(env):
    from services import jobs

    seen = []
    jobs.subscribe("test.inline", seen.append)
    jobs.publish("test.inline", {"x": 2})
    assert seen == [{"x": 2}]


def test_worker_retries_only_the_failing_handler_then_dead_letters(redis_mode, monkeypatch):
    from services import jobs, worker
    from services.config import get_settings

    monkeypatch.setenv("POR_JOB_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    good, calls = [], {"bad": 0}

    def ok_handler(payload):
        good.append(payload)

    def bad_handler(payload):
        calls["bad"] += 1
        raise RuntimeError("downstream is down")

    jobs.subscribe("test.retry", ok_handler)
    jobs.subscribe("test.retry", bad_handler)
    fields = {"event": "test.retry", "payload": json.dumps({"n": 1}), "attempt": "1"}
    worker.handle(redis_mode, jobs.EVENT_STREAM, "1-0", fields)
    assert good == [{"n": 1}] and calls["bad"] == 1
    (queued,) = redis_mode.zset
    retry = json.loads(queued)["fields"]
    assert retry["only"] == jobs.handler_name(bad_handler) and retry["attempt"] == "2"
    # Make it due, release it, and process the retry: only the failing handler runs again.
    redis_mode.zset[queued] = 0
    assert jobs.release_delayed(redis_mode) == 1
    msg = redis_mode.streams[jobs.EVENT_STREAM][-1]
    worker.handle(redis_mode, jobs.EVENT_STREAM, "2-0", msg)
    assert good == [{"n": 1}] and calls["bad"] == 2
    dead = redis_mode.streams[jobs.DEAD_STREAM]
    assert dead and "downstream is down" in dead[0]["error"]
    assert len(redis_mode.acked) == 2  # every message is acknowledged; failures live on as retries


def test_scheduler_requeues_submissions_stuck_in_processing(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    from db.models import Submission, utcnow
    from db.session import session_scope
    from services import scheduler

    with session_scope() as db:
        s = db.get(Submission, sub["id"])
        s.status = "processing"  # simulate a worker that died mid-way
    with session_scope() as db:
        db.get(Submission, sub["id"]).updated_at = utcnow() - timedelta(hours=1)
    scheduler.recover_submissions()  # back to received, due now
    scheduler.recover_submissions()  # picked up and processed
    with session_scope() as db:
        assert db.get(Submission, sub["id"]).status in ("in_review", "received")


def test_periodic_tasks_take_a_lock(env):
    from services import scheduler

    runs = []
    release = scheduler._acquire("busy", 60)
    assert scheduler.run_task("busy", lambda: runs.append(1)) is False
    release()
    assert scheduler.run_task("busy", lambda: runs.append(1)) is True and runs == [1]


# --- Holds, retries, cancellation and SLAs ------------------------------------------------


def test_cancel_notifies_the_source_app_and_frees_capacity(client, sink):
    world = make_tenant(cap_policy={"daily_minutes": 4, "hourly_minutes": 4}, reviewers=1)
    first = submit_text(client, world, external_ref="C1")
    second = submit_text(client, world, external_ref="C2")
    lead = auth(world["users"]["lead"]["token"])
    held = client.get(f"/v1/oversight/submissions/{second['id']}", headers=lead).json()
    assert held["hold_reason"] == "awaiting_capacity"
    r = client.post(f"/v1/submissions/{first['id']}/cancel", headers={"X-API-Key": world["api_key"]})
    assert r.status_code == 200
    assert any(e["event"] == "submission.cancelled" for e in sink.events())


def test_oversight_assigns_a_capacity_hold_past_the_caps(client, sink):
    world = make_tenant(cap_policy={"daily_minutes": 4, "hourly_minutes": 4}, reviewers=2)
    subs = [submit_text(client, world, external_ref=f"A{i}") for i in range(3)]
    lead = auth(world["users"]["lead"]["token"])
    held = [s for s in subs if client.get(f"/v1/oversight/submissions/{s['id']}", headers=lead).json()["hold_reason"]]
    assert held, "expected at least one item waiting for capacity"
    detail = client.get(f"/v1/oversight/submissions/{held[0]['id']}", headers=lead).json()
    assert "assign" in detail["actions"] and detail["document"]["sections"]
    r = client.post(f"/v1/oversight/submissions/{held[0]['id']}/action", headers=lead,
                    json={"action": "assign", "reviewer_id": world["users"]["r1"]["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["hold_reason"] is None
    who, _ = task_for(client, world, held[0]["id"])
    assert who == "r1"


def test_deferred_items_can_be_retried(client, sink):
    world = make_tenant(cap_policy={"daily_minutes": 4, "hourly_minutes": 4, "overflow": "defer"}, reviewers=1)
    submit_text(client, world, external_ref="F1")
    second = submit_text(client, world, external_ref="F2")
    lead = auth(world["users"]["lead"]["token"])
    assert client.get(f"/v1/oversight/submissions/{second['id']}", headers=lead).json()["status"] == "deferred"
    # More capacity (a new reviewer): the oversight lead retries it. It keeps its workflow version.
    admin = auth(world["users"]["admin"]["token"])
    client.post("/v1/admin/users", headers=admin, json={"email": "extra@acme.test", "roles": ["reviewer"],
                                                        "team_ids": [world["team_id"]]})
    r = client.post(f"/v1/oversight/submissions/{second['id']}/action", headers=lead, json={"action": "retry"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_review"


def test_sla_breach_can_reassign_to_another_reviewer(client, sink):
    world = make_tenant(stages=None, reviewers=2)
    lead = auth(world["users"]["lead"]["token"])
    client.put(f"/v1/admin/workflows/{world['workflow_id']}", headers=lead, json={
        "name": "Default", "stages": [{"name": "Review", "team_id": world["team_id"], "sla_hours": 1,
                                       "on_sla_breach": "reassign"}],
        "risk_policy": {"mandatory_severities": ["high"]}, "assurance_policy": {"min_score": 0, "below_action": "release_flag"}})
    sub = submit_text(client, world)
    first, item = task_for(client, world, sub["id"])
    from db.models import ReviewTask, utcnow
    from db.session import session_scope
    from services import workflow

    with session_scope() as db:
        db.get(ReviewTask, item["task_id"]).due_at = utcnow() - timedelta(minutes=5)
    with session_scope() as db:
        assert workflow.check_slas(db) == 1
    second, _ = task_for(client, world, sub["id"])
    assert second != first
    assert any(e["event"] == "submission.escalated" for e in sink.events())


def test_resolving_with_reject_needs_a_reason(client, sink):
    world = make_tenant(spot_check_rate=1.0)
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve")
    review(client, world, sub["id"], "reject", kind="spot_check", reason="Wrong figure")
    lead = auth(world["users"]["lead"]["token"])
    assert client.post(f"/v1/oversight/submissions/{sub['id']}/resolve", headers=lead, json={"outcome": "reject"}).status_code == 422
    detail = client.get(f"/v1/oversight/submissions/{sub['id']}", headers=lead).json()
    assert {r["decision"]["outcome"] for r in detail["reviews"] if r["decision"]} == {"approve", "reject"}


# --- Retention and erasure ---------------------------------------------------------------


def test_purge_removes_content_but_keeps_the_proof(client, sink):
    world = make_tenant()
    sub = submit_text(client, world, reference_texts=["Reference: the premium is 420 pounds."])
    review(client, world, sub["id"], "approve")
    admin = auth(world["users"]["admin"]["token"])
    from db.models import Document, Span, Submission
    from db.session import session_scope
    from services.storage import get_storage

    with session_scope() as db:
        uris = [d.original_file_uri for d in db.scalars(select(Document).where(Document.submission_id == sub["id"])).all()]
    r = client.delete(f"/v1/admin/submissions/{sub['id']}/content", headers=admin)
    assert r.status_code == 200, r.text and r.json()["files"] == len(uris)
    with session_scope() as db:
        s = db.get(Submission, sub["id"])
        assert s.purged_at and s.final_outcome == "approved"
        doc_ids = [d.id for d in s.documents]
        assert all(sp.text == "" for sp in db.scalars(select(Span).where(Span.document_id.in_(doc_ids))).all())
    for uri in uris:
        with pytest.raises(FileNotFoundError):
            get_storage().get(uri)
    # The decision and evidence pack are still there.
    pdf = client.get(f"/v1/submissions/{sub['id']}/evidence?format=pdf", headers={"X-API-Key": world["api_key"]})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get("/v1/audit/verify", headers=auth(world["users"]["auditor"]["token"])).json()["valid"]


def test_open_submissions_cannot_be_purged_and_sweep_follows_retention_days(client, sink):
    world = make_tenant()
    open_sub = submit_text(client, world)
    admin = auth(world["users"]["admin"]["token"])
    assert client.delete(f"/v1/admin/submissions/{open_sub['id']}/content", headers=admin).status_code == 409
    done = submit_text(client, world)
    review(client, world, done["id"], "approve")
    client.put("/v1/admin/tenant", headers=admin, json={"retention_days": 30})
    from db.models import Submission, utcnow
    from db.session import session_scope
    from services import retention

    assert retention.sweep() == 0
    with session_scope() as db:
        db.get(Submission, done["id"]).released_at = utcnow() - timedelta(days=31)
    assert retention.sweep() == 1
    with session_scope() as db:
        assert db.get(Submission, done["id"]).purged_at and db.get(Submission, open_sub["id"]).purged_at is None


def test_erasing_a_user_removes_personal_data(client):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    uid = world["users"]["r3"]["id"]
    assert client.post(f"/v1/admin/users/{uid}/erase", headers=admin).status_code == 409  # still active
    client.put(f"/v1/admin/users/{uid}", headers=admin, json={"active": False})
    r = client.post(f"/v1/admin/users/{uid}/erase", headers=admin)
    assert r.status_code == 200
    assert r.json()["name"] == "Erased user" and "r3@" not in r.json()["email"] and r.json()["erased"]
    assert client.put(f"/v1/admin/users/{uid}", headers=admin, json={"active": True}).status_code == 409


# --- Virus scanning ---------------------------------------------------------------------


class FakeSock:
    def __init__(self, reply: bytes):
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def sendall(self, data):
        pass

    def recv(self, n):
        r, self.reply = self.reply, b""
        return r


@pytest.mark.parametrize("reply,outcome", [
    (b"stream: OK\0", "ok"),
    (b"stream: Win.Test.EICAR_HDB-1 FOUND\0", "virus"),
    (b"INSTREAM size limit exceeded. ERROR\0", "unavailable"),
    (b"", "unavailable"),
])
def test_clamav_only_passes_an_explicit_ok(env, monkeypatch, reply, outcome):
    from services.config import get_settings
    from services.ingestion import scanning

    monkeypatch.setenv("POR_CLAMAV_HOST", "clamav")
    get_settings.cache_clear()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: FakeSock(reply))
    if outcome == "ok":
        scanning.scan(b"hello")
    elif outcome == "virus":
        with pytest.raises(scanning.VirusFound):
            scanning.scan(b"hello")
    else:
        with pytest.raises(scanning.ScanUnavailable):
            scanning.scan(b"hello")


def test_uploads_are_refused_when_scanning_is_required_but_missing(client, monkeypatch):
    from services.config import get_settings

    monkeypatch.setenv("POR_REQUIRE_VIRUS_SCAN", "true")
    get_settings.cache_clear()
    world = make_tenant()
    r = client.post("/v1/submissions", json={"workflow_id": world["workflow_id"], "text": "Hello there."},
                    headers={"X-API-Key": world["api_key"]})
    assert r.status_code == 503


# --- Outbound URL checks ----------------------------------------------------------------


def test_private_webhook_targets_are_refused_in_prod_mode(client, monkeypatch):
    from services.config import get_settings

    world = make_tenant()
    monkeypatch.setenv("POR_ALLOW_PRIVATE_TARGETS", "false")
    get_settings.cache_clear()
    owner = auth(world["users"]["owner"]["token"])
    for url in ("http://hooks.example.com/x", "https://127.0.0.1/x", "https://10.1.2.3/hook", "https://169.254.169.254/latest",
                "https://metadata.internal/x"):
        r = client.post("/v1/integrations/apps", headers=owner, json={"name": "x", "webhook_url": url})
        assert r.status_code == 422, url
    assert client.post("/v1/integrations/apps", headers=owner, json={"name": "ok", "webhook_url": "https://8.8.8.8/hook"}).status_code == 201


# --- Connectors -------------------------------------------------------------------------


class Services:
    def __init__(self):
        self.calls: list[tuple[str, str, bytes]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url, request.content))
        if "/_apis/wit/wiql" in url:
            return httpx.Response(200, json={"workItems": [{"id": 42}]})
        if "/_apis/wit/workitems/42" in url and request.method == "GET":
            return httpx.Response(200, json={"id": 42, "rev": 3, "fields": {
                "System.Title": "Client letter", "System.Rev": 3,
                "System.Description": "<p>Dear client,</p><p>Your premium is &pound;420 a year.</p>"}})
        if "/_apis/wit/attachments" in url:
            return httpx.Response(201, json={"id": "att-1", "url": "https://dev.azure.com/org/_apis/wit/attachments/att-1"})
        if "/rest/api/3/search/jql" in url:
            return httpx.Response(200, json={"issues": [{"key": "LEG-7"}]})
        if "/rest/api/3/issue/LEG-7" in url and request.method == "GET":
            return httpx.Response(200, json={"id": "7007", "fields": {"summary": "Engagement letter", "description": "Fees are fixed."}})
        return httpx.Response(200, json={})


@pytest.fixture()
def services(client):
    from api.main import app
    from connectors import base
    from fastapi.testclient import TestClient

    base.set_por_client_factory(lambda app_id, tenant_id: TestClient(app))
    fake = Services()
    base.set_transport(httpx.MockTransport(fake))
    yield fake
    base.set_transport(None)
    base.set_por_client_factory(None)


def test_connector_secrets_are_encrypted_masked_and_kept_on_edit(client):
    world = make_tenant()
    owner = auth(world["users"]["owner"]["token"])
    r = client.post("/v1/integrations/connectors", headers=owner, json={
        "source_app_id": world["app_id"], "kind": "azure_devops",
        "config": {"organization": "org", "project": "proj", "pat": "pat-secret"}})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["config"]["pat"] == "***"
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        raw = db.get(ConnectorConfig, cid).config["pat"]
    assert raw.startswith("enc:v1:") and "pat-secret" not in raw
    r = client.patch(f"/v1/integrations/connectors/{cid}", headers=owner, json={"config": {
        "organization": "org", "project": "other", "pat": "***"}})
    assert r.status_code == 200
    from connectors.base import config_of

    with session_scope() as db:
        cfg = db.get(ConnectorConfig, cid)
        assert config_of(cfg)["pat"] == "pat-secret" and cfg.config["project"] == "other"
    # Missing settings are refused, and audit entries never hold the secret.
    assert client.post("/v1/integrations/connectors", headers=owner, json={
        "source_app_id": world["app_id"], "kind": "sharepoint", "config": {"drive_id": "d"}}).status_code == 422
    events = client.get("/v1/audit/events?entity=connector", headers=auth(world["users"]["auditor"]["token"])).json()
    assert events and "pat-secret" not in json.dumps(events)
    assert client.delete(f"/v1/integrations/connectors/{cid}", headers=owner).status_code == 204


def test_azure_devops_submits_work_items_and_attaches_the_evidence_pack(client, services, sink):
    world = make_tenant()
    owner = auth(world["users"]["owner"]["token"])
    cid = client.post("/v1/integrations/connectors", headers=owner, json={
        "source_app_id": world["app_id"], "kind": "azure_devops",
        "config": {"organization": "org", "project": "proj", "pat": "pat", "workflow_id": world["workflow_id"],
                   "submit_wiql": "SELECT [System.Id] FROM WorkItems", "states": {"approved": "Done"}}}).json()["id"]
    run = client.post(f"/v1/integrations/connectors/{cid}/run", headers=owner).json()
    assert run["last_error"] is None and len(run["result"]["submitted"]) == 1
    sub_id = run["result"]["submitted"][0]
    # Polling again does not submit the same work item twice.
    assert client.post(f"/v1/integrations/connectors/{cid}/run", headers=owner).json()["result"]["submitted"] == []
    review(client, world, sub_id, "approve")
    methods = [(m, u) for m, u, _ in services.calls]
    assert any(m == "POST" and "/_apis/wit/attachments" in u for m, u in methods)
    patch = next(c for m, u, c in services.calls if m == "PATCH" and "/workitems/42" in u)
    ops = json.loads(patch)
    assert {"op": "add", "path": "/fields/System.State", "value": "Done"} in ops
    assert any(o["path"] == "/relations/-" and o["value"]["rel"] == "AttachedFile" for o in ops)


def test_jira_polls_a_jql_query(client, services, sink):
    world = make_tenant()
    owner = auth(world["users"]["owner"]["token"])
    cid = client.post("/v1/integrations/connectors", headers=owner, json={
        "source_app_id": world["app_id"], "kind": "jira",
        "config": {"base_url": "https://acme.atlassian.example", "email": "bot@acme.test", "api_token": "t",
                   "workflow_id": world["workflow_id"], "submit_jql": "status = 'Ready for review'"}}).json()["id"]
    from connectors import poll_one

    result = poll_one(cid)
    assert len(result["submitted"]) == 1
    sub = client.get(f"/v1/submissions/{result['submitted'][0]}", headers={"X-API-Key": world["api_key"]}).json()
    assert sub["metadata"]["jira_issue_key"] == "LEG-7"


def test_a_failing_connector_records_its_error_without_stopping_others(client, services):
    world = make_tenant()
    from connectors import poll_one
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        broken = ConnectorConfig(tenant_id=world["tenant_id"], source_app_id=world["app_id"], kind="sharepoint",
                                 config={"drive_id": "d", "workflow_id": world["workflow_id"]})  # no credentials
        db.add(broken)
        db.flush()
        bid = broken.id
    result = poll_one(bid)
    assert "error" in result
    with session_scope() as db:
        assert db.get(ConnectorConfig, bid).last_error


# --- Reference documents, metrics, pagination -------------------------------------------


def test_reviewers_can_read_reference_documents(client, sink):
    world = make_tenant()
    sub = submit_text(client, world, reference_texts=["Policy schedule: the annual premium is 420 pounds."])
    who, item = task_for(client, world, sub["id"])
    token = auth(world["users"][who]["token"])
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=token).json()
    assert len(view["submission"]["reference_documents"]) == 1
    refs = client.get(f"/v1/review/tasks/{item['task_id']}/references", headers=token).json()
    assert "420 pounds" in refs[0]["sections"][0]["spans"][0]["text"]
    f = client.get(f"/v1/review/tasks/{item['task_id']}/references/{refs[0]['id']}/file", headers=token)
    assert f.status_code == 200 and b"420 pounds" in f.content


def test_metrics_endpoint(client, monkeypatch):
    make_tenant()
    client.get("/healthz")
    body = client.get("/metrics").text
    assert "por_http_requests_total" in body and 'route="/healthz"' in body and "por_review_tasks_open" in body
    from services.config import get_settings

    monkeypatch.setenv("POR_METRICS_TOKEN", "scrape-me")
    get_settings.cache_clear()
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer scrape-me"}).status_code == 200
    assert client.get("/readyz").json() == {"ok": True}


def test_lists_are_paginated_with_a_total(client, sink):
    world = make_tenant()
    for i in range(3):
        submit_text(client, world, external_ref=f"P-{i}")
    lead = auth(world["users"]["lead"]["token"])
    r = client.get("/v1/oversight/submissions?limit=2", headers=lead)
    assert r.headers["X-Total-Count"] == "3" and r.headers["X-Next-Offset"] == "2" and len(r.json()) == 2
    r = client.get("/v1/oversight/submissions?limit=2&offset=2", headers=lead)
    assert len(r.json()) == 1 and "X-Next-Offset" not in r.headers
    apps = client.get("/v1/submissions?external_ref=P-1", headers={"X-API-Key": world["api_key"]})
    assert [s["external_ref"] for s in apps.json()] == ["P-1"]
    events = client.get("/v1/audit/events?order=desc&limit=5&action=submission.", headers=auth(world["users"]["auditor"]["token"]))
    seqs = [e["seq"] for e in events.json()]
    assert seqs == sorted(seqs, reverse=True) and all(e["action"].startswith("submission.") for e in events.json())


def test_portal_revision_must_follow_your_own_submission(client, sink):
    world = make_tenant()
    sub_token = auth(world["users"]["sub"]["token"])
    mine = client.post("/v1/portal/submissions", headers=sub_token, json={"workflow_id": world["workflow_id"], "text": "Mine.\n\nTwo."}).json()
    other_app = submit_text(client, world)
    bad = client.post("/v1/portal/submissions", headers=sub_token, json={
        "workflow_id": world["workflow_id"], "text": "Revision.", "previous_submission_id": other_app["id"]})
    assert bad.status_code == 404
    ok = client.post("/v1/portal/submissions", headers=sub_token, json={
        "workflow_id": world["workflow_id"], "text": "Revision.\n\nTwo.", "previous_submission_id": mine["id"]})
    assert ok.status_code == 201 and ok.json()["previous_submission_id"] == mine["id"]
