"""Connector robustness: SharePoint paging, per-item write-back, deferred items and content markers; Jira and Azure
DevOps paging, revisions and idempotent write-back; Teams guard and SLA reminders; connector locks."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from tests.conftest import make_tenant, submit_text


# --- Shared fixtures --------------------------------------------------------------------


@pytest.fixture()
def por_api(client):
    """Connectors call the in-process API."""
    from fastapi.testclient import TestClient

    from api.main import app
    from connectors import base

    base.set_por_client_factory(lambda app_id, tenant_id: TestClient(app))
    yield
    base.set_transport(None)
    base.set_por_client_factory(None)


def install(handler):
    from connectors import base

    base.set_transport(httpx.MockTransport(handler))


def set_status(sub_id: str, status: str) -> None:
    from db.models import Submission
    from db.session import session_scope

    with session_scope() as s:
        s.get(Submission, sub_id).status = status


def state_of(cid: str) -> dict:
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as s:
        c = s.get(ConnectorConfig, cid)
        return {"state": dict(c.state or {}), "last_error": c.last_error}


# --- SharePoint -------------------------------------------------------------------------


class FakeGraph:
    def __init__(self, files: dict):
        self.files = files  # id -> {"name", "content", "eTag", "cTag"}
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail_patch: set[str] = set()
        self.page_size = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url, request.content))
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "graph-token"})
        if ":/children" in url:
            ids = list(self.files)
            start = int(request.url.params.get("page", "0"))
            chunk = ids[start:start + self.page_size]
            body = {"value": [{"id": i, "name": self.files[i]["name"], "eTag": self.files[i]["eTag"],
                               "cTag": self.files[i]["cTag"], "file": {}} for i in chunk]}
            if start + self.page_size < len(ids):
                body["@odata.nextLink"] = (f"https://graph.microsoft.com/v1.0/drives/d1/root:/Review/Inbox:/children"
                                           f"?page={start + self.page_size}")
            return httpx.Response(200, json=body)
        if url.endswith("/content"):
            item = url.split("/items/")[1].split("/")[0]
            return httpx.Response(200, content=self.files[item]["content"])
        if request.method == "PATCH":
            item = url.split("/items/")[1].split("/")[0].split("?")[0]
            if item in self.fail_patch:
                return httpx.Response(503, text="busy")
            if item in self.files:
                self.files[item]["eTag"] += "x"  # metadata changes move the eTag, not the cTag
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    def patches(self, item: str) -> list[dict]:
        return [json.loads(c) for m, u, c in self.calls if m == "PATCH" and f"/items/{item}" in u]


def sharepoint(world, **extra):
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        c = ConnectorConfig(tenant_id=world["tenant_id"], source_app_id=world["app_id"], kind="sharepoint",
                            config={"aad_tenant_id": "aad", "client_id": "c", "client_secret": "s", "drive_id": "d1",
                                    "workflow_id": world["workflow_id"], **extra})
        db.add(c)
        db.flush()
        return c.id


def files(*names):
    return {f"item-{i}": {"name": n, "content": f"Document {i}: the premium is {i}00 pounds.".encode(),
                          "eTag": f"e{i}", "cTag": f"c{i}"} for i, n in enumerate(names, 1)}


def test_sharepoint_follows_paging_and_writes_back_each_item_on_its_own(por_api):
    from connectors import poll_one

    world = make_tenant()
    graph = FakeGraph(files("a.txt", "b.txt"))
    install(graph)
    cid = sharepoint(world)
    out = poll_one(cid)
    assert len(out["submitted"]) == 2  # the second file came from @odata.nextLink
    pending = state_of(cid)["state"]["pending"]
    by_item = {v["item"]: k for k, v in pending.items()}

    for sid in pending:
        set_status(sid, "approved")
    graph.fail_patch = {"item-1"}
    out = poll_one(cid)
    assert out["completed"] == [by_item["item-2"]]  # one failing file does not stop the other
    st = state_of(cid)
    assert st["last_error"] and "503" in st["last_error"]
    assert st["state"]["pending"][by_item["item-1"]]["failures"] == 1

    graph.fail_patch = set()
    out = poll_one(cid)
    assert out["completed"] == [by_item["item-1"]]
    assert state_of(cid)["state"]["pending"] == {} and state_of(cid)["last_error"] is None
    # Our own metadata PATCH moved the eTag, but the content did not change: nothing is resubmitted.
    assert poll_one(cid)["submitted"] == []


def test_sharepoint_poll_state_survives_a_failing_sync(por_api, monkeypatch):
    from connectors import poll_one, sharepoint as sp

    world = make_tenant()
    install(FakeGraph(files("a.txt")))
    cid = sharepoint(world)

    def broken(self):
        raise RuntimeError("graph is down")

    monkeypatch.setattr(sp.SharePointConnector, "sync_decisions", broken)
    out = poll_one(cid)
    assert len(out["submitted"]) == 1 and "sync" in out["error"]
    st = state_of(cid)
    assert st["state"]["pending"] and st["state"]["items"] == {"item-1": "c1"}  # committed despite the sync failure
    assert "graph is down" in st["last_error"]
    assert poll_one(cid)["submitted"] == []  # not submitted a second time


def test_sharepoint_deferred_items_stay_tracked_until_final(por_api):
    from connectors import poll_one

    world = make_tenant()
    graph = FakeGraph(files("a.txt"))
    install(graph)
    cid = sharepoint(world)
    (sid,) = poll_one(cid)["submitted"]

    set_status(sid, "deferred")
    assert poll_one(cid)["completed"] == []
    first = graph.patches("item-1")
    assert first[0]["ReviewStatus"] == "deferred" and first[1]["parentReference"]["path"].endswith("Review/Deferred")
    assert state_of(cid)["state"]["pending"][sid]["status"] == "deferred"
    poll_one(cid)
    assert len(graph.patches("item-1")) == 2  # the deferral is written once

    set_status(sid, "approved")
    assert poll_one(cid)["completed"] == [sid]
    last = graph.patches("item-1")[-2:]
    assert last[0]["ReviewStatus"] == "approved" and last[1]["parentReference"]["path"].endswith("Review/Approved")


def test_sharepoint_keeps_items_in_place_and_resubmits_changed_content(por_api):
    from connectors import poll_one

    world = make_tenant()
    graph = FakeGraph(files("a.txt"))
    install(graph)
    cid = sharepoint(world, cancelled_folder="Review/Inbox")
    (sid,) = poll_one(cid)["submitted"]
    set_status(sid, "cancelled")
    assert poll_one(cid)["completed"] == [sid]
    assert len(graph.patches("item-1")) == 1  # metadata only: the cancelled file stays in the inbox
    assert poll_one(cid)["submitted"] == []
    graph.files["item-1"].update(cTag="c1-v2", content=b"Revised document: the premium is 500 pounds.")
    assert len(poll_one(cid)["submitted"]) == 1


def test_sharepoint_reads_older_state(por_api):
    """Configurations saved before content markers: "<item id>:<eTag>" entries still count as submitted."""
    from connectors import poll_one
    from db.models import ConnectorConfig
    from db.session import session_scope

    world = make_tenant()
    install(FakeGraph(files("a.txt")))
    cid = sharepoint(world)
    with session_scope() as s:
        s.get(ConnectorConfig, cid).state = {"seen": ["item-1:e1"], "pending": {}}
    assert poll_one(cid)["submitted"] == []
    assert state_of(cid)["state"]["items"] == {"item-1": "c1"}


# --- Jira -------------------------------------------------------------------------------


class FakeJira:
    def __init__(self):
        self.issues = {
            "A-1": {"id": "101", "fields": {"summary": "Letter one", "description": "Fees are fixed at 100 pounds.",
                                            "updated": "2026-01-01T10:00:00"}},
            "A-2": {"id": "102", "fields": {"summary": "Letter two", "description": "Fees are fixed at 200 pounds.",
                                            "updated": "2026-01-01T10:00:00"}},
        }
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail_transition = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url, request.content))
        if "/rest/api/3/search/jql" in url:
            keys = list(self.issues)
            token = request.url.params.get("nextPageToken")
            start = int(token) if token else 0
            page = keys[start:start + 1]
            body = {"issues": [{"key": k, "fields": self.issues[k]["fields"]} for k in page],
                    "isLast": start + 1 >= len(keys)}
            if start + 1 < len(keys):
                body["nextPageToken"] = str(start + 1)
            return httpx.Response(200, json=body)
        if "/rest/api/3/issue/" in url and request.method == "GET":
            key = url.split("/issue/")[1].split("?")[0]
            return httpx.Response(200, json=self.issues[key])
        if url.endswith("/transitions") and self.fail_transition:
            self.fail_transition -= 1
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={})

    def posts(self, suffix: str) -> int:
        return sum(1 for m, u, _ in self.calls if m == "POST" and u.endswith(suffix))


def jira_connector(world, **extra) -> str:
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        c = ConnectorConfig(tenant_id=world["tenant_id"], source_app_id=world["app_id"], kind="jira",
                            config={"base_url": "https://acme.atlassian.example", "email": "bot@acme.test",
                                    "api_token": "t", "workflow_id": world["workflow_id"],
                                    "submit_jql": "status = 'Ready'", **extra})
        db.add(c)
        db.flush()
        return c.id


def test_jira_pages_through_results_and_resubmits_revised_tickets(por_api):
    from connectors import poll_one
    from db.models import Submission
    from db.session import session_scope

    world = make_tenant()
    jira = FakeJira()
    install(jira)
    cid = jira_connector(world)
    first = poll_one(cid)["submitted"]
    assert len(first) == 2  # two pages
    assert any("nextPageToken=1" in u for _, u, _ in jira.calls)
    tracked = state_of(cid)["state"]["tracked"]
    old = tracked["A-1"]["sub"]

    # A comment or status change alone (our own write-back included) does not resubmit.
    jira.issues["A-1"]["fields"]["updated"] = "2026-01-02T10:00:00"
    set_status(old, "changes_requested")
    assert poll_one(cid)["submitted"] == []

    # A revised description after "changes requested" is submitted as a revision.
    jira.issues["A-1"]["fields"].update(description="Fees are fixed at 150 pounds.", updated="2026-01-03T10:00:00")
    (new,) = poll_one(cid)["submitted"]
    with session_scope() as s:
        assert s.get(Submission, new).previous_submission_id == old
    # A revised ticket whose review is still open waits for the decision.
    jira.issues["A-2"]["fields"].update(description="Fees are fixed at 250 pounds.", updated="2026-01-03T10:00:00")
    assert poll_one(cid)["submitted"] == []


class FakePorApi:
    """Stands in for the Proof of Review API when testing write-back."""

    def __init__(self, sub: dict):
        self.sub = sub

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evidence"):
            return httpx.Response(200, content=b"%PDF-1.4 fake")
        return httpx.Response(200, json=self.sub)


@pytest.fixture()
def fake_api(env):
    from connectors import base

    def use(sub):
        base.set_por_client_factory(lambda app_id, tenant_id: httpx.Client(
            transport=httpx.MockTransport(FakePorApi(sub)), base_url="http://por.test"))

    yield use
    base.set_transport(None)
    base.set_por_client_factory(None)


def test_jira_write_back_skips_steps_already_done(fake_api):
    from connectors.base import ConnectorError
    from connectors.jira import JiraConnector
    from db.models import ConnectorConfig
    from db.session import session_scope

    world = make_tenant()
    jira = FakeJira()
    jira.fail_transition = 1
    install(jira)
    cid = jira_connector(world, transitions={"approved": "31"})
    fake_api({"id": "sub-1", "status": "approved", "metadata": {"jira_issue_key": "A-1"},
              "decision": {"reason": "ok", "assurance": {"score": 90}}})
    with session_scope(world["tenant_id"]) as s, pytest.raises(ConnectorError):
        JiraConnector(s.get(ConnectorConfig, cid)).on_decided("sub-1")
    assert jira.posts("/comment") == 1 and jira.posts("/attachments") == 0
    with session_scope(world["tenant_id"]) as s:  # the event is retried
        JiraConnector(s.get(ConnectorConfig, cid)).on_decided("sub-1")
    assert jira.posts("/comment") == 1  # not commented twice
    assert jira.posts("/transitions") == 2 and jira.posts("/attachments") == 1
    assert state_of(cid)["state"]["writeback"]["sub-1:approved"] == {"comment": True, "transition": True, "attachment": True}


# --- Azure DevOps -----------------------------------------------------------------------


class FakeAdo:
    def __init__(self, n: int = 3):
        self.items = {i: {"id": i, "rev": 1, "fields": {"System.Title": f"Item {i}", "System.Rev": 1,
                                                       "System.Description": f"<p>Premium {i}00 pounds.</p>"}}
                      for i in range(1, n + 1)}
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail_patch = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url, request.content))
        if "/_apis/wit/wiql" in url:
            return httpx.Response(200, json={"workItems": [{"id": i} for i in self.items]})
        if "/_apis/wit/workitems?" in url and request.method == "GET":
            ids = [int(i) for i in request.url.params["ids"].split(",")]
            return httpx.Response(200, json={"value": [self.items[i] for i in ids if i in self.items]})
        if "/_apis/wit/attachments" in url:
            return httpx.Response(201, json={"id": "att", "url": "https://dev.azure.com/org/_apis/wit/attachments/att"})
        if request.method == "PATCH" and self.fail_patch:
            self.fail_patch -= 1
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={})


def ado_connector(world) -> str:
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        c = ConnectorConfig(tenant_id=world["tenant_id"], source_app_id=world["app_id"], kind="azure_devops",
                            config={"organization": "org", "project": "proj", "pat": "p", "workflow_id": world["workflow_id"],
                                    "submit_wiql": "SELECT [System.Id] FROM WorkItems", "states": {"approved": "Done"}})
        db.add(c)
        db.flush()
        return c.id


def test_azure_devops_batches_fetches_and_resubmits_revisions(por_api, monkeypatch):
    from connectors import azure_devops, poll_one
    from db.models import Submission
    from db.session import session_scope

    monkeypatch.setattr(azure_devops, "BATCH", 2)
    world = make_tenant()
    ado = FakeAdo(3)
    install(ado)
    cid = ado_connector(world)
    assert len(poll_one(cid)["submitted"]) == 3
    batches = [u for m, u, _ in ado.calls if m == "GET" and "/_apis/wit/workitems?" in u]
    assert len(batches) == 2  # ids fetched 2 at a time, not one request each
    old = state_of(cid)["state"]["tracked"]["1"]["sub"]

    ado.items[1]["fields"]["System.Rev"] = 2  # e.g. our own history entry: same content
    set_status(old, "changes_requested")
    assert poll_one(cid)["submitted"] == []
    ado.items[1]["fields"].update({"System.Rev": 3, "System.Description": "<p>Premium 150 pounds.</p>"})
    (new,) = poll_one(cid)["submitted"]
    with session_scope() as s:
        assert s.get(Submission, new).previous_submission_id == old


def test_azure_devops_write_back_uploads_the_evidence_once(fake_api):
    from connectors.azure_devops import AzureDevOpsConnector
    from connectors.base import ConnectorError
    from db.models import ConnectorConfig
    from db.session import session_scope

    world = make_tenant()
    ado = FakeAdo(1)
    ado.fail_patch = 1
    install(ado)
    cid = ado_connector(world)
    fake_api({"id": "sub-9", "status": "approved", "metadata": {"ado_work_item_id": "1"}, "decision": {}})
    with session_scope(world["tenant_id"]) as s, pytest.raises(ConnectorError):
        AzureDevOpsConnector(s.get(ConnectorConfig, cid)).on_decided("sub-9")
    for _ in range(2):  # retried, then delivered again: nothing is repeated once done
        with session_scope(world["tenant_id"]) as s:
            AzureDevOpsConnector(s.get(ConnectorConfig, cid)).on_decided("sub-9")
    uploads = [u for m, u, _ in ado.calls if m == "POST" and "/attachments" in u]
    patches = [u for m, u, _ in ado.calls if m == "PATCH"]
    assert len(uploads) == 1 and len(patches) == 2


# --- Teams ------------------------------------------------------------------------------


class TeamsSink:
    def __init__(self):
        self.cards: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.cards.append(json.loads(request.content))
        return httpx.Response(200, json={})

    def texts(self) -> list[str]:
        return [json.dumps(c) for c in self.cards]


def teams_connector(world) -> str:
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        c = ConnectorConfig(tenant_id=world["tenant_id"], source_app_id=world["app_id"], kind="teams",
                            config={"webhook_url": "https://teams.example/webhook", "app_base_url": "https://por.example"})
        db.add(c)
        db.flush()
        return c.id


def test_teams_assigned_card_survives_a_missing_reviewer(client):
    from connectors import teams

    world = make_tenant()
    sink = TeamsSink()
    install(sink)
    teams_connector(world)
    teams.on_task_assigned({"tenant_id": world["tenant_id"], "reviewer_id": "gone", "submission_id": "none",
                            "task_id": "t1", "stage": "Review"})
    assert sink.cards and "There is a Review task" in sink.texts()[0]


def test_teams_sends_one_sla_reminder_per_task(client):
    from connectors import teams
    from db.models import ReviewTask, utcnow
    from db.session import session_scope
    from sqlalchemy import select

    world = make_tenant()
    sink = TeamsSink()
    install(sink)
    teams_connector(world)
    submit_text(client, world, metadata={"title": "Renewal letter"})
    assert teams.send_sla_nudges() == 0  # just assigned: well inside the SLA
    with session_scope() as s:
        task = s.scalars(select(ReviewTask).where(ReviewTask.tenant_id == world["tenant_id"])).one()
        task.assigned_at = utcnow() - timedelta(hours=20)
        task.due_at = task.assigned_at + timedelta(hours=24)  # 83% of the SLA used
    assert teams.send_sla_nudges() == 1
    assert any("Review due soon" in t and "Renewal letter" in t for t in sink.texts())
    assert teams.send_sla_nudges() == 0  # once per task


# --- Locks ------------------------------------------------------------------------------


def test_a_running_connector_is_not_run_twice(client, por_api):
    from connectors import LOCK_TTL, lock_name, poll_one
    from services import scheduler
    from tests.conftest import auth

    world = make_tenant()
    install(FakeJira())
    cid = jira_connector(world)
    release = scheduler.acquire(lock_name(cid), LOCK_TTL)
    try:
        assert poll_one(cid) == {"skipped": True, "busy": True}
        r = client.post(f"/v1/integrations/connectors/{cid}/run", headers=auth(world["users"]["owner"]["token"]))
        assert r.status_code == 409
    finally:
        release()
    assert len(poll_one(cid)["submitted"]) == 2


def test_scheduler_polls_each_connector_on_its_own_schedule(por_api):
    from services import scheduler

    world = make_tenant()
    jira = FakeJira()
    install(jira)
    first, second = jira_connector(world), jira_connector(world)
    scheduler.poll_connectors(wait=True)
    for cid in (first, second):
        assert state_of(cid)["state"]["tracked"]  # both polled, each on its own pool thread and lock
    searches = sum(1 for _, u, _ in jira.calls if "/search/jql" in u)
    scheduler.poll_connectors(wait=True)  # within the minute: not polled again
    assert sum(1 for _, u, _ in jira.calls if "/search/jql" in u) == searches
    for cid in (first, second):
        scheduler._last_run.pop(f"connector:{cid}", None)
