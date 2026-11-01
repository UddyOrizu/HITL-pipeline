"""Agno agent: raise risk flags on extracted claims, optionally checking them against reference documents."""

from __future__ import annotations

import json

from agents.schemas import ClaimList, RiskFlagList
from services.model_gateway import ModelGateway, load_prompt

NAME = "risk_flagger"


def build(gateway: ModelGateway):
    from agno.agent import Agent

    return Agent(
        name=NAME,
        model=gateway.agno_model(),
        instructions=load_prompt(NAME),
        output_schema=RiskFlagList,
    )


def run(gateway: ModelGateway, claims: ClaimList, reference_text: str, risk_tier: str) -> RiskFlagList | None:
    payload = {
        "risk_tier": risk_tier,
        "claims": [c.model_dump() for c in claims.claims],
        "reference_documents": reference_text[:60000],
    }
    return gateway.run_agent(build(gateway), NAME, json.dumps(payload), RiskFlagList)
