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
