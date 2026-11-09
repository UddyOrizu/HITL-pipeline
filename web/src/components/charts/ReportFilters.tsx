import { useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { daysAgo, isoDate, useLoad } from "../ui";

export type FilterOptions = { workflows: { id: string; name: string }[]; source_apps: { id: string; name: string; kind: string }[] };

export type RangeFilters = { from: string; to: string; workflow_id: string; source_app_id: string };

export const PRESETS = [7, 30, 90];

// Report state lives in the URL, so a filtered report can be bookmarked or shared. Defaults are left out.
export function useUrlState<T extends Record<string, string>>(defaults: T): [T, (patch: Partial<T>) => void] {
  const [params, setParams] = useSearchParams();
  const state = useMemo(() => {
    const out = { ...defaults };
    for (const k of Object.keys(defaults) as (keyof T)[]) {
      const v = params.get(k as string);
      if (v != null && v !== "") out[k] = v as T[keyof T];
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  const update = (patch: Partial<T>) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v == null || v === "" || v === defaults[k]) next.delete(k);
        else next.set(k, String(v));
      }
      return next;
    }, { replace: true });
  };
  return [state, update];
}

export function rangeDefaults(): RangeFilters {
  return { from: daysAgo(29), to: isoDate(new Date()), workflow_id: "", source_app_id: "" };
}

export function rangeError(f: { from: string; to: string }): string | null {
  if (!f.from || !f.to) return "Choose a start and end date.";
  if (f.from > f.to) return "The start date must be on or before the end date.";
  const days = (new Date(f.to).getTime() - new Date(f.from).getTime()) / 86400000;
  if (days > 731) return "The range can be at most two years.";
  return null;
}

export function useFilterOptions() {
  return useLoad<FilterOptions>(() => api("/v1/reports/filters"), []);
}

// Date range (presets first), then any page-specific controls, then workflow and source-app filters.
export function ReportFilters({ value, onChange, options, children }: {
  value: RangeFilters; onChange: (patch: Partial<RangeFilters>) => void; options: FilterOptions | null; children?: ReactNode;
}) {
  const today = isoDate(new Date());
  const preset = PRESETS.find((n) => value.to === today && value.from === daysAgo(n - 1));
  return (
    <div className="toolbar rp-filters" role="group" aria-label="Report filters">
      <div>
        <span className="rp-label" id="rp-range">Range</span>
        <div className="segmented" role="group" aria-labelledby="rp-range">
          {PRESETS.map((n) => (
            <button key={n} type="button" className={preset === n ? "on" : ""} aria-pressed={preset === n}
              onClick={() => onChange({ from: daysAgo(n - 1), to: today })}>
              {n} days
            </button>
          ))}
        </div>
      </div>
      <div>
        <label htmlFor="rp-from">From</label>
        <input id="rp-from" type="date" value={value.from} max={value.to || today} onChange={(e) => onChange({ from: e.target.value })} />
      </div>
      <div>
        <label htmlFor="rp-to">To</label>
        <input id="rp-to" type="date" value={value.to} min={value.from} max={today} onChange={(e) => onChange({ to: e.target.value })} />
      </div>
      {children}
      <div>
        <label htmlFor="rp-wf">Workflow</label>
        <select id="rp-wf" value={value.workflow_id} onChange={(e) => onChange({ workflow_id: e.target.value })}>
          <option value="">All workflows</option>
          {options?.workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="rp-app">Source app</label>
        <select id="rp-app" value={value.source_app_id} onChange={(e) => onChange({ source_app_id: e.target.value })}>
          <option value="">All source apps</option>
          {options?.source_apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </div>
    </div>
  );
}
