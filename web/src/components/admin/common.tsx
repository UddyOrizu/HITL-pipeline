// Shared pieces for the admin pages (integrations console, organisation settings, organisations).
import { useState, type ReactNode } from "react";
import { ApiError } from "../../api";
import { Modal } from "../ui";

// A readable message for any error, including FastAPI validation lists and permission/server failures.
export function errorText(e: unknown): string {
  if (!e) return "";
  if (e instanceof ApiError) {
    const d = e.detail;
    if (Array.isArray(d)) {
      return d
        .map((x: any) => {
          const loc = Array.isArray(x?.loc) ? x.loc.filter((p: unknown) => p !== "body").join(".") : "";
          const msg = x?.msg ?? String(x);
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join("; ");
    }
    if (e.status === 403) return `You don't have access to this: ${typeof d === "string" ? d : "forbidden"}.`;
    if (e.status === 404 && typeof d === "string") return `Not found: ${d}.`;
    if (e.status >= 500) return `The server could not complete this (${e.status}${typeof d === "string" && d ? `: ${d}` : ""}). Try again; if it keeps failing, check the API logs.`;
    return e.message;
  }
  if (e instanceof TypeError) return `Could not reach the server (${e.message}).`;
  return e instanceof Error ? e.message : String(e);
}

export function Err({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error" role="alert">{errorText(error)}</div>;
}

export function Ok({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <div className="success" role="status">{children}</div>;
}

// Run an action with a busy flag, an error and an optional success message.
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [ok, setOk] = useState<string | null>(null);
  async function run<T>(key: string, fn: () => Promise<T>, success?: string | ((r: T) => string)): Promise<T | undefined> {
    setBusy(key);
    setError(null);
    setOk(null);
    try {
      const r = await fn();
      if (success) setOk(typeof success === "function" ? success(r) : success);
      return r;
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      setBusy(null);
    }
  }
  return { busy, error, ok, run, setError, setOk, clear: () => (setError(null), setOk(null)) };
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const done = document.execCommand("copy");
      ta.remove();
      return done;
    } catch {
      return false;
    }
  }
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <button type="button" className="btn small" onClick={async () => {
      setState((await copyText(text)) ? "copied" : "failed");
      setTimeout(() => setState("idle"), 1800);
    }}>
      {state === "copied" ? "Copied" : state === "failed" ? "Select and copy" : label}
    </button>
  );
}

// A value with a copy button, e.g. a one-time secret or a redirect URI.
export function CopyField({ label, value, hint }: { label: string; value: string; hint?: ReactNode }) {
  return (
    <div className="copy-field">
      <div className="copy-field-label">{label}</div>
      <div className="copy-field-row">
        <input readOnly value={value} className="mono" onFocus={(e) => e.currentTarget.select()} aria-label={label} />
        <CopyButton text={value} />
      </div>
      {hint && <div className="sub hint">{hint}</div>}
    </div>
  );
}

// A confirmation dialog that runs the action itself and shows its error without closing.
export function Confirm({ title, children, confirmLabel = "Confirm", danger, onConfirm, onClose, disabled }: {
  title: string; children: ReactNode; confirmLabel?: string; danger?: boolean; disabled?: boolean;
  onConfirm: () => Promise<unknown>; onClose: () => void;
}) {
  const a = useAction();
  return (
    <Modal title={title} onClose={onClose}>
      <div className="modal-body">{children}</div>
      <Err error={a.error} />
      <div className="btn-row modal-actions">
        {/* Focus starts on the safe choice for destructive actions. */}
        <button className="btn" onClick={onClose} autoFocus={danger}>Cancel</button>
        <button className={`btn ${danger ? "danger" : "primary"}`} disabled={!!a.busy || disabled} autoFocus={!danger}
          onClick={async () => {
            const ok = await a.run("confirm", async () => { await onConfirm(); return true; });
            if (ok) onClose();
          }}>
          {a.busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

export function Section({ id, title, intro, children }: { id?: string; title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section className="card admin-section" id={id} aria-labelledby={id ? `${id}-title` : undefined}>
      <h2 id={id ? `${id}-title` : undefined}>{title}</h2>
      {intro && <div className="sub admin-intro">{intro}</div>}
      {children}
    </section>
  );
}
