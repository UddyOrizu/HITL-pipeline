import { useState } from "react";
import { LiveReadout, Tooltip } from "./ChartFrame";
import { niceTicks, stepKey, textWidth, useWidth, type Row } from "./util";

// One series over periods. Gaps (null) break the line; isolated points get a dot. A crosshair snaps to the
// nearest period on hover; arrow keys do the same from the keyboard. Points sit at band centres so the
// x positions line up with a ColumnChart over the same periods.
export function LineChart({ label, rows, valueKey, seriesLabel, color, format, axisFormat, height = 220 }: {
  label: string; rows: Row[]; valueKey: string; seriesLabel: string; color: string; format: (v: number) => string;
  axisFormat?: (v: number) => string; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const n = rows.length;
  const vals = rows.map((r) => (r.values[valueKey] == null ? null : Number(r.values[valueKey])));
  const present = vals.filter((v): v is number => v != null);
  const ticks = niceTicks(Math.max(0, ...present), 4);
  const top = ticks[ticks.length - 1];
  const fmtAxis = axisFormat || format;

  const ml = Math.max(...ticks.map((t) => textWidth(fmtAxis(t)))) + 12;
  const mr = 8, mt = 18, mb = 26;
  const pw = Math.max(10, width - ml - mr), ph = height - mt - mb;
  const band = pw / Math.max(1, n);
  const x = (i: number) => ml + i * band + band / 2;
  const y = (v: number) => mt + ph - (v / top) * ph;
  const every = Math.max(1, Math.ceil((n * 64) / pw));

  // Path segments between gaps.
  let d = "";
  vals.forEach((v, i) => {
    if (v == null) return;
    d += `${i > 0 && vals[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  const isolated = vals.map((v, i) => v != null && vals[i - 1] == null && vals[i + 1] == null);
  let last = -1;
  vals.forEach((v, i) => { if (v != null) last = i; });

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left - ml) / band);
    setActive(i >= 0 && i < n ? i : null);
  };
  const shown = (i: number) => (vals[i] == null ? "no decisions" : format(vals[i] as number));
  const summary = present.length
    ? `${label}. ${present.length} of ${n} periods have a value; lowest ${format(Math.min(...present))}, highest ${format(Math.max(...present))}.`
    : `${label}. No values in this range.`;

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
        <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {vals.map((v, i) => (v != null && (isolated[i] || i === last) ? (
          <circle key={i} cx={x(i)} cy={y(v)} r={4} fill={color} className="rp-ring" />
        ) : null))}
        {last >= 0 && active == null && (
          <text x={Math.min(x(last), ml + pw)} y={y(vals[last] as number) - 9} textAnchor={x(last) > ml + pw - 40 ? "end" : "middle"}
            className="rp-value">{format(vals[last] as number)}</text>
        )}
        {active != null && (
          <g>
            <line className="rp-crosshair" x1={x(active)} x2={x(active)} y1={mt} y2={mt + ph} />
            {vals[active] != null && <circle cx={x(active)} cy={y(vals[active] as number)} r={4} fill={color} className="rp-ring" />}
          </g>
        )}
        <line className="rp-axis" x1={ml} x2={ml + pw} y1={mt + ph + 0.5} y2={mt + ph + 0.5} />
        {rows.map((r, i) => (i % every === 0 ? (
          <text key={r.key} x={x(i)} y={height - 8} textAnchor="middle" className="rp-tick">{r.label}</text>
        ) : null))}
      </svg>
      {active != null && rows[active] && (
        <Tooltip x={x(active)} y={vals[active] == null ? mt + ph / 2 : y(vals[active] as number)} width={width}
          title={rows[active].long || rows[active].label}
          rows={[{ label: seriesLabel, value: shown(active), color }]} />
      )}
      <LiveReadout text={active != null && rows[active] ? `${rows[active].long || rows[active].label}: ${seriesLabel} ${shown(active)}` : ""} />
    </div>
  );
}
