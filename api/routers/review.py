"""Reviewer workspace API: queues, review copy, flags, comments, decisions and telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, request_channel, require_roles
from api.pagination import Page, set_headers
from db.models import ReviewTask
from services import review, telemetry
from services.review import ReviewError

router = APIRouter(prefix="/v1", tags=["reviewer"])

reviewer = require_roles("reviewer")

STATUS = {"not_found": 404, "conflict": 409, "mandatory_flags": 409, "confirm_required": 409,
          "below_review_standard": 409, "channel_not_allowed": 403, "invalid": 422}


def _raise(exc: ReviewError):
    raise HTTPException(STATUS.get(exc.code, 422), {"code": exc.code, "message": str(exc), **exc.details}) from None


@router.get("/review/queue")
def get_queue(kind: str | None = Query(None, pattern="^(primary|spot_check)$"), p: UserPrincipal = Depends(reviewer),
              db: Session = Depends(get_db)):
    return review.queue(db, p.user, kind)


@router.get("/review/history")
def get_history(response: Response, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    """A reviewer's own decisions, telemetry and canary results: reviewers can always see their own data.
    Newest first, paged with offset and limit (X-Total-Count and X-Next-Offset headers)."""
    rows = review.history(db, p.user, limit=limit, offset=offset)
    set_headers(response, review.history_count(db, p.user), Page(offset=offset, limit=limit), len(rows))
    return rows


@router.get("/review/tasks/{task_id}")
def get_task(task_id: str, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    try:
        return review.task_view(db, p.user, task_id)
    except ReviewError as exc:
        _raise(exc)


@router.get("/review/tasks/{task_id}/references")
def get_references(task_id: str, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    """Reference documents sent with the submission (client file, source material), as extracted text."""
    try:
        return review.references(db, p.user, task_id)
    except ReviewError as exc:
        _raise(exc)


@router.get("/review/tasks/{task_id}/references/{document_id}/file")
def get_reference_file(task_id: str, document_id: str, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    try:
        doc, data = review.reference_file(db, p.user, task_id, document_id)
    except ReviewError as exc:
        _raise(exc)
    from urllib.parse import quote

    from fastapi.responses import Response as Raw

    return Raw(data, media_type=doc.mime_type,
               headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.filename)}"})


@router.post("/review/tasks/{task_id}/flags/{flag_id}/open")
def open_flag(task_id: str, flag_id: str, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    try:
        return review.open_flag(db, p.user, task_id, flag_id)
    except ReviewError as exc:
        _raise(exc)


@router.post("/review/tasks/{task_id}/flags/{flag_id}/acknowledge")
def ack_flag(task_id: str, flag_id: str, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    try:
        return review.acknowledge_flag(db, p.user, task_id, flag_id)
    except ReviewError as exc:
        _raise(exc)


class CommentIn(BaseModel):
    span_id: str | None = None
    body: str = ""
    kind: str = "comment"
    suggested_text: str | None = None


@router.post("/review/tasks/{task_id}/comments", status_code=201)
def add_comment(task_id: str, body: CommentIn, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    try:
        c = review.add_comment(db, p.user, task_id, body.span_id, body.body, body.kind, body.suggested_text)
    except ReviewError as exc:
        _raise(exc)
    return {"id": c.id, "span_id": c.span_id, "kind": c.kind, "body": c.body, "suggested_text": c.suggested_text,
            "created_at": c.created_at.isoformat() + "Z"}


@router.delete("/review/tasks/{task_id}/comments/{comment_id}", status_code=204)
def delete_comment(task_id: str, comment_id: str, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    try:
        review.delete_comment(db, p.user, task_id, comment_id)
    except ReviewError as exc:
        _raise(exc)


class DecisionIn(BaseModel):
    outcome: str
    reason: str = ""
    cited_span_ids: list[str] = Field(default_factory=list)
    confirm_warnings: bool = False


@router.post("/review/tasks/{task_id}/decision")
def decide(task_id: str, body: DecisionIn, request: Request, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    """Record a decision. Works from the web app, the API and the MCP server. Send X-PoR-Channel to label the
    client; API and MCP decisions carry no reading telemetry, so the workflow's reading standard applies to them."""
    try:
        result = review.submit_decision(db, p.user, task_id, body.outcome, body.reason, body.cited_span_ids,
                                        body.confirm_warnings, channel=request_channel(request))
    except ReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


class TelemetryIn(BaseModel):
    task_id: str
    events: list[dict] = Field(default_factory=list, max_length=1000)


@router.post("/telemetry", status_code=202)
def post_telemetry(body: TelemetryIn, request: Request, p: UserPrincipal = Depends(reviewer), db: Session = Depends(get_db)):
    """Batched reviewer events, sent by the workspace every 5 seconds. Only the web workspace measures reading,
    so telemetry from an API or MCP credential is refused (it could otherwise be made up to pass the standard)."""
    if request_channel(request) != "ui":
        raise HTTPException(403, "reading telemetry is only accepted from the web workspace")
    task = db.get(ReviewTask, body.task_id)
    if task is None or task.reviewer_id != p.user.id or task.tenant_id != p.tenant_id:
        raise HTTPException(404, "task not found")
    if task.status != "assigned":
        return {"stored": 0, "ignored": len(body.events), "reason": "task closed"}
    return {"stored": telemetry.ingest(db, task, body.events)}
