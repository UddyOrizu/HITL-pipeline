import { useState } from "react";
import { LiveReadout, Tooltip } from "./ChartFrame";
import { barPath, niceTicks, stepKey, textWidth, truncate, useWidth, type Row, type Series } from "./util";

const GAP = 2;
const ROW = 30;
const THICK = 16;

// Horizontal bars for named groups (ranked by the caller), stacked when there are several series.
// Category names on the left, the total at each bar's tip; hover or arrow keys show every series.
export function BarChart({ label, series, rows, format, axisFormat }: {
  label: string; series: Series[]; rows: Row[]; format: (v: number) => string; axisFormat?: (v: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const n = rows.length;
  const val = (r: Row, k: string) => Math.max(0, Number(r.values[k] ?? 0) || 0);
  const totals = rows.map((r) => series.reduce((s, se) => s + val(r, se.key), 0));
  const ticks = niceTicks(Math.max(0, ...totals), 4);
  const top = ticks[ticks.length - 1];
  const fmtAxis = axisFormat || format;

  const labelW = Math.min(Math.max(...rows.map((r) => textWidth(r.label, 12)), 40) + 16, Math.max(100, width * 0.32));
  const valueW = Math.max(...totals.map((t) => textWidth(format(t), 12)), 30) + 10;
  const ml = labelW, mr = valueW, mt = 4, mb = 22;
  const pw = Math.max(10, width - ml - mr);
  const height = mt + n * ROW + mb;
  const x = (v: number) => ml + (v / top) * pw;
  const k = series.length;

  const readout = (i: number) => {
    const r = rows[i];
    const parts = series.map((s) => `${s.label} ${format(val(r, s.key))}`);
    if (k > 1) parts.push(`total ${format(totals[i])}`);
    return `${r.long || r.label}: ${parts.join(", ")}`;
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientY - box.top - mt) / ROW);
    setActive(i >= 0 && i < n ? i : null);
  };
  const best = totals.indexOf(Math.max(...totals));
  const summary = n
    ? `${label}. ${n} ${n === 1 ? "group" : "groups"}; largest ${rows[best]?.long || rows[best]?.label} at ${format(totals[best])}.`
    : `${label}. No data.`;

  return (
    <div ref={ref} className="rp-plot" tabIndex={0} role="group"
      aria-label={`${label}. Use the up and down arrow keys to read each value.`}
      onKeyDown={(e) => {
        const next = stepKey(e, active, n, true);
        if (next !== undefined) { e.preventDefault(); setActive(next); }
      }}
      onBlur={() => setActive(null)}>
      <svg className="chart rp-svg" width={width} height={height} role="img" aria-label={summary}
        onPointerMove={onMove} onPointerLeave={() => setActive(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="rp-grid" x1={Math.round(x(t)) + 0.5} x2={Math.round(x(t)) + 0.5} y1={mt} y2={mt + n * ROW} />
            <text x={x(t)} y={height - 6} textAnchor="middle" className="rp-tick">{fmtAxis(t)}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const y0 = mt + i * ROW + (ROW - THICK) / 2;
          let acc = 0;
          const nonZero = series.filter((s) => val(r, s.key) > 0);
          return (
            <g key={r.key}>
              {active === i && <rect className="rp-hover-band" x={0} y={mt + i * ROW} width={width} height={ROW} />}
              <text x={ml - 10} y={y0 + THICK / 2} dy="0.32em" textAnchor="end" className="rp-cat">
                <title>{r.long || r.label}</title>
                {truncate(r.label, ml - 10)}
              </text>
              {series.map((s) => {
                const v = val(r, s.key);
                if (v <= 0) return null;
                const x0 = x(acc), x1 = x(acc + v);
                acc += v;
                const isEnd = s.key === nonZero[nonZero.length - 1]?.key;
                const w = x1 - x0 - (isEnd ? 0 : GAP);
                return isEnd
                  ? <path key={s.key} d={barPath(x0, y0, Math.max(1, w), THICK)} fill={s.color} />
                  : <rect key={s.key} x={x0} y={y0} width={Math.max(0, w)} height={THICK} fill={s.color} />;
              })}
              <text x={x(totals[i]) + 6} y={y0 + THICK / 2} dy="0.32em" className="rp-value">{format(totals[i])}</text>
            </g>
          );
        })}
        <line className="rp-axis" x1={ml + 0.5} x2={ml + 0.5} y1={mt} y2={mt + n * ROW} />
      </svg>
      {active != null && rows[active] && (
        <Tooltip x={x(totals[active] / 2)} y={mt + active * ROW + 2} width={width}
          title={rows[active].long || rows[active].label}
          rows={series.map((s) => ({ label: s.label, value: format(val(rows[active], s.key)), color: s.color }))}
          footer={k > 1 ? { label: "Total", value: format(totals[active]) } : undefined} />
      )}
      <LiveReadout text={active != null && rows[active] ? readout(active) : ""} />
    </div>
  );
}
