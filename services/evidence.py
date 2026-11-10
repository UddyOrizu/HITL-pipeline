"""Evidence service: per-decision evidence record (JSON) and PDF evidence pack.

The record carries canary presence, type and outcome but never the canary text, so it can be handed
to a source application, auditor, client or regulator as it stands.
"""

from __future__ import annotations

import hashlib
import html
import io
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Canary,
    Decision,
    EvidencePack,
    FlagAcknowledgement,
    ReviewTask,
    RiskFlag,
    Submission,
    User,
    Workflow,
    utcnow,
)
from services import assurance, audit, outbound
from services.storage import get_storage


def build_record(session: Session, sub: Submission) -> dict:
    wf = session.get(Workflow, sub.workflow_id)
    canaries = outbound.canaries_for(session, [sub.id])
    canary_span_ids = {c.span_id for c in canaries}
    tasks = session.scalars(select(ReviewTask).where(ReviewTask.submission_id == sub.id).order_by(ReviewTask.assigned_at)).all()
    decisions = {d.review_task_id: d for d in session.scalars(select(Decision).where(Decision.submission_id == sub.id)).all()}
    acks = session.scalars(select(FlagAcknowledgement).where(FlagAcknowledgement.review_task_id.in_([t.id for t in tasks]))).all()

    flags = []
    for f in session.scalars(select(RiskFlag).where(RiskFlag.submission_id == sub.id)).all():
        if f.span_id in canary_span_ids:
            continue
        f_acks = [a for a in acks if a.risk_flag_id == f.id]
        flags.append({
            "id": f.id, "span_id": f.span_id, "category": f.category, "severity": f.severity, "mandatory": f.mandatory,
            "rationale": f.rationale,
            "opened": any(a.opened_at for a in f_acks), "acknowledged": any(a.acknowledged_at for a in f_acks),
        })

    reviews = []
    for t in tasks:
        reviewer = session.get(User, t.reviewer_id)
        d = decisions.get(t.id)
        reviews.append({
            "task_id": t.id, "reviewer_id": t.reviewer_id, "reviewer_roles": reviewer.roles if reviewer else [],
            "kind": t.kind, "stage": t.stage_name, "status": t.status, "effort_minutes": t.effort_minutes,
            "assigned_at": t.assigned_at.isoformat() + "Z", "completed_at": t.completed_at.isoformat() + "Z" if t.completed_at else None,
            "sla_escalated": t.sla_escalated,
            "decision": {
                "outcome": d.outcome, "reason": outbound.redact(d.reason, canaries), "warnings": d.warnings,
                "decided_at": d.decided_at.isoformat() + "Z", "channel": d.channel,
                "telemetry_summary": d.telemetry_summary,
            } if d else None,
        })

    primary = [r for r in reviews if r["kind"] == "primary" and r["decision"]]
    spot = [r for r in reviews if r["kind"] == "spot_check" and r["decision"]]
    spot_open = [r for r in reviews if r["kind"] == "spot_check" and r["status"] == "assigned"]
    spot_result = None
    if spot:
        agree = primary and spot[-1]["decision"]["outcome"] == primary[-1]["decision"]["outcome"]
        spot_result = {"sampled": True, "pending": False, "agreement": bool(agree),
                       "spot_check_outcome": spot[-1]["decision"]["outcome"]}
    elif spot_open or sub.status == "spot_check":
        # Sampled for a second review that has not been decided yet.
        spot_result = {"sampled": True, "pending": True, "agreement": None, "spot_check_outcome": None}

    ids = [sub.id] + [d.id for d in sub.documents] + [t.id for t in tasks]
    events = audit.events_for(session, sub.tenant_id, ids)
    trail = [
        {"seq": e.seq, "at": e.created_at.isoformat() + "Z", "actor": e.actor, "action": e.action, "entity": e.entity,
         "entity_id": e.entity_id, "payload": e.payload, "prev_hash": e.prev_hash, "hash": e.hash}
        for e in events
    ]
    # Verify this submission's events and their links to the chain (not the whole tenant chain, which grows
    # without bound), plus the current head; record the head so a later full verification can detect events
    # removed from the end of the chain. GET /v1/audit/verify still walks the whole chain.
    head_seq, head_hash = audit.head(session, sub.tenant_id)
    from db.models import AuditEvent

    head_event = session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == sub.tenant_id,
                                                          AuditEvent.seq == head_seq)).first() if head_seq else None
    checked = list(events) + ([head_event] if head_event is not None and head_event.seq not in {e.seq for e in events} else [])
    chain_valid = audit.verify_events(session, sub.tenant_id, checked)

    record = {
        "schema": "proof-of-review/evidence/v1",
        "generated_at": utcnow().isoformat() + "Z",
        "submission": {
            "id": sub.id, "external_ref": sub.external_ref, "status": sub.status, "content_origin": sub.content_origin,
            "risk_tier": sub.risk_tier, "ai_model": sub.ai_model, "received_at": sub.created_at.isoformat() + "Z",
            "released_at": sub.released_at.isoformat() + "Z" if sub.released_at else None,
            "final_outcome": sub.final_outcome, "final_reason": outbound.redact(sub.final_reason, canaries),
        },
        "documents": [
            {"id": d.id, "role": d.role, "filename": d.filename, "mime_type": d.mime_type, "sha256": d.sha256, "size_bytes": d.size_bytes}
            for d in sub.documents
        ],
        "content_hash": sub.content_hash,
        "workflow": {
            "id": wf.family_id, "version": wf.version, "name": wf.name, "stages": wf.stages, "canary_rate": wf.canary_rate,
            "spot_check_rate": wf.spot_check_rate, "telemetry_policy": wf.telemetry_policy, "cap_policy": wf.cap_policy,
            "assurance_policy": assurance.policy_for(wf),
        },
        "risk_flags": flags,
        "reviews": reviews,
        "canary": {
            "present": bool(canaries),
            "items": [{"type": c.type, "difficulty": c.difficulty, "caught": c.caught,
                       "revealed_at": c.revealed_at.isoformat() + "Z" if c.revealed_at else None} for c in canaries],
        },
        "spot_check": spot_result or {"sampled": False},
        "assurance": assurance.score_payload(assurance.latest(session, sub.id)),
        "audit_trail": trail,
        "audit_chain": {"valid": chain_valid, "scope": "submission_events", "events_verified": len(checked),
                        "events_in_tenant_chain": head_seq, "head_seq": head_seq, "head_hash": head_hash},
    }
    outbound.assert_no_canary_leak(session, record, [sub.id])
    return record


def _row(cells: list[str], header: bool = False) -> str:
    tag = "th" if header else "td"
    return "<tr>" + "".join(f"<{tag}>{html.escape(str(c))}</{tag}>" for c in cells) + "</tr>"


def render_pdf(record: dict) -> bytes:
    import pymupdf

    s = record["submission"]
    a = record["assurance"] or {}
    parts = [
        "<h1>Proof of Review: evidence pack</h1>",
        f"<p>Submission <b>{html.escape(s['id'])}</b> &middot; ref {html.escape(str(s['external_ref'] or '-'))}<br/>"
        f"Outcome: <b>{html.escape(str(s['final_outcome'] or s['status']))}</b> &middot; "
        f"Review Assurance Score: <b>{a.get('score', '-')}</b>{' (below threshold)' if a.get('below_threshold') else ''}<br/>"
        f"Generated {html.escape(record['generated_at'])}</p>",
        "<h2>Content</h2><table>",
        _row(["Role", "File", "SHA-256"], True),
        *[_row([d["role"], d["filename"], d["sha256"]]) for d in record["documents"]],
        "</table>",
        f"<p>Normalised content hash: {html.escape(str(record['content_hash']))}<br/>"
        f"Origin: {html.escape(s['content_origin'])} &middot; risk tier: {html.escape(s['risk_tier'])} &middot; "
        f"model: {html.escape(str(s['ai_model'] or '-'))}</p>",
        f"<h2>Workflow</h2><p>{html.escape(record['workflow']['name'])}, version {record['workflow']['version']}. "
        f"Canary rate {record['workflow']['canary_rate']}, spot-check rate {record['workflow']['spot_check_rate']}.</p>",
        "<h2>Assurance score</h2><table>",
        _row(["Component", "Value", "Weight", "Points"], True),
        *[_row([k.replace("_", " "), v.get("value"), v.get("weight"), v.get("points")]) for k, v in (a.get("components") or {}).items()],
        "</table>",
        "<h2>Reviews</h2><table>",
        _row(["Kind", "Stage", "Reviewer", "Outcome", "Channel", "Active s", "Expected s", "Coverage"], True),
    ]
    for r in record["reviews"]:
        d = r["decision"] or {}
        t = d.get("telemetry_summary") or {}
        parts.append(_row([r["kind"], r["stage"], r["reviewer_id"][:8], d.get("outcome", r["status"]), d.get("channel", "-"),
                           t.get("active_seconds", "-"), t.get("expected_seconds", "-"), t.get("coverage", "-")]))
    parts.append("</table>")
    for r in record["reviews"]:
        d = r["decision"]
        if d and d.get("reason"):
            parts.append(f"<p><b>{html.escape(r['stage'])} reason:</b> {html.escape(d['reason'])}</p>")
    c = record["canary"]
    parts.append("<h2>Canary</h2>")
    if c["present"]:
        for item in c["items"]:
            status = "caught" if item["caught"] else ("missed" if item["caught"] is False else "not scored")
            parts.append(f"<p>A {html.escape(item['difficulty'])} {html.escape(item['type'].replace('_', ' '))} canary was present and was {status}. "
                         "It existed only in the review copy.</p>")
    else:
        parts.append("<p>No canary was present in this review.</p>")
    sc = record["spot_check"]
    parts.append("<h2>Spot-check</h2><p>" + (
        "Sampled for blind second review: pending." if sc.get("pending") else
        f"Sampled for blind second review: {'agreement' if sc.get('agreement') else 'disagreement, escalated to oversight'}."
        if sc.get("sampled") else "Not sampled.") + "</p>")
    parts.append("<h2>Risk flags</h2><table>")
    parts.append(_row(["Category", "Severity", "Mandatory", "Opened", "Acknowledged"], True))
    for f in record["risk_flags"]:
        parts.append(_row([f["category"], f["severity"], "yes" if f["mandatory"] else "no",
                           "yes" if f["opened"] else "no", "yes" if f["acknowledged"] else "no"]))
    parts.append("</table>")
    ch = record["audit_chain"]
    parts.append(f"<h2>Audit trail</h2><p>Tenant chain valid for this submission's events and their links: "
                 f"<b>{'yes' if ch['valid'] else 'NO'}</b>; chain head seq {ch.get('head_seq', '-')}, "
                 f"hash {html.escape(str(ch['head_hash']))}.</p><table>")
    parts.append(_row(["Seq", "Time", "Actor", "Action", "Hash"], True))
    for e in record["audit_trail"]:
        parts.append(_row([e["seq"], e["at"], e["actor"][:28], e["action"], e["hash"][:16] + "..."]))
    parts.append("</table>")

    css = ("body{font-family:sans-serif;font-size:9pt;} h1{font-size:16pt;} h2{font-size:12pt;margin-top:10pt;}"
           "table{border-collapse:collapse;width:100%;} th,td{border:0.5pt solid #999;padding:2pt;text-align:left;font-size:8pt;}")
    story = pymupdf.Story(html="".join(parts), user_css=css)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (36, 36, -36, -36)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def generate_pack(session: Session, sub: Submission) -> EvidencePack:
    record = build_record(session, sub)
    data_json = json.dumps(record, indent=2, sort_keys=True).encode()
    pdf = render_pdf(record)
    storage = get_storage()
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    pdf_uri = storage.put(f"{sub.tenant_id}/{sub.id}/evidence/{stamp}.pdf", pdf, "application/pdf")
    json_uri = storage.put(f"{sub.tenant_id}/{sub.id}/evidence/{stamp}.json", data_json, "application/json")
    chain = record["audit_chain"]
    pack = EvidencePack(tenant_id=sub.tenant_id, submission_id=sub.id, file_uri=pdf_uri, json_uri=json_uri,
                        sha256=hashlib.sha256(pdf).hexdigest(), generated_at=utcnow(),
                        audit_head_seq=chain["head_seq"] or None, audit_head_hash=chain["head_hash"] if chain["head_seq"] else None)
    session.add(pack)
    session.flush()
    audit.record(session, sub.tenant_id, "system:evidence", "evidence.generated", "submission", sub.id,
                 {"pack_id": pack.id, "pdf_sha256": pack.sha256})
    return pack


def latest_pack(session: Session, sub: Submission) -> EvidencePack | None:
    return session.scalars(
        select(EvidencePack).where(EvidencePack.submission_id == sub.id).order_by(EvidencePack.generated_at.desc())
    ).first()


__all__ = ["Canary", "build_record", "generate_pack", "latest_pack", "render_pdf"]
