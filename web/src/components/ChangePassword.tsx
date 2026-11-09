import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "./ui";

// Set or change your own password. Other sessions are signed out; this one continues with the new token.
export default function ChangePassword({ onClose }: { onClose: () => void }) {
  const { me, replaceToken } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const hasPassword = !!me?.password_set;
  const mismatch = !!confirm && next !== confirm;

  async function save() {
    setErr(null);
    setBusy(true);
    try {
      const r = await api<{ access_token: string }>("/v1/me/password", {
        body: { current_password: hasPassword ? current : null, new_password: next },
      });
      await replaceToken(r.access_token);
      setDone(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>{hasPassword ? "Change password" : "Set a password"}</h2>
        {done ? (
          <>
            <div className="success">Password saved. You stay signed in here; other sessions have been signed out.</div>
            <div className="btn-row" style={{ justifyContent: "flex-end" }}>
              <button className="btn primary" onClick={onClose}>Done</button>
            </div>
          </>
        ) : (
          <form onSubmit={(e) => (e.preventDefault(), save())}>
            <input type="email" autoComplete="username" value={me?.email || ""} readOnly hidden />
            {hasPassword && (
              <>
                <label htmlFor="cur">Current password</label>
                <input id="cur" type="password" autoComplete="current-password" value={current}
                  onChange={(e) => setCurrent(e.target.value)} autoFocus />
              </>
            )}
            {!hasPassword && <p className="sub">You sign in with single sign-on. A password lets you sign in without it too.</p>}
            <label htmlFor="new">New password (at least 12 characters)</label>
            <input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)}
              autoFocus={!hasPassword} />
            <label htmlFor="confirm">Confirm new password</label>
            <input id="confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            {mismatch && <div className="notice">The passwords don't match.</div>}
            <ErrorBox error={err} />
            <div className="btn-row" style={{ marginTop: 18, justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={onClose}>Cancel</button>
              <button className="btn primary" disabled={busy || !next || next !== confirm || (hasPassword && !current)}>Save</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
