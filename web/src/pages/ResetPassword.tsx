import { useState } from "react";
import { api } from "../api";
import { ErrorBox } from "../components/ui";

// Opened from the reset link in the email: /reset-password?token=...
export default function ResetPassword() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const mismatch = !!confirm && next !== confirm;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/reset-password", { auth: false, body: { token, new_password: next } });
      setDone(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <main className="card">
        <div className="brand" style={{ padding: 0, marginBottom: 18 }}>
          <div className="brand-mark" aria-hidden="true">✓</div>
          Proof of Review
        </div>
        <h1>Choose a new password</h1>
        {!token ? (
          <div className="error">This link is incomplete. Open the link from the email again, or ask for a new one.</div>
        ) : done ? (
          <>
            <div className="success" role="status">Your password has been changed. Every other session has been signed out.</div>
            <a className="btn primary" href="/login" style={{ marginTop: 12 }}>Sign in</a>
          </>
        ) : (
          <form onSubmit={(e) => (e.preventDefault(), save())}>
            <label htmlFor="new">New password (at least 12 characters)</label>
            <input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} autoFocus />
            <label htmlFor="confirm">Confirm new password</label>
            <input id="confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            {mismatch && <div className="notice">The passwords don't match.</div>}
            <ErrorBox error={error} />
            <div style={{ marginTop: 14 }}>
              <button className="btn primary" disabled={busy || !next || next !== confirm}>Change password</button>
            </div>
          </form>
        )}
      </main>
    </div>
  );
}
