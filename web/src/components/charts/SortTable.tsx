import { useMemo, useState, type ReactNode } from "react";

export type Column<T> = {
  key: string;
  label: string;
  // Sort value. null/undefined always sorts last.
  value: (row: T) => number | string | null | undefined;
  render?: (row: T) => ReactNode;
  numeric?: boolean;
  hint?: string; // shown as the header's tooltip
};

// A table whose column headers sort it (click again to reverse). Sorting is announced with aria-sort.
export function SortTable<T>({ caption, columns, rows, rowKey, initial, footer, empty }: {
  caption: string; columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string;
  initial?: { key: string; dir: "asc" | "desc" } | null; footer?: ReactNode; empty?: ReactNode;
}) {
  const [sort, setSort] = useState(initial ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const m = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = col.value(a), vb = col.value(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * m;
      return String(va).localeCompare(String(vb)) * m;
    });
  }, [rows, columns, sort]);

  const toggle = (c: Column<T>) =>
    setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === "asc" ? "desc" : "asc" } : { key: c.key, dir: c.numeric ? "desc" : "asc" }));

  return (
    <div className="rp-scroll">
      <table className="rp-table">
        <caption className="sr-only">{caption}. Select a column heading to sort.</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const on = sort?.key === c.key;
              return (
                <th key={c.key} scope="col" className={c.numeric ? "rp-num" : undefined} title={c.hint}
                  aria-sort={on ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="rp-sort" onClick={() => toggle(c)}>
                    {c.label}
                    <span className="rp-sort-mark" aria-hidden="true">{on ? (sort!.dir === "asc" ? "▲" : "▼") : ""}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && empty && (
            <tr><td colSpan={columns.length} className="sub" style={{ textAlign: "center", padding: 24 }}>{empty}</td></tr>
          )}
          {sorted.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) => (
                <td key={c.key} className={c.numeric ? "rp-num" : undefined}>
                  {c.render ? c.render(r) : (c.value(r) ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot>{footer}</tfoot>}
      </table>
    </div>
  );
}
