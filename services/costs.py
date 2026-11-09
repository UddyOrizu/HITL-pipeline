"""Cost breakdown: model spend (tokens x price) and reviewer time (measured minutes x hourly rate).

- Model prices are per million tokens in USD. Defaults below are Anthropic list prices for the first-party API
  (as of October 2026); tenants override or add models (including other providers) in settings.model_prices.
  Prices apply when the report runs, so correcting a price corrects past periods too.
- Reviewer time is the active reading time measured in the workspace. Decisions made over the API or MCP have no
  telemetry, so the task's effort estimate is used instead (reported separately as estimated minutes).
- Hourly rate: the user's own rate, else their team's, else the tenant's default_hourly_rate.
- Everything is reported in the tenant's currency (settings.currency, default USD); model spend is converted with
  settings.usd_to_currency.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Decision, ModelCall, ReviewTask, SourceApp, Submission, Team, Tenant, User, Workflow

PRICES_AS_OF = "2026-10"
# USD per million tokens: input, output, cache_read, cache_write (5-minute cache writes cost 1.25x input).
DEFAULT_MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5},
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": 12.5},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
}
GROUPS = ("source_app", "workflow", "team", "reviewer", "model", "submission", "day", "week", "month")


def tenant_cost_settings(tenant: Tenant) -> dict:
    st = tenant.settings or {}
    return {
        "currency": st.get("currency") or "USD",
        "usd_to_currency": float(st.get("usd_to_currency") or 1.0),
        "default_hourly_rate": st.get("default_hourly_rate"),
        "model_prices": {**DEFAULT_MODEL_PRICES, **(st.get("model_prices") or {})},
    }


def price_for(prices: dict[str, dict], model: str) -> dict | None:
    """Exact id first, then the longest known id contained in it (Bedrock/Vertex style ids, dated snapshots)."""
    if model in prices:
        return prices[model]
    hits = [k for k in prices if k in model]
    return prices[max(hits, key=len)] if hits else None


def call_cost_usd(prices: dict, c: ModelCall) -> float | None:
    p = price_for(prices, c.model)
    if p is None:
        return None
    return (c.input_tokens * p.get("input", 0) + c.output_tokens * p.get("output", 0)
            + c.cache_read_tokens * p.get("cache_read", p.get("input", 0) * 0.1)
            + c.cache_write_tokens * p.get("cache_write", p.get("input", 0) * 1.25)) / 1_000_000


def _period(dt: datetime, group: str) -> str:
    if group == "day":
        return dt.strftime("%Y-%m-%d")
    if group == "week":
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    return dt.strftime("%Y-%m")


def _empty() -> dict:
    return {"model_cost": 0.0, "model_cost_usd": 0.0, "labour_cost": 0.0, "input_tokens": 0, "output_tokens": 0,
            "model_calls": 0, "review_minutes": 0.0, "estimated_minutes": 0.0, "tasks": 0, "submissions": set(),
            "unrated_minutes": 0.0}


def breakdown(db: Session, tenant_id: str, start: datetime, end: datetime, group_by: str = "source_app",
              workflow_id: str | None = None, source_app_id: str | None = None) -> dict:
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {', '.join(GROUPS)}")
    tenant = db.get(Tenant, tenant_id)
    cfg = tenant_cost_settings(tenant)
    fx = cfg["usd_to_currency"]
    users = {u.id: u for u in db.scalars(select(User).where(User.tenant_id == tenant_id)).all()}
    teams = {t.id: t for t in db.scalars(select(Team).where(Team.tenant_id == tenant_id)).all()}
    apps = {a.id: a for a in db.scalars(select(SourceApp).where(SourceApp.tenant_id == tenant_id)).all()}
    workflows = {w.id: w for w in db.scalars(select(Workflow).where(Workflow.tenant_id == tenant_id)).all()}

    calls = db.scalars(select(ModelCall).where(ModelCall.tenant_id == tenant_id, ModelCall.created_at >= start,
                                               ModelCall.created_at < end)).all()
    tasks = db.execute(
        select(ReviewTask, Decision).outerjoin(Decision, Decision.review_task_id == ReviewTask.id)
        .where(ReviewTask.tenant_id == tenant_id, ReviewTask.status == "completed", ReviewTask.completed_at >= start,
               ReviewTask.completed_at < end)
    ).all()
    sub_ids = {c.submission_id for c in calls if c.submission_id} | {t.submission_id for t, _ in tasks}
    subs = {s.id: s for s in db.scalars(select(Submission).where(Submission.id.in_(sub_ids))).all()} if sub_ids else {}

    def keep(sub: Submission | None) -> bool:
        if workflow_id and (sub is None or workflows.get(sub.workflow_id) is None
                            or workflows[sub.workflow_id].family_id != workflow_id):
            return False
        return not (source_app_id and (sub is None or sub.source_app_id != source_app_id))

    def sub_key(sub: Submission | None, when: datetime) -> tuple[str, str]:
        if group_by in ("day", "week", "month"):
            p = _period(when, group_by)
            return p, p
        if sub is None:
            return "none", "Not linked to a submission"
        if group_by == "workflow":
            wf = workflows.get(sub.workflow_id)
            return (wf.family_id, wf.name) if wf else ("none", "Unknown workflow")
        if group_by == "source_app":
            a = apps.get(sub.source_app_id)
            return (a.id, a.name) if a else ("none", "Unknown source")
        if group_by == "submission":
            return sub.id, (sub.meta or {}).get("title") or sub.external_ref or sub.id[:8]
        return "none", "Not applicable"

    def rate_for(u: User | None) -> float | None:
        if u is None:
            return cfg["default_hourly_rate"]
        if u.hourly_rate is not None:
            return u.hourly_rate
        for tid in u.team_ids or []:
            t = teams.get(tid)
            if t and t.hourly_rate is not None:
                return t.hourly_rate
        return cfg["default_hourly_rate"]

    rows: dict[str, dict] = defaultdict(_empty)
    labels: dict[str, str] = {}
    unpriced: dict[str, int] = defaultdict(int)
    for c in calls:
        sub = subs.get(c.submission_id) if c.submission_id else None
        if not keep(sub):
            continue
        if group_by == "model":
            key, label = c.model, c.model
        elif group_by in ("team", "reviewer"):
            key, label = "model", "Model usage (not a reviewer cost)"
        else:
            key, label = sub_key(sub, c.created_at)
        usd = call_cost_usd(cfg["model_prices"], c)
        r = rows[key]
        labels[key] = label
        r["model_calls"] += 1
        r["input_tokens"] += c.input_tokens + c.cache_read_tokens + c.cache_write_tokens
        r["output_tokens"] += c.output_tokens
        if usd is None:
            if c.input_tokens or c.output_tokens:
                unpriced[c.model] += c.input_tokens + c.output_tokens
        else:
            r["model_cost_usd"] += usd
            r["model_cost"] += usd * fx
        if sub:
            r["submissions"].add(sub.id)

    for t, d in tasks:
        sub = subs.get(t.submission_id)
        if not keep(sub):
            continue
        u = users.get(t.reviewer_id)
        active = ((d.telemetry_summary or {}).get("active_seconds") or 0) if d else 0
        measured = active > 0
        minutes = active / 60 if measured else (t.effort_minutes or 0)
        if group_by == "model":
            key, label = "review", "Reviewer time"
        elif group_by == "reviewer":
            key, label = t.reviewer_id, (u.name or u.email) if u else t.reviewer_id
        elif group_by == "team":
            team = next((teams[x] for x in (u.team_ids or []) if x in teams), None) if u else None
            key, label = (team.id, team.name) if team else ("none", "No team")
        else:
            key, label = sub_key(sub, t.completed_at)
        r = rows[key]
        labels[key] = label
        r["tasks"] += 1
        r["review_minutes" if measured else "estimated_minutes"] += minutes
        rate = rate_for(u)
        if rate is None:
            r["unrated_minutes"] += minutes
        else:
            r["labour_cost"] += minutes / 60 * float(rate)
        if sub:
            r["submissions"].add(sub.id)

    out_rows = []
    totals = _empty()
    for key, r in rows.items():
        for k in totals:
            if k == "submissions":
                totals[k] |= r[k]
            else:
                totals[k] += r[k]
        out_rows.append(_finish(key, labels[key], r))
    out_rows.sort(key=lambda x: (-(x["total_cost"]), x["label"]) if group_by not in ("day", "week", "month") else x["key"])
    total = _finish("total", "Total", totals)
    n = total["submissions"]
    return {
        "currency": cfg["currency"], "usd_to_currency": fx, "group_by": group_by,
        "from": start.isoformat() + "Z", "to": end.isoformat() + "Z",
        "rows": out_rows, "total": total,
        "per_submission": round(total["total_cost"] / n, 6) if n else None,
        "unpriced_models": [{"model": m, "tokens": tok} for m, tok in sorted(unpriced.items())],
        "default_hourly_rate": cfg["default_hourly_rate"], "prices_as_of": PRICES_AS_OF,
    }


def _finish(key: str, label: str, r: dict) -> dict:
    return {
        "key": key, "label": label,
        "model_cost": round(r["model_cost"], 6), "model_cost_usd": round(r["model_cost_usd"], 6),
        "labour_cost": round(r["labour_cost"], 2),
        "total_cost": round(r["model_cost"] + r["labour_cost"], 6),
        "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"], "model_calls": r["model_calls"],
        "review_minutes": round(r["review_minutes"], 2), "estimated_minutes": round(r["estimated_minutes"], 2),
        "unrated_minutes": round(r["unrated_minutes"], 1), "tasks": r["tasks"], "submissions": len(r["submissions"]),
    }


COLUMNS = ["key", "label", "total_cost", "model_cost", "model_cost_usd", "labour_cost", "input_tokens", "output_tokens",
           "model_calls", "review_minutes", "estimated_minutes", "unrated_minutes", "tasks", "submissions"]


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: _csv_safe(r.get(k)) for k in columns})
    return buf.getvalue()


def _csv_safe(v):
    # Stop spreadsheet formula injection from names and titles.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v
