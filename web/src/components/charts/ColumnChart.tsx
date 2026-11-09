import { useState } from "react";
import { LiveReadout, Tooltip } from "./ChartFrame";
import { columnPath, niceTicks, stepKey, textWidth, useWidth, type Row, type Series } from "./util";

const GAP = 2; // surface gap between adjacent bars and stacked segments

// Vertical columns over categories (usually periods): grouped side by side, or stacked.
// One y-axis only. Hover or arrow keys show every series for that category.
export function ColumnChart({ label, series, rows, stacked = false, format, axisFormat, integer = false, height = 220 }: {
  label: string; series: Series[]; rows: Row[]; stacked?: boolean; format: (v: number) => string;
  axisFormat?: (v: number) => string; integer?: boolean; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const n = rows.length;
  const val = (r: Row, k: string) => Number(r.values[k] ?? 0) || 0;
  const totals = rows.map((r) => series.reduce((s, se) => s + val(r, se.key), 0));
  const max = stacked ? Math.max(0, ...totals) : Math.max(0, ...rows.flatMap((r) => series.map((s) => val(r, s.key))));
  const ticks = niceTicks(max, 4, integer);
  const top = ticks[ticks.length - 1];
  const fmtAxis = axisFormat || format;

  const ml = Math.max(...ticks.map((t) => textWidth(fmtAxis(t)))) + 12;
  const mr = 8, mt = 10, mb = 26;
  const pw = Math.max(10, width - ml - mr), ph = height - mt - mb;
  const band = pw / Math.max(1, n);
  const y = (v: number) => mt + ph - (v / top) * ph;
  const k = series.length;
  const gap = band > 16 ? GAP : 1;
  const groupW = Math.min(stacked ? 24 : 24 * k + gap * (k - 1), band * 0.72);
  const barW = stacked ? groupW : Math.max(1, (groupW - gap * (k - 1)) / k);
  const every = Math.max(1, Math.ceil((n * 64) / pw));

  const readout = (i: number) => {
    const r = rows[i];
    const parts = series.map((s) => `${s.label} ${format(val(r, s.key))}`);
    if (stacked && k > 1) parts.push(`total ${format(totals[i])}`);
    return `${r.long || r.label}: ${parts.join(", ")}`;
  };

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left - ml) / band);
    setActive(i >= 0 && i < n ? i : null);
  };

  const summary = `${label}. ${n} ${n === 1 ? "category" : "categories"}; highest ${stacked ? "total" : "value"} ${format(max)}. Use the table view for every value.`;

  return (
    <div ref={ref} className="rp-plot" tabIndex={0} role="group"
      aria-label={`${label}. Use the left and right arrow keys to read each value.`}
      onKeyDown={(e) => {
        const next = stepKey(e, active, n);
        if (next !== undefined) { e.preventDefault(); setActive(next); }
      }}
      onBlur={() => setActive(null)}>
      <svg className="chart rp-svg" width={width} height={height} role="img" aria-label={summary}
        onPointerMove={onMove} onPointerLeave={() => setActive(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="rp-grid" x1={ml} x2={ml + pw} y1={Math.round(y(t)) + 0.5} y2={Math.round(y(t)) + 0.5} />
            <text x={ml - 8} y={y(t)} dy="0.32em" textAnchor="end" className="rp-tick">{fmtAxis(t)}</text>
          </g>
        ))}
        {active != null && <rect className="rp-hover-band" x={ml + active * band} y={mt} width={band} height={ph} />}
        {rows.map((r, i) => {
          const cx = ml + i * band + band / 2;
          if (stacked) {
            let acc = 0;
            const nonZero = series.filter((s) => val(r, s.key) > 0);
            return (
              <g key={r.key}>
                {series.map((s) => {
                  const v = val(r, s.key);
                  if (v <= 0) return null;
                  const y0 = y(acc), y1 = y(acc + v);
                  acc += v;
                  const isTop = s.key === nonZero[nonZero.length - 1]?.key;
                  const h = y0 - y1 - (isTop ? 0 : gap);
                  return isTop
                    ? <path key={s.key} d={columnPath(cx - barW / 2, y1, barW, h)} fill={s.color} />
                    : <rect key={s.key} x={cx - barW / 2} y={y1 + gap} width={barW} height={Math.max(0, h)} fill={s.color} />;
                })}
              </g>
            );
          }
          return (
            <g key={r.key}>
              {series.map((s, si) => {
                const v = val(r, s.key);
                const x = cx - groupW / 2 + si * (barW + gap);
                return <path key={s.key} d={columnPath(x, y(v), barW, y(0) - y(v))} fill={s.color} />;
              })}
            </g>
          );
        })}
        <line className="rp-axis" x1={ml} x2={ml + pw} y1={mt + ph + 0.5} y2={mt + ph + 0.5} />
        {rows.map((r, i) => (i % every === 0 ? (
          <text key={r.key} x={ml + i * band + band / 2} y={height - 8} textAnchor="middle" className="rp-tick">{r.label}</text>
        ) : null))}
      </svg>
      {active != null && rows[active] && (
        <Tooltip x={ml + active * band + band / 2} y={stacked ? y(totals[active]) : y(Math.max(...series.map((s) => val(rows[active], s.key))))}
          width={width} title={rows[active].long || rows[active].label}
          rows={series.map((s) => ({ label: s.label, value: format(val(rows[active], s.key)), color: s.color }))}
          footer={stacked && k > 1 ? { label: "Total", value: format(totals[active]) } : undefined} />
      )}
      <LiveReadout text={active != null && rows[active] ? readout(active) : ""} />
    </div>
  );
}
