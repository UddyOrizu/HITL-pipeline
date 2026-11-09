import { useEffect, useRef, useState, type KeyboardEvent } from "react";

// A series is one coloured thing on a chart. `color` is a CSS colour, normally a chart token from
// pages/reports.css (var(--viz-1), var(--viz-2)) so light and dark mode each get their own validated step.
export type Series = { key: string; label: string; color: string };

// One category (a period, a group) with a value per series. `long` is the full name for tooltips and tables.
export type Row = { key: string; label: string; long?: string; values: Record<string, number | null | undefined> };

// Clean axis ticks from zero up to (at least) max.
export function niceTicks(max: number, count = 4, integer = false): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  let step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  if (integer) step = Math.max(1, Math.ceil(step));
  const out: number[] = [];
  for (let v = 0; v < max + step * 0.999; v += step) out.push(Number(v.toFixed(10)));
  return out.length > 1 ? out : [0, step];
}

export function compact(v: number) {
  return new Intl.NumberFormat(undefined, { notation: Math.abs(v) >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(v);
}

export function moneyCompact(v: number, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: "currency", currency, notation: Math.abs(v) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(v) < 10 && v !== 0 ? 2 : 0,
  }).format(v);
}

// Hours as minutes, hours or days, whichever reads best.
export function hours(h: number | null | undefined) {
  if (h == null) return "–";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(h < 10 ? 1 : 0)} h`;
  return `${(h / 24).toFixed(1)} d`;
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const WEEK = /^(\d{4})-W(\d{2})$/;
const MONTH = /^\d{4}-\d{2}$/;

// Period keys from the API: YYYY-MM-DD (a day, or the Monday of a week), YYYY-Www (ISO week) or YYYY-MM.
export function periodLabel(key: string, interval?: string, long = false) {
  if (DAY.test(key)) {
    const d = new Date(`${key}T00:00:00`);
    const s = d.toLocaleDateString(undefined, { day: "numeric", month: "short", ...(long ? { year: "numeric" } : {}) });
    return interval === "week" ? (long ? `Week of ${s}` : s) : s;
  }
  const w = WEEK.exec(key);
  if (w) return long ? `Week ${Number(w[2])}, ${w[1]}` : `W${Number(w[2])}`;
  if (MONTH.test(key)) {
    const d = new Date(`${key}-01T00:00:00`);
    return d.toLocaleDateString(undefined, { month: "short", year: long ? "numeric" : "2-digit" });
  }
  return key;
}

// Column with a 4px rounded data end and a square baseline.
export function columnPath(x: number, y: number, w: number, h: number, r = 4) {
  if (h <= 0 || w <= 0) return "";
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h}Z`;
}

// Horizontal bar with a 4px rounded data end on the right.
export function barPath(x: number, y: number, w: number, h: number, r = 4) {
  if (h <= 0 || w <= 0) return "";
  r = Math.min(r, w, h / 2);
  return `M${x},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x}Z`;
}

// Rough text width for 11–12px UI sans; good enough to reserve axis space and decide whether a label fits.
export function textWidth(s: string, px = 11) {
  return s.length * px * 0.6;
}

export function truncate(s: string, maxPx: number, px = 12) {
  const max = Math.floor(maxPx / (px * 0.6));
  return s.length <= max ? s : `${s.slice(0, Math.max(1, max - 1))}…`;
}

// Width of an element, kept up to date, so charts draw at real pixel size (text stays 11px at any width).
export function useWidth<T extends HTMLElement>(initial = 640) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth || initial);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [initial]);
  return [ref, width] as const;
}

// Arrow-key stepping through a chart's categories; Escape clears.
export function stepKey(e: KeyboardEvent, active: number | null, n: number, vertical = false): number | null | undefined {
  const prev = vertical ? "ArrowUp" : "ArrowLeft";
  const next = vertical ? "ArrowDown" : "ArrowRight";
  if (e.key === prev) return Math.max(0, (active ?? n) - 1);
  if (e.key === next) return Math.min(n - 1, (active ?? -1) + 1);
  if (e.key === "Home") return 0;
  if (e.key === "End") return n - 1;
  if (e.key === "Escape") return null;
  return undefined;
}
