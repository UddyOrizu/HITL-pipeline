import { useState } from "react";
import { Link } from "react-router-dom";
import { api, download, qs } from "../api";
import { useAuth } from "../auth";
import { BarChart } from "../components/charts/BarChart";
import { ChartFrame } from "../components/charts/ChartFrame";
import { ColumnChart } from "../components/charts/ColumnChart";
import { ReportFilters, rangeDefaults, rangeError, useFilterOptions, useUrlState } from "../components/charts/ReportFilters";
import { SortTable, type Column } from "../components/charts/SortTable";
import { compact, moneyCompact, periodLabel, type Row, type Series } from "../components/charts/util";
import { Empty, ErrorBox, Stat, isoDate, money, num, useLoad } from "../components/ui";
import "./reports.css";

type CostRow = {
  key: string; label: string; total_cost: number; model_cost: number; model_cost_usd: number; labour_cost: number;
  input_tokens: number; output_tokens: number; model_calls: number; review_minutes: number; estimated_minutes: number;
  unrated_minutes: number; tasks: number; submissions: number;
};
type CostReport = {
  currency: string; usd_to_currency: number; group_by: string; rows: CostRow[]; total: CostRow;
  per_submission: number | null; unpriced_models: { model: string; tokens: number }[];
  default_hourly_rate: number | null; prices_as_of: string;
};

const GROUPS = [
  { id: "source_app", name: "Source app" },
  { id: "workflow", name: "Workflow" },
  { id: "team", name: "Team" },
  { id: "reviewer", name: "Reviewer" },
  { id: "model", name: "Model" },
  { id: "submission", name: "Submission" },
  { id: "day", name: "Day" },
  { id: "week", name: "Week" },
  { id: "month", name: "Month" },
] as const;
const PERIODS = ["day", "week", "month"];
const CHART_ROWS = 12; // the chart shows the largest groups; the rest fold into "Other" (the table has every row)

const SERIES: Series[] = [
  { key: "model", label: "Model spend", color: "var(--viz-1)" },
  { key: "labour", label: "Reviewer time", color: "var(--viz-2)" },
];

// Minutes as "45 min" or "3.2 h".
function mins(m: number | null | undefined) {
  if (m == null) return "–";
  if (m < 60) return `${num(m, 0)} min`;
  return `${num(m / 60, 1)} h`;
}

function isoWeek(d: Date) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y = t.getUTCFullYear();
  const w = Math.ceil(((t.getTime() - Date.UTC(y, 0, 1)) / 86400000 + 1) / 7);
  return `${y}-W${String(w).padStart(2, "0")}`;
}

// The API only returns periods with activity; fill the quiet ones so the time axis is continuous.
function periodKeys(from: string, to: string, group: string): string[] {
  const out: string[] = [];
  const d = new Date(`${from}T00:00:00`);
  const end = new Date(`${to}T00:00:00`);
  for (let i = 0; d <= end && i < 800; i++, d.setDate(d.getDate() + 1)) {
    const k = group === "day" ? isoDate(d) : group === "week" ? isoWeek(d) : isoDate(d).slice(0, 7);
    if (out[out.length - 1] !== k) out.push(k);
  }
  return out;
}

export default function Costs() {
  const { me } = useAuth();
  const isAdmin = !!me?.roles.includes("admin");
  const [f, setF] = useUrlState({ ...rangeDefaults(), group_by: "source_app" });
  const options = useFilterOptions();
  const invalid = rangeError(f);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);
  const params = { from: f.from, to: f.to, group_by: f.group_by, workflow_id: f.workflow_id, source_app_id: f.source_app_id };

  const { data, error, loading } = useLoad<CostReport>(
    () => (invalid ? Promise.reject(new Error(invalid)) : api(`/v1/reports/costs${qs(params)}`)),
    [f.from, f.to, f.group_by, f.workflow_id, f.source_app_id],
  );

  async function exportCsv() {
    setExporting(true);
    setExportError(null);
    try {
      await download(`/v1/reports/costs${qs({ ...params, format: "csv" })}`, `costs-by-${f.group_by}-${f.from}-to-${f.to}.csv`);
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
          <h1>Costs</h1>
          <div className="sub">Model spend and reviewer time, by {GROUPS.find((g) => g.id === f.group_by)?.name.toLowerCase() || "group"}. Dates are UTC.</div>
        </div>
        <div className="rp-head-actions">
          <button type="button" className="btn" onClick={exportCsv} disabled={exporting || !!invalid}>
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>
      </div>

      <ReportFilters value={f} onChange={setF} options={options.data}>
        <div>
          <label htmlFor="rp-group">Group by</label>
          <select id="rp-group" value={f.group_by} onChange={(e) => setF({ group_by: e.target.value })}>
            {GROUPS.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        </div>
      </ReportFilters>
      <ErrorBox error={exportError} />

      {error && !data ? <ErrorBox error={error} /> : !data ? <Empty>Loading…</Empty> : (
        <CostBody data={data} from={f.from} to={f.to} loading={loading} error={error} isAdmin={isAdmin} />
      )}
    </div>
  );
}

function CostBody({ data, from, to, loading, error, isAdmin }: {
  data: CostReport; from: string; to: string; loading: boolean; error: unknown; isAdmin: boolean;
}) {
  const cur = data.currency;
  const t = data.total;
  const m = (v: number | null | undefined) => money(v, cur);
  const isPeriod = PERIODS.includes(data.group_by);
  const groupName = GROUPS.find((g) => g.id === data.group_by)?.name || "Group";
  const reviewMinutes = t.review_minutes + t.estimated_minutes;
  const settingsLink = isAdmin
    ? <Link to="/admin/settings">Organisation settings</Link>
    : "Organisation settings (ask an admin)";

  // Chart rows: a continuous time axis for periods; otherwise the largest groups, the rest folded into "Other".
  let chartRows: Row[];
  if (isPeriod) {
    const byKey = new Map(data.rows.map((r) => [r.key, r]));
    chartRows = periodKeys(from, to, data.group_by).map((k) => {
      const r = byKey.get(k);
      return { key: k, label: periodLabel(k), long: periodLabel(k, data.group_by, true), values: { model: r?.model_cost ?? 0, labour: r?.labour_cost ?? 0 } };
    });
  } else {
    const ranked = [...data.rows].sort((a, b) => b.total_cost - a.total_cost);
    const head = ranked.length > CHART_ROWS ? ranked.slice(0, CHART_ROWS - 1) : ranked;
    const tail = ranked.slice(head.length);
    chartRows = head.map((r) => ({ key: r.key, label: r.label, values: { model: r.model_cost, labour: r.labour_cost } }));
    if (tail.length) {
      chartRows.push({
        key: "__other", label: `Other (${tail.length})`, long: `${tail.length} other groups`,
        values: { model: tail.reduce((s, r) => s + r.model_cost, 0), labour: tail.reduce((s, r) => s + r.labour_cost, 0) },
      });
    }
  }
  const chartTitle = isPeriod ? `Cost per ${data.group_by}` : `Cost by ${groupName.toLowerCase()}`;

  const showUsd = cur !== "USD";
  const columns: Column<CostRow>[] = [
    { key: "label", label: groupName, value: (r) => (isPeriod ? r.key : r.label.toLowerCase()),
      render: (r) => (isPeriod ? periodLabel(r.key, data.group_by, true) : r.label) },
    { key: "total_cost", label: "Total", numeric: true, value: (r) => r.total_cost, render: (r) => <strong>{m(r.total_cost)}</strong> },
    { key: "model_cost", label: "Model spend", numeric: true, value: (r) => r.model_cost, render: (r) => m(r.model_cost) },
    ...(showUsd ? [{ key: "model_cost_usd", label: "Model spend (USD)", numeric: true, value: (r: CostRow) => r.model_cost_usd,
      render: (r: CostRow) => money(r.model_cost_usd, "USD") }] : []),
    { key: "labour_cost", label: "Reviewer time cost", numeric: true, value: (r) => r.labour_cost, render: (r) => m(r.labour_cost) },
    { key: "input_tokens", label: "Input tokens", numeric: true, value: (r) => r.input_tokens, render: (r) => num(r.input_tokens), hint: "Includes cache reads and writes" },
    { key: "output_tokens", label: "Output tokens", numeric: true, value: (r) => r.output_tokens, render: (r) => num(r.output_tokens) },
    { key: "model_calls", label: "Model calls", numeric: true, value: (r) => r.model_calls, render: (r) => num(r.model_calls) },
    { key: "review_minutes", label: "Measured time", numeric: true, value: (r) => r.review_minutes, render: (r) => mins(r.review_minutes), hint: "Active reading time measured in the workspace" },
    { key: "estimated_minutes", label: "Estimated time", numeric: true, value: (r) => r.estimated_minutes, render: (r) => mins(r.estimated_minutes), hint: "Effort estimates for API and MCP decisions (no reading telemetry)" },
    { key: "unrated_minutes", label: "Unrated time", numeric: true, value: (r) => r.unrated_minutes, render: (r) => mins(r.unrated_minutes), hint: "Reviewer time with no hourly rate; it costs nothing here" },
    { key: "tasks", label: "Tasks", numeric: true, value: (r) => r.tasks, render: (r) => num(r.tasks) },
    { key: "submissions", label: "Submissions", numeric: true, value: (r) => r.submissions, render: (r) => num(r.submissions) },
  ];
  const footer = (
    <tr>
      {columns.map((c, i) => (
        <td key={c.key} className={c.numeric ? "rp-num" : undefined}>{i === 0 ? "Total" : c.render ? c.render(t) : null}</td>
      ))}
    </tr>
  );

  return (
    <div className={loading ? "rp-refreshing" : undefined} aria-busy={loading || undefined}>
      <ErrorBox error={error} />
      <div className="grid rp-kpis">
        <Stat label="Total cost" value={m(t.total_cost)} hint={`Model spend plus reviewer time, in ${cur}`} />
        <Stat label="Model spend" value={m(t.model_cost)}
          hint={showUsd ? `${money(t.model_cost_usd, "USD")} at USD list prices × ${data.usd_to_currency}` : `${num(t.model_calls)} model calls`} />
        <Stat label="Reviewer time cost" value={m(t.labour_cost)} hint={`${num(t.tasks)} completed review tasks`} />
        <Stat label="Cost per submission" value={data.per_submission == null ? "–" : m(data.per_submission)} hint={`${num(t.submissions)} submissions with costs`} />
        <Stat label="Tokens" value={compact(t.input_tokens + t.output_tokens)} hint={`${compact(t.input_tokens)} in · ${compact(t.output_tokens)} out`} />
        <Stat label="Review hours" value={num(reviewMinutes / 60, 1)} hint={`${mins(t.review_minutes)} measured · ${mins(t.estimated_minutes)} estimated`} />
      </div>

      {data.unpriced_models.length > 0 && (
        <div className="notice" role="note">
          <strong>Some model usage has no price, so it counts as zero.</strong>{" "}
          {data.unpriced_models.map((u) => `${u.model} (${num(u.tokens)} tokens)`).join(", ")}.{" "}
          Add prices for {data.unpriced_models.length === 1 ? "this model" : "these models"} in {settingsLink}.
        </div>
      )}
      {t.unrated_minutes > 0 && (
        <div className="notice" role="note">
          <strong>{mins(t.unrated_minutes)} of reviewer time has no hourly rate, so it costs nothing here.</strong>{" "}
          Set an hourly rate on the reviewers or their teams, or a default rate in {settingsLink}.
        </div>
      )}
      {t.estimated_minutes > 0 && (
        <div className="rp-info" role="note">
          {mins(t.estimated_minutes)} of review time is estimated: decisions made through the API or MCP have no reading telemetry, so each task's effort estimate is used instead.
        </div>
      )}

      {data.rows.length === 0 ? (
        <div className="card rp-section"><Empty>No model usage or completed reviews in this range. Try a longer range or clear the filters.</Empty></div>
      ) : (
        <>
          <div className="card rp-section">
            <ChartFrame title={chartTitle} loading={loading}
              subtitle={
                data.group_by === "team" || data.group_by === "reviewer" ? "Model spend is not a reviewer cost, so it appears as its own “Model usage” row."
                  : data.group_by === "model" ? "Reviewer time appears as its own “Reviewer time” row."
                    : !isPeriod && data.rows.length > CHART_ROWS ? `The ${CHART_ROWS - 1} largest groups; the table lists all ${data.rows.length}.` : `In ${cur}`
              }
              legend={SERIES.map((s) => ({ label: s.label, color: s.color }))}
              table={{
                columns: [groupName, "Model spend", "Reviewer time", "Total"],
                rows: chartRows.map((r) => {
                  const a = Number(r.values.model) || 0, b = Number(r.values.labour) || 0;
                  return [r.long || r.label, m(a), m(b), m(a + b)];
                }),
              }}>
              {isPeriod
                ? <ColumnChart label={chartTitle} series={SERIES} rows={chartRows} stacked format={m} axisFormat={(v) => moneyCompact(v, cur)} height={240} />
                : <BarChart label={chartTitle} series={SERIES} rows={chartRows} format={m} axisFormat={(v) => moneyCompact(v, cur)} />}
            </ChartFrame>
          </div>

          <div className="card rp-section">
            <div className="rp-card-head">
              <div>
                <h2>Breakdown</h2>
                <div className="sub rp-small">Every group, in {cur}. Select a column heading to sort.</div>
              </div>
            </div>
            <SortTable key={data.group_by} caption={`Costs by ${groupName.toLowerCase()}`} columns={columns} rows={data.rows}
              rowKey={(r) => r.key} footer={footer}
              initial={isPeriod ? { key: "label", dir: "asc" } : { key: "total_cost", dir: "desc" }} />
          </div>
        </>
      )}

      <details className="card rp-section rp-explainer">
        <summary>How costs are calculated</summary>
        <ul>
          <li><strong>Model spend</strong> is tokens × the price per million tokens for each model (input, output, cache reads and cache writes). Prices are USD list prices as of {data.prices_as_of}, or your own prices from {settingsLink}, converted to {cur}{showUsd ? ` at ${data.usd_to_currency} ${cur} per USD` : ""}. A price change applies to past periods too.</li>
          <li><strong>Reviewer time</strong> is the active reading time measured in the review workspace × an hourly rate: the reviewer's own rate, else their team's, else the organisation default{data.default_hourly_rate != null ? ` (${m(data.default_hourly_rate)} an hour)` : " (not set)"}.</li>
          <li>Decisions made through the API or MCP have no reading telemetry, so the task's effort estimate is used and shown as <em>estimated time</em>.</li>
          <li>Time with no hourly rate at any level is <em>unrated</em> and costs nothing; usage of a model with no price counts as zero spend.</li>
          <li><strong>Cost per submission</strong> is the total divided by the submissions that had model usage or a completed review in range. Model usage that isn't linked to a submission still counts toward the total.</li>
        </ul>
      </details>
    </div>
  );
}
