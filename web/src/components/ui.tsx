import { useEffect, useState, type ReactNode } from "react";

export function Badge({ tone, children }: { tone?: string; children: ReactNode }) {
  return <span className={`badge ${tone || ""}`}>{children}</span>;
}

export function riskTone(tier: string) {
  return tier === "high" ? "high" : tier === "medium" ? "medium" : "low";
}

export function statusTone(status: string) {
  if (status === "approved") return "ok";
  if (status === "rejected") return "high";
  if (status === "changes_requested" || status === "spot_check" || status === "deferred") return "medium";
  return "accent";
}

export function label(s: string | null | undefined) {
  return (s || "").replace(/_/g, " ");
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="error">{msg}</div>;
}

export function Stat({ label: l, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card stat">
      <div className="label">{l}</div>
      <div className="value">{value ?? "–"}</div>
      {hint && <div className="sub" style={{ fontSize: 12 }}>{hint}</div>}
    </div>
  );
}

export function Bar({ value, max = 1, text }: { value: number | null | undefined; max?: number; text?: string }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar">
      <div className="track"><div className="fill" style={{ width: `${pct}%` }} /></div>
      <span className="mono" style={{ minWidth: 44, textAlign: "right" }}>{text ?? (value == null ? "–" : `${Math.round(pct)}%`)}</span>
    </div>
  );
}

export function pct(v: number | null | undefined) {
  return v == null ? "–" : `${Math.round(v * 100)}%`;
}

export function when(iso: string | null | undefined) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn()
      .then((d) => live && (setData(d), setError(null)))
      .catch((e) => live && setError(e))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export const ROLE_INFO: Record<string, { name: string; tone: string; blurb: string }> = {
  admin: { name: "Admin", tone: "high", blurb: "Manages users, teams, workflows and integrations. Includes every other kind of access except reviewing." },
  reviewer: { name: "Reviewer", tone: "accent", blurb: "Reviews assigned items. Measured by canaries, telemetry and spot-checks." },
  submitter: { name: "Submitter", tone: "ok", blurb: "Sends work for review from the web app and tracks the decision. Never reviews their own work." },
  oversight_lead: { name: "Oversight lead", tone: "medium", blurb: "Assurance dashboard, held items and workflow settings." },
  auditor: { name: "Auditor", tone: "low", blurb: "Read-only evidence explorer and audit log." },
  integration_owner: { name: "Integration owner", tone: "", blurb: "Source apps, API keys, webhooks and connectors." },
};

export function RoleBadge({ role }: { role: string }) {
  const info = ROLE_INFO[role];
  return <Badge tone={info?.tone}>{info?.name || label(role)}</Badge>;
}

// --- Shared helpers for paged lists, dialogs, money and dates ---------------------------------------------

export function Pager({ offset, limit, total, onChange }: { offset: number; limit: number; total: number; onChange: (offset: number) => void }) {
  if (total <= limit && offset === 0) return null;
  const from = total ? offset + 1 : 0;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pager" aria-label="Pages">
      <span className="sub">{from}–{to} of {total}</span>
      <button className="btn small" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
      <button className="btn small" disabled={to >= total} onClick={() => onChange(offset + limit)}>Next</button>
    </nav>
  );
}

export function Modal({ title, onClose, children, width = 520 }: { title: string; onClose: () => void; children: ReactNode; width?: number }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} style={{ maxWidth: width }}
        onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="btn small" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

export function money(v: number | null | undefined, currency = "USD", digits?: number) {
  if (v == null) return "–";
  const small = Math.abs(v) > 0 && Math.abs(v) < 1;
  return new Intl.NumberFormat(undefined, {
    style: "currency", currency,
    minimumFractionDigits: digits ?? (small ? 4 : 2), maximumFractionDigits: digits ?? (small ? 4 : 2),
  }).format(v);
}

export function num(v: number | null | undefined, digits = 0) {
  return v == null ? "–" : new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(v);
}

// yyyy-mm-dd for date inputs (local date).
export function isoDate(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function daysAgo(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
}
