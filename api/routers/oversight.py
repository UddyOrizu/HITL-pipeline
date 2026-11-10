"""Oversight lead API: dashboards, held items and workflow settings. Users and teams live in users.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, require_roles
from api.pagination import Page, paginate
from db.models import (
    Comment,
    Decision,
    ReviewTask,
    RiskFlag,
    SourceApp,
    Submission,
    Team,
    User,
    Workflow,
    new_id,
)
from services import assurance, audit, evidence, ingestion, workflow

router = APIRouter(prefix="/v1", tags=["oversight"])

oversight = require_roles("oversight_lead")


@router.get("/oversight/dashboard")
def dashboard(days: int = Query(30, ge=1, le=365), p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    return assurance.dashboard(db, p.tenant_id, days)


def _actions(s: Submission, db: Session | None = None) -> list[str]:
    """What an oversight lead can do with this item now."""
    acts = []
    if s.hold_reason in workflow.DECISION_HOLDS and s.status in ("in_review", "spot_check"):
        acts.append("resolve")
    if s.hold_reason in ("awaiting_capacity", "awaiting_spot_check_reviewer"):
        acts.append("assign")
    if s.hold_reason and s.status in ("in_review", "spot_check"):
        acts.append("defer")
    if s.status == "deferred" and not s.purged_at and (db is None or workflow.can_retry(db, s)[0]):
        acts.append("retry")
    if s.status not in workflow.FINAL:
        acts.append("cancel")
    return acts


def _deferral(db: Session, s: Submission) -> dict | None:
    if s.status != "deferred":
        return None
    from db.models import AuditEvent

    for e in db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == s.tenant_id, AuditEvent.entity_id == s.id,
                                                 AuditEvent.action == "submission.status_changed")
                        .order_by(AuditEvent.seq.desc())).all():
        if (e.payload or {}).get("to") == "deferred":
            return {"reason": e.payload.get("reason"), "by": e.actor, "at": e.created_at.isoformat() + "Z"}
    return None


def _sub_row(db: Session, s: Submission) -> dict:
    wf = db.get(Workflow, s.workflow_id)
    score = assurance.latest(db, s.id)
    app = db.get(SourceApp, s.source_app_id)
    return {"id": s.id, "status": s.status, "hold_reason": s.hold_reason, "escalated": s.escalated,
            "workflow": wf.name if wf else None, "risk_tier": s.risk_tier, "external_ref": s.external_ref,
            "title": (s.meta or {}).get("title"), "source_app": app.name if app else None,
            "created_at": s.created_at.isoformat() + "Z", "updated_at": s.updated_at.isoformat() + "Z",
            "final_outcome": s.final_outcome, "score": score.score if score else None,
            "processing_error": s.processing_error, "deferred": _deferral(db, s), "actions": _actions(s, db)}


@router.get("/oversight/submissions")
def list_submissions(
    response: Response, held: bool = False, status: str | None = None, q: str | None = None, page: Page = Depends(),
    p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db),
):
    stmt = select(Submission).where(Submission.tenant_id == p.tenant_id)
    if held:
        stmt = stmt.where(Submission.hold_reason.is_not(None))
    if status:
        stmt = stmt.where(Submission.status == status)
    if q:
        from sqlalchemy import or_

        stmt = stmt.where(or_(Submission.id.startswith(q), Submission.external_ref.ilike(f"%{q}%"),
                              Submission.meta["title"].as_string().ilike(f"%{q}%")))
    return [_sub_row(db, s) for s in paginate(db, stmt.order_by(Submission.created_at.desc()), page, response)]


def _get(db: Session, p: UserPrincipal, submission_id: str) -> Submission:
    sub = db.get(Submission, submission_id)
    if sub is None or sub.tenant_id != p.tenant_id:
        raise HTTPException(404, "submission not found")
    return sub


@router.get("/oversight/submissions/{submission_id}")
def get_submission(submission_id: str, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    """Everything an oversight lead needs to settle a held item: both decisions, comments, flags, the score and the
    original document (never the review copy, so no canary text)."""
    sub = _get(db, p, submission_id)
    users = {u.id: u for u in db.scalars(select(User).where(User.tenant_id == p.tenant_id)).all()}
    tasks = db.scalars(select(ReviewTask).where(ReviewTask.submission_id == sub.id).order_by(ReviewTask.assigned_at)).all()
    decisions = {d.review_task_id: d for d in db.scalars(select(Decision).where(Decision.submission_id == sub.id)).all()}
    comments = db.scalars(select(Comment).where(Comment.review_task_id.in_([t.id for t in tasks] or ["-"]))
                          .order_by(Comment.created_at)).all()
    reviews = []
    for t in tasks:
        d = decisions.get(t.id)
        u = users.get(t.reviewer_id)
        reviews.append({
            "task_id": t.id, "kind": t.kind, "stage": t.stage_name, "status": t.status,
            "reviewer": (u.name or u.email) if u else t.reviewer_id, "reviewer_id": t.reviewer_id,
            "assigned_at": t.assigned_at.isoformat() + "Z", "due_at": t.due_at.isoformat() + "Z" if t.due_at else None,
            "sla_escalated": t.sla_escalated,
            "decision": {"outcome": d.outcome, "reason": d.reason, "channel": d.channel, "warnings": d.warnings,
                         "decided_at": d.decided_at.isoformat() + "Z", "telemetry": d.telemetry_summary} if d else None,
            "comments": [{"span_id": c.span_id, "kind": c.kind, "body": c.body, "suggested_text": c.suggested_text}
                         for c in comments if c.review_task_id == t.id],
        })
    primary = next((d for d in sub.documents if d.role == "primary"), None)
    sections = []
    if primary and not sub.purged_at:
        current = None
        for sec, sp in ingestion.original_spans(db, primary.id):
            if current is None or current["id"] != sec.id:
                current = {"id": sec.id, "kind": sec.kind, "title": sec.title, "spans": []}
                sections.append(current)
            current["spans"].append({"id": sp.id, "kind": sp.kind, "text": sp.text})
    flags = [{"id": f.id, "span_id": f.span_id, "category": f.category, "severity": f.severity, "mandatory": f.mandatory,
              "rationale": f.rationale}
             for f in db.scalars(select(RiskFlag).where(RiskFlag.submission_id == sub.id)).all()
             if any(f.span_id == sp["id"] for s in sections for sp in s["spans"])]
    score = assurance.latest(db, sub.id)
    reviewers = [{"id": u.id, "name": u.name or u.email} for u in users.values()
                 if u.active and "reviewer" in (u.roles or [])]
    return {**_sub_row(db, sub), "reviews": reviews, "flags": flags,
            "score_detail": assurance.score_payload(score), "has_evidence_pack": evidence.latest_pack(db, sub) is not None,
            "document": {"filename": primary.filename if primary else None, "render_kind": primary.render_kind if primary else None,
                         "sections": sections, "purged": bool(sub.purged_at)},
            "reviewers": reviewers}


class ResolveIn(BaseModel):
    outcome: str = Field(pattern="^(approve|reject|request_changes)$")
    reason: str = ""


@router.post("/oversight/submissions/{submission_id}/resolve")
def resolve(submission_id: str, body: ResolveIn, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    sub = _get(db, p, submission_id)
    if body.outcome != "approve" and not body.reason.strip():
        raise HTTPException(422, "a reason is required to reject or request changes")
    try:
        workflow.resolve(db, sub, body.outcome, body.reason, f"user:{p.user.id}")
    except workflow.TransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return _sub_row(db, sub)


class ActionIn(BaseModel):
    action: str = Field(pattern="^(assign|defer|retry|cancel)$")
    reviewer_id: str | None = None
    reason: str = ""


@router.post("/oversight/submissions/{submission_id}/action")
def act(submission_id: str, body: ActionIn, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    """Settle items that need an action rather than a decision:
    assign (to a named reviewer, past the caps), defer (stop and tell the source app), retry (a deferred item),
    cancel."""
    sub = _get(db, p, submission_id)
    actor = f"user:{p.user.id}"
    try:
        if body.action == "assign":
            reviewer = db.get(User, body.reviewer_id or "")
            if reviewer is None or reviewer.tenant_id != p.tenant_id:
                raise HTTPException(422, "choose a reviewer")
            workflow.assign_to(db, sub, reviewer, actor)
        elif body.action == "defer":
            if not body.reason.strip():
                raise HTTPException(422, "a reason is required to defer")
            workflow.defer(db, sub, body.reason.strip(), actor)
        elif body.action == "retry":
            workflow.retry(db, sub, actor)
        else:
            workflow.cancel(db, sub, actor)
    except workflow.TransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return _sub_row(db, db.get(Submission, sub.id))


# --- Workflows (versioned) --------------------------------------------------------------


class StageIn(BaseModel):
    name: str
    team_id: str | None = None
    sla_hours: float | None = 24
    on_sla_breach: str = Field("escalate", pattern="^(escalate|reassign)$")


class WorkflowIn(BaseModel):
    name: str
    stages: list[StageIn] = Field(default_factory=lambda: [StageIn(name="Review")], min_length=1)
    canary_rate: float = Field(0.05, ge=0, le=1)
    canary_domain: str = "general"
    telemetry_policy: dict = Field(default_factory=dict)
    cap_policy: dict = Field(default_factory=dict)
    spot_check_rate: float = Field(0.05, ge=0, le=1)
    risk_policy: dict = Field(default_factory=dict)
    assurance_policy: dict = Field(default_factory=dict)
    source_app_ids: list[str] = Field(default_factory=list)


def wf_out(w: Workflow) -> dict:
    return {"id": w.family_id, "version_id": w.id, "version": w.version, "is_current": w.is_current, "name": w.name,
            "stages": w.stages, "canary_rate": w.canary_rate, "canary_domain": w.canary_domain,
            "telemetry_policy": w.telemetry_policy, "cap_policy": w.cap_policy, "spot_check_rate": w.spot_check_rate,
            "risk_policy": w.risk_policy, "assurance_policy": w.assurance_policy, "source_app_ids": w.source_app_ids,
            "updated_at": w.created_at.isoformat() + "Z"}


SEVERITIES = {"low", "medium", "high"}


POLICIES = ("telemetry_policy", "cap_policy", "risk_policy", "assurance_policy")


def _data(body: WorkflowIn) -> dict:
    """The workflow as stored. Empty (null) policy values are dropped, so the defaults apply instead."""
    data = body.model_dump()
    data["stages"] = [s.model_dump() for s in body.stages]
    for key in POLICIES:
        data[key] = {k: v for k, v in (data[key] or {}).items() if v is not None}
    return data


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and v == v  # not NaN


def _validate(body: WorkflowIn, db: Session, tenant_id: str) -> None:
    for st in body.stages:
        if st.team_id:
            team = db.get(Team, st.team_id)
            if team is None or team.tenant_id != tenant_id:
                raise HTTPException(422, f"unknown team {st.team_id}")
        if st.sla_hours is not None and st.sla_hours <= 0:
            raise HTTPException(422, "sla_hours must be positive, or empty for no SLA")
    overflow = body.cap_policy.get("overflow")
    if overflow and overflow not in ("queue", "escalate", "defer"):
        raise HTTPException(422, "cap_policy.overflow must be queue, escalate or defer")
    for k in ("daily_minutes", "hourly_minutes", "team_daily_minutes", "max_consecutive_same_source"):
        v = body.cap_policy.get(k)
        if v is not None and (not _number(v) or v < 0):
            raise HTTPException(422, f"cap_policy.{k} must be a positive number or empty")
    if body.cap_policy.get("max_consecutive_same_source") is not None and             float(body.cap_policy["max_consecutive_same_source"]) != int(body.cap_policy["max_consecutive_same_source"]):
        raise HTTPException(422, "cap_policy.max_consecutive_same_source must be a whole number")
    action = body.telemetry_policy.get("action")
    if action and action not in ("warn", "block", "flag"):
        raise HTTPException(422, "telemetry_policy.action must be warn, block or flag")
    channels = body.telemetry_policy.get("review_channels")
    if channels is not None and (not isinstance(channels, list) or not channels or set(channels) - {"ui", "api", "mcp"}):
        raise HTTPException(422, "telemetry_policy.review_channels must be a non-empty list of ui, api, mcp")
    below = body.assurance_policy.get("below_action")
    if below and below not in ("block", "second_review", "release_flag"):
        raise HTTPException(422, "assurance_policy.below_action must be block, second_review or release_flag")
    min_score = body.assurance_policy.get("min_score")
    if min_score is not None and (not _number(min_score) or not 0 <= min_score <= 100):
        raise HTTPException(422, "assurance_policy.min_score must be a number from 0 to 100")
    weights = body.assurance_policy.get("weights")
    if weights is not None:
        if not isinstance(weights, dict):
            raise HTTPException(422, "assurance_policy.weights must be an object of component weights")
        unknown = set(weights) - set(assurance.DEFAULT_WEIGHTS)
        if unknown:
            raise HTTPException(422, f"assurance_policy.weights: unknown components {sorted(unknown)}; use "
                                     f"{', '.join(assurance.DEFAULT_WEIGHTS)}")
        if any(v is not None and (not _number(v) or not 0 <= v <= 1) for v in weights.values()):
            raise HTTPException(422, "assurance_policy.weights must be numbers from 0 to 1 (they are scaled to sum to 1)")
        if weights and not any(_number(v) and v > 0 for v in weights.values()):
            raise HTTPException(422, "assurance_policy.weights: at least one weight must be above 0")
    for k in ("min_reading_ratio", "min_coverage"):
        v = body.telemetry_policy.get(k)
        if v is not None and (not _number(v) or v < 0 or (k == "min_coverage" and v > 1)):
            raise HTTPException(422, f"telemetry_policy.{k} must be a non-negative number"
                                     + (" no greater than 1" if k == "min_coverage" else ""))
    sev = body.risk_policy.get("mandatory_severities")
    if sev is not None and (not isinstance(sev, list) or set(sev) - SEVERITIES):
        raise HTTPException(422, "risk_policy.mandatory_severities must be a list of low, medium, high")
    team = body.risk_policy.get("spot_check_team_id")
    if team:
        t = db.get(Team, team)
        if t is None or t.tenant_id != tenant_id:
            raise HTTPException(422, "risk_policy.spot_check_team_id: unknown team")
    if body.source_app_ids:
        found = set(db.scalars(select(SourceApp.id).where(SourceApp.tenant_id == tenant_id,
                                                          SourceApp.id.in_(body.source_app_ids))).all())
        missing = [a for a in body.source_app_ids if a not in found]
        if missing:
            raise HTTPException(422, f"unknown source apps {missing}")


@router.get("/admin/workflows")
def list_workflows(response: Response, include_history: bool = False, family_id: str | None = None,
                   page: Page = Depends(), p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    """Workflows (current versions unless include_history). Paged with offset and limit; the total is in
    X-Total-Count and the next page's offset in X-Next-Offset."""
    q = select(Workflow).where(Workflow.tenant_id == p.tenant_id)
    if family_id:
        q = q.where(Workflow.family_id == family_id)
    if not include_history:
        q = q.where(Workflow.is_current.is_(True))
    return [wf_out(w) for w in paginate(db, q.order_by(Workflow.name, Workflow.version, Workflow.id), page, response)]


@router.get("/admin/source-apps")
def list_source_apps(p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    """Source apps (including the web portal) by name, for limiting a workflow to some of them."""
    return [{"id": a.id, "name": a.name, "kind": a.kind, "active": a.active}
            for a in db.scalars(select(SourceApp).where(SourceApp.tenant_id == p.tenant_id).order_by(SourceApp.name)).all()]


@router.post("/admin/workflows", status_code=201)
def create_workflow(body: WorkflowIn, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    _validate(body, db, p.tenant_id)
    data = _data(body)
    w = Workflow(tenant_id=p.tenant_id, family_id=new_id(), version=1, is_current=True, **data)
    db.add(w)
    db.flush()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "workflow.created", "workflow", w.family_id, {"version": 1, **data})
    return wf_out(w)


@router.put("/admin/workflows/{family_id}")
def update_workflow(family_id: str, body: WorkflowIn, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    """Saving creates a new version; submissions keep the version they were received under."""
    _validate(body, db, p.tenant_id)
    current = db.scalars(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.family_id == family_id,
                                                Workflow.is_current.is_(True))).first()
    if current is None:
        raise HTTPException(404, "workflow not found")
    current.is_current = False
    db.flush()
    data = _data(body)
    w = Workflow(tenant_id=p.tenant_id, family_id=family_id, version=current.version + 1, is_current=True, **data)
    db.add(w)
    db.flush()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "workflow.versioned", "workflow", family_id, {"version": w.version, **data})
    return wf_out(w)
