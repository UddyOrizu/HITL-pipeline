"""Structured outputs for the Agno agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ClaimTag = Literal["numeric", "legal_regulatory", "advice", "named_entity", "citation"]


class Claim(BaseModel):
    span_id: str
    text: str
    tags: list[ClaimTag] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, description="Model confidence that the claim is well supported")
    hedged: bool = False


class ClaimList(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


class RiskFlagOut(BaseModel):
    span_id: str
    category: Literal[
        "low_confidence", "unsupported_figure", "unresolved_citation", "reference_mismatch", "novel_claim", "regulated_advice"
    ]
    severity: Literal["low", "medium", "high"]
    rationale: str
    claim_text: str | None = None


class RiskFlagList(BaseModel):
    flags: list[RiskFlagOut] = Field(default_factory=list)


class CanaryOut(BaseModel):
    type: Literal["factual_error", "internal_contradiction", "fabricated_citation", "policy_breach", "absurd_statement"]
    difficulty: Literal["easy", "medium", "hard"]
    mode: Literal["insert", "replace"]
    target_span_id: str
    text: str = Field(description="The full text of the canary span as the reviewer will see it")
