import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, takeReturn } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/ui";

// Single sign-on, authorization code flow with PKCE. The verifier never leaves this browser tab:
// only its SHA-256 challenge goes to the identity provider, and the verifier goes to our API with the code.
const VERIFIER_KEY = "por.sso.verifier";
const STATE_KEY = "por.sso.state";

function randomVerifier(): string {
  const bytes = new Uint8Array(48);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

export async function startSso(email: string | null) {
  const verifier = randomVerifier();
  const r = await api<{ authorize_url: string; state: string }>("/v1/auth/sso/start", {
    auth: false,
    body: { redirect_uri: `${window.location.origin}/login/sso`, code_challenge: await challengeFor(verifier), email },
  });
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, r.state);
  window.location.assign(r.authorize_url);
}

export default function SsoCallback() {
  const { signIn } = useAuth();
  const nav = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const [choice, setChoice] = useState<{ ticket: string; tenants: { id: string; name: string }[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  async function finish(token: string) {
    await signIn(token);
    nav(takeReturn() || "/", { replace: true });
  }

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice; a code can only be used once
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    const verifier = sessionStorage.getItem(VERIFIER_KEY);
    const expected = sessionStorage.getItem(STATE_KEY);
    sessionStorage.removeItem(VERIFIER_KEY);
    sessionStorage.removeItem(STATE_KEY);
    if (params.get("error")) {
      setError(params.get("error_description") || params.get("error"));
      return;
    }
    if (!code || !state || !verifier || state !== expected) {
      setError("This sign-in could not be completed (it may have expired or been opened in another tab). Start again.");
      return;
    }
    api<{ access_token: string }>("/v1/auth/sso/callback", { auth: false, body: { code, state, code_verifier: verifier } })
      .then((r) => finish(r.access_token))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 409 && e.detail?.code === "choose_tenant") {
          setChoice({ ticket: e.detail.ticket, tenants: e.detail.tenants });
        } else {
          setError(e);
        }
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function choose(tenantId: string) {
    if (!choice) return;
    setBusy(true);
    try {
      const r = await api<{ access_token: string }>("/v1/auth/sso/choose", { auth: false, body: { ticket: choice.ticket, tenant_id: tenantId } });
      await finish(r.access_token);
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
        <h1>Single sign-on</h1>
        {choice ? (
          <>
            <p className="sub">Your email has accounts in more than one organisation. Which one do you want?</p>
            <div className="pill-list">
              {choice.tenants.map((t) => (
                <button key={t.id} disabled={busy} onClick={() => choose(t.id)}><strong>{t.name}</strong></button>
              ))}
            </div>
          </>
        ) : !error ? <p className="sub" role="status">Signing you in…</p> : null}
        <ErrorBox error={error} />
        {error ? <a className="btn" href="/login" style={{ marginTop: 12 }}>Back to sign in</a> : null}
      </main>
    </div>
  );
}
