"""Canary service: select, generate and inject known errors into the review copy; score catches; reveal.

Canaries only ever exist as review_only spans. The original spans, the content hash and every outbound
payload are untouched; services/outbound.py enforces that no canary text leaves the platform.
Scoring is deterministic (no AI), so scores stay process-quality measures.
"""

from __future__ import annotations

import random
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Canary, Comment, RiskFlag, Section, Span, Submission, TelemetryEvent, Workflow, utcnow
from services import audit, canary_templates as T
from services.model_gateway import get_gateway

_rng = random.SystemRandom()

NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?%)?")


def set_rng(rng: random.Random) -> None:
    """Tests can make injection deterministic."""
    global _rng
    _rng = rng


def should_inject(rate: float) -> bool:
    return rate > 0 and _rng.random() < rate


def _alter_number(text: str) -> tuple[str, str] | None:
    """Change one number in the text. Returns (new_text, description) or None if there is no number."""
    matches = [m for m in NUMBER_RE.finditer(text) if not (len(m.group(1)) == 4 and m.group(1).startswith(("19", "20")))]
    if not matches:
        matches = list(NUMBER_RE.finditer(text))
    if not matches:
        return None
    m = _rng.choice(matches)
    raw = m.group(1).replace(",", "")
    value = int(raw)
    if len(raw) == 4 and raw.startswith(("19", "20")):
        new_value = value + _rng.choice([-2, -1, 1, 2])
    elif value < 10:
        new_value = value + _rng.choice([2, 3, 4])
    else:
        factor = _rng.choice([0.6, 0.7, 1.3, 1.5, 2.0])
        new_value = max(1, int(round(value * factor)))
        if new_value == value:
            new_value = value + 7
    formatted = f"{new_value:,}" if "," in m.group(1) else str(new_value)
    replacement = formatted + (m.group(2) or "") + (m.group(3) or "")
    return text[: m.start()] + replacement + text[m.end() :], f"{m.group(0)} changed to {replacement}"


def _fill(template: str) -> str:
    return template.format(
        n=_rng.randint(2, 19), n2=_rng.randint(10, 29), n3=_rng.randint(101, 989),
        yy=_rng.randint(19, 25), yyyy=_rng.randint(2018, 2025), pct=_rng.randint(23, 61),
    )


def _pick(library: dict[str, list[str]], domain: str) -> str:
    options = library.get(domain, []) + library["general"]
    return _fill(_rng.choice(options))


def _candidate_spans(session: Session, submission: Submission) -> list[Span]:
    doc_ids = [d.id for d in submission.documents if d.role == "primary"]
    return list(
        session.scalars(
            select(Span)
            .join(Section, Section.id == Span.section_id)
            .where(Span.document_id.in_(doc_ids), Span.review_only.is_(False))
            .order_by(Section.order, Span.order)
        ).all()
    )


def _template_canary(spans: list[Span], flagged_ids: set[str], domain: str) -> tuple[str, str, Span, str] | None:
    """Returns (type, mode, target_span, text)."""
    body = [s for s in spans if s.kind in ("paragraph", "list_item", "notes", "table") and len(s.text.split()) >= 4]
    if not body:
        return None
    middle = body[1:-1] if len(body) > 2 else body
    # Place canaries in unflagged spans as well as flagged ones, so risk-pointing cannot become tunnel vision.
    unflagged = [s for s in middle if s.id not in flagged_ids]
    pool = unflagged if unflagged and _rng.random() < 0.6 else middle
    numeric = [s for s in pool if NUMBER_RE.search(s.text) and s.kind != "table"]

    types = list(T.TYPES)
    _rng.shuffle(types)
    for ctype in types:
        if ctype == "factual_error" and numeric:
            target = _rng.choice(numeric)
            altered = _alter_number(target.text)
            if altered:
                return ctype, "replace", target, altered[0]
        elif ctype == "internal_contradiction" and numeric:
            source = _rng.choice(numeric)
            sentences = [x for x in re.split(r"(?<=[.!?])\s+", source.text) if NUMBER_RE.search(x)]
            altered = _alter_number(sentences[0]) if sentences else None
            later = [s for s in pool if s.order != source.order or s.section_id != source.section_id]
            if altered and later:
                sentence = altered[0][0].lower() + altered[0][1:]
                return ctype, "insert", _rng.choice(later), f"To restate the position set out elsewhere in this document, {sentence}"
        elif ctype == "fabricated_citation":
            return ctype, "insert", _rng.choice(pool), _pick(T.FABRICATED_CITATION, domain)
        elif ctype == "policy_breach":
            return ctype, "insert", _rng.choice(pool), _pick(T.POLICY_BREACH, domain)
        elif ctype == "absurd_statement":
            return ctype, "insert", _rng.choice(pool), _pick(T.ABSURD, domain)
    return None


def inject(session: Session, submission: Submission, workflow: Workflow, force: bool | None = None) -> Canary | None:
    """Decide whether to seed this review, and if so add a canary span to the review copy."""
    inject_now = should_inject(workflow.canary_rate) if force is None else force
    if not inject_now:
        return None
    spans = _candidate_spans(session, submission)
    flagged = set(session.scalars(select(RiskFlag.span_id).where(RiskFlag.submission_id == submission.id)).all())
    by_id = {s.id: s for s in spans}

    picked = None
    gateway = get_gateway()
    if gateway.enabled:
        from agents import canary_generator

        ctype = _rng.choice(T.TYPES)
        out = canary_generator.run(
            gateway, [{"span_id": s.id, "text": s.text} for s in spans], ctype, T.DIFFICULTY[ctype], workflow.canary_domain
        )
        if out and out.target_span_id in by_id and out.text.strip():
            picked = (out.type, out.mode, by_id[out.target_span_id], out.text.strip(), out.difficulty)
    if picked is None:
        tmpl = _template_canary(spans, flagged, workflow.canary_domain)
        if tmpl is None:
            return None
        picked = (*tmpl, T.DIFFICULTY[tmpl[0]])

    ctype, mode, target, text, difficulty = picked
    if mode == "replace":
        order, replaces = target.order, target.id
    else:
        siblings = [s.order for s in spans if s.section_id == target.section_id and s.order > target.order]
        order = (target.order + min(siblings)) / 2 if siblings else target.order + 0.5
        replaces = None
    span = Span(
        tenant_id=submission.tenant_id, document_id=target.document_id, section_id=target.section_id, order=order,
        kind="paragraph" if target.kind in ("table", "notes") and mode == "insert" else target.kind,
        text=text, location=dict(target.location or {}), review_only=True, replaces_span_id=replaces,
    )
    session.add(span)
    session.flush()
    canary = Canary(
        tenant_id=submission.tenant_id, submission_id=submission.id, span_id=span.id, target_span_id=target.id,
        type=ctype, difficulty=difficulty, injected_text=text,
    )
    session.add(canary)
    session.flush()
    _mirror_flags(session, submission, span)
    audit.record(
        session, submission.tenant_id, "system:canary", "canary.injected", "submission", submission.id,
        {"canary_id": canary.id, "type": ctype, "difficulty": difficulty, "mode": mode},
    )
    return canary


def _mirror_flags(session: Session, submission: Submission, span: Span) -> None:
    """Flag the canary span the way a genuine span would be flagged, so the absence of flags is not a tell."""
    from services.risk import heuristic_claims, heuristic_flags

    flags = heuristic_flags(heuristic_claims([span]), "", submission.risk_tier, []).flags
    seen = set()
    for f in flags:
        if f.category in seen:
            continue
        seen.add(f.category)
        session.add(RiskFlag(
            tenant_id=submission.tenant_id, submission_id=submission.id, span_id=span.id, category=f.category,
            severity="medium" if f.severity == "high" else f.severity, rationale=f.rationale, mandatory=False,
            claim_text=None, claim_tags=[],
        ))
    session.flush()


def canary_for_task(session: Session, task_id: str) -> Canary | None:
    return session.scalars(select(Canary).where(Canary.scored_task_id == task_id)).first()


def score(session: Session, task, outcome: str, cited_span_ids: list[str]) -> list[Canary]:
    """Score canaries assigned to this task. Caught = comment or edit on the canary span,
    or a reject / request-changes decision that cites the canary span."""
    results = []
    for canary in session.scalars(
        select(Canary).where(Canary.scored_task_id == task.id, Canary.caught.is_(None))
    ).all():
        event_id = session.scalars(
            select(Comment.id).where(Comment.review_task_id == task.id, Comment.span_id == canary.span_id)
        ).first()
        if event_id is None:
            event_id = session.scalars(
                select(TelemetryEvent.id).where(
                    TelemetryEvent.review_task_id == task.id, TelemetryEvent.span_id == canary.span_id,
                    TelemetryEvent.type == "edit_made",
                )
            ).first()
        if event_id is None and outcome in ("reject", "request_changes") and canary.span_id in cited_span_ids:
            event_id = f"decision:{task.id}"
        canary.caught = event_id is not None
        canary.caught_by_event_id = event_id
        canary.revealed_at = utcnow()
        audit.record(
            session, task.tenant_id, f"user:{task.reviewer_id}", "canary.caught" if canary.caught else "canary.missed",
            "submission", canary.submission_id,
            {"canary_id": canary.id, "task_id": task.id, "type": canary.type, "difficulty": canary.difficulty},
        )
        results.append(canary)
    session.flush()
    return results


def reveal(session: Session, canary: Canary) -> dict:
    """Shown to the reviewer only, after their decision: turns the control into training, not a trap."""
    original = session.get(Span, canary.target_span_id) if canary.target_span_id else None
    return {
        "type": canary.type,
        "difficulty": canary.difficulty,
        "caught": bool(canary.caught),
        "canary_span_id": canary.span_id,
        "canary_text": canary.injected_text,
        "original_text": original.text if original and canary.type == "factual_error" else None,
        "explanation": T.EXPLANATION.get(canary.type, ""),
        "message": (
            "Well spotted: this was a canary, a known error placed to measure review quality. It never reaches the client."
            if canary.caught
            else "This review contained a canary, a known error placed to measure review quality, and it was not flagged. "
            "It never reaches the client. Use it as a calibration point."
        ),
    }


def catch_rate(session: Session, tenant_id: str, reviewer_id: str, window: int = 20) -> tuple[float, int]:
    """Rolling catch rate over the reviewer's last N scored canaries. Returns (rate, n)."""
    rows = session.scalars(
        select(Canary.caught)
        .where(Canary.tenant_id == tenant_id, Canary.scored_reviewer_id == reviewer_id, Canary.caught.is_not(None))
        .order_by(Canary.revealed_at.desc())
        .limit(window)
    ).all()
    n = len(rows)
    caught = sum(1 for r in rows if r)
    # Beta(1,1) prior so a new reviewer starts at 0.5 rather than 0 or 1.
    return (caught + 1) / (n + 2), n
