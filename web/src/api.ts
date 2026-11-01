// Thin fetch wrapper. The token lives in localStorage; requests go to /v1 (proxied to the API in dev).

const TOKEN_KEY = "por.token";

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

export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(typeof detail === "string" ? detail : detail?.message || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T = any>(path: string, options: { method?: string; body?: any; raw?: boolean } = {}): Promise<T> {
  // X-PoR-Channel labels decisions and submissions made from the web app in the evidence record.
  const headers: Record<string, string> = { "X-PoR-Channel": "ui" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const form = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body !== undefined && !form) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: options.method || (options.body !== undefined ? "POST" : "GET"),
    headers,
    body: options.body === undefined ? undefined : form ? options.body : JSON.stringify(options.body),
  });
  if (res.status === 401) {
    setToken(null);
    if (!location.pathname.startsWith("/login")) location.assign("/login");
  }
  if (!res.ok) {
    let detail: any = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (options.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return res.json();
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
