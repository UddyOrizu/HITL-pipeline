import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, ErrorBox, useLoad, when } from "../components/ui";

type Stage = { name: string; team_id: string | null; sla_hours: number | null };
type WF = {
  id: string; version: number; name: string; stages: Stage[]; canary_rate: number; canary_domain: string;
  telemetry_policy: any; cap_policy: any; spot_check_rate: number; risk_policy: any; assurance_policy: any;
  source_app_ids: string[]; updated_at: string;
};

const BLANK: WF = {
  id: "", version: 0, name: "", stages: [{ name: "Review", team_id: null, sla_hours: 24 }], canary_rate: 0.05,
  canary_domain: "general", telemetry_policy: { min_reading_ratio: 0.3, min_coverage: 0.6, action: "warn" },
  cap_policy: { daily_minutes: 240, hourly_minutes: 90, overflow: "queue", max_consecutive_same_source: 3 },
  spot_check_rate: 0.05, risk_policy: { mandatory_severities: ["high"] }, assurance_policy: { min_score: 60, below_action: "release_flag" },
  source_app_ids: [], updated_at: "",
};

function Editor({ wf, teams, onDone }: { wf: WF; teams: { id: string; name: string }[]; onDone: () => void }) {
  const [f, setF] = useState<WF>(JSON.parse(JSON.stringify(wf)));
  const [err, setErr] = useState<unknown>(null);
  const set = (path: string[], value: any) => {
    const next: any = { ...f };
    let o = next;
    for (const k of path.slice(0, -1)) { o[k] = { ...o[k] }; o = o[k]; }
    o[path[path.length - 1]] = value;
    setF(next);
  };
  const num = (v: string) => (v === "" ? null : Number(v));

  async function save() {
    setErr(null);
    const body = { ...f } as any;
    delete body.id; delete body.version; delete body.updated_at;
    try {
      if (wf.id) await api(`/v1/admin/workflows/${wf.id}`, { method: "PUT", body });
      else await api("/v1/admin/workflows", { body });
      onDone();
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2>{wf.id ? `Edit ${wf.name} (saves as version ${wf.version + 1})` : "New workflow"}</h2>
      <label>Name</label>
      <input value={f.name} onChange={(e) => set(["name"], e.target.value)} />
      <label>Stages</label>
      {f.stages.map((s, i) => (
        <div className="field-row" key={i} style={{ marginBottom: 6 }}>
          <input value={s.name} onChange={(e) => { const st = [...f.stages]; st[i] = { ...s, name: e.target.value }; set(["stages"], st); }} />
          <select value={s.team_id || ""} onChange={(e) => { const st = [...f.stages]; st[i] = { ...s, team_id: e.target.value || null }; set(["stages"], st); }}>
            <option value="">Any reviewer</option>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <input type="number" placeholder="SLA hours" value={s.sla_hours ?? ""} onChange={(e) => { const st = [...f.stages]; st[i] = { ...s, sla_hours: num(e.target.value) }; set(["stages"], st); }} />
          {f.stages.length > 1 && <button className="btn small" onClick={() => set(["stages"], f.stages.filter((_, j) => j !== i))}>Remove</button>}
        </div>
      ))}
      <button className="btn small" onClick={() => set(["stages"], [...f.stages, { name: `Stage ${f.stages.length + 1}`, team_id: null, sla_hours: 24 }])}>Add stage</button>

      <div className="field-row">
        <div><label>Canary rate (0–1)</label><input type="number" step="0.01" value={f.canary_rate} onChange={(e) => set(["canary_rate"], Number(e.target.value))} /></div>
        <div><label>Canary domain</label>
          <select value={f.canary_domain} onChange={(e) => set(["canary_domain"], e.target.value)}>
            {["general", "financial", "insurance", "professional_services", "legal"].map((d) => <option key={d}>{d}</option>)}
          </select></div>
        <div><label>Spot-check rate (0–1)</label><input type="number" step="0.01" value={f.spot_check_rate} onChange={(e) => set(["spot_check_rate"], Number(e.target.value))} /></div>
      </div>
      <div className="field-row">
        <div><label>Min reading ratio</label><input type="number" step="0.05" value={f.telemetry_policy.min_reading_ratio} onChange={(e) => set(["telemetry_policy", "min_reading_ratio"], Number(e.target.value))} /></div>
        <div><label>Min section coverage</label><input type="number" step="0.05" value={f.telemetry_policy.min_coverage} onChange={(e) => set(["telemetry_policy", "min_coverage"], Number(e.target.value))} /></div>
        <div><label>Below review standard</label>
          <select value={f.telemetry_policy.action} onChange={(e) => set(["telemetry_policy", "action"], e.target.value)}>
            <option value="warn">Warn and confirm</option><option value="block">Block approval</option><option value="flag">Allow and flag</option>
          </select></div>
      </div>
      <label>Decisions accepted from</label>
      <div className="btn-row">
        {([["ui", "Web workspace"], ["api", "API"], ["mcp", "MCP"]] as const).map(([k, name]) => {
          const on: string[] = f.telemetry_policy.review_channels || ["ui", "api", "mcp"];
          const has = on.includes(k);
          return (
            <label key={k} className="check" style={{ margin: 0 }}>
              <input type="checkbox" checked={has} disabled={has && on.length === 1}
                onChange={() => set(["telemetry_policy", "review_channels"], has ? on.filter((c) => c !== k) : [...on, k])} />
              <span>{name}</span>
            </label>
          );
        })}
      </div>
      <p className="sub" style={{ fontSize: 12, marginTop: 4 }}>
        Only the web workspace measures reading. API and MCP decisions have no reading telemetry, so the standard above applies to them.
      </p>
      <div className="field-row">
        <div><label>Daily cap (review minutes)</label><input type="number" value={f.cap_policy.daily_minutes} onChange={(e) => set(["cap_policy", "daily_minutes"], Number(e.target.value))} /></div>
        <div><label>Hourly cap</label><input type="number" value={f.cap_policy.hourly_minutes} onChange={(e) => set(["cap_policy", "hourly_minutes"], Number(e.target.value))} /></div>
        <div><label>When at capacity</label>
          <select value={f.cap_policy.overflow} onChange={(e) => set(["cap_policy", "overflow"], e.target.value)}>
            <option value="queue">Queue</option><option value="escalate">Queue and escalate</option><option value="defer">Defer to source</option>
          </select></div>
        <div><label>Max consecutive from one source</label><input type="number" value={f.cap_policy.max_consecutive_same_source} onChange={(e) => set(["cap_policy", "max_consecutive_same_source"], Number(e.target.value))} /></div>
      </div>
      <div className="field-row">
        <div><label>Minimum assurance score</label><input type="number" value={f.assurance_policy.min_score} onChange={(e) => set(["assurance_policy", "min_score"], Number(e.target.value))} /></div>
        <div><label>Below minimum score</label>
          <select value={f.assurance_policy.below_action} onChange={(e) => set(["assurance_policy", "below_action"], e.target.value)}>
            <option value="release_flag">Release with a flag</option><option value="second_review">Require a second review</option><option value="block">Block release</option>
          </select></div>
      </div>
      <ErrorBox error={err} />
      <div className="btn-row" style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={save} disabled={!f.name}>Save</button>
        <button className="btn" onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}

export default function Workflows() {
  const { data, error, reload } = useLoad(() => api<WF[]>("/v1/admin/workflows"));
  const { data: teams } = useLoad(() => api<{ id: string; name: string }[]>("/v1/admin/teams"));
  const [editing, setEditing] = useState<WF | null>(null);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Workflow settings</h1>
          <div className="sub">Every save creates a new version. Submissions keep the version they arrived under.</div>
        </div>
        <button className="btn primary" onClick={() => setEditing(BLANK)}>New workflow</button>
      </div>
      <ErrorBox error={error} />
      {editing && <Editor wf={editing} teams={teams || []} onDone={() => { setEditing(null); reload(); }} />}
      <div className="card" style={{ padding: 0 }}>
        {!data?.length ? <Empty>No workflows yet.</Empty> : (
          <table>
            <thead><tr><th>Workflow</th><th>Stages</th><th>Canaries</th><th>Spot-checks</th><th>Caps</th><th>Min score</th><th>Updated</th><th /></tr></thead>
            <tbody>
              {data.map((w) => (
                <tr key={w.id}>
                  <td><div style={{ fontWeight: 600 }}>{w.name}</div><Badge>v{w.version}</Badge> <span className="mono sub">{w.id.slice(0, 8)}</span></td>
                  <td>{w.stages.map((s) => s.name).join(" → ")}</td>
                  <td>{Math.round(w.canary_rate * 100)}%</td>
                  <td>{Math.round(w.spot_check_rate * 100)}%</td>
                  <td>{w.cap_policy?.daily_minutes ?? 240} min/day</td>
                  <td>{w.assurance_policy?.min_score ?? 60}</td>
                  <td>{when(w.updated_at)}</td>
                  <td><button className="btn small" onClick={() => setEditing(w)}>Edit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
