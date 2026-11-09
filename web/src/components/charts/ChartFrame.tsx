import { useId, useState, type ReactNode } from "react";

export type LegendItem = { label: string; color: string; shape?: "rect" | "line" };

export type TableData = { columns: string[]; rows: (string | number)[][]; numeric?: boolean[] };

// A chart card: heading, legend (two or more series only), the plot, and a table view of the same numbers.
// The table is the accessible fallback: every value in a chart can be read there without hovering.
export function ChartFrame({ title, subtitle, legend, table, loading, empty, children }: {
  title: string; subtitle?: ReactNode; legend?: LegendItem[]; table: TableData; loading?: boolean;
  empty?: ReactNode; children: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  const id = useId();
  return (
    <figure className={`rp-figure${loading ? " is-loading" : ""}`} aria-labelledby={`${id}-t`} aria-busy={loading || undefined}>
      <div className="rp-figure-head">
        <div>
          <h3 id={`${id}-t`}>{title}</h3>
          {subtitle && <div className="sub rp-small">{subtitle}</div>}
        </div>
        {!empty && (
          <button type="button" className="btn small" aria-pressed={showTable} aria-controls={`${id}-b`}
            onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Show chart" : "Show table"}
          </button>
        )}
      </div>
      <div id={`${id}-b`}>
        {empty ? <div className="rp-chart-empty">{empty}</div> : showTable ? <DataTable caption={title} data={table} /> : (
          <>
            {legend && legend.length > 1 && <Legend items={legend} />}
            {children}
          </>
        )}
      </div>
    </figure>
  );
}

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="rp-legend" aria-label="Legend">
      {items.map((it) => (
        <li key={it.label}>
          <span className={it.shape === "line" ? "rp-key-line" : "rp-swatch"} style={{ background: it.color }} aria-hidden="true" />
          {it.label}
        </li>
      ))}
    </ul>
  );
}

export function DataTable({ caption, data }: { caption: string; data: TableData }) {
  return (
    <div className="rp-scroll">
      <table className="rp-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>{data.columns.map((c, i) => <th key={c} scope="col" className={i && data.numeric?.[i] !== false ? "rp-num" : undefined}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {data.rows.map((r, ri) => (
            <tr key={ri}>
              {r.map((v, i) => i === 0
                ? <th key={i} scope="row" className="rp-row-head">{v}</th>
                : <td key={i} className={data.numeric?.[i] !== false ? "rp-num" : undefined}>{v}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Hover/focus readout. Values lead (strong), series names follow; each row keyed with a short line in the series colour.
export function Tooltip({ x, y, width, title, rows, footer }: {
  x: number; y: number; width: number; title: string;
  rows: { label: string; value: string; color?: string }[]; footer?: { label: string; value: string };
}) {
  const half = 90;
  const left = Math.max(half, Math.min(width - half, x));
  return (
    <div className="rp-tip" style={{ left, top: y }} aria-hidden="true">
      <div className="rp-tip-title">{title}</div>
      {rows.map((r) => (
        <div className="rp-tip-row" key={r.label}>
          {r.color && <span className="rp-tip-key" style={{ background: r.color }} />}
          <strong>{r.value}</strong>
          <span className="sub">{r.label}</span>
        </div>
      ))}
      {footer && (
        <div className="rp-tip-row rp-tip-foot">
          <strong>{footer.value}</strong>
          <span className="sub">{footer.label}</span>
        </div>
      )}
    </div>
  );
}

// Text for screen readers when arrow keys move through a chart.
export function LiveReadout({ text }: { text: string }) {
  return <div className="sr-only" aria-live="polite">{text}</div>;
}
