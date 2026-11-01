import { Fragment, useState } from "react";
import { api } from "../api";
import { Badge, Empty, ErrorBox, label, riskTone, useLoad, when } from "../components/ui";

type Row = { id: string; status: string; hold_reason: string | null; escalated: boolean; workflow: string; risk_tier: string; external_ref: string | null; title: string | null; created_at: string; score: number | null };

const EXPLAIN: Record<string, string> = {
  spot_check_disagreement: "The blind second reviewer disagreed with the first. Decide the outcome.",
  below_threshold: "The Review Assurance Score was below this workflow's minimum and policy blocks release.",
  awaiting_capacity: "Every eligible reviewer is at their review budget. It will be assigned when capacity frees up.",
  awaiting_spot_check_reviewer: "No eligible second reviewer is available for the spot-check.",
  release_check_failed: "The content about to be released did not match the original submission. Investigate before releasing.",
};

export default function Held() {
  const { data, error, reload } = useLoad(() => api<Row[]>("/v1/oversight/submissions?held=true"));
  const [open, setOpen] = useState<string | null>(null);
  const [outcome, setOutcome] = useState("approve");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);

  async function resolve(id: string) {
    setErr(null);
    try {
      await api(`/v1/oversight/submissions/${id}/resolve`, { body: { outcome, reason } });
      setOpen(null);
      setReason("");
      reload();
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Held items</h1>
          <div className="sub">Items the workflow will not release on its own. Nothing here has been sent to the source application.</div>
        </div>
        <button className="btn" onClick={reload}>Refresh</button>
      </div>
      <ErrorBox error={error} />
      <div className="card" style={{ padding: 0 }}>
        {!data?.length ? <Empty>Nothing is held.</Empty> : (
          <table>
            <thead><tr><th>Item</th><th>Why it is held</th><th>Risk</th><th>Score</th><th>Received</th><th /></tr></thead>
            <tbody>
              {data.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td><div style={{ fontWeight: 600 }}>{r.title || r.external_ref || r.id.slice(0, 8)}</div><div className="sub" style={{ fontSize: 12 }}>{r.workflow} · {label(r.status)}</div></td>
                    <td><Badge tone="medium">{label(r.hold_reason)}</Badge>{r.escalated && <> <Badge tone="high">escalated</Badge></>}<div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{EXPLAIN[r.hold_reason || ""]}</div></td>
                    <td><Badge tone={riskTone(r.risk_tier)}>{r.risk_tier}</Badge></td>
                    <td>{r.score ?? "–"}</td>
                    <td>{when(r.created_at)}</td>
                    <td>{["spot_check_disagreement", "below_threshold", "awaiting_spot_check_reviewer"].includes(r.hold_reason || "") && (
                      <button className="btn small" onClick={() => setOpen(open === r.id ? null : r.id)}>Resolve</button>
                    )}</td>
                  </tr>
                  {open === r.id && (
                    <tr key={r.id + "-resolve"}>
                      <td colSpan={6}>
                        <div className="field-row">
                          <div>
                            <label>Outcome</label>
                            <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
                              <option value="approve">Approve</option>
                              <option value="request_changes">Request changes</option>
                              <option value="reject">Reject</option>
                            </select>
                          </div>
                          <div style={{ gridColumn: "span 2" }}>
                            <label>Reason (recorded in the evidence pack)</label>
                            <input value={reason} onChange={(e) => setReason(e.target.value)} />
                          </div>
                        </div>
                        <ErrorBox error={err} />
                        <div className="btn-row" style={{ marginTop: 10 }}>
                          <button className="btn primary" onClick={() => resolve(r.id)}>Release decision</button>
                          <button className="btn" onClick={() => setOpen(null)}>Cancel</button>
                        </div>
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
