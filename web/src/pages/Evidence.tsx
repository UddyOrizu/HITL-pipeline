import { Fragment, useEffect, useState } from "react";
import { api, apiPage, download, qs } from "../api";
import { Badge, Empty, ErrorBox, Pager, label, statusTone, useDebounced, useLoad, when } from "../components/ui";
import "./oversight.css";

type Row = { id: string; external_ref: string | null; status: string; workflow: string; risk_tier: string; content_origin: string; title: string | null; created_at: string; released_at: string | null; score: number | null; below_threshold: boolean | null; has_pack: boolean; purged?: boolean };
type Chain = { valid: boolean; count: number; broken_at: number | null; head?: string };

const LIMIT = 25;
const STATUSES = ["received", "processing", "in_review", "spot_check", "approved", "rejected", "changes_requested", "deferred", "cancelled"];

function Record({ id }: { id: string }) {
  const { data: r, error } = useLoad(() => api<any>(`/v1/audit/submissions/${id}/evidence`), [id]);
  if (error) return <ErrorBox error={error} />;
  if (!r) return <p className="sub">Loading…</p>;
  return (
    <div className="grid grid-2" style={{ marginTop: 10 }}>
      <div>
        <h3>Decision</h3>
        <div className="kv">
          <span className="k">Outcome</span><span>{label(r.submission.final_outcome || r.submission.status)}</span>
          <span className="k">Assurance score</span><span>{r.assurance?.score ?? "–"}{r.assurance?.below_threshold ? " (below threshold)" : ""}</span>
          <span className="k">Workflow</span><span>{r.workflow.name} v{r.workflow.version}</span>
          <span className="k">Canary</span><span>{r.canary.present ? r.canary.items.map((c: any) => `${c.difficulty} ${label(c.type)}: ${c.caught ? "caught" : "missed"}`).join(", ") : "none"}</span>
          <span className="k">Spot-check</span><span>{r.spot_check.sampled ? (r.spot_check.agreement ? "agreed" : "disagreed") : "not sampled"}</span>
          <span className="k">Audit chain</span><span>{r.audit_chain.valid ? `valid (${r.audit_chain.events_in_tenant_chain} events)` : "BROKEN"}</span>
        </div>
        <h3 style={{ marginTop: 14 }}>Content hashes</h3>
        {r.documents.map((d: any) => <div key={d.id} className="mono" style={{ wordBreak: "break-all" }}>{d.role}: {d.sha256}</div>)}
      </div>
      <div>
        <h3>Reviews</h3>
        {r.reviews.map((rv: any) => (
          <div key={rv.task_id} className="comment">
            <strong>{rv.stage}</strong> ({label(rv.kind)}) · {rv.decision ? label(rv.decision.outcome) : label(rv.status)}
            {rv.decision?.channel && <> · <Badge tone={rv.decision.channel === "ui" ? "ok" : "medium"}>via {rv.decision.channel === "ui" ? "workspace" : rv.decision.channel}</Badge></>}
            {rv.decision?.telemetry_summary && <div className="sub" style={{ fontSize: 12 }}>
              {Math.round(rv.decision.telemetry_summary.active_seconds)}s active of {Math.round(rv.decision.telemetry_summary.expected_seconds)}s expected · coverage {Math.round(rv.decision.telemetry_summary.coverage * 100)}%
            </div>}
          </div>
        ))}
        <h3 style={{ marginTop: 14 }}>Audit trail ({r.audit_trail.length} events)</h3>
        <div style={{ maxHeight: 200, overflow: "auto" }}>
          {r.audit_trail.map((e: any) => <div key={e.seq} className="mono" style={{ fontSize: 11 }}>#{e.seq} {e.action} · {e.actor} · {e.hash.slice(0, 10)}</div>)}
        </div>
      </div>
    </div>
  );
}

export default function Evidence() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const dq = useDebounced(q.trim(), 300);
  useEffect(() => setOffset(0), [dq, status]);

  const path = `/v1/audit/submissions${qs({ q: dq, status, offset, limit: LIMIT })}`;
  const { data, error, loading } = useLoad(() => apiPage<Row>(path), [path]);
  const { data: chain, error: chainErr } = useLoad(() => api<Chain>("/v1/audit/verify"));
  const [open, setOpen] = useState<string | null>(null);
  const [pdfErr, setPdfErr] = useState<unknown>(null);
  const rows = data?.items || [];

  async function pdf(id: string) {
    setPdfErr(null);
    try {
      await download(`/v1/audit/submissions/${id}/evidence.pdf`, `evidence-${id}.pdf`);
    } catch (e) {
      setPdfErr(e);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Evidence explorer</h1>
          <div className="sub">Read-only. Every decision, with the evidence to show how it was reviewed.</div>
        </div>
        {chain && (chain.valid
          ? <Badge tone="ok">Audit chain verified · {chain.count} events</Badge>
          : <Badge tone="high">Audit chain broken at event #{chain.broken_at}</Badge>)}
      </div>
      {chain && !chain.valid && (
        <div className="error" role="alert">
          The audit chain fails verification at event #{chain.broken_at} of {chain.count}: that event, or one before it, was
          changed or removed after it was written. Records after it can't be relied on until this is investigated.
        </div>
      )}
      <ErrorBox error={chainErr} />

      <div className="toolbar">
        <div>
          <label htmlFor="ev-q">Search</label>
          <input id="ev-q" placeholder="Title, reference or submission id" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 300 }} />
        </div>
        <div>
          <label htmlFor="ev-status">Status</label>
          <select id="ev-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </select>
        </div>
        {data && <span className="sub" style={{ paddingBottom: 8 }}>{data.total} submission{data.total === 1 ? "" : "s"}</span>}
      </div>
      <ErrorBox error={error} />
      <ErrorBox error={pdfErr} />
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {loading && !data ? <Empty>Loading…</Empty> : !rows.length ? <Empty>{dq || status ? "No submissions match." : "No submissions."}</Empty> : (
          <table>
            <thead><tr><th>Item</th><th>Status</th><th>Score</th><th>Origin</th><th>Released</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.title || r.external_ref || r.id.slice(0, 8)}</div>
                      <div className="sub mono">{r.id.slice(0, 8)} · {r.workflow}</div>
                    </td>
                    <td>
                      <div className="ov-badges">
                        <Badge tone={statusTone(r.status)}>{label(r.status)}</Badge>
                        {r.purged && <Badge tone="low">content purged</Badge>}
                      </div>
                    </td>
                    <td>{r.score ?? "–"}{r.below_threshold ? <> <Badge tone="medium">below</Badge></> : null}</td>
                    <td>{label(r.content_origin)}</td>
                    <td>{when(r.released_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {r.has_pack && <button className="btn small" onClick={() => pdf(r.id)}>PDF</button>}
                    </td>
                  </tr>
                  {open === r.id && (
                    <tr>
                      <td colSpan={6}>
                        {r.purged && <div className="notice">The document content was purged under the retention policy. Hashes, decisions and the audit trail remain.</div>}
                        <Record id={r.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && <Pager offset={offset} limit={LIMIT} total={data.total} onChange={setOffset} />}
    </>
  );
}
