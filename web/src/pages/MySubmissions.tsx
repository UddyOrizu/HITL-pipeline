import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, apiPage, download, qs } from "../api";
import { Badge, Empty, ErrorBox, label, Pager, statusTone, useLoad, when } from "../components/ui";
import "./review.css";

type Comment = {
  span_id: string | null; quote: string | null; kind: string; body: string; suggested_text: string | null;
  stage: string | null; created_at: string | null;
};
type Sub = {
  id: string; title: string | null; status: string; current_stage: string | null; risk_tier: string; created_at: string;
  workflow_id: string | null; previous_submission_id: string | null;
  decision: { outcome: string; reason: string | null; assurance?: { score: number | null } | null } | null;
  feedback: Comment[]; has_evidence_pack: boolean;
};

// Still moving through review: the list refreshes itself while any of these are on screen.
const OPEN = new Set(["received", "processing", "in_review", "spot_check"]);
// Finished with a request to change the work: the submitter can send a revision.
const REVISABLE = new Set(["changes_requested", "rejected"]);
const LIMIT = 25;
// The portal list's ?status= filter (one exact submission status).
const STATUSES = ["received", "processing", "in_review", "spot_check", "approved", "rejected", "changes_requested", "deferred", "cancelled"];
const REFRESH_MS = 15000;

function isVisible() {
  return typeof document === "undefined" || document.visibilityState === "visible";
}

export default function MySubmissions() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const fresh = params.get("new");
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(
    () => apiPage<Sub>(`/v1/portal/submissions${qs({ status, offset, limit: LIMIT })}`), [status, offset]);
  const [open, setOpen] = useState<string | null>(fresh);
  const [err, setErr] = useState<unknown>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const items = data?.items ?? [];
  const live = items.some((s) => OPEN.has(s.status));

  useEffect(() => {
    if (data) setUpdatedAt(new Date());
  }, [data]);

  // Auto-refresh every 15 s while something listed is still in progress and the tab is visible; refresh at once
  // when the tab becomes visible again.
  const reloadRef = useRef(reload);
  reloadRef.current = reload;
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => {
      if (isVisible()) reloadRef.current();
    }, REFRESH_MS);
    const onVis = () => isVisible() && reloadRef.current();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [live]);

  async function cancel(id: string) {
    setErr(null);
    try {
      await api(`/v1/portal/submissions/${id}/cancel`, { method: "POST" });
      reload();
    } catch (e) {
      setErr(e);
    }
  }

  async function evidence(id: string) {
    setErr(null);
    try {
      await download(`/v1/portal/submissions/${id}/evidence.pdf`, `evidence-${id}.pdf`);
    } catch (e) {
      setErr(e);
    }
  }

  function revise(s: Sub) {
    nav("/submit", { state: { previous_submission_id: s.id, title: s.title, workflow_id: s.workflow_id } });
  }

  const toggle = (id: string) => setOpen(open === id ? null : id);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My submissions</h1>
          <div className="sub">Work you've sent for review, with the decision and evidence once it's released.</div>
        </div>
        <div className="btn-row">
          <select aria-label="Filter by status" value={status} style={{ width: 190 }}
            onChange={(e) => { setStatus(e.target.value); setOffset(0); setOpen(null); }}>
            <option value="">All statuses</option>
            {STATUSES.map((st) => <option key={st} value={st}>{label(st)}</option>)}
          </select>
          <button className="btn" onClick={reload} disabled={loading}>{loading && data ? "Refreshing…" : "Refresh"}</button>
          <Link className="btn primary" to="/submit">Submit for review</Link>
        </div>
      </div>
      {fresh && <div className="success">Submitted. It's been routed to a reviewer.</div>}
      {live && (
        <p className="sub" style={{ fontSize: 12 }} aria-live="polite">
          Some items are still in review. This list updates every 15 seconds{updatedAt ? ` (last updated ${updatedAt.toLocaleTimeString()})` : ""}.
        </p>
      )}
      <ErrorBox error={error || err} />
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {!data ? <Empty>{error ? "Couldn't load your submissions." : "Loading…"}</Empty> : !items.length ? <Empty>{offset ? "No more submissions." : status ? `No submissions are ${label(status)}.` : "Nothing submitted yet."}</Empty> : (
          <table>
            <thead><tr><th>Item</th><th>Status</th><th>Decision</th><th>Submitted</th><th /></tr></thead>
            <tbody>
              {items.map((s) => (
                <Fragment key={s.id}>
                  <tr className="clickable" onClick={() => toggle(s.id)}>
                    <td>
                      <button className="linklike" style={{ fontWeight: 600, fontSize: "inherit", color: "inherit", textDecoration: "none", textAlign: "left" }}
                        aria-expanded={open === s.id} aria-controls={`sub-detail-${s.id}`}
                        onClick={(e) => (e.stopPropagation(), toggle(s.id))}>
                        {s.title || s.id.slice(0, 8)}
                      </button>
                      <div className="sub mono">{s.id.slice(0, 8)} · {label(s.risk_tier)} risk</div>
                      {s.previous_submission_id && <div className="sub" style={{ fontSize: 12 }}>Revision of <span className="mono">{s.previous_submission_id.slice(0, 8)}</span></div>}
                    </td>
                    <td>
                      <Badge tone={statusTone(s.status)}>{label(s.status)}</Badge>
                      {s.current_stage && <div className="sub" style={{ fontSize: 12 }}>{s.current_stage}</div>}
                    </td>
                    <td>
                      {s.decision ? label(s.decision.outcome) : <span className="sub">Waiting</span>}
                      {s.decision?.assurance?.score != null && <div className="sub" style={{ fontSize: 12 }}>Assurance score {s.decision.assurance.score}</div>}
                    </td>
                    <td className="sub">{when(s.created_at)}</td>
                    <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                      <div className="btn-row" style={{ justifyContent: "flex-end" }}>
                        {REVISABLE.has(s.status) && (
                          <button className="btn small primary" onClick={() => revise(s)}>Revise and submit again</button>
                        )}
                        {s.has_evidence_pack && (
                          <button className="btn small" onClick={() => evidence(s.id)}>Evidence PDF</button>
                        )}
                        {OPEN.has(s.status) && <button className="btn small danger" onClick={() => cancel(s.id)}>Withdraw</button>}
                      </div>
                    </td>
                  </tr>
                  {open === s.id && (
                    <tr id={`sub-detail-${s.id}`}>
                      <td colSpan={5}>
                        {!s.decision ? <p className="sub">No decision yet. This page shows the outcome once a reviewer has signed it off.</p> : (
                          <div>
                            {s.decision.reason && <p><strong>Reason:</strong> {s.decision.reason}</p>}
                            {s.feedback.length ? (
                              <>
                                <h3>Reviewer comments ({s.feedback.length})</h3>
                                {s.feedback.map((c, i) => (
                                  <div key={i} className="comment">
                                    {c.quote && <blockquote className="quote" aria-label="Passage">{c.quote}</blockquote>}
                                    {c.kind === "edit" && <Badge tone="accent">suggested edit</Badge>} {c.body}
                                    {c.suggested_text && <div className="sub">Suggested: “{c.suggested_text}”</div>}
                                    {c.stage && <div className="sub" style={{ fontSize: 11 }}>{c.stage}{c.created_at ? ` · ${when(c.created_at)}` : ""}</div>}
                                  </div>
                                ))}
                              </>
                            ) : <p className="sub">No comments.</p>}
                            {REVISABLE.has(s.status) && (
                              <div className="btn-row" style={{ alignItems: "center", marginTop: 8 }}>
                                <span className="sub">
                                  {s.status === "changes_requested" ? "Revise the content and submit it again." : "You can send a revised version for a fresh review."}
                                </span>
                                <button className="btn small primary" onClick={() => revise(s)}>Revise and submit again</button>
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && <Pager offset={offset} limit={LIMIT} total={data.total} onChange={(o) => { setOffset(o); setOpen(null); }} />}
    </>
  );
}
