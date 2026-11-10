import { useState } from "react";
import { apiPage, qs } from "../api";
import { Badge, Empty, ErrorBox, label, Pager, pct, statusTone, useLoad, when } from "../components/ui";

type Row = {
  task_id: string; submission_id: string; kind: string; outcome: string; decided_at: string;
  telemetry: Record<string, number>;
  canary: { type: string; difficulty: string; caught: boolean } | null;
};

const LIMIT = 50;

function secs(v: number | undefined) {
  return v == null ? "–" : `${Math.round(v)}s`;
}

export default function History() {
  const [offset, setOffset] = useState(0);
  // Paged: X-Total-Count / X-Next-Offset headers (newest decisions first).
  const { data, error, loading, reload } = useLoad(() => apiPage<Row>(`/v1/review/history${qs({ offset, limit: LIMIT })}`), [offset]);
  const rows = data?.items || [];
  const canaries = rows.filter((r) => r.canary);
  const caught = canaries.filter((r) => r.canary!.caught).length;
  const paged = !!data && (data.total > LIMIT || offset > 0);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>My reviews</h1>
          <div className="sub">Your own decisions, reading telemetry and canary results. You can always see what is measured about you.</div>
        </div>
      </div>
      {!!error && (
        <div className="btn-row" style={{ alignItems: "center", marginBottom: 16 }}>
          <div style={{ flex: 1 }}><ErrorBox error={error} /></div>
          <button className="btn" onClick={reload} disabled={loading}>{loading ? "Retrying…" : "Retry"}</button>
        </div>
      )}
      {canaries.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <strong>Canary catch rate{paged ? " (decisions on this page)" : ""}:</strong> {caught} of {canaries.length} ({pct(caught / canaries.length)}).
          Canaries are for calibration and coaching, not discipline.
        </div>
      )}
      <div className="card" style={{ padding: 0, overflowX: "auto", opacity: loading && data ? 0.6 : 1 }} aria-busy={loading}>
        {loading && !data ? (
          <Empty>Loading…</Empty>
        ) : !rows.length ? (
          <Empty>{error ? "Your reviews couldn't be loaded." : offset > 0 ? "No more decisions." : "No decisions yet."}</Empty>
        ) : (
          <table>
            <thead><tr><th>Decided</th><th>Kind</th><th>Outcome</th><th>Active / expected</th><th>Coverage</th><th>Flags</th><th>Canary</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const t = r.telemetry || {};
                return (
                  <tr key={r.task_id}>
                    <td>{when(r.decided_at)}</td>
                    <td>{label(r.kind)}</td>
                    <td><Badge tone={statusTone(r.outcome === "approve" ? "approved" : r.outcome === "reject" ? "rejected" : "changes_requested")}>{label(r.outcome)}</Badge></td>
                    <td>{secs(t.active_seconds)} / {secs(t.expected_seconds)}</td>
                    <td>{pct(t.coverage)}</td>
                    <td>{t.flags_total != null ? `${t.flags_acknowledged ?? 0}/${t.flags_total}` : "–"}</td>
                    <td>{r.canary ? <Badge tone={r.canary.caught ? "ok" : "medium"}>{r.canary.caught ? "caught" : "missed"} · {label(r.canary.type)}</Badge> : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {data && <Pager offset={offset} limit={LIMIT} total={data.total} onChange={setOffset} />}
    </>
  );
}
