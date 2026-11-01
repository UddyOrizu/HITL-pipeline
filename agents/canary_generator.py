"""Agno agent: write a context-aware canary that fits the document's tone (used when a model provider is set)."""

from __future__ import annotations

import json

from agents.schemas import CanaryOut
from services.model_gateway import ModelGateway, load_prompt

NAME = "canary_generator"


def build(gateway: ModelGateway):
    from agno.agent import Agent

    return Agent(
        name=NAME,
        model=gateway.agno_model(),
        instructions=load_prompt(NAME),
        output_schema=CanaryOut,
    )


def run(gateway: ModelGateway, spans: list[dict], canary_type: str, difficulty: str, domain: str) -> CanaryOut | None:
    payload = {"canary_type": canary_type, "difficulty": difficulty, "domain": domain, "spans": spans[:80]}
    return gateway.run_agent(build(gateway), NAME, json.dumps(payload), CanaryOut)
