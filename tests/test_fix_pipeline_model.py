"""Model outages are retried (not hidden behind heuristics), processing failures are classified, LLM inputs are
bounded, and canary spans are flagged exactly like genuine ones."""

import random
import subprocess

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant, submit_text, task_for


class _Run:
    def __init__(self, content, status="COMPLETED"):
        self.content = content
        self.status = status
        self.metrics = None


class _Agent:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def run(self, *_):
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        return self.behaviour


def _llm(monkeypatch, behaviour):
    from services import model_gateway

    gw = model_gateway.ModelGateway("anthropic", "claude-sonnet-5-5", api_key="test")
    model_gateway.set_gateway(gw)
    monkeypatch.setattr("agents.claim_extractor.build", lambda g: _Agent(behaviour))
    return gw


def _sub(sid):
    from db.models import Submission
    from db.session import session_scope

    with session_scope() as db:
        s = db.get(Submission, sid)
        return {"status": s.status, "attempts": s.processing_attempts, "error": s.processing_error,
                "retry_at": s.retry_at}


def _retry_now(sid):
    from db.models import Submission, utcnow
    from db.session import session_scope
    from services import scheduler

    with session_scope() as db:
        db.get(Submission, sid).retry_at = utcnow()
    scheduler.recover_submissions()


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("read timed out"),
    _Run("Error code: 529 - overloaded_error", status="ERROR"),  # how Agno reports a failed run
    _Run("Error code: 503 - service unavailable", status="ERROR"),
])
def test_model_outage_is_retried_with_backoff_then_deferred(client, sink, monkeypatch, failure):
    from services.config import get_settings

    _llm(monkeypatch, failure)
    world = make_tenant()
    sub = submit_text(client, world)
    s = _sub(sub["id"])
    # Not silently flagged by the heuristics: the submission waits for a retry.
    assert s["status"] == "received" and s["retry_at"] is not None and "ModelUnavailable" in s["error"]
    for _ in range(get_settings().processing_max_attempts - 1):
        _retry_now(sub["id"])
    s = _sub(sub["id"])
    assert s["status"] == "deferred" and s["attempts"] == get_settings().processing_max_attempts
    from db.models import AuditEvent
    from db.session import session_scope

    with session_scope() as db:
        assert not db.scalars(select(AuditEvent).where(AuditEvent.action == "risk.flagged")).all()


def test_model_refusing_the_request_defers_at_once(client, sink, monkeypatch):
    _llm(monkeypatch, _Run("Error code: 401 - invalid x-api-key", status="ERROR"))
    world = make_tenant()
    sub = submit_text(client, world)
    s = _sub(sub["id"])
    assert s["status"] == "deferred" and s["attempts"] == 1 and "401" in s["error"]


def test_model_recovering_on_retry_uses_the_llm(client, sink, monkeypatch):
    from agents.schemas import ClaimList, RiskFlagList

    _llm(monkeypatch, httpx.ConnectError("down"))
    monkeypatch.setattr("agents.risk_flagger.run", lambda *a: RiskFlagList(flags=[]))
    world = make_tenant()
    sub = submit_text(client, world)
    assert _sub(sub["id"])["status"] == "received"
    monkeypatch.setattr("agents.claim_extractor.build", lambda g: _Agent(_Run(ClaimList().model_dump_json())))
    _retry_now(sub["id"])
    assert _sub(sub["id"])["status"] == "in_review"
    from db.models import AuditEvent
    from db.session import session_scope

    with session_scope() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "risk.flagged")).one()
        assert ev.payload["engine"] == "llm:anthropic"


def test_classify_provider_errors():
    from services.model_gateway import classify

    class APIStatusError(Exception):
        def __init__(self, status):
            super().__init__(f"status {status}")
            self.status_code = status

    assert classify(APIStatusError(429)).retryable
    assert classify(APIStatusError(500)).retryable
    assert not classify(APIStatusError(400)).retryable
    assert not classify(APIStatusError(401)).retryable
    assert classify(TimeoutError()).retryable
    wrapped = RuntimeError("agno wrapper")
    wrapped.__cause__ = httpx.ConnectError("no route")
    assert classify(wrapped).retryable


def test_processing_failure_classification():
    from services import ingestion, model_gateway, pipeline

    req = httpx.Request("POST", "http://lo/convert")
    assert pipeline.is_transient(subprocess.TimeoutExpired("soffice", 120))
    assert not pipeline.is_transient(subprocess.CalledProcessError(1, "soffice"))
    assert not pipeline.is_transient(httpx.HTTPStatusError("x", request=req, response=httpx.Response(415, request=req)))
    assert pipeline.is_transient(httpx.HTTPStatusError("x", request=req, response=httpx.Response(503, request=req)))
    assert not pipeline.is_transient(FileNotFoundError("original missing"))
    assert pipeline.is_transient(ingestion.ConversionUnavailable("down"))
    assert not pipeline.is_transient(ingestion.ExtractionError("bad file"))
    assert pipeline.is_transient(model_gateway.ModelUnavailable("down"))
    assert not pipeline.is_transient(model_gateway.ModelUnavailable("refused", retryable=False))


def test_conversion_errors_are_typed(env, monkeypatch):
    from services.config import get_settings
    from services.ingestion import conversion
    from services.ingestion.extractors import ExtractionError

    monkeypatch.setattr(conversion.shutil, "which", lambda _: "/usr/bin/soffice")

    def failed(*a, **k):
        raise subprocess.CalledProcessError(77, "soffice")

    monkeypatch.setattr(conversion.subprocess, "run", failed)
    with pytest.raises(ExtractionError):
        conversion.convert_ppt_to_pptx(b"x")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("soffice", 120)

    monkeypatch.setattr(conversion.subprocess, "run", slow)
    with pytest.raises(conversion.ConversionUnavailable):
        conversion.convert_ppt_to_pptx(b"x")

    monkeypatch.setenv("POR_LIBREOFFICE_URL", "http://libreoffice.test")
    get_settings.cache_clear()
    for status, exc in ((422, ExtractionError), (502, conversion.ConversionUnavailable)):
        monkeypatch.setattr(conversion.httpx, "post",
                            lambda *a, _s=status, **k: httpx.Response(_s, request=httpx.Request("POST", "http://x")))
        with pytest.raises(exc):
            conversion.convert_ppt_to_pptx(b"x")


def test_claim_extraction_is_batched(monkeypatch):
    from agents import claim_extractor
    from agents.schemas import Claim, ClaimList

    seen = []

    class GW:
        provider = "anthropic"

        def run_agent(self, agent, name, payload, schema):
            import json

            spans = json.loads(payload)["spans"]
            seen.append(len(spans))
            return ClaimList(claims=[Claim(span_id=s["span_id"], text=s["text"], confidence=0.9) for s in spans])

    monkeypatch.setattr(claim_extractor, "build", lambda g: None)
    spans = [{"span_id": str(i), "text": "word " * 50} for i in range(150)]
    out = claim_extractor.run(GW(), spans)
    assert len(out.claims) == 150 and max(seen) <= claim_extractor.MAX_SPANS and len(seen) >= 3
    assert all(sum(len(s["text"]) for s in b) <= claim_extractor.MAX_CHARS for b in claim_extractor.batches(spans))


def test_canary_generator_samples_the_whole_document_and_rejects_no_op_replacements(monkeypatch):
    from agents import canary_generator
    from agents.schemas import CanaryOut

    spans = [{"span_id": str(i), "text": f"Paragraph {i} says something."} for i in range(300)]
    picked = canary_generator.sample_spans(spans, {"5", "250"}, random.Random(1))
    ids = [int(s["span_id"]) for s in picked]
    assert len(picked) <= canary_generator.MAX_SPANS and max(ids) > 80 and ids == sorted(ids)

    class GW:
        provider = "anthropic"

        def __init__(self, out):
            self.out = out

        def run_agent(self, *a):
            return self.out

    monkeypatch.setattr(canary_generator, "build", lambda g: None)
    small = spans[:5]
    same = CanaryOut(type="factual_error", difficulty="medium", mode="replace", target_span_id="2",
                     text="Paragraph 2 says  something.")
    assert canary_generator.run(GW(same), small, "factual_error", "medium", "general") is None
    changed = same.model_copy(update={"text": "Paragraph 2 says nothing at all."})
    assert canary_generator.run(GW(changed), small, "factual_error", "medium", "general") is not None


def test_canary_span_is_flagged_like_a_genuine_span(client):
    from db.models import Canary, RiskFlag, Span, Workflow
    from db.session import session_scope
    from services import risk

    world = make_tenant(canary_rate=1.0)
    text = ("Revenue rose to £4.2m in 2025 according to the board.\n\n"
            "We recommend the client relies on this to meet its FCA regulatory obligations.\n\n"
            "Margins may possibly improve next year, we believe.\n\n"
            "Headcount grew to 240 staff across 12 offices in 2025.")
    sub = submit_text(client, world, text=text, risk_tier="high")
    with session_scope() as db:
        c = db.scalars(select(Canary).where(Canary.submission_id == sub["id"])).one()
        span = db.get(Span, c.span_id)
        rows = db.scalars(select(RiskFlag).where(RiskFlag.span_id == span.id)).all()
        wf = db.scalars(select(Workflow)).first()
        mandatory = set(wf.risk_policy["mandatory_severities"])
        expected = {(f.category, f.severity, f.rationale) for f in
                    risk.heuristic_flags(risk.heuristic_claims([span]), "", "high", []).flags}
        got = {(r.category, r.severity, r.rationale) for r in rows}
        assert got == expected
        assert all(r.mandatory == (r.severity in mandatory) for r in rows)
    # Flags come back in document order, not with the canary's last.
    who, item = task_for(client, world, sub["id"])
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(world["users"][who]["token"])).json()
    order = [sp["id"] for s in view["document"]["sections"] for sp in s["spans"]]
    positions = [order.index(f["span_id"]) for f in view["flags"]]
    assert positions == sorted(positions)
