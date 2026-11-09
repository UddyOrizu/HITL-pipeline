import { Fragment, useState } from "react";
import { api, qs } from "../api";
import { Badge, Empty, ErrorBox, label, useLoad, when } from "../components/ui";
import "./oversight.css";

type Stage = { name: string; team_id: string | null; sla_hours: number | null; on_sla_breach?: "escalate" | "reassign" };
type Policy = Record<string, any>;
type WF = {
  id: string; version_id?: string; version: number; is_current?: boolean; name: string; stages: Stage[];
  canary_rate: number | null; canary_domain: string; telemetry_policy: Policy; cap_policy: Policy; spot_check_rate: number | null;
  risk_policy: Policy; assurance_policy: Policy; source_app_ids: string[]; updated_at: string;
};
type Team = { id: string; name: string };
type SourceApp = { id: string; name: string; kind: string; active: boolean };

const SEVERITIES = ["low", "medium", "high"] as const;

const BLANK: WF = {
  id: "", version: 0, name: "", stages: [{ name: "Review", team_id: null, sla_hours: 24, on_sla_breach: "escalate" }], canary_rate: 0.05,
  canary_domain: "general", telemetry_policy: { min_reading_ratio: 0.3, min_coverage: 0.6, action: "warn" },
  cap_policy: { daily_minutes: 240, hourly_minutes: 90, overflow: "queue", max_consecutive_same_source: 3 },
  spot_check_rate: 0.05, risk_policy: { mandatory_severities: ["high"] }, assurance_policy: { min_score: 60, below_action: "release_flag" },
  source_app_ids: [], updated_at: "",
};

// Empty number inputs become null (never 0).
const numOrNull = (v: string): number | null => (v.trim() === "" || !Number.isFinite(Number(v)) ? null : Number(v));

// Policy dicts are merged over the server defaults, so an empty value is left out rather than sent as null
// (null would override the default).
function compact(o: Policy): Policy {
  return Object.fromEntries(Object.entries(o || {}).filter(([, v]) => v !== null && v !== undefined && v !== ""));
}

function NumberInput({ value, onChange, step, placeholder, min, id }: {
  value: number | null | undefined; onChange: (v: number | null) => void; step?: string; placeholder?: string; min?: number; id?: string;
}) {
  return (
    <input id={id} type="number" step={step} min={min} placeholder={placeholder} value={value ?? ""}
      onChange={(e) => onChange(numOrNull(e.target.value))} />
  );
}

function Editor({ wf, teams, apps, onDone, onCancel }: {
  wf: WF; teams: Team[]; apps: SourceApp[]; onDone: (msg: string) => void; onCancel: () => void;
}) {
  const [f, setF] = useState<WF>(() => {
    const copy: WF = JSON.parse(JSON.stringify(wf));
    copy.stages = copy.stages.map((s) => ({ ...s, on_sla_breach: s.on_sla_breach || "escalate" }));
    copy.risk_policy = { mandatory_severities: ["high"], ...(copy.risk_policy || {}) };
    copy.telemetry_policy ||= {};
    copy.cap_policy ||= {};
    copy.assurance_policy ||= {};
    copy.source_app_ids ||= [];
    return copy;
  });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (path: string[], value: any) => {
    setF((cur) => {
      const next: any = { ...cur };
      let o = next;
      for (const k of path.slice(0, -1)) { o[k] = { ...o[k] }; o = o[k]; }
      o[path[path.length - 1]] = value;
      return next;
    });
  };
  const setStage = (i: number, patch: Partial<Stage>) => set(["stages"], f.stages.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const severities: string[] = f.risk_policy.mandatory_severities || [];
  const appIds = f.source_app_ids;

  async function save() {
    setErr(null);
    setBusy(true);
    const body: any = {
      name: f.name.trim(),
      stages: f.stages.map((s) => ({ name: s.name.trim(), team_id: s.team_id || null, sla_hours: s.sla_hours, on_sla_breach: s.on_sla_breach || "escalate" })),
      canary_domain: f.canary_domain,
      telemetry_policy: compact(f.telemetry_policy),
      cap_policy: compact(f.cap_policy),
      risk_policy: compact({ ...f.risk_policy, spot_check_team_id: f.risk_policy.spot_check_team_id || null }),
      assurance_policy: compact(f.assurance_policy),
      source_app_ids: appIds,
    };
    if (f.canary_rate != null) body.canary_rate = f.canary_rate;
    if (f.spot_check_rate != null) body.spot_check_rate = f.spot_check_rate;
    try {
      const saved = wf.id
        ? await api<WF>(`/v1/admin/workflows/${wf.id}`, { method: "PUT", body })
        : await api<WF>("/v1/admin/workflows", { body });
      onDone(`Saved ${saved.name} as version ${saved.version}.`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2>{wf.id ? `Edit ${wf.name} (saves as version ${wf.version + 1})` : "New workflow"}</h2>
      <label htmlFor="wf-name">Name</label>
      <input id="wf-name" value={f.name} onChange={(e) => set(["name"], e.target.value)} />

      <label>Stages</label>
      <div className="ov-stage ov-stage-head" aria-hidden="true">
        <span>Stage name</span><span>Routed to</span><span>SLA (hours)</span><span>When the SLA is missed</span><span />
      </div>
      {f.stages.map((s, i) => (
        <div className="ov-stage" key={i}>
          <input aria-label={`Stage ${i + 1} name`} value={s.name} onChange={(e) => setStage(i, { name: e.target.value })} />
          <select aria-label={`Stage ${i + 1} team`} value={s.team_id || ""} onChange={(e) => setStage(i, { team_id: e.target.value || null })}>
            <option value="">Any reviewer</option>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <NumberInput value={s.sla_hours} placeholder="No SLA" min={0} onChange={(v) => setStage(i, { sla_hours: v })} />
          <select aria-label={`Stage ${i + 1} on SLA breach`} value={s.on_sla_breach || "escalate"} disabled={s.sla_hours == null}
            onChange={(e) => setStage(i, { on_sla_breach: e.target.value as Stage["on_sla_breach"] })}>
            <option value="escalate">Escalate</option>
            <option value="reassign">Reassign to another reviewer</option>
          </select>
          {f.stages.length > 1 ? <button className="btn small" onClick={() => set(["stages"], f.stages.filter((_, j) => j !== i))}>Remove</button> : <span />}
        </div>
      ))}
      <button className="btn small" onClick={() => set(["stages"], [...f.stages, { name: `Stage ${f.stages.length + 1}`, team_id: null, sla_hours: 24, on_sla_breach: "escalate" }])}>Add stage</button>

      <div className="field-row">
        <div><label>Canary rate (0–1)</label><NumberInput step="0.01" min={0} placeholder="Default 0.05" value={f.canary_rate} onChange={(v) => set(["canary_rate"], v)} /></div>
        <div><label>Canary domain</label>
          <select value={f.canary_domain} onChange={(e) => set(["canary_domain"], e.target.value)}>
            {["general", "financial", "insurance", "professional_services", "legal"].map((d) => <option key={d} value={d}>{label(d)}</option>)}
          </select></div>
        <div><label>Spot-check rate (0–1)</label><NumberInput step="0.01" min={0} placeholder="Default 0.05" value={f.spot_check_rate} onChange={(v) => set(["spot_check_rate"], v)} /></div>
        <div><label>Spot-check team</label>
          <select value={f.risk_policy.spot_check_team_id || ""} onChange={(e) => set(["risk_policy", "spot_check_team_id"], e.target.value || null)}>
            <option value="">Any eligible reviewer</option>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select></div>
      </div>

      <label>Risk flags that must be acknowledged before a decision</label>
      <div className="ov-checks">
        {SEVERITIES.map((s) => (
          <label key={s} className="check">
            <input type="checkbox" checked={severities.includes(s)}
              onChange={() => set(["risk_policy", "mandatory_severities"], severities.includes(s) ? severities.filter((x) => x !== s) : [...severities, s])} />
            <span style={{ textTransform: "capitalize" }}>{s} severity</span>
          </label>
        ))}
      </div>
      {!severities.length && <p className="sub" style={{ fontSize: 12 }}>No flags are mandatory: reviewers can decide without acknowledging any.</p>}

      <div className="field-row">
        <div><label>Min reading ratio</label><NumberInput step="0.05" min={0} placeholder="Default 0.3" value={f.telemetry_policy.min_reading_ratio} onChange={(v) => set(["telemetry_policy", "min_reading_ratio"], v)} /></div>
        <div><label>Min section coverage</label><NumberInput step="0.05" min={0} placeholder="Default 0.6" value={f.telemetry_policy.min_coverage} onChange={(v) => set(["telemetry_policy", "min_coverage"], v)} /></div>
        <div><label>Below review standard</label>
          <select value={f.telemetry_policy.action || "warn"} onChange={(e) => set(["telemetry_policy", "action"], e.target.value)}>
            <option value="warn">Warn and confirm</option><option value="block">Block approval</option><option value="flag">Allow and flag</option>
          </select></div>
      </div>
      <label>Decisions accepted from</label>
      <div className="ov-checks">
        {([["ui", "Web workspace"], ["api", "API"], ["mcp", "MCP"]] as const).map(([k, name]) => {
          const on: string[] = f.telemetry_policy.review_channels || ["ui", "api", "mcp"];
          const has = on.includes(k);
          return (
            <label key={k} className="check">
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
        <div><label>Daily cap (review minutes)</label><NumberInput min={0} placeholder="Default 240" value={f.cap_policy.daily_minutes} onChange={(v) => set(["cap_policy", "daily_minutes"], v)} /></div>
        <div><label>Hourly cap</label><NumberInput min={0} placeholder="Default 90" value={f.cap_policy.hourly_minutes} onChange={(v) => set(["cap_policy", "hourly_minutes"], v)} /></div>
        <div><label>When at capacity</label>
          <select value={f.cap_policy.overflow || "queue"} onChange={(e) => set(["cap_policy", "overflow"], e.target.value)}>
            <option value="queue">Queue</option><option value="escalate">Queue and escalate</option><option value="defer">Defer to source</option>
          </select></div>
        <div><label>Max consecutive from one source</label><NumberInput min={0} placeholder="Default 3" value={f.cap_policy.max_consecutive_same_source} onChange={(v) => set(["cap_policy", "max_consecutive_same_source"], v)} /></div>
      </div>
      <div className="field-row">
        <div><label>Minimum assurance score</label><NumberInput min={0} placeholder="Default 60" value={f.assurance_policy.min_score} onChange={(v) => set(["assurance_policy", "min_score"], v)} /></div>
        <div><label>Below minimum score</label>
          <select value={f.assurance_policy.below_action || "release_flag"} onChange={(e) => set(["assurance_policy", "below_action"], e.target.value)}>
            <option value="release_flag">Release with a flag</option><option value="second_review">Require a second review</option><option value="block">Block release</option>
          </select></div>
      </div>

      <label>Source applications that can use this workflow</label>
      {!apps.length ? <p className="sub">No source applications yet.</p> : (
        <div className="pill-list" style={{ marginTop: 0 }} role="group" aria-label="Allowed source applications">
          <button type="button" className={!appIds.length ? "on" : ""} aria-pressed={!appIds.length} onClick={() => set(["source_app_ids"], [])}>
            {!appIds.length ? "✓ " : ""}All applications
          </button>
          {apps.map((a) => {
            const on = appIds.includes(a.id);
            return (
              <button key={a.id} type="button" className={on ? "on" : ""} aria-pressed={on}
                onClick={() => set(["source_app_ids"], on ? appIds.filter((x) => x !== a.id) : [...appIds, a.id])}>
                {on ? "✓ " : ""}{a.name}{!a.active ? " (inactive)" : ""}
              </button>
            );
          })}
        </div>
      )}
      <p className="sub" style={{ fontSize: 12, marginTop: 4 }}>
        {appIds.length ? `Only ${appIds.length} selected application${appIds.length === 1 ? "" : "s"} can submit to this workflow.` : "Every application can submit to this workflow."}
      </p>

      <ErrorBox error={err} />
      <div className="btn-row" style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={save} disabled={busy || !f.name.trim() || f.stages.some((s) => !s.name.trim())}>{busy ? "Saving…" : "Save"}</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

// --- Version history ------------------------------------------------------------------------------------------

const FIELD_NAME: Record<string, string> = {
  name: "name", canary_rate: "canary rate", canary_domain: "canary domain", spot_check_rate: "spot-check rate",
  source_app_ids: "source apps", stages: "stages",
};

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const short = (v: unknown) => {
  if (v == null || v === "") return "default";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (typeof v === "object") return "changed";
  return String(v);
};

// One line saying what a version changed from the one before.
function summarize(prev: WF | undefined, w: WF, teamName: (id: string) => string): string {
  if (!prev) return "Created";
  const out: string[] = [];
  for (const k of ["name", "canary_rate", "canary_domain", "spot_check_rate"] as const) {
    if (!same(prev[k], w[k])) out.push(`${FIELD_NAME[k]} ${short(prev[k])} → ${short(w[k])}`);
  }
  if (!same(prev.stages, w.stages)) {
    out.push(prev.stages.length !== w.stages.length
      ? `stages ${prev.stages.length} → ${w.stages.length}`
      : `stages: ${w.stages.map((s) => `${s.name}${s.team_id ? ` (${teamName(s.team_id)})` : ""}`).join(" → ")}`);
  }
  for (const pol of ["telemetry_policy", "cap_policy", "risk_policy", "assurance_policy"] as const) {
    const a = prev[pol] || {}, b = w[pol] || {};
    for (const key of Array.from(new Set([...Object.keys(a), ...Object.keys(b)]))) {
      if (same(a[key], b[key])) continue;
      const fmt = (v: unknown) => (key === "spot_check_team_id" && typeof v === "string" ? teamName(v) : short(v));
      out.push(`${label(key)} ${fmt(a[key])} → ${fmt(b[key])}`);
    }
  }
  if (!same(prev.source_app_ids, w.source_app_ids)) {
    out.push(`source apps ${prev.source_app_ids?.length || "all"} → ${w.source_app_ids?.length || "all"}`);
  }
  return out.length ? out.join("; ") : "Saved with no changes";
}

function History({ wf, teams }: { wf: WF; teams: Team[] }) {
  const { data, error, loading } = useLoad(
    () => api<WF[]>(`/v1/admin/workflows${qs({ include_history: true, family_id: wf.id })}`), [wf.id, wf.version]);
  // Who saved each version comes from the audit log; it's optional (needs auditor or oversight access).
  const { data: events } = useLoad(
    () => api<{ actor: string; payload: any }[]>(`/v1/audit/events${qs({ entity: "workflow", entity_id: wf.id, order: "desc", limit: 200 })}`)
      .catch(() => []), [wf.id, wf.version]);
  const teamName = (id: string) => teams.find((t) => t.id === id)?.name || id.slice(0, 8);
  const byVersion = new Map<number, string>();
  (events || []).forEach((e) => e.payload?.version != null && !byVersion.has(e.payload.version) && byVersion.set(e.payload.version, e.actor));

  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <p className="sub">Loading history…</p>;
  const versions = [...(data || [])].sort((a, b) => a.version - b.version);
  return (
    <ol aria-label={`Version history of ${wf.name}`}>
      {[...versions].reverse().map((v) => {
        const prev = versions.find((p) => p.version === v.version - 1);
        return (
          <li key={v.version_id || v.version}>
            <span><Badge tone={v.is_current ? "accent" : undefined}>v{v.version}</Badge></span>
            <span className="sub">{when(v.updated_at)}{byVersion.get(v.version) ? <div className="mono" style={{ fontSize: 11 }}>{byVersion.get(v.version)}</div> : null}</span>
            <span>{summarize(prev, v, teamName)}</span>
          </li>
        );
      })}
    </ol>
  );
}

// --- Page ---------------------------------------------------------------------------------------------------

export default function Workflows() {
  const { data, error, loading, reload } = useLoad(() => api<WF[]>("/v1/admin/workflows"));
  const { data: teams } = useLoad(() => api<Team[]>("/v1/admin/teams").catch(() => [] as Team[]));
  const { data: apps } = useLoad(() => api<SourceApp[]>("/v1/admin/source-apps").catch(() => [] as SourceApp[]));
  const [editing, setEditing] = useState<WF | null>(null);
  const [history, setHistory] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const appName = (id: string) => apps?.find((a) => a.id === id)?.name || id.slice(0, 8);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Workflow settings</h1>
          <div className="sub">Every save creates a new version. Submissions keep the version they arrived under.</div>
        </div>
        <button className="btn primary" onClick={() => { setMsg(""); setEditing(BLANK); }}>New workflow</button>
      </div>
      {msg && <div className="success">{msg}</div>}
      <ErrorBox error={error} />
      {editing && (
        <Editor key={editing.id || "new"} wf={editing} teams={teams || []} apps={apps || []}
          onCancel={() => setEditing(null)} onDone={(m) => { setEditing(null); setMsg(m); reload(); }} />
      )}
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {loading && !data ? <Empty>Loading…</Empty> : !data?.length ? <Empty>No workflows yet.</Empty> : (
          <table>
            <thead><tr><th>Workflow</th><th>Stages</th><th>Canaries</th><th>Spot-checks</th><th>Mandatory flags</th><th>Caps</th><th>Min score</th><th>Apps</th><th>Updated</th><th /></tr></thead>
            <tbody>
              {data.map((w) => (
                <Fragment key={w.id}>
                  <tr>
                    <td><div style={{ fontWeight: 600 }}>{w.name}</div><Badge>v{w.version}</Badge> <span className="mono sub">{w.id.slice(0, 8)}</span></td>
                    <td>
                      {w.stages.map((s) => s.name).join(" → ")}
                      {w.stages.some((s) => s.on_sla_breach === "reassign") && <div className="sub" style={{ fontSize: 12 }}>reassigns on SLA breach</div>}
                    </td>
                    <td>{w.canary_rate == null ? "–" : `${Math.round(w.canary_rate * 100)}%`}</td>
                    <td>{w.spot_check_rate == null ? "–" : `${Math.round(w.spot_check_rate * 100)}%`}</td>
                    <td>{(w.risk_policy?.mandatory_severities ?? ["high"]).join(", ") || <span className="sub">none</span>}</td>
                    <td>{w.cap_policy?.daily_minutes ?? 240} min/day</td>
                    <td>{w.assurance_policy?.min_score ?? 60}</td>
                    <td className="sub" style={{ fontSize: 12 }}>{w.source_app_ids?.length ? w.source_app_ids.map(appName).join(", ") : "All"}</td>
                    <td>{when(w.updated_at)}</td>
                    <td>
                      <div className="btn-row" style={{ flexWrap: "nowrap" }}>
                        <button className="btn small" onClick={() => { setMsg(""); setEditing(w); window.scrollTo({ top: 0, behavior: "smooth" }); }}>Edit</button>
                        <button className="btn small" aria-expanded={history === w.id} onClick={() => setHistory(history === w.id ? null : w.id)}>
                          {history === w.id ? "Hide history" : "History"}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {history === w.id && (
                    <tr className="ov-history"><td colSpan={10}><History wf={w} teams={teams || []} /></td></tr>
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
