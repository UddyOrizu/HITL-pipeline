"""Operations fixes: object storage, the worker (Redis outages, malformed and reclaimed messages, dead letters,
heartbeat, metrics), event publishing retries, cluster-wide scheduling, token clean-up, the hosted MCP server,
integrations API details and the LibreOffice worker."""

from __future__ import annotations

import base64
import importlib.util
import json
import socket
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import auth, make_tenant

# --- Storage ----------------------------------------------------------------------------


class ClientError(Exception):
    """Shaped like botocore's ClientError."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self, head_error: Exception | None = None):
        self.head_error = head_error
        self.created = []
        self.objects: dict[str, dict] = {}

    def head_bucket(self, Bucket):  # noqa: N803
        if self.head_error:
            raise self.head_error

    def create_bucket(self, Bucket):  # noqa: N803
        self.created.append(Bucket)

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs


@pytest.fixture()
def s3_env(env, monkeypatch):
    from services.config import get_settings

    monkeypatch.setenv("POR_S3_BUCKET", "por-files")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_s3_sends_the_encryption_header_only_when_configured(s3_env):
    from services.config import get_settings
    from services.storage import S3Storage

    fake = FakeS3()
    S3Storage(client=fake).put("a/b.txt", b"x", "text/plain")
    assert fake.objects["a/b.txt"]["ServerSideEncryption"] == "AES256"

    s3_env.setenv("POR_S3_SERVER_SIDE_ENCRYPTION", "")  # MinIO without a KMS
    get_settings.cache_clear()
    fake = FakeS3()
    S3Storage(client=fake).put("a/b.txt", b"x", "text/plain")
    assert "ServerSideEncryption" not in fake.objects["a/b.txt"]


def test_s3_creates_the_bucket_only_when_it_is_missing(s3_env):
    from services.storage import S3Storage

    assert FakeS3().created == []
    missing = FakeS3(head_error=ClientError("404", 404))
    S3Storage(client=missing)
    assert missing.created == ["por-files"]
    for error in (ClientError("403", 403), ConnectionError("endpoint down")):
        denied = FakeS3(head_error=error)
        with pytest.raises(type(error)):
            S3Storage(client=denied)  # a credentials or network problem must surface
        assert denied.created == []


class AzureError(Exception):
    def __init__(self, status_code: int):
        super().__init__(status_code)
        self.status_code = status_code


class FakeContainer:
    def __init__(self, exists: bool, create_error: Exception | None = None):
        self._exists = exists
        self.create_error = create_error
        self.created = 0
        self.blobs: dict[str, bytes] = {}

    def exists(self):
        return self._exists

    def create_container(self):
        self.created += 1
        if self.create_error:
            raise self.create_error

    def upload_blob(self, name, data, overwrite, content_settings):
        self.blobs[name] = data

    def download_blob(self, name):
        data = self.blobs[name]
        return type("D", (), {"readall": lambda self: data})()


class FakeBlobService:
    def __init__(self, container: FakeContainer):
        self.container = container

    def get_container_client(self, name):
        return self.container


def test_azure_storage_creates_the_container_only_when_missing(env, monkeypatch):
    from services.config import get_settings
    from services.storage import AzureBlobStorage

    monkeypatch.setenv("POR_AZURE_STORAGE_ACCOUNT_URL", "https://acct.blob.core.windows.net/")
    get_settings.cache_clear()
    present = FakeContainer(exists=True)
    store = AzureBlobStorage(service=FakeBlobService(present))
    assert present.created == 0
    uri = store.put("k/file.pdf", b"%PDF", "application/pdf")
    assert uri == "azblob://por-files/k/file.pdf" and store.get(uri) == b"%PDF"

    missing = FakeContainer(exists=False, create_error=AzureError(409))  # another replica won the race
    AzureBlobStorage(service=FakeBlobService(missing))
    assert missing.created == 1
    with pytest.raises(AzureError):
        AzureBlobStorage(service=FakeBlobService(FakeContainer(exists=False, create_error=AzureError(403))))


def test_local_storage_refuses_keys_outside_its_root(tmp_path):
    from services.storage import LocalStorage

    store = LocalStorage(str(tmp_path / "storage"))
    (tmp_path / "storage-evil").mkdir()
    for key in ("../storage-evil/x", "../x", "a/../../x"):
        with pytest.raises(ValueError):
            store.put(key, b"x")
    assert store.get(store.put("ok/x.txt", b"x")) == b"x"


# --- Worker -----------------------------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.zset: dict[str, float] = {}
        self.kv: dict[str, bytes] = {}
        self.acked: list = []
        self.claimable: list = []
        self.deliveries: dict[str, int] = {}
        self.fail_xadd = 0

    def xadd(self, stream, fields, maxlen=None):
        if self.fail_xadd:
            self.fail_xadd -= 1
            raise ConnectionError("redis down")
        rows = self.streams.setdefault(stream, [])
        msg_id = f"{len(rows) + 1}-0"
        rows.append((msg_id, dict(fields)))
        return msg_id

    def xack(self, stream, group, msg_id):
        self.acked.append((stream, msg_id))

    def xlen(self, stream):
        return len(self.streams.get(stream, []))

    def xrange(self, stream, min="-", max="+", count=None):  # noqa: A002
        rows = self.streams.get(stream, [])
        return [r for r in rows if min in ("-", r[0])]

    def xrevrange(self, stream, count=None):
        return list(reversed(self.streams.get(stream, [])))[:count]

    def xdel(self, stream, *ids):
        before = len(self.streams.get(stream, []))
        self.streams[stream] = [r for r in self.streams.get(stream, []) if r[0] not in ids]
        return before - len(self.streams[stream])

    def delete(self, key):
        self.streams.pop(key, None)
        self.kv.pop(key, None)

    def xautoclaim(self, stream, group, consumer, min_idle_time, start_id="0-0", count=None):
        claimed = [(i, f) for s, i, f in self.claimable if s == stream]
        self.claimable = [c for c in self.claimable if c[0] != stream]
        return ["0-0", claimed, []]

    def xpending_range(self, stream, group, min, max, count):  # noqa: A002
        return [{"message_id": min, "times_delivered": self.deliveries.get(min, 1)}]

    def xinfo_groups(self, stream):
        return [{"name": b"por-workers", "pending": 2, "lag": 5}]

    def zadd(self, key, mapping):
        self.zset.update(mapping)

    def zcard(self, key):
        return len(self.zset)

    def zrangebyscore(self, key, lo, hi, start=0, num=100):
        return [m for m, score in self.zset.items() if score <= hi][:num]

    def zrem(self, key, member):
        return self.zset.pop(member, None) is not None

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value.encode() if isinstance(value, str) else value
        return True

    def get(self, key):
        return self.kv.get(key)

    def expire(self, key, ttl):
        return key in self.kv


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


def dead(fake) -> list[dict]:
    from services import jobs

    return [f for _, f in fake.streams.get(jobs.DEAD_STREAM, [])]


def test_malformed_messages_are_dead_lettered_at_once(redis_mode):
    from services import jobs, worker

    worker.handle(redis_mode, jobs.EVENT_STREAM, "1-0", {"event": "x", "payload": "{not json", "attempt": "1"})
    worker.handle(redis_mode, jobs.JOB_STREAM, "2-0", {"name": "process_submission", "attempt": "1"})  # no kwargs
    assert [d["stream"] for d in dead(redis_mode)] == [jobs.EVENT_STREAM, jobs.JOB_STREAM]
    assert "malformed" in dead(redis_mode)[0]["error"] and not redis_mode.zset  # no retries scheduled
    assert len(redis_mode.acked) == 2


def test_reclaimed_messages_count_earlier_deliveries_as_attempts(redis_mode, monkeypatch):
    from services import jobs, worker
    from services.config import get_settings

    monkeypatch.setenv("POR_JOB_MAX_ATTEMPTS", "3")
    get_settings.cache_clear()
    seen = []
    jobs.subscribe("test.reclaim", seen.append)
    payload = json.dumps({"n": 1})
    # Delivered twice before (two workers died on it): this is attempt 3 of 3, so it still runs.
    redis_mode.claimable = [(jobs.EVENT_STREAM, "5-0", {b"event": b"test.reclaim", b"payload": payload.encode(), b"attempt": b"1"})]
    redis_mode.deliveries["5-0"] = 3
    assert worker.reclaim(redis_mode, "me") == 1 and seen == [{"n": 1}]
    # A fourth delivery is past the limit: dead-lettered without running it again.
    redis_mode.claimable = [(jobs.EVENT_STREAM, "6-0", {"event": "test.reclaim", "payload": payload, "attempt": "1"})]
    redis_mode.deliveries["6-0"] = 4
    worker.reclaim(redis_mode, "me")
    assert seen == [{"n": 1}]
    (d,) = dead(redis_mode)
    assert d["attempt"] == "4" and "stopped" in d["error"] and (jobs.EVENT_STREAM, "6-0") in redis_mode.acked


def test_dead_letter_cli_lists_replays_and_purges(redis_mode, capsys):
    from services import jobs, worker

    jobs.dead_letter(redis_mode, jobs.EVENT_STREAM, {"event": "e1", "payload": "{}", "attempt": "5", "only": "h"}, "boom")
    jobs.dead_letter(redis_mode, jobs.JOB_STREAM, {"name": "j1", "kwargs": "{}", "attempt": "5"}, "bang")
    assert worker.cli(["dead", "list"]) == 0
    out = capsys.readouterr().out
    assert "e1" in out and "boom" in out and "j1" in out
    first_id = redis_mode.streams[jobs.DEAD_STREAM][0][0]
    assert worker.cli(["dead", "replay", first_id]) == 0
    (_, replayed), = redis_mode.streams[jobs.EVENT_STREAM]
    assert replayed == {"event": "e1", "payload": "{}", "attempt": "1", "only": "h"}
    assert len(dead(redis_mode)) == 1
    assert worker.cli(["dead", "purge"]) == 0 and dead(redis_mode) == []
    assert worker.cli(["dead"]) == 2


def test_worker_survives_redis_outages(redis_mode, monkeypatch):
    import redis as redis_lib

    from services import scheduler, worker

    monkeypatch.setattr(scheduler, "periodic", lambda: None)
    calls = {"n": 0}

    def xgroup_create(*args, **kwargs):
        return True

    def xreadgroup(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise redis_lib.exceptions.ConnectionError("Connection refused")
        raise KeyboardInterrupt  # end the test loop

    redis_mode.xgroup_create = xgroup_create
    redis_mode.xreadgroup = xreadgroup
    sleeps = []
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    with pytest.raises(KeyboardInterrupt):
        worker.run_forever()
    assert sleeps == [1, 2]  # backed off and kept going


def test_heartbeat_health_check(tmp_path, monkeypatch):
    from services import worker

    monkeypatch.setattr(worker, "HEARTBEAT_FILE", tmp_path / "hb")
    assert worker.cli(["health"]) == 1
    worker.heartbeat()
    assert worker.healthy() and worker.cli(["health"]) == 0


def test_publishing_retries_when_redis_blips(redis_mode, monkeypatch):
    from services import jobs

    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    seen = []
    jobs.subscribe("test.blip", seen.append)
    redis_mode.fail_xadd = 2
    jobs.publish("test.blip", {"n": 1})
    assert seen == [] and redis_mode.streams[jobs.EVENT_STREAM][0][1]["event"] == "test.blip"
    redis_mode.fail_xadd = 10  # Redis is down: handlers run here rather than the event being lost
    jobs.publish("test.blip", {"n": 2})
    assert seen == [{"n": 2}]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_worker_serves_metrics_with_stream_lengths(redis_mode, monkeypatch):
    from services import jobs, worker
    from services.config import get_settings

    monkeypatch.setenv("POR_METRICS_TOKEN", "scrape-me")
    get_settings.cache_clear()
    jobs.dead_letter(redis_mode, jobs.EVENT_STREAM, {"event": "e", "payload": "{}"}, "x")
    port = _free_port()
    worker.start_metrics_server(port)
    url = f"http://127.0.0.1:{port}/metrics"
    for _ in range(50):
        try:
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(url, timeout=2)
            break
        except urllib.error.URLError:
            time.sleep(0.05)
    assert err.value.code == 401
    body = urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer scrape-me"}),
                                  timeout=5).read().decode()
    assert "por_dead_letters 1" in body
    assert 'por_stream_pending{stream="por:events"} 2' in body and 'por_stream_lag{stream="por:jobs"} 5' in body


# --- Scheduler --------------------------------------------------------------------------


def test_task_frequency_is_cluster_wide_with_redis(redis_mode):
    from services import scheduler

    runs = []
    assert scheduler.run_task("hourly", lambda: runs.append(1), every_seconds=3600)
    scheduler._last_run.clear()  # another replica: its own clock says it never ran
    assert not scheduler.run_task("hourly", lambda: runs.append(1), every_seconds=3600)
    assert runs == [1]
    assert scheduler.acquire("held", 60) is not None and scheduler.acquire("held", 60) is None


def test_token_cleanup_drops_used_and_expired_reset_links(client):
    from sqlalchemy import select

    from db.models import PasswordResetToken, utcnow
    from db.session import session_scope
    from services import scheduler

    world = make_tenant()
    uid = world["users"]["r1"]["id"]
    now = utcnow()
    with session_scope() as s:
        for name, expires, used in (("expired", now - timedelta(minutes=1), None),
                                    ("used", now + timedelta(minutes=30), now),
                                    ("valid", now + timedelta(minutes=30), None)):
            s.add(PasswordResetToken(tenant_id=world["tenant_id"], user_id=uid, token_hash=name, expires_at=expires,
                                     used_at=used))
    scheduler.cleanup_tokens()
    with session_scope() as s:
        assert [t.token_hash for t in s.scalars(select(PasswordResetToken)).all()] == ["valid"]


# --- Integrations API -------------------------------------------------------------------


def test_app_patch_ignores_nulls_and_lists_carry_totals(client):
    world = make_tenant()
    owner = auth(world["users"]["owner"]["token"])
    app = client.post("/v1/integrations/apps", headers=owner, json={"name": "CRM"}).json()
    r = client.patch(f"/v1/integrations/apps/{app['id']}", headers=owner, json={"name": None, "active": None})
    assert r.status_code == 200 and r.json()["name"] == "CRM" and r.json()["active"] is True
    assert client.patch(f"/v1/integrations/apps/{app['id']}", headers=owner, json={"name": "  "}).status_code == 422
    r = client.get("/v1/integrations/apps?limit=1", headers=owner)
    assert r.headers["X-Total-Count"] == "2" and r.headers["X-Next-Offset"] == "1" and len(r.json()) == 1
    for path in ("/v1/integrations/connectors", "/v1/integrations/workflows", "/v1/integrations/deliveries"):
        r = client.get(path, headers=owner)
        assert r.status_code == 200 and "X-Total-Count" in r.headers and isinstance(r.json(), list)


# --- Hosted MCP server ------------------------------------------------------------------


def test_hosted_mcp_needs_a_token_off_localhost():
    from mcp_server import server

    srv = server.build("submitter", hosted=True)
    with pytest.raises(SystemExit):
        server.http_app(srv, "0.0.0.0", None)  # noqa: S104
    assert isinstance(server.http_app(srv, "0.0.0.0", "t" * 40), server.BearerAuth)  # noqa: S104
    assert not isinstance(server.http_app(srv, "127.0.0.1", None), server.BearerAuth)
    server.build("submitter")


def test_bearer_auth_checks_every_request():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from mcp_server.server import BearerAuth

    inner = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("ok"), methods=["GET", "POST"])])
    c = TestClient(BearerAuth(inner, "s3cret-token"))
    assert c.post("/mcp").status_code == 401
    assert c.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/mcp", headers={"Authorization": "Bearer s3cret-token"}).text == "ok"


async def test_hosted_mcp_does_not_read_server_files(tmp_path):
    from mcp_server import server, tools

    path = tmp_path / "secret.txt"
    path.write_text("server-side file")
    server.build("submitter", hosted=True)
    try:
        with pytest.raises(tools.ToolFailure, match="file_path is not available"):
            await tools.submit_for_review("wf", file_path=str(path))
    finally:
        server.build("submitter")
    assert tools._allow_file_paths


# --- LibreOffice worker -----------------------------------------------------------------


def _libreoffice_app():
    path = Path(__file__).resolve().parent.parent / "infra" / "libreoffice" / "app.py"
    spec = importlib.util.spec_from_file_location("por_libreoffice_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_libreoffice_worker_limits_uploads_and_converts_off_the_event_loop(monkeypatch):
    from fastapi.testclient import TestClient

    lo = _libreoffice_app()
    monkeypatch.setattr(lo, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(lo, "_convert", lambda data, suffix, to: base64.b64encode(data))
    c = TestClient(lo.app)
    assert c.get("/healthz").json() == {"ok": True}
    assert c.post("/convert?to=pptx", files={"file": ("a.ppt", b"x" * 5000)}).status_code == 413
    r = c.post("/convert?to=pptx", files={"file": ("a.ppt", b"hello")})
    assert r.status_code == 200 and r.content == base64.b64encode(b"hello")
    assert c.post("/convert?to=exe", files={"file": ("a.ppt", b"x")}).status_code == 400
