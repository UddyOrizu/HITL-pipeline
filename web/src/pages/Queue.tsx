import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Badge, Empty, ErrorBox, label, riskTone, useLoad, when } from "../components/ui";

type Item = {
  task_id: string; submission_id: string; kind: string; stage: string; workflow: string; title: string;
  risk_tier: string; content_origin: string; effort_minutes: number; assigned_at: string; due_at: string | null;
  opened: boolean; render_kind: string;
};

export default function Queue({ kind }: { kind: "primary" | "spot_check" }) {
  const { data, error, loading, reload } = useLoad(() => api<Item[]>(`/v1/review/queue?kind=${kind}`), [kind]);
  const nav = useNavigate();
  const total = (data || []).reduce((s, i) => s + i.effort_minutes, 0);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{kind === "primary" ? "Review queue" : "Spot-checks"}</h1>
          <div className="sub">
            {kind === "primary"
              ? "Items assigned to you, within your review budget for today."
              : "Blind second reviews. You will not see the first reviewer's decision or comments."}
          </div>
        </div>
        <button className="btn" onClick={reload}>Refresh</button>
      </div>
      <ErrorBox error={error} />
      <div className="card" style={{ padding: 0 }}>
        {loading && !data ? (
          <Empty>Loading…</Empty>
        ) : !data?.length ? (
          <Empty>Nothing waiting. New items appear here when they are assigned to you.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Item</th><th>Workflow · stage</th><th>Risk</th><th>Origin</th><th>Effort</th><th>Due</th>
              </tr>
            </thead>
            <tbody>
              {data.map((i) => (
                <tr key={i.task_id} className="clickable" onClick={() => nav(`/review/${i.task_id}`)}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{i.title}</div>
                    <div className="sub" style={{ fontSize: 12 }}>
                      {label(i.render_kind)} {i.opened ? "· opened" : <Badge tone="accent">new</Badge>}
                    </div>
                  </td>
                  <td>{i.workflow}<div className="sub" style={{ fontSize: 12 }}>{i.stage}</div></td>
                  <td><Badge tone={riskTone(i.risk_tier)}>{i.risk_tier}</Badge></td>
                  <td>{label(i.content_origin)}</td>
                  <td>{i.effort_minutes} min</td>
                  <td>{when(i.due_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {!!data?.length && <p className="sub" style={{ marginTop: 10 }}>{data.length} item(s), about {Math.round(total)} review minutes.</p>}
    </>
  );
}
