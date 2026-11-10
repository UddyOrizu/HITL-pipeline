"""Risk engine: claim extraction and risk flags per span.

With a model provider configured the Agno agents do the work; otherwise (or if the model's answer is unusable)
a deterministic heuristic produces the same flag categories, so the gate never runs unflagged. A provider outage
raises ModelUnavailable, so the pipeline retries the submission instead of quietly using the heuristics.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.schemas import Claim, ClaimList, RiskFlagList, RiskFlagOut
from db.models import Document, RiskFlag, Section, Span, Submission, Workflow
from services import audit
from services.model_gateway import get_gateway

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

NUM_RE = re.compile(
    r"(?:[£$€]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:m|bn|k|million|billion))?|\b\d[\d,]*(?:\.\d+)?\s?%|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b)",
    re.I,
)
CITATION_RE = re.compile(
    r"(\(([A-Z][A-Za-z&.\- ]+),?\s+(19|20)\d{2}\)|\[\d+\]|according to [^,.;]+|et al\.?|\bsection \d+[\w.()]*|\barticle \d+|\breport\b|\bstudy\b|\bsurvey\b)",
    re.I,
)
LEGAL_WORDS = re.compile(
    r"\b(regulat\w*|law|legal|statut\w*|complian\w*|act\b|FCA|PRA|GDPR|SEC|liab\w*|contract\w*|obligat\w*|consumer duty|tax\w*|HMRC|court|breach)\b",
    re.I,
)
ADVICE_WORDS = re.compile(
    r"\b(should|recommend\w*|advis\w*|must|we suggest|consider\w*|ought to|invest\w*|guarantee\w*|you need to)\b", re.I
)
HEDGE_WORDS = re.compile(
    r"\b(may|might|could|possibly|perhaps|approximately|around|roughly|likely|unlikely|we believe|it appears|estimated|probably|about)\b",
    re.I,
)
ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|[A-Z]{2,}(?:\s+[A-Z]{2,})*)\b")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(£$€])")


def split_claims(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_RE.split(text.replace("\n", " ")) if p.strip()]
    return [p for p in parts if len(p.split()) >= 3]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,£$€]", "", m.group(0)).lower() for m in NUM_RE.finditer(text)}


def heuristic_claims(spans: list[Span]) -> ClaimList:
    claims: list[Claim] = []
    for span in spans:
        if span.kind in ("heading", "title"):
            continue
        for sentence in split_claims(span.text):
            tags = []
            if NUM_RE.search(sentence):
                tags.append("numeric")
            if LEGAL_WORDS.search(sentence):
                tags.append("legal_regulatory")
            if ADVICE_WORDS.search(sentence):
                tags.append("advice")
            if ENTITY_RE.search(sentence[1:]):
                tags.append("named_entity")
            if CITATION_RE.search(sentence):
                tags.append("citation")
            hedged = bool(HEDGE_WORDS.search(sentence))
            confidence = 0.55 if hedged else 0.85
            claims.append(Claim(span_id=span.id, text=sentence, tags=tags, confidence=confidence, hedged=hedged))
    return ClaimList(claims=claims)


def heuristic_flags(
    claims: ClaimList, reference_text: str, risk_tier: str, approved_corpus: list[set[str]]
) -> RiskFlagList:
    flags: list[RiskFlagOut] = []
    ref_numbers = _numbers(reference_text) if reference_text else set()
    ref_lower = reference_text.lower()
    bump = risk_tier == "high"

    def sev(base: str) -> str:
        if bump and base != "high":
            return "medium" if base == "low" else "high"
        return base

    for c in claims.claims:
        tags = set(c.tags)
        if "advice" in tags and "legal_regulatory" in tags:
            flags.append(RiskFlagOut(span_id=c.span_id, category="regulated_advice", severity="high",
                                     rationale="Advice that touches law or regulation: check it is permitted and correct.",
                                     claim_text=c.text))
        if "numeric" in tags and "citation" not in tags:
            if reference_text:
                missing = [n for n in _numbers(c.text) if n not in ref_numbers and len(n) > 1]
                if missing:
                    flags.append(RiskFlagOut(span_id=c.span_id, category="reference_mismatch", severity="high",
                                             rationale=f"Figure(s) {', '.join(sorted(missing)[:3])} not found in the reference documents.",
                                             claim_text=c.text))
                    continue
            else:
                flags.append(RiskFlagOut(span_id=c.span_id, category="unsupported_figure", severity=sev("medium"),
                                         rationale="Figure stated without a source: verify it.", claim_text=c.text))
        if "citation" in tags:
            cited = CITATION_RE.search(c.text)
            cited_text = cited.group(0) if cited else ""
            name = re.sub(r"[^A-Za-z ]", " ", cited_text).strip().split()
            resolvable = bool(reference_text) and any(w.lower() in ref_lower for w in name if len(w) > 3)
            if not resolvable:
                flags.append(RiskFlagOut(span_id=c.span_id, category="unresolved_citation", severity=sev("medium"),
                                         rationale=f"Citation '{cited_text[:80]}' could not be resolved against supplied sources.",
                                         claim_text=c.text))
        if c.hedged or c.confidence < 0.6:
            flags.append(RiskFlagOut(span_id=c.span_id, category="low_confidence",
                                     severity=sev("medium" if "numeric" in tags else "low"),
                                     rationale="Hedged or low-confidence wording: confirm the claim is supported.",
                                     claim_text=c.text))
        if len(approved_corpus) >= 20:
            toks = _tokens(c.text)
            if toks:
                best = max((len(toks & a) / len(toks | a) for a in approved_corpus if a), default=0)
                if best < 0.15:
                    flags.append(RiskFlagOut(span_id=c.span_id, category="novel_claim", severity="low",
                                             rationale="Claim not seen in previously approved outputs.", claim_text=c.text))
    return RiskFlagList(flags=flags)


def _approved_corpus(session: Session, tenant_id: str, limit: int = 500) -> list[set[str]]:
    rows = session.execute(
        select(Span.text)
        .join(Document, Document.id == Span.document_id)
        .join(Submission, Submission.id == Document.submission_id)
        .where(Submission.tenant_id == tenant_id, Submission.status == "approved",
               Span.review_only.is_(False), Document.role == "primary")
        .order_by(Span.created_at.desc())
        .limit(limit)
    ).all()
    return [_tokens(r[0]) for r in rows]


def _reference_text(session: Session, submission: Submission) -> str:
    refs = [d for d in submission.documents if d.role == "reference"]
    if not refs:
        return ""
    return "\n".join(s for s in session.scalars(select(Span.text).where(Span.document_id.in_([d.id for d in refs]))).all())


def _run_engine(session: Session, submission: Submission, spans: list[Span], reference_text: str,
                use_llm: bool) -> tuple[ClaimList, RiskFlagList, str]:
    """Claims and flags for these spans: the LLM agents when use_llm (a provider outage raises ModelUnavailable),
    the heuristics otherwise or when the model's answer is unusable (recorded as a fallback)."""
    gateway = get_gateway()
    if use_llm and gateway.enabled:
        from agents import claim_extractor, risk_flagger

        claims = claim_extractor.run(gateway, [{"span_id": s.id, "text": s.text} for s in spans])
        flags = risk_flagger.run(gateway, claims, reference_text, submission.risk_tier) if claims is not None else None
        if flags is not None:
            return claims, flags, f"llm:{gateway.provider}"
        audit.record(session, submission.tenant_id, "system:risk_engine", "model.fallback", "submission", submission.id,
                     {"agent": "risk_flagger" if claims is not None else "claim_extractor", "reason": "invalid_output",
                      "engine": "heuristic"})
    claims = heuristic_claims(spans)
    flags = heuristic_flags(claims, reference_text, submission.risk_tier, _approved_corpus(session, submission.tenant_id))
    return claims, flags, "heuristic"


def _persist(session: Session, submission: Submission, workflow: Workflow, spans: list[Span], claims: ClaimList,
             flags: RiskFlagList) -> list[RiskFlag]:
    valid_ids = {s.id for s in spans}
    mandatory_sev = set((workflow.risk_policy or {}).get("mandatory_severities", ["high"]))
    merged: dict[tuple[str, str], RiskFlagOut] = {}
    for f in flags.flags:
        if f.span_id not in valid_ids:
            continue
        key = (f.span_id, f.category)
        if key not in merged or SEVERITY_ORDER[f.severity] > SEVERITY_ORDER[merged[key].severity]:
            merged[key] = f
    rows = []
    claim_tags: dict[str, set] = {}
    for c in (claims.claims if claims else []):
        claim_tags.setdefault(c.span_id, set()).update(c.tags)
    for f in merged.values():
        row = RiskFlag(
            tenant_id=submission.tenant_id, submission_id=submission.id, span_id=f.span_id, category=f.category,
            severity=f.severity, rationale=f.rationale, mandatory=f.severity in mandatory_sev,
            claim_text=f.claim_text, claim_tags=sorted(claim_tags.get(f.span_id, [])),
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def flag_submission(session: Session, submission: Submission, workflow: Workflow) -> list[RiskFlag]:
    primary = [d for d in submission.documents if d.role == "primary"]
    spans = list(
        session.scalars(
            select(Span)
            .join(Section, Section.id == Span.section_id)
            .where(Span.document_id.in_([d.id for d in primary]), Span.review_only.is_(False))
            .order_by(Section.order, Span.order)
        ).all()
    )
    claims, flags, engine = _run_engine(session, submission, spans, _reference_text(session, submission), use_llm=True)
    rows = _persist(session, submission, workflow, spans, claims, flags)
    audit.record(
        session, submission.tenant_id, "system:risk_engine", "risk.flagged", "submission", submission.id,
        {"engine": engine, "claims": len(claims.claims) if claims else 0, "flags": len(rows),
         "mandatory": sum(1 for r in rows if r.mandatory)},
    )
    return rows


def engine_used(session: Session, submission: Submission) -> str | None:
    """The engine that flagged this submission's genuine spans (from its risk.flagged audit event)."""
    from db.models import AuditEvent

    ev = session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == submission.tenant_id, AuditEvent.entity_id == submission.id,
                                 AuditEvent.action == "risk.flagged").order_by(AuditEvent.seq.desc())
    ).first()
    return (ev.payload or {}).get("engine") if ev else None


def flag_spans(session: Session, submission: Submission, workflow: Workflow, spans: list[Span]) -> list[RiskFlag]:
    """Flag extra spans (a canary) exactly as the genuine spans were flagged: the same engine, the same reference
    documents and approved corpus, the same severity rules for the risk tier and the same mandatory rule. Writes
    no risk.flagged event, so the audit trail does not show that an extra span was flagged."""
    engine = engine_used(session, submission) or "heuristic"
    claims, flags, _ = _run_engine(session, submission, spans, _reference_text(session, submission),
                                   use_llm=engine.startswith("llm:"))
    return _persist(session, submission, workflow, spans, claims, flags)
