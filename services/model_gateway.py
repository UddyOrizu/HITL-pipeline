"""The one model gateway. Every LLM call in the platform goes through here.

- Provider is a setting (POR_MODEL_PROVIDER): heuristic (offline, deterministic), anthropic, openai.
- A customer-supplied endpoint can be set with POR_MODEL_BASE_URL.
- Every call is logged with provider, model, agent, latency and outcome.
No provider SDK is imported anywhere else in the codebase.
"""

from __future__ import annotations

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


def load_prompt(name: str) -> str:
    """Prompts live in prompts/<name>.md and are loaded by file name."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


@dataclass
class CallRecord:
    agent: str
    provider: str
    model: str
    ok: bool
    latency_ms: int
    input_chars: int
    error: str | None = None


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

            kwargs = {"id": self.model_id}
            if self.api_key:
                kwargs["api_key"] = self.api_key
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
        try:
            result = agent.run(user_input)
            content = result.content
            if isinstance(content, str):
                content = output_schema.model_validate_json(content)
            elif not isinstance(content, output_schema):
                content = output_schema.model_validate(content)
            self._log(agent_name, True, start, len(user_input))
            return content
        except Exception as exc:  # noqa: BLE001
            self._log(agent_name, False, start, len(user_input), str(exc))
            return None

    def _log(self, agent: str, ok: bool, start: float, chars: int, error: str | None = None) -> None:
        rec = CallRecord(agent, self.provider, self.model_id, ok, int((time.monotonic() - start) * 1000), chars, error)
        self.calls.append(rec)
        if len(self.calls) > 1000:
            del self.calls[:500]
        log.info(
            "model_call agent=%s provider=%s model=%s ok=%s latency_ms=%s input_chars=%s error=%s",
            rec.agent, rec.provider, rec.model, rec.ok, rec.latency_ms, rec.input_chars, rec.error,
        )


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
