"""The one model gateway. Every LLM call in the platform goes through here.

- Provider is a setting (POR_MODEL_PROVIDER): heuristic (offline, deterministic), anthropic, openai.
- A customer-supplied endpoint can be set with POR_MODEL_BASE_URL (anthropic and openai providers).
- Every call is logged with provider, model, agent, latency, outcome and token usage, and stored in model_calls
  (tenant and submission come from call_context) so cost reports can price it.
- A provider outage (connection error, timeout, 429, 5xx) raises ModelUnavailable, so the pipeline retries the
  submission with backoff instead of quietly using the heuristics. Only an unusable answer (one that does not parse
  or match the output schema) makes run_agent return None, so the caller falls back to the heuristic engine; each
  fallback is counted in por_model_fallbacks_total and logged.
No provider SDK is imported anywhere else in the codebase.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from services.config import get_settings

log = logging.getLogger("por.model_gateway")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)


class ModelUnavailable(RuntimeError):
    """The model provider could not answer. `retryable` is False for errors that fail the same way every time
    (a rejected request or bad credentials): the submission is deferred at once instead of retried."""

    def __init__(self, message: str, retryable: bool = True, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


# Exception class names (anywhere in the cause chain) that mean the provider or the network is unavailable.
_UNAVAILABLE_NAMES = {
    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "OverloadedError",
    "ServiceUnavailableError", "ModelRateLimitError", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "TimeoutException", "ReadError", "WriteError", "RemoteProtocolError", "NetworkError",
    "TransportError", "ProxyError", "TimeoutError", "ConnectionError", "ConnectionResetError",
    "ConnectionRefusedError", "ConnectionAbortedError", "BrokenPipeError",
}
# Answers that fail the same way on every attempt.
_PERMANENT_STATUS = {400, 401, 403, 404, 413, 422}
_STATUS_RE = re.compile(r"(?:error code|status(?: code)?)[:= ]+(\d{3})", re.I)


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _chain(exc: BaseException | None):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def is_output_error(exc: BaseException) -> bool:
    """The provider answered, but the answer does not parse or match the schema."""
    from pydantic import ValidationError

    return isinstance(exc, ValidationError | json.JSONDecodeError | ValueError | TypeError)


def classify(exc: BaseException) -> ModelUnavailable:
    """Turn a provider or transport exception into ModelUnavailable: retryable unless it is a permanent 4xx."""
    status = None
    for e in _chain(exc):
        status = status or _status_of(e)
        if type(e).__name__ in _UNAVAILABLE_NAMES or isinstance(e, TimeoutError | ConnectionError):
            return ModelUnavailable(f"{type(e).__name__}: {e}", True, status)
    if status is None:
        m = _STATUS_RE.search(str(exc))
        status = int(m.group(1)) if m else None
    if status is not None:
        return ModelUnavailable(f"provider returned {status}: {exc}", status not in _PERMANENT_STATUS, status)
    # An error we cannot place: treat it as an outage (retried, then deferred) rather than hide it behind heuristics.
    return ModelUnavailable(f"{type(exc).__name__}: {exc}", True, None)


def _classify_message(message: str) -> ModelUnavailable:
    """Agno reports a failed run (status ERROR, content = the error text) instead of raising."""
    m = _STATUS_RE.search(message)
    status = int(m.group(1)) if m else None
    if status is not None:
        return ModelUnavailable(f"provider returned {status}: {message[:500]}", status not in _PERMANENT_STATUS, status)
    return ModelUnavailable(message[:500] or "model run failed", True, None)


def _run_failed(result) -> bool:
    status = getattr(result, "status", None)
    return status is not None and str(getattr(status, "value", status)).lower() == "error"

_context: contextvars.ContextVar[tuple[str | None, str | None, list | None]] = contextvars.ContextVar(
    "por_model_ctx", default=(None, None, None))


@contextlib.contextmanager
def call_context(tenant_id: str | None, submission_id: str | None = None):
    """Attribute model calls made inside this block to a tenant and submission (for cost reports).

    Yields a list that collects the calls; pass it to persist() with your session (so usage is stored in the same
    transaction, even on SQLite). Calls made outside any context are not stored."""
    buffer: list = []
    token = _context.set((tenant_id, submission_id, buffer))
    try:
        yield buffer
    finally:
        _context.reset(token)


def load_prompt(name: str) -> str:
    """Prompts live in prompts/<name>.md and are loaded by file name."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def usage_of(result) -> Usage:
    """Token usage from an Agno run result (RunOutput.metrics), tolerant of missing fields."""
    m = getattr(result, "metrics", None)
    if m is None:
        return Usage()

    def val(name: str) -> int:
        v = getattr(m, name, None) if not isinstance(m, dict) else m.get(name)
        if isinstance(v, list):  # older Agno versions keep a list per model call
            v = sum(x or 0 for x in v)
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return Usage(val("input_tokens"), val("output_tokens"), val("cache_read_tokens"), val("cache_write_tokens"))


@dataclass
class CallRecord:
    agent: str
    provider: str
    model: str
    ok: bool
    latency_ms: int
    input_chars: int
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


@dataclass
class ModelGateway:
    provider: str
    model_id: str
    api_key: str | None = None
    base_url: str | None = None

    @property
    def enabled(self) -> bool:
        return self.provider != "heuristic"

    def agno_model(self):
        """Build the Agno model object for the configured provider."""
        if self.provider == "anthropic":
            from agno.models.anthropic import Claude

            kwargs: dict = {"id": self.model_id}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["client_params"] = {"base_url": self.base_url}
            return Claude(**kwargs)
        if self.provider == "openai":
            from agno.models.openai import OpenAIChat

            kwargs = {"id": self.model_id}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            return OpenAIChat(**kwargs)
        raise RuntimeError(f"provider '{self.provider}' has no model")

    def run_agent(self, agent, agent_name: str, user_input: str, output_schema: type[T]) -> T | None:
        """Run an Agno agent built with this gateway's model.

        Raises ModelUnavailable when the provider cannot answer (outage, timeout, rate limit, 5xx, or a request it
        refuses). Returns None only when the answer cannot be parsed into output_schema, so the caller can fall
        back to the heuristic engine."""
        start = time.monotonic()
        result = None
        try:
            result = agent.run(user_input)
        except Exception as exc:  # noqa: BLE001
            err = classify(exc)
            self._log(agent_name, False, start, len(user_input), str(err), usage=usage_of(result))
            raise err from exc
        if _run_failed(result):
            err = _classify_message(str(getattr(result, "content", "") or ""))
            self._log(agent_name, False, start, len(user_input), str(err), usage=usage_of(result))
            raise err
        try:
            content = result.content
            if isinstance(content, str):
                content = output_schema.model_validate_json(content)
            elif not isinstance(content, output_schema):
                content = output_schema.model_validate(content)
        except Exception as exc:  # noqa: BLE001
            if not is_output_error(exc):
                raise
            self._log(agent_name, False, start, len(user_input), f"invalid output: {exc}", usage=usage_of(result))
            record_fallback(agent_name, self.provider, "invalid_output", str(exc))
            return None
        self._log(agent_name, True, start, len(user_input), usage=usage_of(result))
        return content

    def _log(self, agent: str, ok: bool, start: float, chars: int, error: str | None = None, usage: Usage | None = None) -> None:
        usage = usage or Usage()
        rec = CallRecord(agent, self.provider, self.model_id, ok, int((time.monotonic() - start) * 1000), chars, error, usage)
        log.info(
            "model_call agent=%s provider=%s model=%s ok=%s latency_ms=%s input_chars=%s in_tok=%s out_tok=%s error=%s",
            rec.agent, rec.provider, rec.model, rec.ok, rec.latency_ms, rec.input_chars, usage.input_tokens,
            usage.output_tokens, rec.error,
        )
        from services import metrics

        metrics.inc("por_model_calls_total", {"provider": rec.provider, "outcome": "ok" if ok else "failed"})
        record_call(rec)


def record_fallback(agent: str, provider: str, reason: str, detail: str = "") -> None:
    """Count and log a fall back to the heuristic engine. Callers with a session also write an audit event."""
    from services import metrics

    metrics.inc("por_model_fallbacks_total", {"agent": agent, "reason": reason})
    log.warning("model_fallback agent=%s provider=%s reason=%s detail=%s", agent, provider, reason, detail[:300])


def record_call(rec: CallRecord) -> None:
    tenant_id, submission_id, buffer = _context.get()
    if tenant_id and buffer is not None:
        buffer.append((tenant_id, submission_id, rec))


def persist(session, buffer: list) -> int:
    """Add buffered calls to the session (call again after a rollback: the usage was still spent)."""
    from db.models import ModelCall

    for tenant_id, submission_id, rec in buffer:
        session.add(ModelCall(
            tenant_id=tenant_id, submission_id=submission_id, agent=rec.agent, provider=rec.provider, model=rec.model,
            ok=rec.ok, latency_ms=rec.latency_ms, input_tokens=rec.usage.input_tokens,
            output_tokens=rec.usage.output_tokens, cache_read_tokens=rec.usage.cache_read_tokens,
            cache_write_tokens=rec.usage.cache_write_tokens, error=(rec.error or None) and rec.error[:2000],
        ))
    n = len(buffer)
    buffer.clear()
    return n


_gateway: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        s = get_settings()
        _gateway = ModelGateway(s.model_provider, s.model_id, s.model_api_key, s.model_base_url)
    return _gateway


def set_gateway(gateway: ModelGateway | None) -> None:
    global _gateway
    _gateway = gateway
