import { useMemo, useState } from "react";
import { api, apiAll } from "../../api";
import { Badge, Empty, Modal, useLoad, when } from "../ui";
import { Confirm, Err, useAction } from "../admin/common";
import { fieldsFor, KIND_INFO, kindTitle, ORIGINS, RISK_TIERS, TEAMS_EVENTS, type FieldDef } from "./connectorFields";
import { fullTime, MASK, type Connector, type KindSpec, type Loaded, type SourceApp, type Workflow } from "./types";

type RunResult = { connector: Connector; result: Record<string, any> };

export function Connectors({ apps }: { apps: Loaded<SourceApp[]> }) {
  const connectors = useLoad(() => apiAll<Connector>("/v1/integrations/connectors"));
  const kinds = useLoad(() => api<Record<string, KindSpec>>("/v1/integrations/connector-kinds"));
  const workflows = useLoad(() => apiAll<Workflow>("/v1/integrations/workflows"));
  const [editing, setEditing] = useState<Connector | "new" | null>(null);
  const [deleting, setDeleting] = useState<Connector | null>(null);
  const [ran, setRan] = useState<RunResult | null>(null);
  const list = useAction();

  const appName = (id: string) => apps.data?.find((a) => a.id === id)?.name ?? id.slice(0, 8);
  const wfName = (id: string | undefined) => (id ? workflows.data?.find((w) => w.id === id)?.name ?? `${id.slice(0, 8)} (not found)` : null);

  async function toggle(c: Connector) {
    await list.run(`toggle:${c.id}`, async () => {
      await api(`/v1/integrations/connectors/${c.id}`, { method: "PATCH", body: { active: !c.active } });
      connectors.reload();
    }, `${kindTitle(c.kind)} connector switched ${c.active ? "off" : "on"}.`);
  }

  async function runNow(c: Connector) {
    const r = await list.run(`run:${c.id}`, () => api<Connector & { result: Record<string, any> }>(`/v1/integrations/connectors/${c.id}/run`, { method: "POST" }));
    if (r) {
      const { result, ...connector } = r;
      setRan({ connector, result: result || {} });
      connectors.reload();
    }
  }

  function summary(c: Connector) {
    const cfg = c.config || {};
    const bits: string[] = [];
    if (c.kind === "sharepoint") bits.push(`folder ${cfg.inbox_folder || "Review/Inbox"}`);
    if (c.kind === "jira" && cfg.base_url) bits.push(String(cfg.base_url).replace(/^https?:\/\//, ""));
    if (c.kind === "azure_devops") bits.push([cfg.organization, cfg.project].filter(Boolean).join(" / "));
    if (c.kind === "teams") bits.push(`cards: ${String(cfg.notify ?? "assigned,escalated,deferred").split(",").filter(Boolean).join(", ") || "none"}`);
    const wf = wfName(cfg.workflow_id);
    if (wf) bits.push(`→ ${wf}`);
    return bits.filter(Boolean).join(" · ");
  }

  function pollingText(c: Connector) {
    if (c.polls) return <Badge tone="accent">polls</Badge>;
    const spec = kinds.data?.[c.kind];
    if (spec && typeof spec.polls === "string") return <span className="sub">events only (no query)</span>;
    return <span className="sub">events only</span>;
  }

  const noApps = apps.data && apps.data.length === 0;

  return (
    <>
      <div className="section-head">
        <div>
          <h2>Connectors</h2>
          <div className="sub">SharePoint / OneDrive folders, Microsoft Teams notifications, and Jira or Azure DevOps tickets. Each connector acts as one of your source apps.</div>
        </div>
        <div className="btn-row">
          <button className="btn small" onClick={() => { connectors.reload(); workflows.reload(); }} disabled={connectors.loading}>Refresh</button>
          <button className="btn small primary" disabled={!kinds.data || !apps.data?.length} title={noApps ? "Register a source application first" : undefined}
            onClick={() => setEditing("new")}>Add connector</button>
        </div>
      </div>
      {noApps && <div className="notice">Register a source application first: connectors submit and receive decisions as one of your apps.</div>}
      <Err error={apps.error} />
      <Err error={connectors.error} />
      <Err error={kinds.error} />
      <Err error={workflows.error} />
      <Err error={list.error} />
      {list.ok && <div className="success">{list.ok}</div>}

      <div className="card flush">
        {connectors.loading && !connectors.data ? <div className="sub pad">Loading…</div>
          : !connectors.data?.length ? <Empty>{connectors.error ? "Connectors could not be loaded." : "No connectors yet."}</Empty>
          : (
            <div className="table-scroll">
              <table>
                <thead><tr><th>Connector</th><th>App</th><th>Status</th><th>Polling</th><th>Last run</th><th>Last error</th><th /></tr></thead>
                <tbody>
                  {connectors.data.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <div className="strong">{kindTitle(c.kind)}</div>
                        <div className="sub small-text">{summary(c)}</div>
                        <div className="mono sub">{c.id.slice(0, 8)}</div>
                      </td>
                      <td>{appName(c.source_app_id)}</td>
                      <td><Badge tone={c.active ? "ok" : ""}>{c.active ? "on" : "off"}</Badge></td>
                      <td>{pollingText(c)}</td>
                      <td title={fullTime(c.last_run_at)}>{c.last_run_at ? when(c.last_run_at) : <span className="sub">never</span>}</td>
                      <td className="error-cell">{c.last_error ? <span className="text-high" title={c.last_error}>{c.last_error.length > 120 ? `${c.last_error.slice(0, 120)}…` : c.last_error}</span> : <span className="sub">–</span>}</td>
                      <td>
                        <div className="btn-row nowrap">
                          {c.polls && (
                            <button className="btn small" disabled={!c.active || list.busy === `run:${c.id}`} title={c.active ? undefined : "Switch it on first"}
                              onClick={() => runNow(c)}>{list.busy === `run:${c.id}` ? "Running…" : "Run now"}</button>
                          )}
                          <button className="btn small" disabled={!kinds.data} onClick={() => setEditing(c)}>Edit</button>
                          <button className="btn small" disabled={list.busy === `toggle:${c.id}`} onClick={() => toggle(c)}>{c.active ? "Switch off" : "Switch on"}</button>
                          <button className="btn small danger" onClick={() => setDeleting(c)}>Delete</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {editing && kinds.data && (
        <ConnectorForm existing={editing === "new" ? null : editing} kinds={kinds.data} apps={apps.data ?? []} workflows={workflows.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={(msg) => { setEditing(null); connectors.reload(); list.clear(); list.setOk(msg); }} />
      )}
      {deleting && (
        <Confirm title="Delete connector" confirmLabel="Delete connector" danger onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api(`/v1/integrations/connectors/${deleting.id}`, { method: "DELETE" });
            connectors.reload();
            list.clear();
            list.setOk(`${kindTitle(deleting.kind)} connector deleted.`);
          }}>
          <p>Delete the <strong>{kindTitle(deleting.kind)}</strong> connector for <strong>{appName(deleting.source_app_id)}</strong>? Its settings and
            stored secrets are removed and cannot be recovered. Submissions it already made are kept.</p>
          <p className="sub">To pause it instead, switch it off.</p>
        </Confirm>
      )}
      {ran && <RunResultModal run={ran} onClose={() => setRan(null)} />}
    </>
  );
}

function RunResultModal({ run, onClose }: { run: RunResult; onClose: () => void }) {
  const r = run.result;
  const submitted: string[] = Array.isArray(r.submitted) ? r.submitted : [];
  const completed: unknown[] = Array.isArray(r.completed) ? r.completed : [];
  const known = new Set(["submitted", "completed", "error", "skipped"]);
  const other = Object.fromEntries(Object.entries(r).filter(([k]) => !known.has(k)));
  return (
    <Modal title={`Ran ${kindTitle(run.connector.kind)} connector`} onClose={onClose}>
      {r.error ? <div className="error">The run failed: {String(r.error)}</div>
        : r.skipped ? <div className="notice">Nothing ran: the connector is off or has no query to poll.</div>
        : <div className="success">Run finished at {fullTime(run.connector.last_run_at)}.</div>}
      {!r.error && !r.skipped && (
        <>
          <p><strong>{submitted.length}</strong> new item{submitted.length === 1 ? "" : "s"} submitted for review
            {"completed" in r && <>, <strong>{completed.length}</strong> decision{completed.length === 1 ? "" : "s"} written back</>}.</p>
          {submitted.length > 0 && (
            <ul className="id-list">{submitted.map((id) => <li key={id} className="mono">{id}</li>)}</ul>
          )}
          {submitted.length === 0 && <p className="sub">Nothing new matched. Items already submitted are not submitted again.</p>}
        </>
      )}
      {Object.keys(other).length > 0 && <pre className="json">{JSON.stringify(other, null, 2)}</pre>}
      <div className="btn-row modal-actions"><button className="btn primary" onClick={onClose}>Close</button></div>
    </Modal>
  );
}

// --- Create / edit form ------------------------------------------------------------------------------------

function initialValue(f: FieldDef, existing: Connector | null): string {
  const v = existing?.config?.[f.key];
  if (f.type === "secret") return "";
  if (existing && v !== undefined && v !== null) {
    if (f.type === "json") return typeof v === "string" ? v : JSON.stringify(v, null, 2);
    return String(v);
  }
  if (f.type === "json") return "";
  return f.fallback ?? "";
}

function parseJson(text: string): { value?: Record<string, string | number>; error?: string } {
  if (!text.trim()) return {};
  try {
    const v = JSON.parse(text);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { error: "Must be a JSON object, e.g. {\"approved\": \"31\"}." };
    const bad = Object.entries(v).filter(([, x]) => typeof x !== "string" && typeof x !== "number").map(([k]) => k);
    if (bad.length) return { error: `Values must be text or numbers (check: ${bad.join(", ")}).` };
    return { value: v };
  } catch (e) {
    return { error: `Not valid JSON: ${(e as Error).message}` };
  }
}

function ConnectorForm({ existing, kinds, apps, workflows, onClose, onSaved }: {
  existing: Connector | null; kinds: Record<string, KindSpec>; apps: SourceApp[]; workflows: Workflow[];
  onClose: () => void; onSaved: (msg: string) => void;
}) {
  const kindKeys = Object.keys(kinds);
  const [kind, setKind] = useState(existing?.kind ?? kindKeys[0] ?? "sharepoint");
  const [appId, setAppId] = useState(existing?.source_app_id ?? apps.find((a) => a.active)?.id ?? apps[0]?.id ?? "");
  const [active, setActive] = useState(existing?.active ?? true);
  const spec = kinds[kind];
  const fields = useMemo(() => fieldsFor(kind, spec), [kind, spec]);
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fieldsFor(kind, spec).map((f) => [f.key, initialValue(f, existing)])));
  const [touched, setTouched] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(() => !!existing && fields.some((f) => f.group === "advanced" && existing.config?.[f.key] != null && String(existing.config[f.key]) !== (f.fallback ?? "")));
  const a = useAction();

  function changeKind(k: string) {
    setKind(k);
    setValues(Object.fromEntries(fieldsFor(k, kinds[k]).map((f) => [f.key, initialValue(f, null)])));
    setTouched(false);
    a.clear();
  }
  const set = (k: string, v: string) => setValues((s) => ({ ...s, [k]: v }));

  const pollKey = typeof spec?.polls === "string" ? spec.polls : null;
  const pollsNow = spec?.polls === true || (!!pollKey && !!values[pollKey]?.trim());
  const required = new Set([...(spec?.required ?? []), ...(pollsNow && fields.some((f) => f.key === "workflow_id") ? ["workflow_id"] : [])]);

  const errors: Record<string, string> = {};
  for (const f of fields) {
    const v = (values[f.key] ?? "").trim();
    const isSecretKept = f.type === "secret" && !!existing && !!existing.config?.[f.key];
    if (required.has(f.key) && !v && !isSecretKept) errors[f.key] = "Required.";
    if (f.type === "json" && v) {
      const p = parseJson(v);
      if (p.error) errors[f.key] = p.error;
    }
    if ((f.type === "url" || (f.type === "secret" && f.key.endsWith("url"))) && v && !/^https?:\/\/[^\s/]+/i.test(v)) errors[f.key] = "Enter a full URL starting with https://";
  }
  const app = apps.find((x) => x.id === appId);
  const wf = workflows.find((w) => w.id === values.workflow_id);
  const wfClosed = wf && wf.source_app_ids && wf.source_app_ids.length > 0 && !wf.source_app_ids.includes(appId);

  function payload(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const f of fields) {
      const raw = values[f.key] ?? "";
      const v = f.type === "json" || f.type === "query" ? raw : raw.trim();
      if (f.type === "secret") {
        if (v.trim()) out[f.key] = v.trim();
        else if (existing) out[f.key] = MASK; // keep the stored secret
        continue;
      }
      if (f.type === "notify") { out[f.key] = v; continue; }
      if (f.type === "json") {
        if (v.trim()) out[f.key] = parseJson(v).value;
        else if (existing) out[f.key] = null;
        continue;
      }
      if (!v.trim()) {
        if (f.fallback !== undefined) out[f.key] = f.fallback;
        else if (existing) out[f.key] = null; // clear a stored optional setting
        continue;
      }
      out[f.key] = v.trim();
    }
    return out;
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length) return;
    const config = payload();
    const r = await a.run("save", () => existing
      ? api<Connector>(`/v1/integrations/connectors/${existing.id}`, { method: "PATCH", body: { config, active } })
      : api<Connector>("/v1/integrations/connectors", { body: { source_app_id: appId, kind, config, active } }));
    if (r) onSaved(existing ? `${kindTitle(kind)} connector saved.` : `${kindTitle(kind)} connector created${r.polls && r.active ? "; use Run now to test it" : ""}.`);
  }

  const info = KIND_INFO[kind];
  const main = fields.filter((f) => f.group !== "advanced");
  const advanced = fields.filter((f) => f.group === "advanced");

  function renderField(f: FieldDef) {
    const id = `cf-${f.key}`;
    const v = values[f.key] ?? "";
    const err = touched || f.type === "json" ? errors[f.key] : undefined;
    const req = required.has(f.key);
    const stored = !!existing && !!existing.config?.[f.key];
    const labelEl = <label htmlFor={id}>{f.label}{req && <span className="req" aria-hidden> *</span>}</label>;
    let input: React.ReactNode;
    switch (f.type) {
      case "secret":
        input = <input id={id} type="password" autoComplete="new-password" value={v} onChange={(e) => set(f.key, e.target.value)}
          placeholder={stored ? "Stored (hidden). Type a new value to replace it." : ""} aria-invalid={!!err} />;
        break;
      case "workflow":
        input = (
          <select id={id} value={v} onChange={(e) => set(f.key, e.target.value)} aria-invalid={!!err}>
            <option value="">{req ? "Choose a workflow…" : "None"}</option>
            {v && !workflows.some((w) => w.id === v) && <option value={v}>{v} (not a current workflow)</option>}
            {workflows.map((w) => {
              const closed = w.source_app_ids && w.source_app_ids.length > 0 && !w.source_app_ids.includes(appId);
              return <option key={w.id} value={w.id}>{w.name}{closed ? " (not open to this app)" : ""}</option>;
            })}
          </select>
        );
        break;
      case "risk":
        input = <select id={id} value={v} onChange={(e) => set(f.key, e.target.value)}>{RISK_TIERS.map((t) => <option key={t} value={t}>{t}</option>)}</select>;
        break;
      case "origin":
        input = <select id={id} value={v} onChange={(e) => set(f.key, e.target.value)}>{ORIGINS.map((t) => <option key={t} value={t}>{t === "ai" ? "AI" : t}</option>)}</select>;
        break;
      case "query":
        input = <textarea id={id} className="mono" rows={2} value={v} onChange={(e) => set(f.key, e.target.value)} placeholder={f.placeholder} />;
        break;
      case "json":
        input = <textarea id={id} className="mono" rows={5} value={v} onChange={(e) => set(f.key, e.target.value)} placeholder={f.placeholder} aria-invalid={!!err} spellCheck={false} />;
        break;
      case "notify": {
        const on = new Set(v.split(",").map((s) => s.trim()).filter(Boolean));
        return (
          <fieldset key={f.key} className="field">
            <legend>{f.label}</legend>
            {TEAMS_EVENTS.map((ev) => (
              <label key={ev.key} className="check">
                <input type="checkbox" checked={on.has(ev.key)} onChange={(e) => {
                  const next = new Set(on);
                  if (e.target.checked) next.add(ev.key); else next.delete(ev.key);
                  set(f.key, TEAMS_EVENTS.map((x) => x.key).filter((k) => next.has(k)).join(","));
                }} /> {ev.label}
              </label>
            ))}
            {on.size === 0 && <div className="notice">No events selected: this connector will not post anything.</div>}
          </fieldset>
        );
      }
      default:
        input = <input id={id} type={f.type === "url" ? "url" : "text"} value={v} onChange={(e) => set(f.key, e.target.value)}
          placeholder={f.placeholder ?? f.fallback} aria-invalid={!!err} />;
    }
    return (
      <div key={f.key} className="field">
        {labelEl}
        {input}
        {f.help && <div className="sub hint">{f.help}</div>}
        {f.type === "workflow" && wfClosed && <div className="notice">This workflow only accepts submissions from other apps, so submissions from {app?.name ?? "this app"} would be refused. Pick another workflow or add the app to it.</div>}
        {err && <div className="field-error">{err}</div>}
      </div>
    );
  }

  return (
    <Modal title={existing ? `Edit ${kindTitle(kind)} connector` : "Add a connector"} onClose={onClose} width={720}>
      <form onSubmit={save} className="modal-scroll" noValidate>
        {!existing && (
          <fieldset className="kind-picker">
            <legend className="sr-only">Connector type</legend>
            {kindKeys.map((k) => (
              <label key={k} className={`choice${k === kind ? " on" : ""}`}>
                <input type="radio" name="kind" checked={k === kind} onChange={() => changeKind(k)} />
                <span><strong>{kindTitle(k)}</strong>
                  <span className="sub">{kinds[k].polls === true ? "Polls" : kinds[k].polls ? "Events, optional polling" : "Events"}</span></span>
              </label>
            ))}
          </fieldset>
        )}
        {info && <p className="sub">{info.blurb}</p>}

        <div className="field">
          <label htmlFor="cf-app">Source application</label>
          {existing
            ? <div>{app?.name ?? appId} <span className="sub small-text">(can't be changed; create a new connector to use another app)</span></div>
            : (
              <select id="cf-app" value={appId} onChange={(e) => setAppId(e.target.value)}>
                {apps.map((x) => <option key={x.id} value={x.id}>{x.name}{x.active ? "" : " (deactivated)"}</option>)}
              </select>
            )}
          {app && !app.active && <div className="notice">This app is deactivated, so the connector can't submit until it is activated.</div>}
        </div>

        {main.map(renderField)}

        {advanced.length > 0 && (
          <details className="advanced" open={showAdvanced || advanced.some((f) => !!errors[f.key])} onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}>
            <summary>More settings</summary>
            {advanced.map(renderField)}
          </details>
        )}

        <label className="check">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Connector is on
        </label>
        {pollsNow && <div className="sub hint">This connector polls on the scheduler's interval while it is on. Use Run now after saving to test the settings.</div>}

        {touched && Object.keys(errors).length > 0 && <div className="error">Fix the highlighted settings first.</div>}
        <Err error={a.error} />
        <div className="btn-row modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!!a.busy || (!existing && !appId)}>{a.busy ? "Saving…" : existing ? "Save changes" : "Create connector"}</button>
        </div>
      </form>
    </Modal>
  );
}
