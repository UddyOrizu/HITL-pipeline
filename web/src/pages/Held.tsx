import { useEffect, useState } from "react";
import { api, apiPage, download, qs } from "../api";
import { Badge, Empty, ErrorBox, Pager, label, pct, riskTone, statusTone, useDebounced, useLoad, when } from "../components/ui";
import "./oversight.css";

type Action = "resolve" | "assign" | "defer" | "retry" | "cancel";
type Row = {
  id: string; status: string; hold_reason: string | null; escalated: boolean; workflow: string | null; risk_tier: string;
  external_ref: string | null; title: string | null; source_app: string | null; created_at: string; updated_at: string;
  final_outcome: string | null; score: number | null; processing_error: string | null; actions: Action[];
  deferred?: { reason: string | null; by: string; at: string } | null;
};
type Telemetry = Record<string, number | undefined>;
type Review = {
  task_id: string; kind: string; stage: string; status: string; reviewer: string | null; reviewer_id: string | null;
  assigned_at: string; due_at: string | null; sla_escalated: boolean;
  decision: { outcome: string; reason: string | null; channel: string | null; warnings: string[] | null; decided_at: string; telemetry: Telemetry | null } | null;
  comments: { span_id: string | null; kind: string; body: string; suggested_text: string | null }[];
};
type Flag = { id: string; span_id: string; category: string; severity: string; mandatory: boolean; rationale: string };
type Section = { id: string; kind: string; title: string | null; spans: { id: string; kind: string; text: string }[] };
type Detail = Row & {
  reviews: Review[]; flags: Flag[]; has_evidence_pack: boolean; reviewers: { id: string; name: string }[];
  score_detail: { score: number; below_threshold: boolean; computed_at: string; components: Record<string, { value: number | null; weight: number; points: number; sampled?: boolean }> } | null;
  document: { filename: string | null; render_kind: string | null; purged: boolean; sections: Section[] };
};

const LIMIT = 25;

const EXPLAIN: Record<string, string> = {
  spot_check_disagreement: "The blind second reviewer disagreed with the first. Decide the outcome.",
  below_threshold: "The Review Assurance Score was below this workflow's minimum and policy blocks release.",
  awaiting_capacity: "Every eligible reviewer is at their review budget. It will be assigned when capacity frees up, or assign it now.",
  awaiting_spot_check_reviewer: "No eligible second reviewer is available for the spot-check. Assign one, or decide the outcome yourself.",
  release_check_failed: "The content about to be released did not match the original submission. Investigate before releasing.",
};

const ACTION_NAME: Record<Action, string> = {
  resolve: "Decide outcome", assign: "Assign reviewer", defer: "Defer to source", retry: "Retry", cancel: "Cancel item",
};

const COMPONENT_NAME: Record<string, string> = {
  reading_coverage: "Reading and coverage", risk_engagement: "Risk flag engagement", reviewer_reliability: "Reviewer reliability",
  load: "Reviewer load", independent_check: "Independent check",
};

const SEV_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

function itemName(r: { title: string | null; external_ref: string | null; id: string }) {
  return r.title || r.external_ref || r.id.slice(0, 8);
}

function outcomeTone(o: string) {
  return o === "approve" || o === "approved" ? "ok" : o === "reject" || o === "rejected" ? "high" : "medium";
}

function secs(v: number | undefined) {
  if (v == null) return "–";
  return v >= 90 ? `${Math.round(v / 60)} min` : `${Math.round(v)}s`;
}

// --- Actions: each one keeps its own form state, keyed by the item, so nothing leaks between rows ---------------

function ActionPanel({ d, onDone }: { d: Detail; onDone: (msg: string) => void }) {
  const [open, setOpen] = useState<Action | null>(null);
  const [outcome, setOutcome] = useState("approve");
  const [reason, setReason] = useState("");
  const [reviewerId, setReviewerId] = useState("");
  const [deferReason, setDeferReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  function pick(a: Action) {
    setOpen(open === a ? null : a);
    setConfirm(false);
    setErr(null);
  }

  async function run(a: Action) {
    setErr(null);
    setBusy(true);
    try {
      if (a === "resolve") {
        await api(`/v1/oversight/submissions/${d.id}/resolve`, { body: { outcome, reason: reason.trim() } });
        onDone(`Decision recorded: ${label(outcome)}.`);
      } else {
        const body: Record<string, string> = { action: a };
        if (a === "assign") body.reviewer_id = reviewerId;
        if (a === "defer") body.reason = deferReason.trim();
        await api(`/v1/oversight/submissions/${d.id}/action`, { body });
        const who = d.reviewers.find((r) => r.id === reviewerId)?.name;
        onDone(a === "assign" ? `Assigned to ${who}.` : a === "defer" ? "Deferred. The source application has been told."
          : a === "retry" ? "Sent back for processing or review." : "Cancelled.");
      }
      setOpen(null);
      setConfirm(false);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  if (!d.actions.length) return <p className="sub">No actions are available for this item in its current state.</p>;

  // Destructive actions ask once more before they run.
  const destructive = open === "cancel" || open === "defer" || (open === "resolve" && outcome === "reject");
  const ready = open === "resolve" ? outcome === "approve" || !!reason.trim()
    : open === "assign" ? !!reviewerId : open === "defer" ? !!deferReason.trim() : true;
  const go = () => (destructive && !confirm ? setConfirm(true) : open && run(open));

  return (
    <div className="ov-actions">
      <div className="btn-row">
        {d.actions.map((a) => (
          <button key={a} className={`btn small ${a === "cancel" ? "danger" : ""}`} aria-pressed={open === a} onClick={() => pick(a)}
            style={open === a ? { outline: "2px solid var(--accent)" } : undefined}>
            {ACTION_NAME[a]}
          </button>
        ))}
      </div>
      {open && (
        <div className="ov-action-form">
          {open === "resolve" && (
            <>
              <label htmlFor="ov-outcome">Outcome</label>
              <select id="ov-outcome" value={outcome} onChange={(e) => { setOutcome(e.target.value); setConfirm(false); }}>
                <option value="approve">Approve</option>
                <option value="request_changes">Request changes</option>
                <option value="reject">Reject</option>
              </select>
              <label htmlFor="ov-reason">Reason {outcome === "approve" ? "(optional)" : "(required)"} – recorded in the evidence pack</label>
              <textarea id="ov-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
            </>
          )}
          {open === "assign" && (
            <>
              <label htmlFor="ov-reviewer">Reviewer</label>
              <select id="ov-reviewer" value={reviewerId} onChange={(e) => setReviewerId(e.target.value)}>
                <option value="">Choose a reviewer…</option>
                {d.reviewers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
              <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
                Assigning goes past the workload caps. People who already reviewed this item, or wrote it, can't be chosen.
              </p>
            </>
          )}
          {open === "defer" && (
            <>
              <label htmlFor="ov-defer">Reason (sent to the source application)</label>
              <textarea id="ov-defer" value={deferReason} onChange={(e) => setDeferReason(e.target.value)} />
              <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>Open review tasks close and the item stops here. It can be retried later.</p>
            </>
          )}
          {open === "retry" && <p style={{ margin: 0 }}>Retry this item: it goes back to processing if that failed, otherwise back into review.</p>}
          {open === "cancel" && <p style={{ margin: 0 }}>Cancel this item. Open review tasks close and the source application is told it was cancelled.</p>}

          {confirm && (
            <div className="notice">
              {open === "cancel" ? "Cancelling can't be undone. Are you sure?"
                : open === "defer" ? "Deferring stops review and notifies the source application. Continue?"
                : "Rejecting releases a final rejection to the source application. Continue?"}
            </div>
          )}
          <ErrorBox error={err} />
          <div className="btn-row" style={{ marginTop: 10 }}>
            <button className={`btn ${destructive ? "danger" : "primary"}`} disabled={busy || !ready} onClick={go}>
              {busy ? "Working…" : confirm ? "Yes, confirm" : open === "resolve" ? "Record decision" : ACTION_NAME[open]}
            </button>
            <button className="btn" onClick={() => (confirm ? setConfirm(false) : setOpen(null))}>{confirm ? "Go back" : "Close"}</button>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Detail drawer ------------------------------------------------------------------------------------------

function ReviewCard({ r }: { r: Review }) {
  const d = r.decision;
  const t = d?.telemetry || null;
  return (
    <div className={`ov-review ${d ? "" : "pending"}`}>
      <div className="ov-badges" style={{ justifyContent: "space-between" }}>
        <strong>{r.kind === "spot_check" ? "Spot-check" : r.stage}</strong>
        {d ? <Badge tone={outcomeTone(d.outcome)}>{label(d.outcome)}</Badge> : <Badge>{label(r.status)}</Badge>}
      </div>
      <div className="sub" style={{ fontSize: 12 }}>
        {r.reviewer || "Unassigned"} · {label(r.kind)}
        {d ? <> · decided {when(d.decided_at)}</> : <> · assigned {when(r.assigned_at)}{r.due_at ? `, due ${when(r.due_at)}` : ""}</>}
      </div>
      <div className="ov-badges" style={{ marginTop: 6 }}>
        {d?.channel && <Badge tone={d.channel === "ui" ? "ok" : "medium"}>via {d.channel === "ui" ? "workspace" : d.channel}</Badge>}
        {r.sla_escalated && <Badge tone="high">SLA breached</Badge>}
      </div>
      {d && <div className="reason">{d.reason?.trim() || <span className="sub">No reason given.</span>}</div>}
      {t && (
        <dl className="ov-tele">
          <dt>Active time</dt><dd>{secs(t.active_seconds)} of {secs(t.expected_seconds)} expected</dd>
          <dt>Reading ratio</dt><dd>{pct(t.reading_ratio)}</dd>
          <dt>Section coverage</dt><dd>{pct(t.coverage)}{t.sections_total != null ? ` (${t.sections_viewed ?? 0}/${t.sections_total})` : ""}</dd>
          {!!t.flags_total && <><dt>Flags acknowledged</dt><dd>{t.flags_acknowledged ?? 0}/{t.flags_total}</dd></>}
          {!!t.focus_lost_count && <><dt>Focus lost</dt><dd>{t.focus_lost_count}×</dd></>}
          {(t.comments || t.edits) ? <><dt>Comments / edits</dt><dd>{t.comments ?? 0} / {t.edits ?? 0}</dd></> : null}
        </dl>
      )}
      {d && !t && d.channel && d.channel !== "ui" && <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>No reading telemetry: decided outside the workspace.</p>}
      {!!d?.warnings?.length && <ul>{d.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {!!r.comments.length && (
        <div style={{ marginTop: 10 }}>
          <div className="sub" style={{ fontSize: 12, fontWeight: 600 }}>Comments</div>
          {r.comments.map((c, i) => (
            <div key={i} className="comment" style={{ marginTop: 6 }}>
              {c.kind === "edit" && <Badge tone="accent">suggested edit</Badge>} {c.body}
              {c.suggested_text && <div className="ov-quote">{c.suggested_text}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DetailDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { data: d, error, loading, reload } = useLoad(() => api<Detail>(`/v1/oversight/submissions/${id}`), [id]);
  const [msg, setMsg] = useState("");
  const [pdfErr, setPdfErr] = useState<unknown>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const spanText = new Map<string, string>();
  d?.document.sections.forEach((s) => s.spans.forEach((sp) => spanText.set(sp.id, sp.text)));
  const spanSev = new Map<string, string>();
  d?.flags.forEach((f) => {
    const cur = spanSev.get(f.span_id);
    if (!cur || SEV_RANK[f.severity] > SEV_RANK[cur]) spanSev.set(f.span_id, f.severity);
  });
  const decided = d?.reviews.filter((r) => r.decision) || [];
  const open = d?.reviews.filter((r) => !r.decision) || [];

  async function pdf() {
    setPdfErr(null);
    try {
      await download(`/v1/audit/submissions/${id}/evidence.pdf`, `evidence-${id}.pdf`);
    } catch (e) {
      setPdfErr(e);
    }
  }

  return (
    <>
      <div className="drawer-back" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Held item">
        <div className="ov-drawer-head">
          <div style={{ minWidth: 0 }}>
            <h2>{d ? itemName(d) : "Loading…"}</h2>
            {d && (
              <div className="sub" style={{ fontSize: 12 }}>
                <span className="mono">{d.id}</span>{d.external_ref ? ` · ref ${d.external_ref}` : ""}
              </div>
            )}
          </div>
          <button className="btn small" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <ErrorBox error={error} />
        {loading && !d && <p className="sub">Loading…</p>}
        {d && (
          <>
            <div className="ov-badges">
              <Badge tone={statusTone(d.status)}>{label(d.status)}</Badge>
              {d.hold_reason && <Badge tone="medium">{label(d.hold_reason)}</Badge>}
              {d.escalated && <Badge tone="high">escalated</Badge>}
              <Badge tone={riskTone(d.risk_tier)}>{d.risk_tier} risk</Badge>
            </div>
            {d.hold_reason && EXPLAIN[d.hold_reason] && <p className="sub" style={{ marginTop: 8 }}>{EXPLAIN[d.hold_reason]}</p>}
            {d.processing_error && <div className="error"><strong>Processing error:</strong> {d.processing_error}</div>}
            {d.deferred && !d.processing_error && (
              <div className="notice">Deferred {when(d.deferred.at)}{d.deferred.reason ? `: ${label(d.deferred.reason)}` : ""}.</div>
            )}
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>Workflow</dt><dd>{d.workflow || "–"}</dd>
              <dt>Source</dt><dd>{d.source_app || "–"}</dd>
              <dt>Received</dt><dd>{when(d.created_at)}</dd>
              <dt>Last change</dt><dd>{when(d.updated_at)}</dd>
              {d.final_outcome && <><dt>Final outcome</dt><dd>{label(d.final_outcome)}</dd></>}
              <dt>Evidence pack</dt>
              <dd>{d.has_evidence_pack ? <button className="btn small" onClick={pdf}>Download evidence PDF</button> : <span className="sub">Not generated yet</span>}</dd>
            </dl>
            <ErrorBox error={pdfErr} />

            <section className="ov-section">
              <h3>Actions</h3>
              {msg && <div className="success">{msg}</div>}
              <ActionPanel key={d.id} d={d} onDone={(m) => { setMsg(m); reload(); onChanged(); }} />
            </section>

            <section className="ov-section">
              <h3>Decisions</h3>
              {!decided.length ? <p className="sub">No decisions yet.</p> : (
                <div className="ov-compare">{decided.map((r) => <ReviewCard key={r.task_id} r={r} />)}</div>
              )}
              {!!open.length && (
                <>
                  <div className="sub" style={{ fontSize: 12, margin: "12px 0 6px" }}>Open or closed without a decision</div>
                  <div className="ov-compare">{open.map((r) => <ReviewCard key={r.task_id} r={r} />)}</div>
                </>
              )}
            </section>

            <section className="ov-section">
              <h3>Review Assurance Score</h3>
              {!d.score_detail ? <p className="sub">Not scored yet.</p> : (
                <>
                  <p>
                    <strong style={{ fontSize: 20 }}>{d.score_detail.score}</strong> / 100{" "}
                    {d.score_detail.below_threshold && <Badge tone="medium">below workflow minimum</Badge>}
                    <span className="sub" style={{ fontSize: 12 }}> · computed {when(d.score_detail.computed_at)}</span>
                  </p>
                  <table className="ov-score-table">
                    <thead><tr><th>Component</th><th>Value</th><th>Weight</th><th>Points</th></tr></thead>
                    <tbody>
                      {Object.entries(d.score_detail.components || {}).map(([k, c]) => (
                        <tr key={k}>
                          <td>{COMPONENT_NAME[k] || label(k)}{k === "independent_check" && c.sampled === false ? <span className="sub"> (not sampled)</span> : null}</td>
                          <td>{pct(c.value)}</td>
                          <td>{pct(c.weight)}</td>
                          <td>{c.points}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </section>

            <section className="ov-section">
              <h3>Risk flags ({d.flags.length})</h3>
              {!d.flags.length ? <p className="sub">No risk flags.</p> : [...d.flags]
                .sort((a, b) => SEV_RANK[b.severity] - SEV_RANK[a.severity])
                .map((f) => (
                  <div key={f.id} className="ov-flag">
                    <div className="ov-badges">
                      <Badge tone={riskTone(f.severity)}>{f.severity}</Badge>
                      <strong>{label(f.category)}</strong>
                      {f.mandatory && <Badge tone="accent">must acknowledge</Badge>}
                    </div>
                    <div style={{ marginTop: 4 }}>{f.rationale}</div>
                    {spanText.get(f.span_id) && <div className="ov-quote">{spanText.get(f.span_id)}</div>}
                  </div>
                ))}
            </section>

            <section className="ov-section">
              <h3>Original document{d.document.filename ? ` · ${d.document.filename}` : ""}</h3>
              {d.document.purged ? <div className="notice">The content has been purged under the retention policy. The record and hashes remain.</div>
                : !d.document.sections.length ? <p className="sub">No document text.</p> : (
                  <div className="ov-doc">
                    {d.document.sections.map((s) => (
                      <div key={s.id} className="ov-doc-section">
                        {s.title && <div className="doc-section-title">{s.title}</div>}
                        {s.spans.map((sp) => (
                          <p key={sp.id} className={`${sp.kind} ${spanSev.has(sp.id) ? `sev-${spanSev.get(sp.id)}` : ""}`}>{sp.text}</p>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
            </section>
          </>
        )}
      </aside>
    </>
  );
}

// --- Page ---------------------------------------------------------------------------------------------------

export default function Held() {
  const [tab, setTab] = useState<"held" | "deferred">("held");
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 300);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => setOffset(0), [tab, dq]);

  const path = `/v1/oversight/submissions${qs({
    held: tab === "held" ? true : undefined, status: tab === "deferred" ? "deferred" : undefined, q: dq, offset, limit: LIMIT,
  })}`;
  const { data, error, loading, reload } = useLoad(() => apiPage<Row>(path), [path]);
  const rows = data?.items || [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Held items</h1>
          <div className="sub">Items the workflow will not release on its own, and items deferred back to their source. Nothing here has been released.</div>
        </div>
        <button className="btn" onClick={reload}>Refresh</button>
      </div>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "held"} onClick={() => setTab("held")}>Held</button>
        <button role="tab" aria-selected={tab === "deferred"} onClick={() => setTab("deferred")}>Deferred</button>
      </div>
      <div className="toolbar">
        <input placeholder="Search by title, reference or id" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 280 }}
          aria-label="Search" />
        {data && <span className="sub">{data.total} item{data.total === 1 ? "" : "s"}</span>}
      </div>

      <ErrorBox error={error} />
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {loading && !data ? <Empty>Loading…</Empty> : !rows.length ? (
          <Empty>{dq ? "No items match." : tab === "held" ? "Nothing is held." : "Nothing has been deferred."}</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>{tab === "held" ? "Why it is held" : "Why it was deferred"}</th>
                <th>Risk</th><th>Score</th><th>{tab === "held" ? "Received" : "Deferred"}</th><th>Can do</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => setOpen(r.id)} tabIndex={0}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setOpen(r.id))}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{itemName(r)}</div>
                    <div className="sub" style={{ fontSize: 12 }}>{r.workflow || "–"}{r.source_app ? ` · ${r.source_app}` : ""} · {label(r.status)}</div>
                  </td>
                  <td>
                    {tab === "held" ? (
                      <>
                        <div className="ov-badges">
                          <Badge tone="medium">{label(r.hold_reason)}</Badge>
                          {r.escalated && <Badge tone="high">escalated</Badge>}
                        </div>
                        <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{EXPLAIN[r.hold_reason || ""]}</div>
                      </>
                    ) : r.processing_error ? (
                      <div style={{ fontSize: 13, color: "var(--high)" }}>Processing failed: {r.processing_error}</div>
                    ) : <span className="sub">Deferred{r.deferred?.reason ? `: ${label(r.deferred.reason)}` : ""}
                        {r.deferred?.by ? ` (${r.deferred.by.startsWith("user:") ? "by an oversight lead" : "by the system"})` : ""}</span>}
                  </td>
                  <td><Badge tone={riskTone(r.risk_tier)}>{r.risk_tier}</Badge></td>
                  <td>{r.score ?? "–"}</td>
                  <td>{when(tab === "held" ? r.created_at : r.updated_at)}</td>
                  <td className="sub" style={{ fontSize: 12 }}>{r.actions.map((a) => ACTION_NAME[a].toLowerCase()).join(", ") || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && <Pager offset={offset} limit={LIMIT} total={data.total} onChange={setOffset} />}

      {open && <DetailDrawer key={open} id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}
