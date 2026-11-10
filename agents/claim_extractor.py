"""Agno agent: split content into atomic claims and tag each one.

Long documents are sent in batches (at most MAX_SPANS spans or MAX_CHARS characters each) and the claims are
merged, so a large document never exceeds the model's input limit.
"""

from __future__ import annotations

import json

from agents.schemas import ClaimList
from services.model_gateway import ModelGateway, load_prompt

NAME = "claim_extractor"
MAX_SPANS = 60
MAX_CHARS = 24000


def build(gateway: ModelGateway):
    from agno.agent import Agent

    return Agent(
        name=NAME,
        model=gateway.agno_model(),
        instructions=load_prompt(NAME),
        output_schema=ClaimList,
    )


def batches(spans: list[dict], max_spans: int = MAX_SPANS, max_chars: int = MAX_CHARS) -> list[list[dict]]:
    out: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for sp in spans:
        n = len(sp.get("text") or "")
        if cur and (len(cur) >= max_spans or size + n > max_chars):
            out.append(cur)
            cur, size = [], 0
        cur.append(sp)
        size += n
    if cur:
        out.append(cur)
    return out


def run(gateway: ModelGateway, spans: list[dict]) -> ClaimList | None:
    """spans: [{"span_id", "text"}]. Returns None if any batch's answer is unusable (the caller falls back to
    the heuristics for the whole document); a provider outage raises ModelUnavailable."""
    claims = []
    for batch in batches(spans):
        out = gateway.run_agent(build(gateway), NAME, json.dumps({"spans": batch}), ClaimList)
        if out is None:
            return None
        claims.extend(out.claims)
    return ClaimList(claims=claims)
