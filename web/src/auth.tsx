import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, clearUserState, getToken, lastActivityAt, markActivity, setToken, tokenClaims } from "./api";

// roles: what the user was given. permissions: what they can do (admin implies every role except reviewer).
export type Me = {
  id: string; email: string; name: string; roles: string[]; permissions: string[]; password_set: boolean;
  sso_linked: boolean; email_notifications: boolean; platform_admin: boolean;
  tenant: { id: string; name: string } | null;
};

type AuthState = {
  me: Me | null;
  loading: boolean;
  signIn: (token: string) => Promise<void>;
  // Swap in a new token and refresh the user without the app-wide loading state (e.g. after a password change).
  replaceToken: (token: string) => Promise<void>;
  setMe: (me: Me) => void;
  // Deliberate sign-out: ends the session and clears this tab's per-user state (return path, review drafts).
  signOut: () => Promise<void>;
  // True after a deliberate sign-out (until the next sign-in): the app must not remember a page to return to.
  signedOut: boolean;
  has: (...roles: string[]) => boolean;
};

const AuthContext = createContext<AuthState>(null as unknown as AuthState);

// Refresh the token while the app is in use, once less than this share of its lifetime is left.
const REFRESH_WHEN_LEFT = 0.25;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [signedOut, setSignedOut] = useState(false);
  const refreshing = useRef(false);

  async function load() {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    try {
      setMe(await api<Me>("/v1/me"));
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const ended = () => setMe(null);
    window.addEventListener("por:session-ended", ended);
    return () => window.removeEventListener("por:session-ended", ended);
  }, []);

  // Sliding session: refresh before expiry, so a long review is not interrupted, but only while the person is
  // using the app: with no pointer, keyboard or scroll activity (or review telemetry) since the last tick, the
  // token is left to expire. The API stops refreshing after POR_SESSION_MAX_HOURS from sign-in; then the person
  // signs in again (and returns to the same page).
  useEffect(() => {
    if (!me) return;
    const opts = { passive: true, capture: true } as const;
    const events = ["pointerdown", "pointermove", "keydown", "wheel", "scroll", "touchstart"];
    // Capture on document: scroll events don't bubble, and inner panes (the review document) scroll themselves.
    events.forEach((e) => document.addEventListener(e, markActivity, opts));
    // The first tick (on load or sign-in) counts the page load itself as activity.
    let lastTick = 0;
    const tick = async () => {
      const since = lastTick;
      lastTick = Date.now();
      const c = tokenClaims();
      if (!c?.exp || !c.iat || refreshing.current) return;
      const now = Date.now() / 1000;
      if (c.exp - now > (c.exp - c.iat) * REFRESH_WHEN_LEFT) return;
      if (lastActivityAt() < since) return; // idle since the last tick: let the session expire
      refreshing.current = true;
      try {
        const r = await api<{ access_token: string }>("/v1/auth/refresh", { method: "POST" });
        setToken(r.access_token);
      } catch {
        /* at the maximum session length: the next request after expiry sends the person to sign in */
      } finally {
        refreshing.current = false;
      }
    };
    tick();
    const id = setInterval(tick, 60_000);
    return () => {
      clearInterval(id);
      events.forEach((e) => document.removeEventListener(e, markActivity, opts));
    };
  }, [me]);

  const value: AuthState = {
    me,
    loading,
    signIn: async (token) => {
      setSignedOut(false);
      setToken(token);
      setLoading(true);
      await load();
    },
    replaceToken: async (token) => {
      setToken(token);
      setMe(await api<Me>("/v1/me"));
    },
    setMe,
    signOut: async () => {
      try {
        if (getToken()) await api("/v1/auth/logout", { method: "POST" });
      } catch {
        /* already signed out */
      }
      setToken(null);
      clearUserState();
      setSignedOut(true);
      setMe(null);
    },
    signedOut,
    has: (...roles) => !!me && roles.some((r) => (me.permissions || me.roles).includes(r)),
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
