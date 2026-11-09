import { useState } from "react";
import { api, apiPage, qs } from "../../api";
import { Badge, Empty, Pager, useLoad, when } from "../ui";
import { Err, useAction } from "../admin/common";
import { fullTime, type Delivery, type Loaded, type SourceApp } from "./types";

const LIMIT = 25;
const STATUSES = ["pending", "delivered", "failed"];
const EVENTS = ["submission.received", "submission.in_review", "submission.decided", "submission.deferred", "submission.escalated", "submission.cancelled"];

function tone(status: string) {
  return status === "delivered" ? "ok" : status === "failed" ? "high" : "medium";
}

export function Deliveries({ apps }: { apps: Loaded<SourceApp[]> }) {
  const [appId, setAppId] = useState("");
  const [status, setStatus] = useState("");
  const [event, setEvent] = useState("");
  const [offset, setOffset] = useState(0);
  const page = useLoad(() => apiPage<Delivery>(`/v1/integrations/deliveries${qs({ app_id: appId, status, event, offset, limit: LIMIT })}`),
    [appId, status, event, offset]);
  const act = useAction();

  const filter = (fn: (v: string) => void) => (e: React.ChangeEvent<HTMLSelectElement>) => { fn(e.target.value); setOffset(0); };
  const filtered = !!(appId || status || event);

  async function redeliver(d: Delivery) {
    await act.run(`re:${d.id}`, () => api<Delivery>(`/v1/integrations/deliveries/${d.id}/redeliver`, { method: "POST" }),
      (r) => r.status === "delivered"
        ? `Redelivered ${d.event} to ${d.source_app ?? "the app"}: accepted${r.last_status_code ? ` (${r.last_status_code})` : ""}.`
        : `Redelivery of ${d.event} did not succeed (${r.last_status_code ?? r.last_error ?? r.status}); it will be retried.`);
    page.reload();
  }

  return (
    <>
      <div className="section-head">
        <div>
          <h2>Webhook delivery log</h2>
          <div className="sub">Every event posted to your apps' webhook URLs. Failed deliveries are retried with back-off; redeliver to send one again now.</div>
        </div>
        <button className="btn small" onClick={page.reload} disabled={page.loading}>Refresh</button>
      </div>

      <div className="toolbar">
        <div>
          <label htmlFor="dl-app">App</label>
          <select id="dl-app" value={appId} onChange={filter(setAppId)}>
            <option value="">All apps</option>
            {apps.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="dl-status">Status</label>
          <select id="dl-status" value={status} onChange={filter(setStatus)}>
            <option value="">Any status</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="dl-event">Event</label>
          <select id="dl-event" value={event} onChange={filter(setEvent)}>
            <option value="">Any event</option>
            {EVENTS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        {filtered && <button className="btn small" onClick={() => { setAppId(""); setStatus(""); setEvent(""); setOffset(0); }}>Clear filters</button>}
      </div>

      <Err error={apps.error} />
      <Err error={page.error} />
      <Err error={act.error} />
      {act.ok && <div className="success">{act.ok}</div>}

      <div className="card flush">
        {page.loading && !page.data ? <div className="sub pad">Loading…</div>
          : !page.data?.items.length ? <Empty>{page.error ? "The delivery log could not be loaded." : filtered ? "No deliveries match these filters." : "No deliveries yet."}</Empty>
          : (
            <div className="table-scroll">
              <table className={page.loading ? "loading" : undefined}>
                <thead><tr><th>Event</th><th>App</th><th>Submission</th><th>Status</th><th>Attempts</th><th>Last response</th><th>Created</th><th /></tr></thead>
                <tbody>
                  {page.data.items.map((d) => (
                    <tr key={d.id}>
                      <td className="mono">{d.event}</td>
                      <td>{d.source_app ?? <span className="mono sub">{d.source_app_id.slice(0, 8)}</span>}</td>
                      <td className="mono" title={d.submission_id ?? undefined}>{d.submission_id ? d.submission_id.slice(0, 8) : "–"}</td>
                      <td>
                        <Badge tone={tone(d.status)}>{d.status}</Badge>
                        {d.status === "pending" && d.next_attempt_at && <div className="sub small-text">next try {when(d.next_attempt_at)}</div>}
                        {d.delivered_at && <div className="sub small-text" title={fullTime(d.delivered_at)}>at {when(d.delivered_at)}</div>}
                      </td>
                      <td>{d.attempts}</td>
                      <td>
                        {d.last_status_code ? <span className={d.last_status_code >= 400 ? "text-high" : undefined}>HTTP {d.last_status_code}</span>
                          : d.last_error ? "no response" : "–"}
                        {d.last_error && <div className="sub small-text" title={d.last_error}>{d.last_error.length > 90 ? `${d.last_error.slice(0, 90)}…` : d.last_error}</div>}
                      </td>
                      <td title={fullTime(d.created_at)}>{when(d.created_at)}</td>
                      <td>
                        <button className="btn small" disabled={act.busy === `re:${d.id}`} onClick={() => redeliver(d)}
                          title={d.status === "delivered" ? "Send this event again (receivers should ignore duplicates)" : "Try this delivery again now"}>
                          {act.busy === `re:${d.id}` ? "Sending…" : "Redeliver"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      {page.data && <Pager offset={offset} limit={LIMIT} total={page.data.total} onChange={setOffset} />}
    </>
  );
}
