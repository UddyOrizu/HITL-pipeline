import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, ErrorBox, label, useLoad, when } from "../components/ui";

type App = { id: string; name: string; api_key_prefix: string; webhook_url: string | null; oauth_client_id: string | null; active: boolean; created_at: string };
type Delivery = { id: string; source_app_id: string; submission_id: string | null; event: string; status: string; attempts: number; last_status_code: number | null; last_error: string | null; created_at: string; next_attempt_at: string | null };

export default function Integrations() {
  const apps = useLoad(() => api<App[]>("/v1/integrations/apps"));
  const deliveries = useLoad(() => api<Delivery[]>("/v1/integrations/deliveries"));
  const connectors = useLoad(() => api<any[]>("/v1/integrations/connectors"));
  const [name, setName] = useState("");
  const [hook, setHook] = useState("");
  const [oauth, setOauth] = useState(false);
  const [secret, setSecret] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);

  async function create() {
    setErr(null);
    try {
      setSecret(await api("/v1/integrations/apps", { body: { name, webhook_url: hook || null, enable_oauth: oauth } }));
      setName(""); setHook(""); apps.reload();
    } catch (e) { setErr(e); }
  }
  async function rotate(id: string) {
    setSecret(await api(`/v1/integrations/apps/${id}/rotate-key`, { method: "POST" }));
    apps.reload();
  }
  async function toggle(a: App) {
    await api(`/v1/integrations/apps/${a.id}`, { method: "PATCH", body: { active: !a.active } });
    apps.reload();
  }
  async function redeliver(id: string) {
    await api(`/v1/integrations/deliveries/${id}/redeliver`, { method: "POST" });
    deliveries.reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Integrations console</h1>
          <div className="sub">Source applications, API keys, webhooks and connectors.</div>
        </div>
      </div>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <h2>Register a source application</h2>
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Drafting assistant" />
          <label>Webhook URL (optional)</label>
          <input value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://app.example.com/hooks/por" />
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={oauth} onChange={(e) => setOauth(e.target.checked)} style={{ width: "auto" }} /> Enable OAuth client credentials
          </label>
          <ErrorBox error={err} />
          <button className="btn primary" onClick={create} disabled={!name} style={{ marginTop: 10 }}>Create</button>
        </div>
        <div className="card">
          <h2>Using the API</h2>
          <pre className="json">{`curl -X POST $POR/v1/submissions \\
  -H "X-API-Key: por_..." \\
  -F workflow_id=<workflow id> \\
  -F file=@letter.docx -F risk_tier=medium`}</pre>
          <p className="sub" style={{ fontSize: 12 }}>
            Webhooks are signed with HMAC-SHA256: header <code>X-PoR-Signature: t=…,v1=…</code> over <code>{"{t}.{body}"}</code>.
            Agents can use the MCP server (<code>python -m mcp_server.server</code>).
          </p>
        </div>
      </div>

      {secret && (
        <div className="success" style={{ marginBottom: 16 }}>
          <strong>Copy these now. They will not be shown again.</strong>
          <pre className="json" style={{ marginTop: 8 }}>{JSON.stringify(
            Object.fromEntries(Object.entries(secret).filter(([k, v]) => ["api_key", "webhook_secret", "oauth_client_id", "oauth_client_secret"].includes(k) && v)), null, 2)}</pre>
          <button className="btn small" onClick={() => setSecret(null)}>Done</button>
        </div>
      )}

      <div className="card" style={{ padding: 0, marginBottom: 16 }}>
        <div style={{ padding: "16px 18px 0" }}><h2>Source applications</h2></div>
        {!apps.data?.length ? <Empty>No source applications.</Empty> : (
          <table>
            <thead><tr><th>Name</th><th>Key</th><th>Webhook</th><th>Status</th><th /></tr></thead>
            <tbody>
              {apps.data.map((a) => (
                <tr key={a.id}>
                  <td><div style={{ fontWeight: 600 }}>{a.name}</div><div className="mono sub">{a.id.slice(0, 8)}</div></td>
                  <td className="mono">{a.api_key_prefix}…</td>
                  <td style={{ wordBreak: "break-all" }}>{a.webhook_url || <span className="sub">none</span>}</td>
                  <td><Badge tone={a.active ? "ok" : "high"}>{a.active ? "active" : "disabled"}</Badge></td>
                  <td><div className="btn-row">
                    <button className="btn small" onClick={() => rotate(a.id)}>Rotate key</button>
                    <button className="btn small" onClick={() => toggle(a)}>{a.active ? "Disable" : "Enable"}</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card" style={{ padding: 0, marginBottom: 16 }}>
        <div style={{ padding: "16px 18px 0", display: "flex", justifyContent: "space-between" }}>
          <h2>Webhook delivery log</h2>
          <button className="btn small" onClick={deliveries.reload}>Refresh</button>
        </div>
        {!deliveries.data?.length ? <Empty>No deliveries yet.</Empty> : (
          <table>
            <thead><tr><th>Event</th><th>Submission</th><th>Status</th><th>Attempts</th><th>Last response</th><th>Created</th><th /></tr></thead>
            <tbody>
              {deliveries.data.map((d) => (
                <tr key={d.id}>
                  <td className="mono">{d.event}</td>
                  <td className="mono">{d.submission_id?.slice(0, 8)}</td>
                  <td><Badge tone={d.status === "delivered" ? "ok" : d.status === "failed" ? "high" : "medium"}>{d.status}</Badge>
                    {d.next_attempt_at && <div className="sub" style={{ fontSize: 11 }}>retry {when(d.next_attempt_at)}</div>}</td>
                  <td>{d.attempts}</td>
                  <td>{d.last_status_code ?? (d.last_error ? "error" : "–")}{d.last_error && <div className="sub" style={{ fontSize: 11 }}>{d.last_error.slice(0, 80)}</div>}</td>
                  <td>{when(d.created_at)}</td>
                  <td>{d.status !== "delivered" && <button className="btn small" onClick={() => redeliver(d.id)}>Redeliver</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Connectors</h2>
        <p className="sub">SharePoint / OneDrive monitored folders, Microsoft Teams notifications, and Jira or Azure DevOps ticket updates. Configure with the API (<code>POST /v1/integrations/connectors</code>).</p>
        {!connectors.data?.length ? <p className="sub">None configured.</p> : connectors.data.map((c) => (
          <div key={c.id} className="comment"><strong>{label(c.kind)}</strong> · app {c.source_app_id.slice(0, 8)} · {c.active ? "active" : "inactive"}</div>
        ))}
      </div>
    </>
  );
}
