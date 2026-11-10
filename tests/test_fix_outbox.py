"""Transactional outbox: events committed with a change reach Redis even if the first copy fails."""

from datetime import timedelta

import pytest
from sqlalchemy import select


class FakeRedis:
    def __init__(self):
        self.added: list[tuple[str, dict]] = []
        self.down = False

    def xadd(self, stream, fields, **kwargs):
        if self.down:
            raise ConnectionError("redis is down")
        self.added.append((stream, dict(fields)))
        return b"1-0"


@pytest.fixture()
def redis_jobs(env, monkeypatch):
    from services import jobs
    from services.config import get_settings

    monkeypatch.setenv("POR_REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setenv("POR_INLINE_JOBS", "false")
    get_settings.cache_clear()
    fake = FakeRedis()
    monkeypatch.setattr(jobs, "_redis", lambda: fake)
    yield fake
    get_settings.cache_clear()


def _rows():
    from db.models import OutboxMessage
    from db.session import session_scope

    with session_scope() as s:
        return [(r.fields["event"], r.published_at is not None, r.attempts) for r in s.scalars(select(OutboxMessage))]


def _age_rows(seconds: int):
    from db.models import OutboxMessage
    from db.session import session_scope

    with session_scope() as s:
        for r in s.scalars(select(OutboxMessage)):
            r.created_at -= timedelta(seconds=seconds)


def test_event_is_written_with_the_transaction_and_copied_after_commit(redis_jobs):
    from db.session import session_scope
    from services import jobs

    with session_scope() as s:
        jobs.publish("submission.received", {"submission_id": "s1"}, session=s)
        assert redis_jobs.added == []  # nothing leaves before the commit
    assert [f["event"] for _, f in redis_jobs.added] == ["submission.received"]
    assert _rows() == [("submission.received", True, 0)]


def test_rolled_back_change_publishes_nothing(redis_jobs):
    from db.session import session_scope
    from services import jobs

    with pytest.raises(RuntimeError):
        with session_scope() as s:
            jobs.publish("submission.received", {"submission_id": "s1"}, session=s)
            raise RuntimeError("the change failed")
    assert redis_jobs.added == [] and _rows() == []


def test_relay_copies_events_the_fast_path_missed(redis_jobs):
    from db.session import session_scope
    from services import jobs

    redis_jobs.down = True
    with session_scope() as s:
        jobs.publish("submission.decided", {"submission_id": "s2"}, session=s)
    assert redis_jobs.added == [] and _rows() == [("submission.decided", False, 1)]

    redis_jobs.down = False
    assert jobs.relay_outbox() == 0  # too recent: the fast path may still be working on it
    _age_rows(60)
    assert jobs.relay_outbox() == 1
    assert [f["event"] for _, f in redis_jobs.added] == ["submission.decided"]
    assert _rows() == [("submission.decided", True, 1)]
    assert jobs.relay_outbox() == 0  # never sent twice by the relay


def test_published_rows_are_pruned(redis_jobs):
    from db.models import OutboxMessage
    from db.session import session_scope
    from services import jobs

    with session_scope() as s:
        jobs.publish("submission.received", {"submission_id": "s3"}, session=s)
    with session_scope() as s:
        for r in s.scalars(select(OutboxMessage)):
            r.published_at -= timedelta(days=jobs.OUTBOX_KEEP_DAYS + 1)
    jobs.relay_outbox()
    assert _rows() == []


def test_inline_mode_does_not_use_the_outbox(env):
    from db.session import session_scope
    from services import jobs

    seen = []
    jobs.subscribe("test.inline_outbox", seen.append)
    with session_scope() as s:
        jobs.publish("test.inline_outbox", {"x": 1}, session=s)
    assert seen == [{"x": 1}] and _rows() == []
