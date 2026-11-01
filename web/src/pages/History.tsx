import { api } from "../api";
import { Badge, Empty, ErrorBox, label, pct, statusTone, useLoad, when } from "../components/ui";

type Row = {
  task_id: string; submission_id: string; kind: string; outcome: string; decided_at: string;
  telemetry: Record<string, number>;
  canary: { type: string; difficulty: string; caught: boolean } | null;
};

export default function History() {
  const { data, error } = useLoad(() => api<Row[]>("/v1/review/history"));
  const canaries = (data || []).filter((r) => r.canary);
  const caught = canaries.filter((r) => r.canary!.caught).length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>My reviews</h1>
          <div className="sub">Your own decisions, reading telemetry and canary results. You can always see what is measured about you.</div>
        </div>
      </div>
      <ErrorBox error={error} />
      {canaries.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <strong>Canary catch rate:</strong> {caught} of {canaries.length} ({pct(caught / canaries.length)}). Canaries are for calibration and coaching, not discipline.
        </div>
      )}
      <div className="card" style={{ padding: 0 }}>
        {!data?.length ? (
          <Empty>No decisions yet.</Empty>
        ) : (
          <table>
            <thead><tr><th>Decided</th><th>Kind</th><th>Outcome</th><th>Active / expected</th><th>Coverage</th><th>Flags</th><th>Canary</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.task_id}>
                  <td>{when(r.decided_at)}</td>
                  <td>{label(r.kind)}</td>
                  <td><Badge tone={statusTone(r.outcome === "approve" ? "approved" : r.outcome === "reject" ? "rejected" : "changes_requested")}>{label(r.outcome)}</Badge></td>
                  <td>{Math.round(r.telemetry.active_seconds)}s / {Math.round(r.telemetry.expected_seconds)}s</td>
                  <td>{pct(r.telemetry.coverage)}</td>
                  <td>{r.telemetry.flags_acknowledged}/{r.telemetry.flags_total}</td>
                  <td>{r.canary ? <Badge tone={r.canary.caught ? "ok" : "medium"}>{r.canary.caught ? "caught" : "missed"} · {label(r.canary.type)}</Badge> : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
