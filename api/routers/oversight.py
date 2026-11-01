"""Oversight lead API: dashboards, held items and workflow settings. Users and teams live in users.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, require_roles
from db.models import Submission, Team, Workflow, new_id
from services import assurance, audit, workflow

router = APIRouter(prefix="/v1", tags=["oversight"])

oversight = require_roles("oversight_lead")


@router.get("/oversight/dashboard")
def dashboard(days: int = Query(30, ge=1, le=365), p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    return assurance.dashboard(db, p.tenant_id, days)


def _sub_row(db: Session, s: Submission) -> dict:
    wf = db.get(Workflow, s.workflow_id)
    score = assurance.latest(db, s.id)
    return {"id": s.id, "status": s.status, "hold_reason": s.hold_reason, "escalated": s.escalated,
            "workflow": wf.name if wf else None, "risk_tier": s.risk_tier, "external_ref": s.external_ref,
            "title": (s.meta or {}).get("title"), "created_at": s.created_at.isoformat() + "Z",
            "final_outcome": s.final_outcome, "score": score.score if score else None}


@router.get("/oversight/submissions")
def list_submissions(
    held: bool = False, status: str | None = None, limit: int = Query(100, le=500),
    p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db),
):
    q = select(Submission).where(Submission.tenant_id == p.tenant_id)
    if held:
        q = q.where(Submission.hold_reason.is_not(None))
    if status:
        q = q.where(Submission.status == status)
    return [_sub_row(db, s) for s in db.scalars(q.order_by(Submission.created_at.desc()).limit(limit)).all()]


class ResolveIn(BaseModel):
    outcome: str = Field(pattern="^(approve|reject|request_changes)$")
    reason: str = ""


@router.post("/oversight/submissions/{submission_id}/resolve")
def resolve(submission_id: str, body: ResolveIn, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if sub is None or sub.tenant_id != p.tenant_id:
        raise HTTPException(404, "submission not found")
    try:
        workflow.resolve(db, sub, body.outcome, body.reason, f"user:{p.user.id}")
    except workflow.TransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return _sub_row(db, sub)


# --- Workflows (versioned) --------------------------------------------------------------


class StageIn(BaseModel):
    name: str
    team_id: str | None = None
    sla_hours: float | None = 24


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


def _validate(body: WorkflowIn, db: Session, tenant_id: str) -> None:
    for st in body.stages:
        if st.team_id:
            team = db.get(Team, st.team_id)
            if team is None or team.tenant_id != tenant_id:
                raise HTTPException(422, f"unknown team {st.team_id}")
    overflow = body.cap_policy.get("overflow")
    if overflow and overflow not in ("queue", "escalate", "defer"):
        raise HTTPException(422, "cap_policy.overflow must be queue, escalate or defer")
    action = body.telemetry_policy.get("action")
    if action and action not in ("warn", "block", "flag"):
        raise HTTPException(422, "telemetry_policy.action must be warn, block or flag")
    channels = body.telemetry_policy.get("review_channels")
    if channels is not None and (not isinstance(channels, list) or not channels or set(channels) - {"ui", "api", "mcp"}):
        raise HTTPException(422, "telemetry_policy.review_channels must be a non-empty list of ui, api, mcp")
    below = body.assurance_policy.get("below_action")
    if below and below not in ("block", "second_review", "release_flag"):
        raise HTTPException(422, "assurance_policy.below_action must be block, second_review or release_flag")


@router.get("/admin/workflows")
def list_workflows(include_history: bool = False, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    q = select(Workflow).where(Workflow.tenant_id == p.tenant_id)
    if not include_history:
        q = q.where(Workflow.is_current.is_(True))
    return [wf_out(w) for w in db.scalars(q.order_by(Workflow.name, Workflow.version)).all()]


@router.post("/admin/workflows", status_code=201)
def create_workflow(body: WorkflowIn, p: UserPrincipal = Depends(oversight), db: Session = Depends(get_db)):
    _validate(body, db, p.tenant_id)
    data = body.model_dump()
    data["stages"] = [s.model_dump() for s in body.stages]
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
    data = body.model_dump()
    data["stages"] = [s.model_dump() for s in body.stages]
    w = Workflow(tenant_id=p.tenant_id, family_id=family_id, version=current.version + 1, is_current=True, **data)
    db.add(w)
    db.flush()
    audit.record(db, p.tenant_id, f"user:{p.user.id}", "workflow.versioned", "workflow", family_id, {"version": w.version, **data})
    return wf_out(w)
