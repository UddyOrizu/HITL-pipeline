import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, download } from "../api";
import { Badge, Empty, ErrorBox, label, statusTone, useLoad, when } from "../components/ui";

type Comment = { span_id: string | null; body: string; suggested_text: string | null };
type Sub = {
  id: string; title: string | null; status: string; current_stage: string | null; risk_tier: string; created_at: string;
  decision: { outcome: string; reason: string | null; assurance?: { score: number | null } | null } | null;
  feedback: Comment[]; has_evidence_pack: boolean;
};

const OPEN = new Set(["received", "processing", "in_review", "spot_check"]);

export default function MySubmissions() {
  const [params] = useSearchParams();
  const fresh = params.get("new");
  const { data, error, reload } = useLoad(() => api<Sub[]>("/v1/portal/submissions"));
  const [open, setOpen] = useState<string | null>(fresh);
  const [err, setErr] = useState<unknown>(null);

  async function cancel(id: string) {
    setErr(null);
    try {
      await api(`/v1/portal/submissions/${id}/cancel`, { method: "POST" });
      reload();
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My submissions</h1>
          <div className="sub">Work you've sent for review, with the decision and evidence once it's released.</div>
        </div>
        <div className="btn-row">
          <button className="btn" onClick={reload}>Refresh</button>
          <Link className="btn primary" to="/submit">Submit for review</Link>
        </div>
      </div>
      {fresh && <div className="success">Submitted. It's been routed to a reviewer.</div>}
      <ErrorBox error={error || err} />
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {!data ? <Empty>Loading…</Empty> : !data.length ? <Empty>Nothing submitted yet.</Empty> : (
          <table>
            <thead><tr><th>Item</th><th>Status</th><th>Decision</th><th>Submitted</th><th /></tr></thead>
            <tbody>
              {data.map((s) => (
                <Fragment key={s.id}>
                  <tr className="clickable" onClick={() => setOpen(open === s.id ? null : s.id)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{s.title || s.id.slice(0, 8)}</div>
                      <div className="sub mono">{s.id.slice(0, 8)} · {label(s.risk_tier)} risk</div>
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
                        {s.has_evidence_pack && (
                          <button className="btn small" onClick={() => download(`/v1/portal/submissions/${s.id}/evidence.pdf`, `evidence-${s.id}.pdf`)}>
                            Evidence PDF
                          </button>
                        )}
                        {OPEN.has(s.status) && <button className="btn small danger" onClick={() => cancel(s.id)}>Withdraw</button>}
                      </div>
                    </td>
                  </tr>
                  {open === s.id && (
                    <tr>
                      <td colSpan={5}>
                        {!s.decision ? <p className="sub">No decision yet. This page shows the outcome once a reviewer has signed it off.</p> : (
                          <div>
                            {s.decision.reason && <p><strong>Reason:</strong> {s.decision.reason}</p>}
                            {s.feedback.length ? (
                              <>
                                <h3>Reviewer comments</h3>
                                {s.feedback.map((c, i) => (
                                  <div key={i} className="comment">
                                    {c.body}
                                    {c.suggested_text && <div className="sub">Suggested: “{c.suggested_text}”</div>}
                                  </div>
                                ))}
                              </>
                            ) : <p className="sub">No comments.</p>}
                            {s.decision.outcome === "changes_requested" && (
                              <p className="sub">Revise the content and submit it again.</p>
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
    </>
  );
}
