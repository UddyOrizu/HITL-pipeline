"""Agno agent: split content into atomic claims and tag each one."""

from __future__ import annotations

import json

from agents.schemas import ClaimList
from services.model_gateway import ModelGateway, load_prompt

NAME = "claim_extractor"


def build(gateway: ModelGateway):
    from agno.agent import Agent

    return Agent(
        name=NAME,
        model=gateway.agno_model(),
        instructions=load_prompt(NAME),
        output_schema=ClaimList,
    )


def run(gateway: ModelGateway, spans: list[dict]) -> ClaimList | None:
    """spans: [{"span_id", "text"}]."""
    return gateway.run_agent(build(gateway), NAME, json.dumps({"spans": spans}), ClaimList)
