import { useState } from "react";
import { api, ApiError } from "../api";
import { Badge, ROLE_INFO, useLoad } from "../components/ui";
import { Confirm, CopyField, Err, Ok, Section, useAction } from "../components/admin/common";
import "./admin.css";

type Price = { input: number; output: number; cache_read?: number | null; cache_write?: number | null };
type Sso = {
  enabled: boolean; issuer: string; client_id: string; client_secret?: string; domains: string[];
  auto_provision_roles: string[]; scopes?: string; authorization_endpoint?: string; token_endpoint?: string; jwks_uri?: string;
  // Require this provider for everyone in the organisation (the platform-wide provider can't sign them in).
  // NOTE: field name must match the backend SsoIn model.
  enforce?: boolean;
};
type Settings = {
  id: string; name: string; region: string; active: boolean; allow_admin_reviewers: boolean; retention_days: number | null;
  currency: string; usd_to_currency: number; default_hourly_rate: number | null; model_prices: Record<string, Price>;
  default_model_prices: Record<string, Price>; prices_as_of: string;
  notifications: { assigned: boolean; escalated: boolean; decided: boolean }; sso: Sso | null;
  sso_redirect_uri?: string | null; sso_redirect_uris?: string[];
};

type SectionProps = { t: Settings; onSaved: (t: Settings) => void };

const MIN_RETENTION = 30;
const SECTIONS = [
  { id: "general", label: "General" },
  { id: "reviewing", label: "Reviewing" },
  { id: "retention", label: "Data retention" },
  { id: "costs", label: "Costs" },
  { id: "notifications", label: "Email notifications" },
  { id: "sso", label: "Single sign-on" },
];

function put(body: Record<string, unknown>) {
  return api<Settings>("/v1/admin/tenant", { method: "PUT", body });
}

export default function TenantSettings() {
  const load = useLoad(() => api<Settings>("/v1/admin/tenant"));
  const [latest, setLatest] = useState<Settings | null>(null);
  const t = latest ?? load.data;

  return (
    <div className="admin-page">
      <div className="page-head">
        <div>
          <h1>Organisation settings</h1>
          <div className="sub">{t ? t.name : "Your organisation"}: reviewing rules, data retention, costs, email and sign-in. Each section saves on its own.</div>
        </div>
      </div>
      <Err error={load.error} />
      {load.loading && !t && <div className="sub">Loading…</div>}
      {!!load.error && !t && <button className="btn" onClick={load.reload}>Try again</button>}
      {t && (
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="Sections">
            {SECTIONS.map((s) => <a key={s.id} href={`#${s.id}`} onClick={(e) => {
              e.preventDefault();
              document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}>{s.label}</a>)}
          </nav>
          <div className="settings-main">
            <General t={t} onSaved={setLatest} />
            <Reviewing t={t} onSaved={setLatest} />
            <Retention t={t} onSaved={setLatest} />
            <Costs t={t} onSaved={setLatest} />
            <Notifications t={t} onSaved={setLatest} />
            <SingleSignOn t={t} onSaved={setLatest} />
          </div>
        </div>
      )}
    </div>
  );
}

function SaveRow({ a, disabled, label = "Save", dirty }: { a: ReturnType<typeof useAction>; disabled?: boolean; label?: string; dirty?: boolean }) {
  return (
    <>
      <Err error={a.error} />
      <Ok>{a.ok}</Ok>
      <div className="btn-row section-actions">
        <button type="submit" className="btn primary" disabled={!!a.busy || disabled}>{a.busy === "save" ? "Saving…" : label}</button>
        {dirty && !a.busy && <span className="sub small-text">Unsaved changes</span>}
      </div>
    </>
  );
}

// --- General ------------------------------------------------------------------------------------------------

function General({ t, onSaved }: SectionProps) {
  const [name, setName] = useState(t.name);
  const a = useAction();
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const r = await a.run("save", () => put({ name: name.trim() }), "Name saved.");
    if (r) { onSaved(r); setName(r.name); }
  }
  return (
    <Section id="general" title="General">
      <form onSubmit={save}>
        <label htmlFor="org-name">Organisation name</label>
        <input id="org-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required />
        <dl className="kv detail-kv">
          <dt>Organisation ID</dt><dd className="mono">{t.id}</dd>
          <dt>Data region</dt><dd>{t.region}</dd>
          <dt>Status</dt><dd><Badge tone={t.active ? "ok" : "high"}>{t.active ? "active" : "deactivated"}</Badge></dd>
        </dl>
        <SaveRow a={a} disabled={!name.trim() || name.trim() === t.name} dirty={name.trim() !== t.name} />
      </form>
    </Section>
  );
}

// --- Reviewing ----------------------------------------------------------------------------------------------

// People named in an error's detail (e.g. the 422 listing users who hold both admin and reviewer), if any.
function namedPeople(e: unknown): string[] {
  if (!(e instanceof ApiError) || !e.detail || typeof e.detail !== "object" || Array.isArray(e.detail)) return [];
  for (const v of Object.values(e.detail as Record<string, unknown>)) {
    if (!Array.isArray(v) || !v.length) continue;
    const names = v.map((x: any) => (typeof x === "string" ? x : x && typeof x === "object" ? (x.name ? `${x.name} (${x.email ?? x.id ?? ""})`.replace(" ()", "") : x.email ?? x.id) : null))
      .filter((x): x is string => !!x);
    if (names.length) return names;
  }
  return [];
}

function Reviewing({ t, onSaved }: SectionProps) {
  const [allow, setAllow] = useState(t.allow_admin_reviewers);
  const a = useAction();
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const r = await a.run("save", () => put({ allow_admin_reviewers: allow }),
      allow ? "Saved. On the Users page, an admin can now also be given review work with \"Also a reviewer\"." : "Admins and reviewers are kept separate.");
    if (r) { onSaved(r); setAllow(r.allow_admin_reviewers); }
  }
  const blocked = namedPeople(a.error);
  return (
    <Section id="reviewing" title="Reviewing" intro={
      <>
        <p>Admins normally don't review. An admin sets the controls that reviewers are measured by (workflows, assignment,
          canaries, spot-checks and thresholds) and can see and change the records those controls produce. If the same
          person also reviews, they could mark their own homework, and the evidence pack would no longer show an
          independent check. Keeping the roles apart is what auditors expect.</p>
        <p>Allow it only when the organisation is too small for separate people. Admins still need the reviewer role
          given explicitly, and their reviews remain measured and logged like anyone else's.</p>
      </>
    }>
      <form onSubmit={save}>
        <label className="check">
          <input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} />
          Allow people to have both the admin and reviewer roles
        </label>
        {allow && !t.allow_admin_reviewers && <div className="notice">Turning this on weakens separation of duties. It is recorded in the audit log.</div>}
        {!allow && t.allow_admin_reviewers && <div className="notice">If anyone has both roles, this can't be turned off until you remove the admin or reviewer role from them on the Users page.</div>}
        <SaveRow a={a} disabled={allow === t.allow_admin_reviewers} dirty={allow !== t.allow_admin_reviewers} />
        {blocked.length > 0 && (
          <div className="notice">
            Still both admin and reviewer:
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{blocked.map((n) => <li key={n}>{n}</li>)}</ul>
          </div>
        )}
      </form>
    </Section>
  );
}

// --- Data retention -----------------------------------------------------------------------------------------

function Retention({ t, onSaved }: SectionProps) {
  const [forever, setForever] = useState(!t.retention_days);
  const [days, setDays] = useState(t.retention_days ? String(t.retention_days) : "365");
  const a = useAction();
  const [purgeId, setPurgeId] = useState("");
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [purged, setPurged] = useState<string | null>(null);

  const n = Number(days);
  const invalid = !forever && (!Number.isInteger(n) || n < MIN_RETENTION);
  const value = forever ? null : n;
  const dirty = value !== (t.retention_days || null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (invalid) return;
    const r = await a.run("save", () => put({ retention_days: value ?? 0 }),
      value ? `Content of finished submissions will be removed ${value} days after release.` : "Content is kept until you remove it.");
    if (r) { onSaved(r); setForever(!r.retention_days); if (r.retention_days) setDays(String(r.retention_days)); }
  }

  return (
    <Section id="retention" title="Data retention" intro={
      <p>How long to keep the content people sent for review once it is finished (approved, rejected, changes requested,
        deferred or cancelled), counted from its release date.</p>
    }>
      <form onSubmit={save}>
        <div className="grid grid-2 explain-grid">
          <div className="explain">
            <h3>Removed</h3>
            <ul>
              <li>Uploaded files (originals) and the extracted document text</li>
              <li>Claim text and rationale in risk flags</li>
              <li>Canary text, reviewer comments and suggested edits</li>
              <li>Reading telemetry</li>
            </ul>
          </div>
          <div className="explain">
            <h3>Kept</h3>
            <ul>
              <li>Decisions and their reasons</li>
              <li>Assurance scores</li>
              <li>The audit log</li>
              <li>The evidence pack, so you can still prove the review happened</li>
            </ul>
          </div>
        </div>
        <fieldset className="choice-list">
          <legend className="sr-only">Retention</legend>
          <label className={`choice${forever ? " on" : ""}`}>
            <input type="radio" name="retention" checked={forever} onChange={() => setForever(true)} />
            <span><strong>Keep content until removed</strong><span className="sub">Nothing is removed automatically.</span></span>
          </label>
          <label className={`choice${!forever ? " on" : ""}`}>
            <input type="radio" name="retention" checked={!forever} onChange={() => setForever(false)} />
            <span>
              <strong>Remove content after</strong>{" "}
              <input type="number" className="inline-number" min={MIN_RETENTION} step={1} value={days} aria-label="Days to keep content"
                onFocus={() => setForever(false)} onChange={(e) => setDays(e.target.value)} aria-invalid={invalid} /> days
              <span className="sub">At least {MIN_RETENTION} days. Content past this age is removed automatically.</span>
            </span>
          </label>
        </fieldset>
        {invalid && <div className="field-error">Enter a whole number of days, {MIN_RETENTION} or more.</div>}
        <SaveRow a={a} disabled={invalid || !dirty} dirty={dirty} />
      </form>

      <div className="subsection">
        <h3>Remove one submission's content now</h3>
        <p className="sub">For an erasure request from a client. The submission must be finished. Decisions, scores, the audit log and the evidence pack are kept; this can't be undone.</p>
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); setPurged(null); if (purgeId.trim()) setConfirmPurge(true); }}>
          <input value={purgeId} onChange={(e) => setPurgeId(e.target.value)} placeholder="Submission ID" className="mono" aria-label="Submission ID" />
          <button className="btn danger" type="submit" disabled={!purgeId.trim()}>Remove content…</button>
        </form>
        <Ok>{purged}</Ok>
      </div>
      {confirmPurge && (
        <Confirm title="Remove submission content" confirmLabel="Remove content permanently" danger onClose={() => setConfirmPurge(false)}
          onConfirm={async () => {
            const r = await api<{ submission_id: string; purged_at: string; files: number; already_purged: boolean }>(
              `/v1/admin/submissions/${encodeURIComponent(purgeId.trim())}/content`, { method: "DELETE" });
            setPurged(r.already_purged
              ? `Submission ${r.submission_id} had already been removed.`
              : `Content of submission ${r.submission_id} removed (${r.files} file${r.files === 1 ? "" : "s"} deleted). The decision and evidence pack are kept.`);
            setPurgeId("");
          }}>
          <p>Permanently remove the files, text, comments and telemetry of submission <span className="mono">{purgeId.trim()}</span>?</p>
          <p className="sub">The decision, assurance score, audit log and evidence pack are kept. This is recorded in the audit log and can't be undone.</p>
        </Confirm>
      )}
    </Section>
  );
}

// --- Costs --------------------------------------------------------------------------------------------------

type PriceRow = { key: number; model: string; input: string; output: string; cache_read: string; cache_write: string };

const CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY", "SEK", "NOK", "DKK", "NZD", "SGD", "INR"];
let rowKey = 0;
const toRow = (model: string, p: Partial<Price>): PriceRow => ({
  key: ++rowKey, model, input: p.input != null ? String(p.input) : "", output: p.output != null ? String(p.output) : "",
  cache_read: p.cache_read != null ? String(p.cache_read) : "", cache_write: p.cache_write != null ? String(p.cache_write) : "",
});
const fmtPrice = (v: number | null | undefined) => (v == null ? "–" : `$${v}`);

function Costs({ t, onSaved }: SectionProps) {
  const [currency, setCurrency] = useState(t.currency || "USD");
  const [rate, setRate] = useState(String(t.usd_to_currency ?? 1));
  const [hourly, setHourly] = useState(t.default_hourly_rate != null ? String(t.default_hourly_rate) : "");
  const [rows, setRows] = useState<PriceRow[]>(() => Object.entries(t.model_prices || {}).map(([m, p]) => toRow(m, p)));
  const [touched, setTouched] = useState(false);
  const a = useAction();

  const cur = currency.trim().toUpperCase();
  const errors: string[] = [];
  if (!/^[A-Z]{3}$/.test(cur)) errors.push("Currency must be a three-letter code such as GBP.");
  const rateN = Number(rate);
  if (cur !== "USD" && (!rate.trim() || !(rateN > 0))) errors.push(`Enter how many ${cur || "units"} one US dollar buys.`);
  if (hourly.trim() && !(Number(hourly) >= 0)) errors.push("The hourly rate must be a number, 0 or more.");
  const seen = new Set<string>();
  const rowErr: Record<number, string> = {};
  for (const r of rows) {
    const m = r.model.trim();
    const nums = [r.input, r.output, r.cache_read, r.cache_write];
    if (!m) rowErr[r.key] = "Enter a model ID.";
    else if (seen.has(m)) rowErr[r.key] = "This model is listed twice.";
    else if (!r.input.trim() || !r.output.trim()) rowErr[r.key] = "Input and output prices are required.";
    else if (nums.some((x) => x.trim() && !(Number(x) >= 0))) rowErr[r.key] = "Prices must be numbers, 0 or more.";
    seen.add(m);
  }
  if (Object.keys(rowErr).length) errors.push("Fix the highlighted price rows.");

  function setRow(key: number, patch: Partial<PriceRow>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (errors.length) return;
    const prices: Record<string, Price> = {};
    for (const r of rows) {
      prices[r.model.trim()] = {
        input: Number(r.input), output: Number(r.output),
        ...(r.cache_read.trim() ? { cache_read: Number(r.cache_read) } : {}),
        ...(r.cache_write.trim() ? { cache_write: Number(r.cache_write) } : {}),
      };
    }
    const res = await a.run("save", () => put({
      currency: cur, usd_to_currency: cur === "USD" ? 1 : rateN,
      default_hourly_rate: hourly.trim() ? Number(hourly) : null, model_prices: prices,
    }), "Cost settings saved. Cost reports use them from now on.");
    if (res) {
      onSaved(res);
      setRows(Object.entries(res.model_prices || {}).map(([m, p]) => toRow(m, p)));
      setTouched(false);
    }
  }

  const defaults = Object.entries(t.default_model_prices || {});
  const overridden = new Set(rows.map((r) => r.model.trim()));

  return (
    <Section id="costs" title="Costs" intro={
      <p>Used by the cost reports: model spend (tokens × price, priced in US dollars and converted) plus reviewer time
        (measured minutes × hourly rate).</p>
    }>
      <form onSubmit={save} noValidate>
        <div className="field-row">
          <div>
            <label htmlFor="cur">Currency</label>
            <input id="cur" list="currencies" value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
            <datalist id="currencies">{CURRENCIES.map((c) => <option key={c} value={c} />)}</datalist>
          </div>
          <div>
            <label htmlFor="rate">Exchange rate</label>
            <div className="input-affix">
              <span>1 USD =</span>
              <input id="rate" type="number" min={0} step="any" value={cur === "USD" ? "1" : rate} disabled={cur === "USD"} onChange={(e) => setRate(e.target.value)} />
              <span>{cur || "…"}</span>
            </div>
          </div>
          <div>
            <label htmlFor="hourly">Default reviewer hourly rate ({cur || "…"})</label>
            <input id="hourly" type="number" min={0} step="any" value={hourly} onChange={(e) => setHourly(e.target.value)} placeholder="Not set" />
          </div>
        </div>
        <div className="sub hint">The hourly rate applies to reviewers without their own or their team's rate. Leave it empty to leave reviewer time unpriced.</div>

        <h3 className="table-title">Model price overrides</h3>
        <p className="sub">US dollars per million tokens. An override replaces the default price for that model ID; a model ID also matches longer IDs that contain it (e.g. Bedrock or dated snapshot IDs). Leave cache prices empty if your provider doesn't charge them separately.</p>
        {rows.length === 0 ? <p className="sub">No overrides: the default prices below apply.</p> : (
          <div className="table-scroll">
            <table className="price-table">
              <thead><tr><th>Model ID</th><th>Input</th><th>Output</th><th>Cache read</th><th>Cache write</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className={touched && rowErr[r.key] ? "row-invalid" : undefined}>
                    <td>
                      <input value={r.model} list="known-models" onChange={(e) => setRow(r.key, { model: e.target.value })} aria-label="Model ID" className="mono" />
                      {touched && rowErr[r.key] && <div className="field-error">{rowErr[r.key]}</div>}
                    </td>
                    {(["input", "output", "cache_read", "cache_write"] as const).map((k) => (
                      <td key={k}><input type="number" min={0} step="any" value={r[k]} onChange={(e) => setRow(r.key, { [k]: e.target.value })}
                        aria-label={`${r.model || "model"} ${k.replace("_", " ")} price`} placeholder={k.startsWith("cache") ? "–" : ""} /></td>
                    ))}
                    <td><button type="button" className="btn small danger" onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))} aria-label={`Remove ${r.model || "row"}`}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <datalist id="known-models">{defaults.map(([m]) => <option key={m} value={m} />)}</datalist>
        <div className="btn-row">
          <button type="button" className="btn small" onClick={() => setRows((rs) => [...rs, toRow("", {})])}>Add a model</button>
        </div>

        <details className="advanced">
          <summary>Default prices (as of {t.prices_as_of})</summary>
          <div className="table-scroll">
            <table className="price-table">
              <thead><tr><th>Model ID</th><th>Input</th><th>Output</th><th>Cache read</th><th>Cache write</th><th /></tr></thead>
              <tbody>
                {defaults.map(([m, p]) => (
                  <tr key={m}>
                    <td className="mono">{m}</td>
                    <td>{fmtPrice(p.input)}</td><td>{fmtPrice(p.output)}</td><td>{fmtPrice(p.cache_read)}</td><td>{fmtPrice(p.cache_write)}</td>
                    <td>{overridden.has(m) ? <Badge tone="accent">overridden</Badge>
                      : <button type="button" className="btn small" onClick={() => setRows((rs) => [...rs, toRow(m, p)])}>Override</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        {touched && errors.length > 0 && <div className="error">{errors.join(" ")}</div>}
        <SaveRow a={a} />
      </form>
    </Section>
  );
}

// --- Email notifications ------------------------------------------------------------------------------------

const NOTES = [
  { key: "assigned", label: "Task assigned", hint: "A reviewer gets an email when a new item is assigned to them." },
  { key: "escalated", label: "Escalations", hint: "Oversight leads hear about escalations: missed deadlines, reviewer disagreements, capacity and release checks." },
  { key: "decided", label: "Decision made", hint: "The person who sent a submission from the web app hears when it has been decided." },
] as const;

function Notifications({ t, onSaved }: SectionProps) {
  const [n, setN] = useState(t.notifications);
  const a = useAction();
  const dirty = NOTES.some((x) => n[x.key] !== t.notifications[x.key]);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const r = await a.run("save", () => put({ notifications: n }), "Email notification settings saved.");
    if (r) { onSaved(r); setN(r.notifications); }
  }
  return (
    <Section id="notifications" title="Email notifications" intro={
      <p>Which emails the platform sends for this organisation. Each person can still turn email off for their own account.</p>
    }>
      <form onSubmit={save}>
        {NOTES.map((x) => (
          <label key={x.key} className="check">
            <input type="checkbox" checked={n[x.key]} onChange={(e) => setN({ ...n, [x.key]: e.target.checked })} />
            <span><strong>{x.label}</strong><br /><span className="sub">{x.hint}</span></span>
          </label>
        ))}
        <SaveRow a={a} disabled={!dirty} dirty={dirty} />
      </form>
    </Section>
  );
}

// --- Single sign-on -----------------------------------------------------------------------------------------

const SSO_ROLES = Object.keys(ROLE_INFO).filter((r) => r !== "admin");

function ssoForm(s: Sso | null) {
  return {
    enabled: s ? s.enabled !== false : true,
    issuer: s?.issuer ?? "", client_id: s?.client_id ?? "", client_secret: "",
    domains: (s?.domains ?? []).join(", "), roles: s?.auto_provision_roles ?? [], scopes: s?.scopes ?? "",
    authorization_endpoint: s?.authorization_endpoint ?? "", token_endpoint: s?.token_endpoint ?? "", jwks_uri: s?.jwks_uri ?? "",
    enforce: !!s?.enforce,
  };
}

function SingleSignOn({ t, onSaved }: SectionProps) {
  const [f, setF] = useState(() => ssoForm(t.sso));
  const [setup, setSetup] = useState(!!t.sso);
  const [touched, setTouched] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const a = useAction();
  // The server only accepts sign-ins that return to its configured app address (POR_APP_BASE_URL).
  const here = `${window.location.origin}/login/sso`;
  const allowed = t.sso_redirect_uris ?? [];
  const redirect = allowed.includes(here) ? here : (t.sso_redirect_uri || here);
  const mismatch = allowed.length > 0 && !allowed.includes(here);
  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }));
  const hasSecret = !!t.sso?.client_secret;

  const domains = f.domains.split(/[\s,;]+/).map((d) => d.trim().toLowerCase()).filter(Boolean);
  const errors: Record<string, string> = {};
  const urlOk = (u: string) => !u.trim() || /^https?:\/\/[^\s/]+/i.test(u.trim());
  if (!f.issuer.trim()) errors.issuer = "Required.";
  else if (!urlOk(f.issuer)) errors.issuer = "Enter the issuer URL, e.g. https://login.microsoftonline.com/<tenant>/v2.0";
  if (!f.client_id.trim()) errors.client_id = "Required.";
  for (const k of ["authorization_endpoint", "token_endpoint", "jwks_uri"] as const) if (!urlOk(f[k])) errors[k] = "Enter a full https:// URL.";
  const badDomains = domains.filter((d) => !/^([a-z0-9-]{1,63}\.)+[a-z]{2,63}$/.test(d));
  if (badDomains.length) errors.domains = `Not valid domain names: ${badDomains.join(", ")}`;
  const err = (k: string) => (touched && errors[k] ? <div className="field-error">{errors[k]}</div> : null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length) return;
    const sso = {
      enabled: f.enabled, issuer: f.issuer.trim(), client_id: f.client_id.trim(),
      client_secret: f.client_secret.trim() || "***", domains, auto_provision_roles: f.roles,
      scopes: f.scopes.trim() || null, authorization_endpoint: f.authorization_endpoint.trim() || null,
      token_endpoint: f.token_endpoint.trim() || null, jwks_uri: f.jwks_uri.trim() || null,
      enforce: f.enforce,
    };
    const r = await a.run("save", () => put({ sso }), f.enabled ? "Single sign-on saved and switched on." : "Single sign-on saved (switched off).");
    if (r) { onSaved(r); setF(ssoForm(r.sso)); setTouched(false); }
  }

  const status = !t.sso ? <Badge>not set up</Badge> : t.sso.enabled === false ? <Badge tone="medium">off</Badge> : <Badge tone="ok">on</Badge>;

  return (
    <Section id="sso" title="Single sign-on" intro={
      <p>Let people sign in with your identity provider (OpenID Connect: Microsoft Entra ID, Okta, Google Workspace and
        others). People whose email domain is listed below are sent to your provider from the sign-in page.</p>
    }>
      <div className="sso-status">Status: {status}</div>
      <CopyField label="Redirect URI to register with your provider" value={redirect}
        hint="Add this as a single-page application redirect URI in the app registration. It must match exactly." />
      {mismatch && (
        <div className="notice">
          You're using the app at {window.location.origin}, but the platform sends sign-ins back to {redirect}. Ask the
          platform operator to set POR_APP_BASE_URL (or POR_CORS_ORIGINS) to this address if people open the app here.
        </div>
      )}

      {!setup ? (
        <>
          <Ok>{a.ok}</Ok>
          <button className="btn primary" onClick={() => { a.clear(); setSetup(true); }}>Set up single sign-on</button>
        </>
      ) : (
        <form onSubmit={save} noValidate>
          <label className="check">
            <input type="checkbox" checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
            Single sign-on is on
          </label>
          {!f.enabled && <div className="sub hint">Off keeps the settings but sends nobody to the provider; people sign in with a password.</div>}
          <label className="check">
            <input type="checkbox" checked={f.enforce} onChange={(e) => set({ enforce: e.target.checked })} />
            Require this provider for everyone in the organisation
          </label>
          <div className="sub hint">
            When ticked, the platform's own sign-in provider can't sign in people from this organisation: their single
            sign-on goes only through the provider set up here.
          </div>
          <div className="field-row">
            <div>
              <label htmlFor="sso-issuer">Issuer URL *</label>
              <input id="sso-issuer" type="url" value={f.issuer} onChange={(e) => set({ issuer: e.target.value })} placeholder="https://login.microsoftonline.com/<tenant id>/v2.0" aria-invalid={!!(touched && errors.issuer)} />
              {err("issuer")}
            </div>
            <div>
              <label htmlFor="sso-client">Client ID *</label>
              <input id="sso-client" value={f.client_id} onChange={(e) => set({ client_id: e.target.value })} aria-invalid={!!(touched && errors.client_id)} />
              {err("client_id")}
            </div>
          </div>
          <label htmlFor="sso-secret">Client secret</label>
          <input id="sso-secret" type="password" autoComplete="new-password" value={f.client_secret} onChange={(e) => set({ client_secret: e.target.value })}
            placeholder={hasSecret ? "Stored (hidden). Type a new secret to replace it." : "Optional for public clients using PKCE"} />
          <div className="sub hint">Write-only: it is stored encrypted and never shown again.</div>

          <label htmlFor="sso-domains">Email domains</label>
          <input id="sso-domains" value={f.domains} onChange={(e) => set({ domains: e.target.value })} placeholder="example.com, example.co.uk" aria-invalid={!!(touched && errors.domains)} />
          <div className="sub hint">Separate with commas. A domain can belong to only one organisation on this platform.</div>
          {err("domains")}

          <fieldset className="field">
            <legend>Roles for new people on their first sign-in</legend>
            <div className="sub hint">If someone with a listed domain signs in without an account, one is created with these roles. Leave all unticked to let only people who already have an account sign in. Admin can't be given this way.</div>
            <div className="role-checks">
              {SSO_ROLES.map((r) => (
                <label key={r} className="check">
                  <input type="checkbox" checked={f.roles.includes(r)} onChange={(e) => set({ roles: e.target.checked ? [...f.roles, r] : f.roles.filter((x) => x !== r) })} />
                  <span><strong>{ROLE_INFO[r].name}</strong><br /><span className="sub">{ROLE_INFO[r].blurb}</span></span>
                </label>
              ))}
            </div>
          </fieldset>

          <details className="advanced" open={!!(f.scopes || f.authorization_endpoint || f.token_endpoint || f.jwks_uri) || (touched && !!(errors.authorization_endpoint || errors.token_endpoint || errors.jwks_uri))}>
            <summary>Scopes and endpoint overrides</summary>
            <label htmlFor="sso-scopes">Scopes</label>
            <input id="sso-scopes" value={f.scopes} onChange={(e) => set({ scopes: e.target.value })} placeholder="openid email profile" />
            <div className="sub hint">Only the endpoints your provider doesn't publish in its discovery document (<code>{"<issuer>"}/.well-known/openid-configuration</code>) are needed.</div>
            {(["authorization_endpoint", "token_endpoint", "jwks_uri"] as const).map((k) => (
              <div key={k}>
                <label htmlFor={`sso-${k}`}>{k === "jwks_uri" ? "JWKS URI" : k === "token_endpoint" ? "Token endpoint" : "Authorization endpoint"}</label>
                <input id={`sso-${k}`} type="url" value={f[k]} onChange={(e) => set({ [k]: e.target.value })} aria-invalid={!!(touched && errors[k])} />
                {err(k)}
              </div>
            ))}
          </details>

          {touched && Object.keys(errors).length > 0 && <div className="error">Fix the highlighted settings first.</div>}
          <Err error={a.error} />
          <Ok>{a.ok}</Ok>
          <div className="btn-row section-actions">
            <button type="submit" className="btn primary" disabled={!!a.busy}>{a.busy ? "Saving…" : "Save single sign-on"}</button>
            {t.sso
              ? <button type="button" className="btn danger" onClick={() => setConfirmRemove(true)}>Remove single sign-on</button>
              : <button type="button" className="btn" onClick={() => { setSetup(false); setF(ssoForm(null)); setTouched(false); a.clear(); }}>Cancel</button>}
          </div>
        </form>
      )}
      {confirmRemove && (
        <Confirm title="Remove single sign-on" confirmLabel="Remove single sign-on" danger onClose={() => setConfirmRemove(false)}
          onConfirm={async () => {
            const r = await put({ sso: null });
            onSaved(r);
            setF(ssoForm(null));
            setSetup(false);
            a.clear();
            a.setOk("Single sign-on removed.");
          }}>
          <p>Delete the provider settings, including the stored client secret? People who only sign in with SSO won't be able to
            sign in until they set a password (from a reset link) or SSO is set up again.</p>
          <p className="sub">To pause it instead, untick "Single sign-on is on" and save.</p>
        </Confirm>
      )}
    </Section>
  );
}
