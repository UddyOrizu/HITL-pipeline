import { useState } from "react";
import { api } from "../../api";
import { Badge, Empty, Modal } from "../ui";
import { Confirm, CopyButton, Err, useAction } from "../admin/common";
import { SecretModal, type SecretItem } from "./SecretModal";
import { fullTime, type AppSecrets, type Loaded, type SourceApp } from "./types";

const GRACE = [
  { hours: 0, label: "None", hint: "The old key stops working immediately. Use this if the key may have leaked." },
  { hours: 24, label: "24 hours", hint: "Time to redeploy the app with the new key." },
  { hours: 72, label: "72 hours", hint: "For apps with a slower release cycle." },
  { hours: 168, label: "7 days", hint: "The longest allowed overlap." },
];

type Secrets = { title: string; items: SecretItem[]; note?: string };

type Dialog =
  | { kind: "edit"; app: SourceApp }
  | { kind: "rotate"; app: SourceApp }
  | { kind: "confirm"; title: string; body: React.ReactNode; label: string; danger?: boolean; action: () => Promise<unknown> }
  | null;

export function SourceApps({ apps }: { apps: Loaded<SourceApp[]> }) {
  const [dialog, setDialog] = useState<Dialog>(null);
  const [secrets, setSecrets] = useState<Secrets | null>(null);
  const list = useAction();

  const path = (a: SourceApp, rest = "") => `/v1/integrations/apps/${a.id}${rest}`;

  function rotateWebhookSecret(a: SourceApp) {
    setDialog({
      kind: "confirm", title: `Rotate webhook secret for ${a.name}`, label: "Rotate secret", danger: true,
      body: <p>Webhooks are signed with the new secret straight away. Update the receiving app at the same time, or it
        will reject deliveries until it has the new secret (failed deliveries are retried and can be redelivered).</p>,
      action: async () => {
        const r = await api<AppSecrets>(path(a, "/rotate-webhook-secret"), { method: "POST" });
        apps.reload();
        setSecrets({ title: "New webhook secret", items: [{ label: "Webhook signing secret", value: r.webhook_secret }] });
      },
    });
  }

  function enableOAuth(a: SourceApp) {
    setDialog({
      kind: "confirm", title: `Enable OAuth for ${a.name}`, label: "Enable OAuth",
      body: <p>The app will be able to get short-lived access tokens with the OAuth client-credentials grant
        (<code>POST /v1/oauth/token</code>) as well as using its API key. A client secret is created and shown once.</p>,
      action: async () => {
        const r = await api<AppSecrets>(path(a, "/oauth"), { method: "PUT", body: { enabled: true } });
        apps.reload();
        setSecrets({
          title: "OAuth client credentials",
          items: [
            { label: "Client ID", value: r.oauth_client_id, secret: false },
            { label: "Client secret", value: r.oauth_client_secret },
          ],
        });
      },
    });
  }

  function disableOAuth(a: SourceApp) {
    setDialog({
      kind: "confirm", title: `Disable OAuth for ${a.name}`, label: "Disable OAuth", danger: true,
      body: <p>New tokens are refused at once and tokens already issued expire within the hour. The API key keeps
        working. Enabling OAuth again creates a new client ID and secret.</p>,
      action: async () => {
        await api(path(a, "/oauth"), { method: "PUT", body: { enabled: false } });
        apps.reload();
      },
    });
  }

  function rotateOAuthSecret(a: SourceApp) {
    setDialog({
      kind: "confirm", title: `Rotate OAuth secret for ${a.name}`, label: "Rotate secret", danger: true,
      body: <p>The old client secret stops working immediately for new tokens. The client ID stays the same.</p>,
      action: async () => {
        const r = await api<AppSecrets>(path(a, "/rotate-oauth-secret"), { method: "POST" });
        apps.reload();
        setSecrets({
          title: "New OAuth client secret",
          items: [
            { label: "Client ID", value: r.oauth_client_id, secret: false },
            { label: "Client secret", value: r.oauth_client_secret },
          ],
        });
      },
    });
  }

  function revokePrevious(a: SourceApp) {
    setDialog({
      kind: "confirm", title: "Revoke the old API key now", label: "Revoke old key", danger: true,
      body: <p>The previous key for <strong>{a.name}</strong> stops working immediately instead of at{" "}
        {fullTime(a.previous_key_valid_until)}. Do this once the app runs with the new key, or if the old key leaked.</p>,
      action: async () => {
        await api(path(a, "/revoke-previous-key"), { method: "POST" });
        apps.reload();
      },
    });
  }

  function setActive(a: SourceApp, active: boolean) {
    if (active) {
      list.run(`active:${a.id}`, async () => {
        await api(path(a), { method: "PATCH", body: { active: true } });
        apps.reload();
      }, `${a.name} is active again.`);
      return;
    }
    setDialog({
      kind: "confirm", title: `Deactivate ${a.name}`, label: "Deactivate", danger: true,
      body: <p>Requests with this app's API key or OAuth tokens are refused and its connectors stop submitting.
        Nothing is deleted, and you can activate it again later.</p>,
      action: async () => {
        await api(path(a), { method: "PATCH", body: { active: false } });
        apps.reload();
      },
    });
  }

  return (
    <>
      <CreateApp onCreated={(r) => {
        apps.reload();
        setSecrets({
          title: `${r.name} registered`,
          note: "Use the API key in the X-API-Key header. Webhooks to your URL are signed with the webhook secret.",
          items: [
            { label: "App ID", value: r.id, secret: false },
            { label: "API key", value: r.api_key },
            { label: "Webhook signing secret", value: r.webhook_secret },
            { label: "OAuth client ID", value: r.oauth_client_id, secret: false },
            { label: "OAuth client secret", value: r.oauth_client_secret },
          ],
        });
      }} />

      <div className="section-head">
        <h2>Source applications</h2>
        <button className="btn small" onClick={apps.reload} disabled={apps.loading}>Refresh</button>
      </div>
      <Err error={apps.error} />
      <Err error={list.error} />
      {list.ok && <div className="success">{list.ok}</div>}
      {apps.loading && !apps.data ? <div className="sub">Loading…</div>
        : !apps.error && !apps.data?.length ? <div className="card"><Empty>No source applications yet. Register one above to get an API key.</Empty></div>
        : (
          <div className="app-list">
            {apps.data?.map((a) => (
              <article key={a.id} className={`card app-card${a.active ? "" : " inactive"}`}>
                <header className="app-card-head">
                  <div>
                    <h3>{a.name}</h3>
                    <div className="mono sub">{a.id} <CopyButton text={a.id} label="Copy ID" /></div>
                  </div>
                  <div className="btn-row">
                    <Badge tone={a.active ? "ok" : "high"}>{a.active ? "active" : "deactivated"}</Badge>
                    {a.oauth_enabled && <Badge tone="accent">OAuth</Badge>}
                  </div>
                </header>
                <dl className="kv">
                  <dt>API key</dt>
                  <dd><span className="mono">{a.api_key_prefix}…</span></dd>
                  {a.previous_key_valid_until && (
                    <>
                      <dt>Old key</dt>
                      <dd>
                        <span className="notice inline-notice">Still valid until {fullTime(a.previous_key_valid_until)}</span>{" "}
                        <button className="btn small danger" onClick={() => revokePrevious(a)}>Revoke old key now</button>
                      </dd>
                    </>
                  )}
                  <dt>Webhook URL</dt>
                  <dd className="break">{a.webhook_url || <span className="sub">none (the app polls for results)</span>}</dd>
                  <dt>OAuth client</dt>
                  <dd>{a.oauth_client_id ? <span className="mono">{a.oauth_client_id}</span> : <span className="sub">not enabled</span>}</dd>
                  <dt>Created</dt>
                  <dd>{fullTime(a.created_at)}</dd>
                </dl>
                <div className="btn-row app-actions">
                  <button className="btn small" onClick={() => setDialog({ kind: "edit", app: a })}>Edit</button>
                  <button className="btn small" onClick={() => setDialog({ kind: "rotate", app: a })}>Rotate API key</button>
                  <button className="btn small" onClick={() => rotateWebhookSecret(a)}>Rotate webhook secret</button>
                  {a.oauth_enabled ? (
                    <>
                      <button className="btn small" onClick={() => rotateOAuthSecret(a)}>Rotate OAuth secret</button>
                      <button className="btn small" onClick={() => disableOAuth(a)}>Disable OAuth</button>
                    </>
                  ) : <button className="btn small" onClick={() => enableOAuth(a)}>Enable OAuth</button>}
                  <span className="spacer" />
                  {a.active
                    ? <button className="btn small danger" onClick={() => setActive(a, false)}>Deactivate</button>
                    : <button className="btn small primary" disabled={list.busy === `active:${a.id}`} onClick={() => setActive(a, true)}>Activate</button>}
                </div>
              </article>
            ))}
          </div>
        )}

      {dialog?.kind === "edit" && <EditApp app={dialog.app} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); apps.reload(); }} />}
      {dialog?.kind === "rotate" && (
        <RotateKey app={dialog.app} onClose={() => setDialog(null)} onRotated={(r, grace) => {
          setDialog(null);
          apps.reload();
          setSecrets({
            title: `New API key for ${r.name}`,
            note: grace && r.previous_key_valid_until
              ? `The old key keeps working until ${fullTime(r.previous_key_valid_until)}. Revoke it sooner from the app card once you have redeployed.`
              : "The old key has stopped working.",
            items: [{ label: "API key", value: r.api_key }],
          });
        }} />
      )}
      {dialog?.kind === "confirm" && (
        <Confirm title={dialog.title} confirmLabel={dialog.label} danger={dialog.danger} onConfirm={dialog.action} onClose={() => setDialog(null)}>
          {dialog.body}
        </Confirm>
      )}
      {secrets && <SecretModal title={secrets.title} items={secrets.items} note={secrets.note} onClose={() => setSecrets(null)} />}
    </>
  );
}

function CreateApp({ onCreated }: { onCreated: (r: AppSecrets) => void }) {
  const [name, setName] = useState("");
  const [hook, setHook] = useState("");
  const [oauth, setOauth] = useState(false);
  const a = useAction();

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const r = await a.run("create", () => api<AppSecrets>("/v1/integrations/apps", {
      body: { name: name.trim(), webhook_url: hook.trim() || null, enable_oauth: oauth },
    }));
    if (r) {
      setName(""); setHook(""); setOauth(false);
      onCreated(r);
    }
  }

  return (
    <div className="grid grid-2 block-gap">
      <form className="card" onSubmit={create}>
        <h2>Register a source application</h2>
        <label htmlFor="app-name">Name</label>
        <input id="app-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Drafting assistant" maxLength={200} required />
        <label htmlFor="app-hook">Webhook URL (optional)</label>
        <input id="app-hook" type="url" value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://app.example.com/hooks/por" />
        <div className="sub hint">Must be https on a public host. Decisions and status changes are posted here.</div>
        <label className="check">
          <input type="checkbox" checked={oauth} onChange={(e) => setOauth(e.target.checked)} /> Also enable OAuth client credentials
        </label>
        <Err error={a.error} />
        <button className="btn primary" type="submit" disabled={!name.trim() || !!a.busy}>{a.busy ? "Creating…" : "Create"}</button>
      </form>
      <div className="card">
        <h2>Using the API</h2>
        <pre className="json">{`curl -X POST $POR/v1/submissions \\
  -H "X-API-Key: por_..." \\
  -F workflow_id=<workflow id> \\
  -F file=@letter.docx -F risk_tier=medium`}</pre>
        <p className="sub small-text">
          Webhooks are signed with HMAC-SHA256: header <code>X-PoR-Signature: t=…,v1=…</code> over <code>{"{t}.{body}"}</code>.
          Agents can use the MCP server (<code>python -m mcp_server.server</code>).
        </p>
      </div>
    </div>
  );
}

function EditApp({ app, onClose, onSaved }: { app: SourceApp; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(app.name);
  const [hook, setHook] = useState(app.webhook_url || "");
  const a = useAction();

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = {};
    if (name.trim() !== app.name) body.name = name.trim();
    if ((hook.trim() || null) !== (app.webhook_url || null)) body.webhook_url = hook.trim() || null;
    if (!Object.keys(body).length) return onClose();
    const r = await a.run("save", () => api(`/v1/integrations/apps/${app.id}`, { method: "PATCH", body }));
    if (r) onSaved();
  }

  return (
    <Modal title={`Edit ${app.name}`} onClose={onClose}>
      <form onSubmit={save}>
        <label htmlFor="edit-name">Name</label>
        <input id="edit-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required autoFocus />
        <label htmlFor="edit-hook">Webhook URL</label>
        <input id="edit-hook" type="url" value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://app.example.com/hooks/por" />
        <div className="sub hint">Leave empty to stop sending webhooks. The signing secret does not change.</div>
        <Err error={a.error} />
        <div className="btn-row modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!name.trim() || !!a.busy}>{a.busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

function RotateKey({ app, onClose, onRotated }: { app: SourceApp; onClose: () => void; onRotated: (r: AppSecrets, grace: number) => void }) {
  const [grace, setGrace] = useState(24);
  const a = useAction();

  async function rotate() {
    const r = await a.run("rotate", () => api<AppSecrets>(`/v1/integrations/apps/${app.id}/rotate-key`, { body: { grace_hours: grace } }));
    if (r) onRotated(r, grace);
  }

  return (
    <Modal title={`Rotate API key for ${app.name}`} onClose={onClose}>
      <p>A new key is created and shown once. How long should the current key (<span className="mono">{app.api_key_prefix}…</span>) keep working?</p>
      {app.previous_key_valid_until && (
        <div className="notice">An older key is still in its grace period (until {fullTime(app.previous_key_valid_until)}). Rotating again ends it now: only the current key gets a grace period.</div>
      )}
      <fieldset className="choice-list">
        <legend className="sr-only">Grace period</legend>
        {GRACE.map((g) => (
          <label key={g.hours} className={`choice${grace === g.hours ? " on" : ""}`}>
            <input type="radio" name="grace" checked={grace === g.hours} onChange={() => setGrace(g.hours)} />
            <span><strong>{g.label}</strong><span className="sub">{g.hint}</span></span>
          </label>
        ))}
      </fieldset>
      <Err error={a.error} />
      <div className="btn-row modal-actions">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className={`btn ${grace === 0 ? "danger" : "primary"}`} disabled={!!a.busy} onClick={rotate}>
          {a.busy ? "Rotating…" : grace === 0 ? "Rotate and revoke old key" : "Rotate key"}
        </button>
      </div>
    </Modal>
  );
}
