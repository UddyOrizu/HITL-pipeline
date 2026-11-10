// Thin fetch wrapper. The token lives in localStorage; requests go to /v1 (proxied to the API in dev).

const TOKEN_KEY = "por.token";
const RETURN_KEY = "por.returnTo";
const EXPIRED_KEY = "por.sessionExpired";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session-only sign-in */
  }
}

// Claims from the signed-in token (not verified here: the API verifies every request).
export function tokenClaims(token: string | null = getToken()): { exp?: number; iat?: number; auth_time?: number } | null {
  if (!token) return null;
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), "=")));
  } catch {
    return null;
  }
}

// Where to go back to after signing in again, and whether the last session expired (for a message).
export function rememberReturn(path: string) {
  try {
    sessionStorage.setItem(RETURN_KEY, path);
  } catch {
    /* ignore */
  }
}

export function takeReturn(): string | null {
  try {
    const v = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
    return v && v.startsWith("/") && !v.startsWith("//") ? v : null;
  } catch {
    return null;
  }
}

export function takeSessionExpired(): boolean {
  try {
    const v = sessionStorage.getItem(EXPIRED_KEY) === "1";
    sessionStorage.removeItem(EXPIRED_KEY);
    return v;
  } catch {
    return false;
  }
}

// Per-user state kept in this tab: the return path, the expired-session flag and in-progress review drafts
// (Workspace, key por.reviewDraft.<task id>). Kept when a session expires (so the person picks up where they were),
// cleared on a deliberate sign-out so the next person on this browser doesn't inherit it.
const USER_STATE_PREFIXES = ["por.reviewDraft."];

export function clearUserState() {
  try {
    sessionStorage.removeItem(RETURN_KEY);
    sessionStorage.removeItem(EXPIRED_KEY);
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k && USER_STATE_PREFIXES.some((p) => k.startsWith(p))) keys.push(k);
    }
    keys.forEach((k) => sessionStorage.removeItem(k));
  } catch {
    /* storage unavailable */
  }
}

// Last time the person did something in this tab (pointer, keyboard, scroll, or review telemetry). The sliding
// session only refreshes after activity, so an idle tab is allowed to expire.
let lastActivity = Date.now();

export function markActivity() {
  lastActivity = Date.now();
}

export function lastActivityAt() {
  return lastActivity;
}

// Called when the session ends mid-use: keep where the person was, then go to the sign-in page.
export function sessionEnded() {
  setToken(null);
  if (location.pathname.startsWith("/login") || location.pathname.startsWith("/reset-password")) return;
  rememberReturn(location.pathname + location.search);
  try {
    sessionStorage.setItem(EXPIRED_KEY, "1");
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event("por:session-ended"));
  location.assign("/login");
}

export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(typeof detail === "string" ? detail : detail?.message || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

// keepalive: let the request outlive the page (final telemetry flush on pagehide / tab hidden). Body must stay under 64 KB.
type Options = { method?: string; body?: any; raw?: boolean; auth?: boolean; keepalive?: boolean };

async function request(path: string, options: Options = {}, retried = false): Promise<Response> {
  // X-PoR-Channel labels decisions and submissions made from the web app in the evidence record.
  const headers: Record<string, string> = { "X-PoR-Channel": "ui" };
  const token = getToken();
  if (token && options.auth !== false) headers.Authorization = `Bearer ${token}`;
  const form = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body !== undefined && !form) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: options.method || (options.body !== undefined ? "POST" : "GET"),
    headers,
    body: options.body === undefined ? undefined : form ? options.body : JSON.stringify(options.body),
    keepalive: options.keepalive || undefined,
  });
  if (res.status === 401 && token && options.auth !== false && !path.startsWith("/v1/auth/")) {
    // A refresh revokes the previous token. If this tab (or another tab sharing localStorage) refreshed while the
    // request was in flight, retry once with the new token instead of ending the session.
    const current = getToken();
    if (!retried && current && current !== token) return request(path, options, true);
    sessionEnded();
  }
  if (!res.ok) {
    let detail: any = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    if (res.status === 429) detail = "Too many attempts. Wait a minute and try again.";
    throw new ApiError(res.status, detail);
  }
  return res;
}

export async function api<T = any>(path: string, options: Options = {}): Promise<T> {
  const res = await request(path, options);
  if (options.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type Page<T> = { items: T[]; total: number; nextOffset: number | null };

// List endpoints return a JSON array; the total and next offset are in headers.
export async function apiPage<T = any>(path: string, options: Options = {}): Promise<Page<T>> {
  const res = await request(path, options);
  const items = (await res.json()) as T[];
  const total = Number(res.headers.get("X-Total-Count") ?? items.length);
  const next = res.headers.get("X-Next-Offset");
  return { items, total, nextOffset: next == null ? null : Number(next) };
}

// Every item of a list endpoint, following X-Next-Offset when the server pages it (one request when it doesn't).
// For short admin lists (users, teams, workflows, source apps, connectors) that the UI filters on the client.
export async function apiAll<T = any>(path: string, maxPages = 50): Promise<T[]> {
  const all: T[] = [];
  let offset = 0;
  for (let i = 0; i < maxPages; i++) {
    const sep = path.includes("?") ? "&" : "?";
    const page = await apiPage<T>(offset ? `${path}${sep}offset=${offset}` : path);
    all.push(...page.items);
    if (page.nextOffset == null || page.nextOffset <= offset || !page.items.length) break;
    offset = page.nextOffset;
  }
  return all;
}

// Build a query string from defined, non-empty values.
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function download(path: string, filename: string) {
  const res = await api<Response>(path, { raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
