import { useState } from "react";
import { api } from "../api";
import { Bar, Empty, ErrorBox, label, pct, Stat, useLoad } from "../components/ui";

type Rate = { key: string; caught: number; total: number; rate: number | null };

function RateTable({ title, rows }: { title: string; rows: Rate[] }) {
  return (
    <div className="card">
      <h2>{title}</h2>
      {!rows.length ? <p className="sub">No canaries scored yet.</p> : (
        <table>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td style={{ width: "40%" }}>{label(r.key)}</td>
                <td><Bar value={r.rate} /></td>
                <td className="sub" style={{ width: 70, textAlign: "right" }}>{r.caught}/{r.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const { data: d, error } = useLoad(() => api<any>(`/v1/oversight/dashboard?days=${days}`), [days]);
  if (error) return <ErrorBox error={error} />;
  if (!d) return <Empty>Loading…</Empty>;
  const dist = d.scores.distribution as Record<string, number>;
  const maxBucket = Math.max(1, ...Object.values(dist));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Assurance dashboard</h1>
          <div className="sub">How credible approvals have been over the last {days} days.</div>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: 140 }}>
          {[7, 30, 90, 365].map((n) => <option key={n} value={n}>Last {n} days</option>)}
        </select>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat label="Canary catch rate" value={pct(d.catch_rate.overall)} hint="Share of seeded errors reviewers caught" />
        <Stat label="Median assurance score" value={d.scores.median ?? "–"} hint={`${d.scores.released_below_threshold} released below threshold`} />
        <Stat label="Fast approvals" value={pct(d.review_time.fast_approval_share)} hint={`${d.review_time.fast_approvals} of ${d.review_time.approvals} under 30% of expected time`} />
        <Stat label="Spot-check disagreement" value={pct(d.spot_checks.disagreement_rate)} hint={`${d.spot_checks.disagreements} of ${d.spot_checks.total} spot-checks`} />
      </div>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <RateTable title="Catch rate by reviewer" rows={d.catch_rate.by_reviewer} />
        <RateTable title="Catch rate by difficulty" rows={d.catch_rate.by_difficulty} />
        <RateTable title="Catch rate by workflow" rows={d.catch_rate.by_workflow} />
        <RateTable title="Catch rate by week" rows={d.catch_rate.by_week} />
      </div>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <h2>Capacity today</h2>
          {!d.capacity.length ? <p className="sub">No teams configured.</p> : d.capacity.map((c: any) => (
            <div key={c.team} style={{ marginBottom: 10 }}>
              <div className="sub" style={{ fontSize: 12 }}>{c.team} · {c.reviewers} reviewers · {c.used_minutes} of {c.capacity_minutes} min</div>
              <Bar value={c.utilisation} />
            </div>
          ))}
          <div className="kv" style={{ marginTop: 12 }}>
            <span className="k">Waiting for capacity</span><span>{d.volume.awaiting_capacity}</span>
            <span className="k">Deferred to source</span><span>{d.volume.deferred}</span>
            <span className="k">Escalated</span><span>{d.volume.escalated}</span>
          </div>
        </div>
        <div className="card">
          <h2>Review time</h2>
          <div className="kv">
            <span className="k">Median active review time</span><span>{d.review_time.median_active_seconds != null ? `${d.review_time.median_active_seconds}s` : "–"}</span>
            <span className="k">Median expected time</span><span>{d.review_time.median_expected_seconds != null ? `${d.review_time.median_expected_seconds}s` : "–"}</span>
          </div>
          <h2 style={{ marginTop: 18 }}>Assurance score distribution</h2>
          <div className="hist">
            {Object.entries(dist).map(([k, v]) => <div key={k} style={{ height: `${(v / maxBucket) * 100}%` }} title={`${k}: ${v}`} />)}
          </div>
          <div className="hist-labels">{Object.keys(dist).map((k) => <span key={k}>{k.split("-")[0]}</span>)}</div>
        </div>
      </div>

      <div className="card">
        <h2>Submissions by status</h2>
        <div className="btn-row">
          {Object.entries(d.volume.by_status as Record<string, number>).map(([k, v]) => (
            <span key={k} className="badge">{label(k)}: {v}</span>
          ))}
        </div>
      </div>
    </>
  );
}
