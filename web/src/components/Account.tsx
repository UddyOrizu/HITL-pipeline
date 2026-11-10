import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth, type Me } from "../auth";
import ChangePassword from "./ChangePassword";
import { CopyField } from "./admin/common";
import { ErrorBox, Modal, RoleBadge } from "./ui";
import "../pages/admin.css";

type Minted = { access_token: string; token_type: string; expires_in: number; channel: string };

// A token for reviewing through the MCP server (reviewer mode), bound to the "mcp" channel. Shown once.
function McpToken() {
  const [token, setToken] = useState<{ value: string; expires: Date } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<Minted>("/v1/me/tokens", { body: { channel: "mcp" } });
      setToken({ value: r.access_token, expires: new Date(Date.now() + r.expires_in * 1000) });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="mcp-token-title" className="admin-page">
      <h3 id="mcp-token-title" style={{ margin: "0 0 4px" }}>Reviewer access token (MCP)</h3>
      <p className="sub" style={{ fontSize: 13, margin: "0 0 8px" }}>
        For reviewing from an AI assistant through the Proof of Review MCP server: set it as <code>POR_MCP_REVIEWER_TOKEN</code>.
        Decisions made with it are recorded as coming from MCP. It is shown only once; "Sign out everywhere" revokes it.
      </p>
      {token ? (
        <>
          <CopyField label="Your token" value={token.value}
            hint={<>Expires {token.expires.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}. Copy it now: it won't be shown again.</>} />
          <button type="button" className="btn small" style={{ marginTop: 8 }} onClick={() => setToken(null)}>Done</button>
        </>
      ) : (
        <button type="button" className="btn small" disabled={busy} onClick={create}>{busy ? "Creating…" : "Create token"}</button>
      )}
      <ErrorBox error={err} />
    </section>
  );
}

// Your account: name, email notifications, password, and signing out (this session or everywhere).
export default function Account({ onClose }: { onClose: () => void }) {
  const { me, setMe, signOut } = useAuth();
  const nav = useNavigate();
  const [name, setName] = useState(me?.name || "");
  const [notify, setNotify] = useState(!!me?.email_notifications);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState(false);
  if (!me) return null;
  if (password) return <ChangePassword onClose={() => setPassword(false)} />;

  async function save() {
    setBusy(true);
    setErr(null);
    setSaved(false);
    try {
      setMe(await api<Me>("/v1/me", { method: "PATCH", body: { name, email_notifications: notify } }));
      setSaved(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function out() {
    await signOut();
    nav("/login", { replace: true });
  }

  async function everywhere() {
    if (!confirm("Sign out of every session on every device, including this one?")) return;
    try {
      await api("/v1/auth/logout-all", { method: "POST" });
    } catch (e) {
      setErr(e);
      return;
    }
    await signOut();
    nav("/login", { replace: true });
  }

  return (
    <Modal title="Your account" onClose={onClose} width={460}>
      <dl className="kv" style={{ marginBottom: 12 }}>
        <dt>Email</dt><dd>{me.email}</dd>
        <dt>Organisation</dt><dd>{me.tenant?.name}</dd>
        <dt>Access</dt><dd className="btn-row" style={{ gap: 4 }}>{me.roles.map((r) => <RoleBadge key={r} role={r} />)}</dd>
        <dt>Sign-in</dt><dd>{[me.sso_linked && "single sign-on", me.password_set && "password"].filter(Boolean).join(" and ") || "not set up"}</dd>
      </dl>
      <form onSubmit={(e) => (e.preventDefault(), save())}>
        <label htmlFor="acct-name">Name</label>
        <input id="acct-name" value={name} onChange={(e) => setName(e.target.value)} />
        <label className="check" style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 400, color: "var(--text)" }}>
          <input type="checkbox" style={{ width: "auto" }} checked={notify} onChange={(e) => setNotify(e.target.checked)} />
          Email me about my work (new tasks, escalations, decisions), when my organisation has these emails switched on
        </label>
        <ErrorBox error={err} />
        {saved && <div className="success" role="status">Saved.</div>}
        <div className="btn-row" style={{ marginTop: 12 }}>
          <button className="btn primary" disabled={busy}>Save</button>
          <button type="button" className="btn" onClick={() => setPassword(true)}>
            {me.password_set ? "Change password" : "Set a password"}
          </button>
        </div>
      </form>
      {me.roles.includes("reviewer") && (
        <>
          <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "18px 0" }} />
          <McpToken />
        </>
      )}
      <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "18px 0" }} />
      <div className="btn-row" style={{ justifyContent: "space-between" }}>
        <button className="btn" onClick={out}>Sign out</button>
        <button className="btn danger" onClick={everywhere}>Sign out everywhere</button>
      </div>
    </Modal>
  );
}
