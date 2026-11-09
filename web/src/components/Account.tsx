import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth, type Me } from "../auth";
import ChangePassword from "./ChangePassword";
import { ErrorBox, Modal, RoleBadge } from "./ui";

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
      <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "18px 0" }} />
      <div className="btn-row" style={{ justifyContent: "space-between" }}>
        <button className="btn" onClick={out}>Sign out</button>
        <button className="btn danger" onClick={everywhere}>Sign out everywhere</button>
      </div>
    </Modal>
  );
}
