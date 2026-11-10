import { useState } from "react";
import { api, download, qs } from "../api";
import { useAuth } from "../auth";
import { BarChart } from "../components/charts/BarChart";
import { ChartFrame } from "../components/charts/ChartFrame";
import { ColumnChart } from "../components/charts/ColumnChart";
import { LineChart } from "../components/charts/LineChart";
import { ReportFilters, rangeDefaults, rangeError, useFilterOptions, useUrlState } from "../components/charts/ReportFilters";
import { SortTable, type Column } from "../components/charts/SortTable";
import { hours, periodLabel, type Row, type Series } from "../components/charts/util";
import { Badge, Bar, Empty, ErrorBox, Stat, isoDate, label, num, pct, riskTone, useLoad, when } from "../components/ui";
import "./reports.css";

type Kpis = {
  received: number; decided: number; approved: number; rejected: number; changes_requested: number;
  approval_rate: number | null; cancelled: number; deferred: number; in_progress: number;
  median_turnaround_hours: number | null; p90_turnaround_hours: number | null; sla_tasks_due: number;
  sla_breaches: number; sla_breach_rate: number | null; escalated: number; held_now: number;
  median_score: number | null; below_threshold: number;
};
type Period = { period: string; received: number; decided: number; approved: number; rejected: number; changes_requested: number; median_turnaround_hours: number | null };
type Breakdown = { key: string | null; label: string | null; received: number; decided: number; approved: number; approval_rate: number | null; median_turnaround_hours: number | null; median_score: number | null };
type Overview = {
  interval: string; kpis: Kpis; series: Period[];
  by_workflow: Breakdown[]; by_source_app: Breakdown[]; by_risk_tier: Breakdown[]; by_origin: Breakdown[];
  by_channel: { channel: string; decisions: number }[]; held_by_reason: { key: string | null; count: number }[];
};
type ReviewerRow = {
  reviewer_id: string; name: string; email: string | null; active: boolean; tasks: number; primary: number; spot_checks: number;
  approval_rate: number | null; median_active_minutes: number | null; median_reading_ratio: number | null;
  mean_coverage: number | null; flag_engagement: number | null; warnings_confirmed: number; canaries_scored: number;
  catch_rate: number | null; spot_check_disagreements: number; sla_breaches: number; open_tasks: number;
  overdue_tasks: number; channels: Record<string, number>;
};
type Overdue = { task_id: string; submission_id: string; title: string | null; stage: string | null; kind: string; reviewer: string | null; due_at: string; hours_overdue: number; escalated: boolean };

const TABS = [
  { id: "overview", name: "Overview", leadOnly: false },
  { id: "reviewers", name: "Reviewers", leadOnly: true },
  { id: "sla", name: "SLA", leadOnly: true },
] as const;

const DIMENSIONS = [
  { id: "by_workflow", name: "Workflow" },
  { id: "by_source_app", name: "Source app" },
  { id: "by_risk_tier", name: "Risk tier" },
  { id: "by_origin", name: "Content origin" },
] as const;

const CHANNELS: Record<string, string> = { ui: "Web app", api: "API", mcp: "MCP" };
const channelName = (c: string) => CHANNELS[c] || label(c);
const count = (v: number) => num(v);

export default function Reports() {
  const { has } = useAuth();
  const lead = has("oversight_lead");
  const [f, setF] = useUrlState({ ...rangeDefaults(), interval: "day", tab: "overview" });
  const tabs = TABS.filter((t) => !t.leadOnly || lead);
  const tab = tabs.some((t) => t.id === f.tab) ? f.tab : "overview";
  const options = useFilterOptions();
  const invalid = rangeError(f);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);

  const filters = { from: f.from, to: f.to, interval: f.interval, workflow_id: f.workflow_id, source_app_id: f.source_app_id };

  const overview = useLoad<Overview | null>(
    () => (tab !== "overview" ? Promise.resolve(null) : invalid ? Promise.reject(new Error(invalid)) : api(`/v1/reports/overview${qs(filters)}`)),
    [tab, f.from, f.to, f.interval, f.workflow_id, f.source_app_id],
  );
  const reviewers = useLoad<{ rows: ReviewerRow[] } | null>(
    () => (tab !== "reviewers" ? Promise.resolve(null) : invalid ? Promise.reject(new Error(invalid)) : api(`/v1/reports/reviewers${qs({ from: f.from, to: f.to })}`)),
    [tab, f.from, f.to],
  );
  const sla = useLoad<{ overdue: Overdue[] } | null>(
    () => (tab !== "sla" ? Promise.resolve(null) : api("/v1/reports/sla")),
    [tab],
  );

  async function exportCsv() {
    setExporting(true);
    setExportError(null);
    try {
      if (tab === "overview") {
        await download(`/v1/reports/overview${qs({ ...filters, format: "csv" })}`, `overview-${f.from}-to-${f.to}-by-${f.interval}.csv`);
      } else if (tab === "reviewers") {
        await download(`/v1/reports/reviewers${qs({ from: f.from, to: f.to, format: "csv" })}`, `reviewers-${f.from}-to-${f.to}.csv`);
      } else {
        // Same (unfiltered) report as the SLA tab, built by the API: current overdue tasks.
        await download(`/v1/reports/sla${qs({ format: "csv" })}`, `overdue-tasks-${isoDate(new Date())}.csv`);
      }
    } catch (e) {
      setExportError(e);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <div className="sub">Throughput, turnaround, outcomes and SLAs{lead ? ", with reviewer performance" : ""}. Dates are UTC.</div>
        </div>
        <div className="rp-head-actions">
          <button type="button" className="btn" onClick={exportCsv} disabled={exporting || (tab !== "sla" && !!invalid) || (tab === "sla" && !sla.data?.overdue.length)}>
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>
      </div>

      {tab !== "sla" && (
        <ReportFilters value={f} onChange={setF} options={options.data}>
          {tab === "overview" && (
            <div>
              <label htmlFor="rp-interval">Interval</label>
              <select id="rp-interval" value={f.interval} onChange={(e) => setF({ interval: e.target.value })}>
                <option value="day">Daily</option>
                <option value="week">Weekly</option>
                <option value="month">Monthly</option>
              </select>
            </div>
          )}
        </ReportFilters>
      )}
      {tab === "reviewers" && (f.workflow_id || f.source_app_id) && (
        <div className="rp-info">The reviewer report covers all workflows and source apps; only the date range applies.</div>
      )}
      <ErrorBox error={exportError} />

      {tabs.length > 1 && (
        <div className="tabs" role="tablist" aria-label="Report">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" id={`rp-tab-${t.id}`} aria-selected={tab === t.id} aria-controls="rp-panel"
              onClick={() => setF({ tab: t.id })}>
              {t.name}
            </button>
          ))}
        </div>
      )}

      <div id="rp-panel" role={tabs.length > 1 ? "tabpanel" : undefined} aria-labelledby={tabs.length > 1 ? `rp-tab-${tab}` : undefined}>
        {tab === "overview" && <OverviewTab state={overview} interval={f.interval} />}
        {tab === "reviewers" && <ReviewersTab state={reviewers} />}
        {tab === "sla" && <SlaTab state={sla} />}
      </div>
    </div>
  );
}

type LoadState<T> = { data: T | null; error: unknown; loading: boolean; reload: () => void };

function Loading() {
  return <Empty>Loading…</Empty>;
}

// --- Overview -----------------------------------------------------------------------------------------------

function OverviewTab({ state, interval }: { state: LoadState<Overview | null>; interval: string }) {
  const { data, error, loading } = state;
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const k = data.kpis;
  const quiet = k.received === 0 && k.decided === 0;
  const per = interval === "week" ? "week" : interval === "month" ? "month" : "day";

  const series: Series[] = [
    { key: "received", label: "Received", color: "var(--viz-1)" },
    { key: "decided", label: "Decided", color: "var(--viz-2)" },
  ];
  const rows: Row[] = data.series.map((p) => ({
    key: p.period, label: periodLabel(p.period, data.interval), long: periodLabel(p.period, data.interval, true),
    values: { received: p.received, decided: p.decided, median: p.median_turnaround_hours },
  }));

  return (
    <div className={loading ? "rp-refreshing" : undefined} aria-busy={loading || undefined}>
      <ErrorBox error={error} />
      <div className="grid rp-kpis">
        <Stat label="Received" value={count(k.received)}
          hint={`${count(k.in_progress)} in progress · ${count(k.deferred)} deferred · ${count(k.cancelled)} cancelled`} />
        <Stat label="Decided" value={count(k.decided)}
          hint={`${count(k.approved)} approved · ${count(k.rejected)} rejected · ${count(k.changes_requested)} changes requested`} />
        <Stat label="Approval rate" value={pct(k.approval_rate)} hint="Share of decided items approved" />
        <Stat label="Median turnaround" value={hours(k.median_turnaround_hours)} hint={`Received to released · 90th percentile ${hours(k.p90_turnaround_hours)}`} />
        <Stat label="SLA breach rate" value={pct(k.sla_breach_rate)} hint={`${count(k.sla_breaches)} of ${count(k.sla_tasks_due)} tasks due in range`} />
        <Stat label="Escalated" value={count(k.escalated)} hint="Received in range and escalated" />
        <Stat label="Held now" value={count(k.held_now)} hint="Currently held, whenever received" />
        <Stat label="Median assurance score" value={k.median_score == null ? "–" : num(k.median_score, 1)} hint={`${count(k.below_threshold)} decided below threshold`} />
      </div>

      {quiet ? (
        <div className="card rp-section"><Empty>Nothing was received or decided in this range. Try a longer range or clear the filters.</Empty></div>
      ) : (
        <>
          <div className="grid grid-2 rp-section">
            <div className="card">
              <ChartFrame title={`Received and decided per ${per}`} loading={loading}
                subtitle="Submissions received, and submissions released with a final decision"
                legend={series.map((s) => ({ label: s.label, color: s.color }))}
                table={{
                  columns: ["Period", "Received", "Decided", "Approved", "Rejected", "Changes requested"],
                  rows: data.series.map((p) => [periodLabel(p.period, data.interval, true), p.received, p.decided, p.approved, p.rejected, p.changes_requested]),
                }}>
                <ColumnChart label={`Received and decided per ${per}`} series={series} rows={rows} format={count} integer />
              </ChartFrame>
            </div>
            <div className="card">
              <ChartFrame title={`Median turnaround per ${per}`} loading={loading}
                subtitle="Hours from received to released, for items decided in each period"
                empty={data.series.every((p) => p.median_turnaround_hours == null) ? "No decisions in this range." : undefined}
                table={{
                  columns: ["Period", "Median turnaround", "Decided"],
                  rows: data.series.map((p) => [periodLabel(p.period, data.interval, true), hours(p.median_turnaround_hours), p.decided]),
                }}>
                <LineChart label={`Median turnaround per ${per}`} rows={rows} valueKey="median" seriesLabel="Median turnaround"
                  color="var(--viz-1)" format={hours} axisFormat={(v) => `${num(v, 1)} h`} />
              </ChartFrame>
            </div>
          </div>

          <BreakdownCard data={data} />

          <div className="grid grid-2 rp-section">
            <div className="card">
              <ChartFrame title="Decisions by channel" loading={loading}
                subtitle="Where reviewers recorded decisions on items released in range"
                empty={data.by_channel.length ? undefined : "No decisions in this range."}
                table={{ columns: ["Channel", "Decisions"], rows: data.by_channel.map((c) => [channelName(c.channel), c.decisions]) }}>
                <BarChart label="Decisions by channel" format={count}
                  series={[{ key: "n", label: "Decisions", color: "var(--viz-1)" }]}
                  rows={[...data.by_channel].sort((a, b) => b.decisions - a.decisions)
                    .map((c) => ({ key: c.channel, label: channelName(c.channel), values: { n: c.decisions } }))} />
              </ChartFrame>
            </div>
            <div className="card">
              <ChartFrame title="Held items by reason" loading={loading}
                subtitle="Items held now, matching the workflow and source-app filters"
                empty={data.held_by_reason.length ? undefined : "Nothing is held."}
                table={{ columns: ["Reason", "Items"], rows: data.held_by_reason.map((h) => [label(h.key) || "No reason", h.count]) }}>
                <BarChart label="Held items by reason" format={count}
                  series={[{ key: "n", label: "Items", color: "var(--viz-1)" }]}
                  rows={data.held_by_reason.map((h) => ({ key: h.key || "none", label: label(h.key) || "No reason", values: { n: h.count } }))} />
              </ChartFrame>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function BreakdownCard({ data }: { data: Overview }) {
  const [dim, setDim] = useState<(typeof DIMENSIONS)[number]["id"]>("by_workflow");
  const rows = data[dim];
  const name = DIMENSIONS.find((d) => d.id === dim)!.name;
  const cols: Column<Breakdown>[] = [
    {
      key: "label", label: name, value: (r) => r.label ?? "",
      render: (r) => dim === "by_risk_tier" && r.key ? <Badge tone={riskTone(r.key)}>{r.key}</Badge>
        : ((dim === "by_origin" ? label(r.label) : r.label) || "Not set"),
    },
    { key: "received", label: "Received", numeric: true, value: (r) => r.received, render: (r) => count(r.received) },
    { key: "decided", label: "Decided", numeric: true, value: (r) => r.decided, render: (r) => count(r.decided) },
    { key: "approved", label: "Approved", numeric: true, value: (r) => r.approved, render: (r) => count(r.approved) },
    {
      key: "approval_rate", label: "Approval rate", numeric: true, value: (r) => r.approval_rate,
      render: (r) => <div style={{ minWidth: 120 }}><Bar value={r.approval_rate} text={pct(r.approval_rate)} /></div>,
    },
    { key: "turnaround", label: "Median turnaround", numeric: true, value: (r) => r.median_turnaround_hours, render: (r) => hours(r.median_turnaround_hours) },
    { key: "score", label: "Median score", numeric: true, value: (r) => r.median_score, render: (r) => (r.median_score == null ? "–" : num(r.median_score, 1)) },
  ];
  return (
    <div className="card rp-section">
      <div className="rp-card-head">
        <div>
          <h2>Breakdown</h2>
          <div className="sub rp-small">Received counts items received in range; decided, approval and turnaround count items released in range.</div>
        </div>
        <div className="segmented" role="group" aria-label="Break down by">
          {DIMENSIONS.map((d) => (
            <button key={d.id} type="button" className={dim === d.id ? "on" : ""} aria-pressed={dim === d.id} onClick={() => setDim(d.id)}>{d.name}</button>
          ))}
        </div>
      </div>
      <SortTable key={dim} caption={`Breakdown by ${name.toLowerCase()}`} columns={cols} rows={rows}
        rowKey={(r) => r.key ?? "none"} empty="No items in this range." />
    </div>
  );
}

// --- Reviewers ----------------------------------------------------------------------------------------------

const ratio = (v: number | null) => pct(v);

const REVIEWER_COLUMNS: Column<ReviewerRow>[] = [
  {
    key: "name", label: "Reviewer", value: (r) => r.name.toLowerCase(),
    render: (r) => (
      <div className="rp-who">
        <span>{r.name} {!r.active && <Badge>inactive</Badge>}</span>
        {r.email && r.email !== r.name && <span className="sub">{r.email}</span>}
      </div>
    ),
  },
  { key: "tasks", label: "Tasks", numeric: true, value: (r) => r.tasks, hint: "Decisions recorded in range" },
  { key: "primary", label: "Primary", numeric: true, value: (r) => r.primary },
  { key: "spot_checks", label: "Spot checks", numeric: true, value: (r) => r.spot_checks },
  { key: "approval_rate", label: "Approval rate", numeric: true, value: (r) => r.approval_rate, render: (r) => ratio(r.approval_rate) },
  { key: "median_active_minutes", label: "Median active time", numeric: true, value: (r) => r.median_active_minutes, hint: "Measured reading time per decision in the web app",
    render: (r) => (r.median_active_minutes == null ? "–" : `${num(r.median_active_minutes, 1)} min`) },
  { key: "median_reading_ratio", label: "Reading ratio", numeric: true, value: (r) => r.median_reading_ratio, hint: "Median share of the expected reading time actually spent",
    render: (r) => ratio(r.median_reading_ratio) },
  { key: "mean_coverage", label: "Coverage", numeric: true, value: (r) => r.mean_coverage, hint: "Mean share of the content viewed", render: (r) => ratio(r.mean_coverage) },
  { key: "flag_engagement", label: "Flag engagement", numeric: true, value: (r) => r.flag_engagement, hint: "Mean share of flags looked at", render: (r) => ratio(r.flag_engagement) },
  { key: "warnings_confirmed", label: "Warnings confirmed", numeric: true, value: (r) => r.warnings_confirmed },
  { key: "canaries_scored", label: "Canaries", numeric: true, value: (r) => r.canaries_scored, hint: "Canaries scored in range" },
  { key: "catch_rate", label: "Catch rate", numeric: true, value: (r) => r.catch_rate, hint: "Share of canaries caught", render: (r) => ratio(r.catch_rate) },
  { key: "spot_check_disagreements", label: "Spot-check disagreements", numeric: true, value: (r) => r.spot_check_disagreements, hint: "Spot checks that reached a different outcome" },
  { key: "sla_breaches", label: "SLA breaches", numeric: true, value: (r) => r.sla_breaches },
  { key: "open_tasks", label: "Open", numeric: true, value: (r) => r.open_tasks, hint: "Assigned now" },
  { key: "overdue_tasks", label: "Overdue", numeric: true, value: (r) => r.overdue_tasks, hint: "Assigned now and past due",
    render: (r) => (r.overdue_tasks ? <Badge tone="high">{r.overdue_tasks}</Badge> : "0") },
  {
    key: "channels", label: "Channels", value: (r) => Object.keys(r.channels).sort().join(","),
    render: (r) => Object.entries(r.channels).map(([c, n]) => `${channelName(c)} ${n}`).join(" · ") || "–",
  },
];

function ReviewersTab({ state }: { state: LoadState<{ rows: ReviewerRow[] } | null> }) {
  const { data, error, loading } = state;
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div className={`card${loading ? " rp-refreshing" : ""}`} aria-busy={loading || undefined}>
      <ErrorBox error={error} />
      <div className="rp-card-head">
        <div>
          <h2>Reviewer performance</h2>
          <div className="sub rp-small">Active reviewers, plus anyone who recorded a decision in range. Telemetry measures apply to web-app decisions only.</div>
        </div>
      </div>
      {data.rows.length === 0
        ? <Empty>No reviewers yet.</Empty>
        : <SortTable caption="Reviewer performance" columns={REVIEWER_COLUMNS} rows={data.rows} rowKey={(r) => r.reviewer_id}
            initial={{ key: "tasks", dir: "desc" }} />}
    </div>
  );
}

// --- SLA ----------------------------------------------------------------------------------------------------

function SlaTab({ state }: { state: LoadState<{ overdue: Overdue[] } | null> }) {
  const { data, error, loading, reload } = state;
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const rows = [...data.overdue].sort((a, b) => b.hours_overdue - a.hours_overdue);
  return (
    <div className={`card${loading ? " rp-refreshing" : ""}`} aria-busy={loading || undefined}>
      <ErrorBox error={error} />
      <div className="rp-card-head">
        <div>
          <h2>Overdue tasks</h2>
          <div className="sub rp-small">Assigned tasks past their due time right now, oldest first. Not affected by the date range.</div>
        </div>
        <button type="button" className="btn small" onClick={reload} disabled={loading}>Refresh</button>
      </div>
      {rows.length === 0 ? <Empty>Nothing is overdue.</Empty> : (
        <div className="rp-scroll">
          <table className="rp-table">
            <caption className="sr-only">Overdue tasks, oldest first</caption>
            <thead>
              <tr>
                <th scope="col">Item</th><th scope="col">Stage</th><th scope="col">Kind</th><th scope="col">Reviewer</th>
                <th scope="col">Due</th><th scope="col" className="rp-num">Overdue by</th><th scope="col">Escalated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.task_id}>
                  <td>{o.title || <span className="mono">{o.submission_id.slice(0, 8)}</span>}</td>
                  <td>{o.stage || "–"}</td>
                  <td><Badge tone={o.kind === "spot_check" ? "medium" : "accent"}>{label(o.kind)}</Badge></td>
                  <td>{o.reviewer || "Unassigned"}</td>
                  <td>{when(o.due_at)}</td>
                  <td className="rp-num">{hours(o.hours_overdue)}</td>
                  <td>{o.escalated ? <Badge tone="high">Escalated</Badge> : <span className="sub">No</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
