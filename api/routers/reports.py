"""Reports and cost breakdown. Add ?format=csv to any report for a spreadsheet-friendly export.

- Overview (oversight leads, auditors): throughput, turnaround, outcomes, SLAs, scores, by workflow / source /
  risk tier / origin / channel, with a time series. The CSV is long-format: a "section" column (kpi, series,
  by_workflow, by_source_app, by_risk_tier, by_origin, by_channel, held_by_reason) tags each row.
- Reviewers (oversight leads): per-reviewer workload and review quality.
- SLA (oversight leads): tasks overdue now.
- Costs (oversight leads; admins): model spend and reviewer time, grouped by source app, workflow, team, reviewer,
  model, submission or period.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, require_roles
from db.models import SourceApp, Workflow, utcnow
from services import costs, reports

router = APIRouter(prefix="/v1/reports", tags=["reports"])

readers = require_roles("oversight_lead", "auditor")
leads = require_roles("oversight_lead")


def _range(start: date | None, end: date | None, default_days: int = 30) -> tuple[datetime, datetime]:
    """Inclusive dates in, [start, end) datetimes out. Defaults to the last 30 days."""
    today = utcnow().date()
    end_d = end or today
    start_d = start or (end_d - timedelta(days=default_days - 1))
    if start_d > end_d:
        raise HTTPException(422, "'from' must be on or before 'to'")
    if (end_d - start_d).days > 731:
        raise HTTPException(422, "the range can be at most two years")
    return datetime.combine(start_d, time.min), datetime.combine(end_d + timedelta(days=1), time.min)


def _csv(text: str, name: str) -> Response:
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/filters")
def filters(p: UserPrincipal = Depends(readers), db: Session = Depends(get_db)):
    """Workflows and source apps by name, for report filters."""
    from sqlalchemy import select

    return {
        "workflows": [{"id": w.family_id, "name": w.name} for w in db.scalars(
            select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.is_current.is_(True)).order_by(Workflow.name)).all()],
        "source_apps": [{"id": a.id, "name": a.name, "kind": a.kind} for a in db.scalars(
            select(SourceApp).where(SourceApp.tenant_id == p.tenant_id).order_by(SourceApp.name)).all()],
    }


@router.get("/overview")
def overview(
    from_: date | None = Query(None, alias="from"), to: date | None = None,
    interval: str = Query("day", pattern="^(day|week|month)$"), workflow_id: str | None = None,
    source_app_id: str | None = None, format: str = Query("json", pattern="^(json|csv)$"),
    p: UserPrincipal = Depends(readers), db: Session = Depends(get_db),
):
    start, end = _range(from_, to)
    data = reports.overview(db, p.tenant_id, start, end, interval, workflow_id, source_app_id)
    if format == "csv":
        return _csv(costs.to_csv(reports.overview_csv_rows(data), reports.OVERVIEW_COLUMNS),
                    f"overview-{from_ or start.date()}-{to or ''}")
    return data


@router.get("/reviewers")
def reviewer_report(
    from_: date | None = Query(None, alias="from"), to: date | None = None,
    format: str = Query("json", pattern="^(json|csv)$"),
    p: UserPrincipal = Depends(leads), db: Session = Depends(get_db),
):
    start, end = _range(from_, to)
    rows = reports.reviewers(db, p.tenant_id, start, end)
    if format == "csv":
        return _csv(costs.to_csv(rows, reports.REVIEWER_COLUMNS), "reviewers")
    return {"from": start.isoformat() + "Z", "to": end.isoformat() + "Z", "rows": rows}


@router.get("/sla")
def sla_report(format: str = Query("json", pattern="^(json|csv)$"), p: UserPrincipal = Depends(leads),
               db: Session = Depends(get_db)):
    data = reports.sla(db, p.tenant_id)
    if format == "csv":
        return _csv(costs.to_csv(data["overdue"], reports.SLA_COLUMNS), f"sla-overdue-{utcnow().date()}")
    return data


@router.get("/costs")
def cost_report(
    from_: date | None = Query(None, alias="from"), to: date | None = None,
    group_by: str = Query("source_app", pattern="^(" + "|".join(costs.GROUPS) + ")$"),
    workflow_id: str | None = None, source_app_id: str | None = None,
    format: str = Query("json", pattern="^(json|csv)$"),
    p: UserPrincipal = Depends(leads), db: Session = Depends(get_db),
):
    start, end = _range(from_, to)
    data = costs.breakdown(db, p.tenant_id, start, end, group_by, workflow_id, source_app_id)
    if format == "csv":
        return _csv(costs.to_csv(data["rows"] + [data["total"]], costs.COLUMNS), f"costs-by-{group_by}")
    return data
