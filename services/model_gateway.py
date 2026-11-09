"""The one model gateway. Every LLM call in the platform goes through here.

- Provider is a setting (POR_MODEL_PROVIDER): heuristic (offline, deterministic), anthropic, openai.
- A customer-supplied endpoint can be set with POR_MODEL_BASE_URL (anthropic and openai providers).
- Every call is logged with provider, model, agent, latency, outcome and token usage, and stored in model_calls
  (tenant and submission come from call_context) so cost reports can price it.
No provider SDK is imported anywhere else in the codebase.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from services.config import get_settings

log = logging.getLogger("por.model_gateway")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)

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
    calls: list[CallRecord] = field(default_factory=list)

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
        """Run an Agno agent built with this gateway's model. Returns None on failure so callers can fall back."""
        start = time.monotonic()
        result = None
        try:
            result = agent.run(user_input)
            content = result.content
            if isinstance(content, str):
                content = output_schema.model_validate_json(content)
            elif not isinstance(content, output_schema):
                content = output_schema.model_validate(content)
            self._log(agent_name, True, start, len(user_input), usage=usage_of(result))
            return content
        except Exception as exc:  # noqa: BLE001
            self._log(agent_name, False, start, len(user_input), str(exc), usage=usage_of(result))
            return None

    def _log(self, agent: str, ok: bool, start: float, chars: int, error: str | None = None, usage: Usage | None = None) -> None:
        usage = usage or Usage()
        rec = CallRecord(agent, self.provider, self.model_id, ok, int((time.monotonic() - start) * 1000), chars, error, usage)
        self.calls.append(rec)
        if len(self.calls) > 1000:
            del self.calls[:500]
        log.info(
            "model_call agent=%s provider=%s model=%s ok=%s latency_ms=%s input_chars=%s in_tok=%s out_tok=%s error=%s",
            rec.agent, rec.provider, rec.model, rec.ok, rec.latency_ms, rec.input_chars, usage.input_tokens,
            usage.output_tokens, rec.error,
        )
        from services import metrics

        metrics.inc("por_model_calls_total", {"provider": rec.provider, "outcome": "ok" if ok else "failed"})
        record_call(rec)


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
