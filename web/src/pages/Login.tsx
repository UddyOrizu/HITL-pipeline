import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/ui";

const DEMO = [
  { email: "admin@demo.test", role: "Admin" },
  { email: "ada@demo.test", role: "Reviewer" },
  { email: "ben@demo.test", role: "Reviewer" },
  { email: "cara@demo.test", role: "Reviewer" },
  { email: "sam@demo.test", role: "Submitter" },
  { email: "olu@demo.test", role: "Oversight" },
  { email: "aud@demo.test", role: "Auditor" },
  { email: "ian@demo.test", role: "Integrations" },
];

export default function Login() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const { signIn } = useAuth();
  const nav = useNavigate();

  async function go(addr: string) {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ access_token: string }>("/v1/auth/dev-login", { body: { email: addr } });
      await signIn(r.access_token);
      nav("/");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="card">
        <div className="brand" style={{ padding: 0, marginBottom: 18 }}>
          <div className="brand-mark">✓</div>
          Proof of Review
        </div>
        <h1>Sign in</h1>
        <p className="sub">Your organisation signs you in with single sign-on. In local development, use an email address.</p>
        <form onSubmit={(e) => (e.preventDefault(), go(email))}>
          <label htmlFor="email">Work email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@firm.com" autoFocus />
          <div style={{ marginTop: 14 }}>
            <button className="btn primary" disabled={busy || !email}>Continue</button>
          </div>
        </form>
        <ErrorBox error={error} />
        <label style={{ marginTop: 20 }}>Demo accounts (after running the seed script)</label>
        <div className="pill-list">
          {DEMO.map((d) => (
            <button key={d.email} onClick={() => go(d.email)} disabled={busy}>
              <strong>{d.role}</strong> · {d.email}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
