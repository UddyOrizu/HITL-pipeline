import { Fragment, useState } from "react";
import { api, download } from "../api";
import { Badge, Empty, ErrorBox, label, statusTone, useLoad, when } from "../components/ui";

type Row = { id: string; external_ref: string | null; status: string; workflow: string; risk_tier: string; content_origin: string; title: string | null; created_at: string; released_at: string | null; score: number | null; below_threshold: boolean | null; has_pack: boolean };

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
          <span className="k">Audit chain</span><span>{r.audit_chain.valid ? "valid" : "BROKEN"}</span>
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
  const { data, error } = useLoad(() => api<Row[]>(`/v1/audit/submissions${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q]);
  const { data: chain } = useLoad(() => api<any>("/v1/audit/verify"));
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Evidence explorer</h1>
          <div className="sub">Read-only. Every decision, with the evidence to show how it was reviewed.</div>
        </div>
        {chain && <Badge tone={chain.valid ? "ok" : "high"}>Audit chain {chain.valid ? "verified" : "broken"} · {chain.count} events</Badge>}
      </div>
      <input placeholder="Search by reference or submission id" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 360, marginBottom: 14 }} />
      <ErrorBox error={error} />
      <div className="card" style={{ padding: 0 }}>
        {!data?.length ? <Empty>No submissions.</Empty> : (
          <table>
            <thead><tr><th>Item</th><th>Status</th><th>Score</th><th>Origin</th><th>Released</th><th /></tr></thead>
            <tbody>
              {data.map((r) => (
                <Fragment key={r.id}>
                  <tr className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td><div style={{ fontWeight: 600 }}>{r.title || r.external_ref || r.id.slice(0, 8)}</div><div className="sub mono">{r.id.slice(0, 8)} · {r.workflow}</div></td>
                    <td><Badge tone={statusTone(r.status)}>{label(r.status)}</Badge></td>
                    <td>{r.score ?? "–"}{r.below_threshold ? <> <Badge tone="medium">below</Badge></> : null}</td>
                    <td>{label(r.content_origin)}</td>
                    <td>{when(r.released_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {r.has_pack && <button className="btn small" onClick={() => download(`/v1/audit/submissions/${r.id}/evidence.pdf`, `evidence-${r.id}.pdf`)}>PDF</button>}
                    </td>
                  </tr>
                  {open === r.id && <tr key={r.id + "-x"}><td colSpan={6}><Record id={r.id} /></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
