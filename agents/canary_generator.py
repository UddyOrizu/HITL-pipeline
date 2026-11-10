"""Agno agent: write a context-aware canary that fits the document's tone (used when a model provider is set)."""

from __future__ import annotations

import json
import random

from agents.schemas import CanaryOut
from services.model_gateway import ModelGateway, load_prompt, record_fallback

NAME = "canary_generator"
MAX_SPANS = 80
MAX_CHARS = 24000


def build(gateway: ModelGateway):
    from agno.agent import Agent

    return Agent(
        name=NAME,
        model=gateway.agno_model(),
        instructions=load_prompt(NAME),
        output_schema=CanaryOut,
    )


def sample_spans(spans: list[dict], flagged_ids: set[str], rng: random.Random, max_spans: int = MAX_SPANS,
                 max_chars: int = MAX_CHARS) -> list[dict]:
    """Candidate spans from across the whole document, a mix of flagged and unflagged ones (as the template path
    does), kept in document order and within the size budget."""
    if len(spans) <= max_spans and sum(len(s.get("text") or "") for s in spans) <= max_chars:
        return list(spans)
    flagged = [s for s in spans if s["span_id"] in flagged_ids]
    unflagged = [s for s in spans if s["span_id"] not in flagged_ids]
    want_unflagged = min(len(unflagged), max(int(max_spans * 0.6), max_spans - len(flagged)))
    picked = rng.sample(unflagged, want_unflagged) + rng.sample(flagged, min(len(flagged), max_spans - want_unflagged))
    order = {s["span_id"]: i for i, s in enumerate(spans)}
    out, size = [], 0
    for s in sorted(picked, key=lambda s: order[s["span_id"]]):
        n = len(s.get("text") or "")
        if out and size + n > max_chars:
            continue
        out.append(s)
        size += n
    return out


def run(gateway: ModelGateway, spans: list[dict], canary_type: str, difficulty: str, domain: str,
        flagged_ids: set[str] | None = None, rng: random.Random | None = None) -> CanaryOut | None:
    rng = rng or random.SystemRandom()
    candidates = sample_spans(spans, flagged_ids or set(), rng)
    payload = {"canary_type": canary_type, "difficulty": difficulty, "domain": domain, "spans": candidates}
    out = gateway.run_agent(build(gateway), NAME, json.dumps(payload), CanaryOut)
    if out is None:
        return None
    by_id = {s["span_id"]: s for s in candidates}
    target = by_id.get(out.target_span_id)
    if target is None or not out.text.strip():
        record_fallback(NAME, gateway.provider, "invalid_target", out.target_span_id)
        return None
    if out.mode == "replace" and " ".join(out.text.split()) == " ".join((target.get("text") or "").split()):
        # A replacement identical to the original is not an error at all: it could never be caught.
        record_fallback(NAME, gateway.provider, "unchanged_replacement", out.target_span_id)
        return None
    return out
