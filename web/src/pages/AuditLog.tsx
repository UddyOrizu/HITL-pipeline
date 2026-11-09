import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, apiPage, qs } from "../api";
import { Badge, Empty, ErrorBox, isoDate, num, useDebounced, useLoad, when } from "../components/ui";
import "./oversight.css";

type Event = {
  seq: number; at: string; actor: string; action: string; entity: string; entity_id: string; payload: unknown;
  prev_hash: string; hash: string;
};
type Chain = { valid: boolean; count: number; broken_at: number | null; head?: string };
type Filters = { action: string; entity: string; actor: string; entity_id: string; since: string; until: string };

const LIMIT = 50;
const NO_FILTERS: Filters = { action: "", entity: "", actor: "", entity_id: "", since: "", until: "" };

// Date inputs are local days; the API takes ISO datetimes. "until" is inclusive of the chosen day.
function dayStart(d: string, plusDays = 0): string | undefined {
  if (!d) return undefined;
  const [y, m, day] = d.split("-").map(Number);
  const dt = new Date(y, m - 1, day + plusDays);
  return Number.isNaN(dt.getTime()) ? undefined : dt.toISOString();
}

function query(f: Filters, extra: Record<string, string | number | undefined> = {}) {
  return qs({
    order: "desc", limit: LIMIT, action: f.action, entity: f.entity.trim(), actor: f.actor.trim(), entity_id: f.entity_id.trim(),
    since: dayStart(f.since), until: dayStart(f.until, 1), ...extra,
  });
}

function saveJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function payloadPreview(p: unknown): string {
  if (p == null) return "";
  if (typeof p !== "object") return String(p);
  const entries = Object.entries(p as Record<string, unknown>);
  if (!entries.length) return "";
  const s = entries.slice(0, 3).map(([k, v]) => `${k}: ${typeof v === "object" && v !== null ? "{…}" : String(v)}`).join(" · ");
  return entries.length > 3 ? `${s} · +${entries.length - 3} more` : s;
}

export default function AuditLog() {
  const [f, setF] = useState<Filters>(NO_FILTERS);
  // Typed filters wait for a pause in typing; selects and dates apply straight away.
  const entity = useDebounced(f.entity, 350);
  const actor = useDebounced(f.actor, 350);
  const entityId = useDebounced(f.entity_id, 350);
  const applied: Filters = { ...f, entity, actor, entity_id: entityId };
  const key = query(applied);

  const [events, setEvents] = useState<Event[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [older, setOlder] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const req = useRef(0);

  const chain = useLoad(() => api<Chain>("/v1/audit/verify"));
  const { data: actions } = useLoad(() => api<string[]>("/v1/audit/actions").catch(() => [] as string[]));

  async function loadFirst() {
    const id = ++req.current;
    setLoading(true);
    setError(null);
    try {
      const page = await apiPage<Event>(`/v1/audit/events${key}`);
      if (id !== req.current) return;
      setEvents(page.items);
      setTotal(page.total);
      setExpanded(new Set());
    } catch (e) {
      if (id === req.current) setError(e);
    } finally {
      if (id === req.current) setLoading(false);
    }
  }

  useEffect(() => {
    loadFirst();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  async function loadOlder() {
    const last = events[events.length - 1];
    if (!last) return;
    const id = req.current;
    setOlder(true);
    setError(null);
    try {
      const page = await apiPage<Event>(`/v1/audit/events${query(applied, { before_seq: last.seq })}`);
      if (id !== req.current) return; // filters changed meanwhile
      setEvents((cur) => [...cur, ...page.items.filter((e) => e.seq < (cur[cur.length - 1]?.seq ?? Infinity))]);
    } catch (e) {
      setError(e);
    } finally {
      setOlder(false);
    }
  }

  // Actions grouped by their prefix ("user.created" -> "user."), so a whole kind can be picked at once.
  const groups = useMemo(() => {
    const m = new Map<string, string[]>();
    (actions || []).forEach((a) => {
      const i = a.indexOf(".");
      const prefix = i > 0 ? a.slice(0, i + 1) : "";
      m.set(prefix, [...(m.get(prefix) || []), a]);
    });
    return Array.from(m.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [actions]);
  const entities = useMemo(() => {
    const s = new Set<string>(groups.map(([p]) => p.replace(/\.$/, "")).filter(Boolean));
    events.forEach((e) => e.entity && s.add(e.entity));
    return Array.from(s).sort();
  }, [groups, events]);

  const set = (k: keyof Filters) => (e: { target: { value: string } }) => setF((cur) => ({ ...cur, [k]: e.target.value }));
  const filtered = Object.values(applied).some(Boolean);
  const hasMore = total != null && events.length < total;

  function toggle(seq: number) {
    setExpanded((cur) => {
      const next = new Set(cur);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });
  }

  function exportJson() {
    const filters = Object.fromEntries(Object.entries(applied).filter(([, v]) => v));
    saveJson({
      exported_at: new Date().toISOString(),
      filters,
      total_matching: total,
      exported: events.length,
      chain_verification: chain.data,
      events,
    }, `audit-events-${isoDate(new Date())}.json`);
  }

  const c = chain.data;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <div className="sub">Every action in the organisation, newest first. Each event is hash-chained to the one before it.</div>
        </div>
        <div className="btn-row">
          <button className="btn" onClick={() => { chain.reload(); loadFirst(); }}>Refresh</button>
          <button className="btn" onClick={exportJson} disabled={!events.length}>Export loaded events (JSON)</button>
        </div>
      </div>

      {chain.loading && !c ? <div className="notice">Verifying the audit chain…</div>
        : chain.error ? <ErrorBox error={chain.error} />
        : c && (
          <div className={`ov-banner ${c.valid ? "success" : "error"}`} role={c.valid ? "status" : "alert"}>
            <div>
              {c.valid
                ? <><strong>Audit chain verified.</strong> All {num(c.count)} events recompute to their stored hashes.</>
                : <><strong>Audit chain broken at event #{c.broken_at}.</strong> That event, or one before it, was changed or removed after it was written ({num(c.count)} events in the chain).</>}
              {c.head && <div className="mono" style={{ fontSize: 11, wordBreak: "break-all", opacity: 0.8 }}>Head {c.head}</div>}
            </div>
          </div>
        )}

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="ov-filters">
          <div>
            <label htmlFor="al-action">Action</label>
            <select id="al-action" value={f.action} onChange={set("action")}>
              <option value="">All actions</option>
              {groups.some(([p]) => p) && (
                <optgroup label="Every action of a kind">
                  {groups.filter(([p]) => p).map(([p, list]) => <option key={p} value={p}>{p}* ({list.length})</option>)}
                </optgroup>
              )}
              {groups.map(([p, list]) => (
                <optgroup key={p || "-"} label={p ? p.slice(0, -1) : "other"}>
                  {list.map((a) => <option key={a} value={a}>{a}</option>)}
                </optgroup>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="al-entity">Entity</label>
            <input id="al-entity" list="al-entities" value={f.entity} onChange={set("entity")} placeholder="e.g. submission" />
            <datalist id="al-entities">{entities.map((e) => <option key={e} value={e} />)}</datalist>
          </div>
          <div>
            <label htmlFor="al-entity-id">Entity id</label>
            <input id="al-entity-id" value={f.entity_id} onChange={set("entity_id")} placeholder="Exact id" />
          </div>
          <div>
            <label htmlFor="al-actor">Actor</label>
            <input id="al-actor" value={f.actor} onChange={set("actor")} placeholder="e.g. user:… or system:workflow" />
          </div>
          <div>
            <label htmlFor="al-since">From</label>
            <input id="al-since" type="date" value={f.since} max={f.until || undefined} onChange={set("since")} />
          </div>
          <div>
            <label htmlFor="al-until">To (inclusive)</label>
            <input id="al-until" type="date" value={f.until} min={f.since || undefined} onChange={set("until")} />
          </div>
        </div>
        <div className="btn-row" style={{ alignItems: "center", justifyContent: "space-between" }}>
          <span className="sub">
            {total == null ? "…" : `${num(total)} event${total === 1 ? "" : "s"}${filtered ? " match" : " in the log"}`}
            {total != null && total > 0 ? ` · showing ${num(events.length)}` : ""}
          </span>
          {filtered && <button className="btn small" onClick={() => setF(NO_FILTERS)}>Clear filters</button>}
        </div>
      </div>

      <ErrorBox error={error} />
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {loading && !events.length ? <Empty>Loading…</Empty> : !events.length ? <Empty>{filtered ? "No events match these filters." : "No events yet."}</Empty> : (
          <table>
            <thead><tr><th>#</th><th>When</th><th>Action</th><th>Entity</th><th>Actor</th><th>Details</th></tr></thead>
            <tbody style={loading ? { opacity: 0.6 } : undefined}>
              {events.map((e) => {
                const isOpen = expanded.has(e.seq);
                const broken = c && !c.valid && c.broken_at === e.seq;
                return (
                  <Fragment key={e.seq}>
                    <tr className="clickable ov-event" onClick={() => toggle(e.seq)} aria-expanded={isOpen}
                      style={broken ? { boxShadow: "inset 3px 0 0 var(--high)" } : undefined}>
                      <td className="mono">{e.seq}{broken && <> <Badge tone="high">broken</Badge></>}</td>
                      <td title={e.at} style={{ whiteSpace: "nowrap" }}>{when(e.at)}</td>
                      <td><span className="mono">{e.action}</span></td>
                      <td>
                        <div>{e.entity}</div>
                        {e.entity_id && (
                          <button className="btn small mono" style={{ padding: "0 6px", marginTop: 2 }} title="Show only this entity's events"
                            onClick={(ev) => { ev.stopPropagation(); setF((cur) => ({ ...cur, entity: e.entity, entity_id: e.entity_id })); }}>
                            {e.entity_id.length > 14 ? `${e.entity_id.slice(0, 12)}…` : e.entity_id}
                          </button>
                        )}
                      </td>
                      <td className="mono">{e.actor}</td>
                      <td className="sub" style={{ fontSize: 12 }}>
                        <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span> {payloadPreview(e.payload) || "No payload"}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={6}>
                          <pre className="ov-json">{JSON.stringify(e.payload ?? null, null, 2)}</pre>
                          <div className="ov-hash mono">
                            {e.at} · entity id {e.entity_id || "–"}<br />
                            prev {e.prev_hash}<br />
                            hash {e.hash}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {hasMore && (
        <div className="pager">
          <button className="btn" onClick={loadOlder} disabled={older || loading}>{older ? "Loading…" : `Load older (${num(total! - events.length)} more)`}</button>
        </div>
      )}
    </>
  );
}
