import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, takeReturn, takeSessionExpired } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/ui";
import { startSso } from "./SsoCallback";

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

type Methods = { password: boolean; password_reset: boolean; dev_login: boolean; sso: boolean; sso_needs_email: boolean };
type Choice = { path: string; body: Record<string, unknown>; tenants: { id: string; name: string }[] };

export default function Login() {
  const [methods, setMethods] = useState<Methods | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [devEmail, setDevEmail] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [expired] = useState(takeSessionExpired);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [forgot, setForgot] = useState(false);
  const [sent, setSent] = useState(false);
  const { me, signIn } = useAuth();
  const nav = useNavigate();

  useEffect(() => {
    api<Methods>("/v1/auth/methods", { auth: false })
      .then(setMethods)
      .catch(() => setMethods({ password: true, password_reset: false, dev_login: false, sso: false, sso_needs_email: false }));
  }, []);

  useEffect(() => {
    if (me) nav(takeReturn() || "/", { replace: true });
  }, [me]); // eslint-disable-line react-hooks/exhaustive-deps

  async function go(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ access_token: string }>(path, { body, auth: false });
      setChoice(null);
      await signIn(r.access_token);
    } catch (e) {
      // The same email in several organisations: ask which one.
      if (e instanceof ApiError && e.status === 409 && e.detail?.code === "choose_tenant") {
        setChoice({ path, body, tenants: e.detail.tenants });
      } else {
        setError(e);
      }
    } finally {
      setBusy(false);
    }
  }

  async function sso() {
    setBusy(true);
    setError(null);
    try {
      await startSso(email || null);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  async function sendReset() {
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/forgot-password", { body: { email }, auth: false });
      setSent(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const ssoNeedsEmail = !!methods?.sso && methods.sso_needs_email && !email.includes("@");

  return (
    <div className="login">
      <main className="card" aria-labelledby="signin-title">
        <div className="brand" style={{ padding: 0, marginBottom: 18 }}>
          <div className="brand-mark" aria-hidden="true">✓</div>
          Proof of Review
        </div>
        <h1 id="signin-title">{forgot ? "Reset your password" : "Sign in"}</h1>
        {expired && !forgot && <div className="notice" role="status">Your session ended. Sign in again to carry on where you left off.</div>}
        {!methods ? <p className="sub">Loading…</p> : choice ? (
          <>
            <p className="sub">This email has accounts in more than one organisation. Which one do you want?</p>
            <div className="pill-list">
              {choice.tenants.map((t) => (
                <button key={t.id} disabled={busy} onClick={() => go(
                  choice.path === "/v1/auth/sso/callback" ? "/v1/auth/sso/choose" : choice.path,
                  { ...choice.body, tenant_id: t.id })}>
                  <strong>{t.name}</strong>
                </button>
              ))}
            </div>
            <button className="btn small" style={{ marginTop: 12 }} onClick={() => setChoice(null)}>Back</button>
            <ErrorBox error={error} />
          </>
        ) : forgot ? (
          <>
            {sent ? (
              <div className="success" role="status">
                If an account with a password exists for {email}, we've emailed a link to reset it. It works for a limited time.
              </div>
            ) : (
              <form onSubmit={(e) => (e.preventDefault(), sendReset())}>
                <p className="sub">Enter your work email and we'll send you a link to choose a new password.</p>
                <label htmlFor="reset-email">Work email</label>
                <input id="reset-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
                <div style={{ marginTop: 14 }}>
                  <button className="btn primary" disabled={busy || !email}>Send reset link</button>
                </div>
              </form>
            )}
            <ErrorBox error={error} />
            <button className="btn small" style={{ marginTop: 12 }} onClick={() => (setForgot(false), setSent(false))}>Back to sign in</button>
          </>
        ) : (
          <>
            {methods.sso && (
              <div style={{ marginBottom: methods.password ? 18 : 0 }}>
                {!methods.password && methods.sso_needs_email && (
                  <>
                    <label htmlFor="sso-email">Work email</label>
                    <input id="sso-email" type="email" autoComplete="username" value={email}
                      onChange={(e) => setEmail(e.target.value)} placeholder="you@firm.com" autoFocus />
                  </>
                )}
                <button className="btn primary" style={{ marginTop: 10, width: "100%", justifyContent: "center" }}
                  onClick={sso} disabled={busy || ssoNeedsEmail}>
                  Sign in with single sign-on
                </button>
                {methods.sso_needs_email && methods.password && (
                  <p className="sub" style={{ fontSize: 12 }}>Enter your work email below first, so we can find your organisation.</p>
                )}
              </div>
            )}
            {methods.password && (
              <form onSubmit={(e) => (e.preventDefault(), go("/v1/auth/login", { email, password }))}>
                {methods.sso && <div className="divider" role="separator"><span>or with a password</span></div>}
                <label htmlFor="email">Work email</label>
                <input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@firm.com" autoFocus={!methods.sso} />
                <label htmlFor="password">Password</label>
                <input id="password" type="password" autoComplete="current-password" value={password}
                  onChange={(e) => setPassword(e.target.value)} />
                <div className="btn-row" style={{ marginTop: 14, alignItems: "center", justifyContent: "space-between" }}>
                  <button className="btn primary" disabled={busy || !email || !password}>Sign in</button>
                  {methods.password_reset && (
                    <button type="button" className="linklike" onClick={() => (setForgot(true), setError(null))}>
                      Forgotten your password?
                    </button>
                  )}
                </div>
              </form>
            )}
            {!methods.password && !methods.sso && !methods.dev_login && (
              <p className="sub">No sign-in method is switched on. Ask your administrator.</p>
            )}
            <ErrorBox error={error} />
            {methods.dev_login && (
              <>
                <label style={{ marginTop: 20 }} htmlFor="dev-email">Local development: sign in by email, no password</label>
                <form onSubmit={(e) => (e.preventDefault(), go("/v1/auth/dev-login", { email: devEmail }))} className="btn-row">
                  <input id="dev-email" type="email" value={devEmail} onChange={(e) => setDevEmail(e.target.value)} placeholder="you@firm.com"
                    style={{ flex: 1 }} />
                  <button className="btn" disabled={busy || !devEmail}>Continue</button>
                </form>
                <label style={{ marginTop: 14 }}>Demo accounts (after running the seed script)</label>
                <div className="pill-list">
                  {DEMO.map((d) => (
                    <button key={d.email} onClick={() => go("/v1/auth/dev-login", { email: d.email })} disabled={busy}>
                      <strong>{d.role}</strong> · {d.email}
                    </button>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
