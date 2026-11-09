import { Fragment, useMemo, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Badge, Empty, ErrorBox, num, pct, ROLE_INFO, RoleBadge, Stat, useLoad, when } from "../components/ui";
import "./oversight.css";

type Stats = { open_tasks: number; completed_30d: number; minutes_today: number; catch_rate: number | null; canaries_scored: number };
type User = {
  id: string; email: string; name: string; roles: string[]; kind: "admin" | "reviewer" | "staff" | "none";
  team_ids: string[]; active: boolean; last_login_at: string | null; created_at: string | null; sso_linked: boolean;
  password_set: boolean; locked: boolean; stats: Stats | null; hourly_rate: number | null; erased: boolean;
};
type Team = { id: string; name: string; daily_ceiling_minutes: number | null; hourly_rate: number | null; members: number; reviewers: number };
type Moved = { task_id: string; submission_id: string; new_reviewer_id: string | null; status: string };

const FILTERS = [
  { key: "all", name: "All" },
  { key: "admin", name: "Admins" },
  { key: "reviewer", name: "Reviewers" },
  { key: "staff", name: "Other staff" },
  { key: "inactive", name: "Deactivated" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

const EXTRA_ROLES = ["submitter", "oversight_lead", "auditor", "integration_owner"];

// Empty rate input means "not set" (null), never 0.
function rateOrNull(v: string): number | null {
  return v.trim() === "" || !Number.isFinite(Number(v)) ? null : Number(v);
}

function rateText(v: number | null | undefined) {
  // Rates are in the organisation's currency (set in organisation settings), so no currency symbol here.
  return v == null ? "" : `${num(v, 2)}/h`;
}

function movedSummary(moved: Moved[]) {
  if (!moved.length) return "";
  const ok = moved.filter((m) => m.new_reviewer_id).length;
  const waiting = moved.length - ok;
  return `${moved.length} open task${moved.length === 1 ? "" : "s"} handed over: ${ok} reassigned` +
    (waiting ? `, ${waiting} waiting for a free reviewer` : "") + ".";
}

function UserEditor({ user, teams, selfId, canManage, onClose, onSaved }: {
  user: User | null; teams: Team[]; selfId: string; canManage: boolean; onClose: () => void; onSaved: (msg: string) => void;
}) {
  const isNew = !user;
  const [email, setEmail] = useState(user?.email || "");
  const [name, setName] = useState(user?.name || "");
  const primary0 = user?.roles.includes("admin") ? "admin" : user?.roles.includes("reviewer") || isNew ? "reviewer" : "none";
  const [primary, setPrimary] = useState<"admin" | "reviewer" | "none">(primary0);
  const [extra, setExtra] = useState<string[]>(user?.roles.filter((r) => EXTRA_ROLES.includes(r)) || []);
  const [teamIds, setTeamIds] = useState<string[]>(user?.team_ids || (teams.length === 1 ? [teams[0].id] : []));
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const [password, setPassword] = useState("");
  const [pwMsg, setPwMsg] = useState("");
  const [rate, setRate] = useState(user?.hourly_rate != null ? String(user.hourly_rate) : "");
  const [confirmErase, setConfirmErase] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const isSelf = user?.id === selfId;
  // Oversight leads can look but not change anything; erased accounts can't be changed by anyone.
  const readOnly = !canManage || !!user?.erased;
  const teamHint = teamIds.map((id) => teams.find((t) => t.id === id)).find((t) => t?.hourly_rate != null);

  async function signOutEverywhere() {
    setErr(null);
    setBusy(true);
    try {
      await api(`/v1/admin/users/${user!.id}/sign-out`, { method: "POST" });
      setConfirmSignOut(false);
      setPwMsg(`Signed ${user!.email} out of every session. They can sign in again straight away.`);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function erase() {
    setErr(null);
    setBusy(true);
    try {
      await api(`/v1/admin/users/${user!.id}/erase`, { method: "POST" });
      onSaved(`Erased the personal data of ${user!.name || user!.email}. Their decisions stay in the record under an anonymised account.`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  }

  async function setUserPassword() {
    setErr(null);
    setPwMsg("");
    setBusy(true);
    try {
      await api(`/v1/admin/users/${user!.id}/password`, { method: "PUT", body: { password } });
      setPassword("");
      setPwMsg(`Password ${user!.password_set ? "reset" : "set"}. Give it to ${user!.email} securely; their other sessions were signed out.`);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const roles = [...(primary === "none" ? [] : [primary]), ...(primary === "admin" ? [] : extra)];
  const lostReviewer = !!user?.roles.includes("reviewer") && primary !== "reviewer";
  const openTasks = user?.stats?.open_tasks || 0;
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  async function save(active?: boolean) {
    setErr(null);
    setBusy(true);
    try {
      if (isNew) {
        await api("/v1/admin/users", { body: { email, name, roles, team_ids: teamIds, password: password || null, hourly_rate: rateOrNull(rate) } });
        const how = password ? "with this email and the password you set" : "with single sign-on";
        onSaved(`Added ${email}. They can sign in ${how}${primary === "reviewer" ? " and will start receiving review work" : ""}.`);
      } else {
        const body: any = active === undefined ? { name, roles, team_ids: teamIds, hourly_rate: rateOrNull(rate) } : { active };
        const r = await api<User & { reassigned: Moved[] }>(`/v1/admin/users/${user!.id}`, { method: "PUT", body });
        const verb = active === false ? "Deactivated" : active === true ? "Reactivated" : "Saved";
        onSaved(`${verb} ${r.email}. ${movedSummary(r.reassigned)}`.trim());
      }
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  }

  const title = isNew ? "Add user" : readOnly ? (user!.name || user!.email) : `Edit ${user!.name || user!.email}`;

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}
        style={{ maxWidth: 620, maxHeight: "calc(100vh - 32px)", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <div className="ov-badges">
            {user?.erased && <Badge tone="high">erased</Badge>}
            {user && !user.active && !user.erased && <Badge>Deactivated</Badge>}
            {readOnly && <Badge>read-only</Badge>}
          </div>
        </div>
        {user?.erased && (
          <div className="notice">This user's personal data was erased. Their decisions stay in the record under this anonymised account, which can't be changed.</div>
        )}
        {!canManage && !user?.erased && <p className="sub" style={{ fontSize: 13 }}>Only admins can change users. You can see their access, teams and workload.</p>}

        {/* A disabled fieldset makes every control inside read-only in one go. */}
        <fieldset disabled={readOnly} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="field-row">
            <div>
              <label htmlFor="ue-email">Email (used to sign in)</label>
              <input id="ue-email" value={email} disabled={!isNew} onChange={(e) => setEmail(e.target.value)} placeholder="name@firm.co.uk" />
            </div>
            <div>
              <label htmlFor="ue-name">Name</label>
              <input id="ue-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
          </div>

          <label>Account type</label>
          <div className="account-types">
            {(["reviewer", "admin", "none"] as const).map((k) => (
              <button key={k} type="button" className={`account-type ${primary === k ? "on" : ""}`}
                disabled={isSelf && k !== "admin" && primary0 === "admin"}
                onClick={() => setPrimary(k)}>
                <strong>{k === "none" ? "Other staff" : ROLE_INFO[k].name}</strong>
                <span>{k === "none" ? "No review work and no admin rights. Pick the access they need below." : ROLE_INFO[k].blurb}</span>
              </button>
            ))}
          </div>
          {!readOnly && isSelf && primary0 === "admin" && <p className="sub" style={{ fontSize: 12 }}>You can't remove your own admin access.</p>}

          {primary !== "admin" && (
            <>
              <label>Additional access</label>
              {EXTRA_ROLES.map((r) => (
                <label key={r} className="check">
                  <input type="checkbox" checked={extra.includes(r)} onChange={() => setExtra(toggle(extra, r))} />
                  <span><strong>{ROLE_INFO[r].name}</strong> <span className="sub">{ROLE_INFO[r].blurb}</span></span>
                </label>
              ))}
            </>
          )}
          {primary === "admin" && (
            <p className="sub" style={{ fontSize: 13 }}>
              Admins already have oversight, audit, integration and submit access. They are never assigned review work: an admin sets the
              canary rates, caps and thresholds, so they shouldn't also be measured by them.
            </p>
          )}

          <label>Teams</label>
          {!teams.length ? <p className="sub">No teams yet.</p> : (
            <div className="pill-list" style={{ marginTop: 0 }}>
              {teams.map((t) => (
                <button key={t.id} type="button" className={teamIds.includes(t.id) ? "on" : ""} onClick={() => setTeamIds(toggle(teamIds, t.id))}>
                  {teamIds.includes(t.id) ? "✓ " : ""}{t.name}
                </button>
              ))}
            </div>
          )}

          <label htmlFor="ue-rate">Hourly rate (optional, in the organisation's currency, for cost reports)</label>
          <input id="ue-rate" type="number" min={0} step="0.01" value={rate} onChange={(e) => setRate(e.target.value)}
            placeholder={teamHint ? `Team rate ${rateText(teamHint.hourly_rate)}` : "Organisation rate"} style={{ maxWidth: 220 }} />
          <p className="sub" style={{ fontSize: 12 }}>Leave empty to use their team's rate, or the organisation's rate when the team has none.</p>
        </fieldset>

        {!readOnly && (
          <>
            <label>Sign-in</label>
            {isNew ? (
              <>
                <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder="Initial password (optional, at least 12 characters)" />
                <p className="sub" style={{ fontSize: 12 }}>Leave blank if they sign in with single sign-on only.</p>
              </>
            ) : (
              <>
                <p className="sub" style={{ fontSize: 13, margin: "4px 0" }}>
                  {user!.password_set ? "Has a password" : "No password: single sign-on only"}
                  {user!.sso_linked ? " · SSO linked" : ""}
                  {user!.locked ? " · locked after failed attempts (setting a password unlocks it)" : ""}
                </p>
                {isSelf ? (
                  <p className="sub" style={{ fontSize: 12 }}>Change your own password with "Change password" in the sidebar.</p>
                ) : (
                  <>
                    <div className="btn-row">
                      <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)}
                        placeholder="New password (at least 12 characters)" style={{ flex: 1 }} />
                      <button type="button" className="btn" disabled={busy || !password} onClick={setUserPassword}>
                        {user!.password_set ? "Reset password" : "Set password"}
                      </button>
                    </div>
                    <div className="btn-row" style={{ marginTop: 8, alignItems: "center" }}>
                      {confirmSignOut ? (
                        <>
                          <span className="sub" style={{ fontSize: 13 }}>End every session {user!.email} has open now?</span>
                          <button type="button" className="btn small danger" disabled={busy} onClick={signOutEverywhere}>Sign them out</button>
                          <button type="button" className="btn small" onClick={() => setConfirmSignOut(false)}>Cancel</button>
                        </>
                      ) : (
                        <button type="button" className="btn small" onClick={() => setConfirmSignOut(true)}>Sign out of all sessions</button>
                      )}
                    </div>
                  </>
                )}
              </>
            )}
            {pwMsg && <div className="success">{pwMsg}</div>}

            {!isNew && !user!.active && !isSelf && (
              <>
                <label>Erasure request</label>
                {confirmErase ? (
                  <div className="notice">
                    <p style={{ margin: "0 0 8px" }}>
                      <strong>Erase the personal data of {user!.name || user!.email}?</strong> Their name, email, sign-in details and hourly
                      rate are removed and can't be recovered. Their review decisions stay in the record, attributed to an anonymised
                      "Erased user" account. Earlier audit log entries are append-only and keep what they recorded at the time.
                      The account can't be reactivated or edited afterwards.
                    </p>
                    <div className="btn-row">
                      <button type="button" className="btn danger" disabled={busy} onClick={erase}>Erase personal data</button>
                      <button type="button" className="btn" onClick={() => setConfirmErase(false)}>Keep</button>
                    </div>
                  </div>
                ) : (
                  <button type="button" className="btn small danger" onClick={() => setConfirmErase(true)}>Erase personal data…</button>
                )}
              </>
            )}

            {lostReviewer && openTasks > 0 && (
              <div className="notice">They have {openTasks} open review task{openTasks === 1 ? "" : "s"}. Saving hands these to other reviewers.</div>
            )}
            {!roles.length && <div className="notice">Pick an account type or at least one kind of access.</div>}
          </>
        )}
        <ErrorBox error={err} />

        {readOnly ? (
          <div className="btn-row" style={{ marginTop: 18, justifyContent: "flex-end" }}>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        ) : (
          <div className="btn-row" style={{ marginTop: 18, justifyContent: "space-between" }}>
            <div className="btn-row">
              {!isNew && user!.active && !isSelf && (
                confirmOff ? (
                  <>
                    <button className="btn danger" disabled={busy} onClick={() => save(false)}>
                      Confirm deactivation{openTasks ? ` and reassign ${openTasks}` : ""}
                    </button>
                    <button className="btn" onClick={() => setConfirmOff(false)}>Keep active</button>
                  </>
                ) : <button className="btn danger" onClick={() => setConfirmOff(true)}>Deactivate</button>
              )}
              {!isNew && !user!.active && <button className="btn" disabled={busy} onClick={() => save(true)}>Reactivate</button>}
            </div>
            <div className="btn-row">
              <button className="btn" onClick={onClose}>Cancel</button>
              <button className="btn primary" disabled={busy || !roles.length || (isNew && !email)} onClick={() => save()}>
                {isNew ? "Add user" : "Save changes"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Teams({ teams, canManage, reload, onMsg }: { teams: Team[]; canManage: boolean; reload: () => void; onMsg: (m: string) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  function start(t: Team | null) {
    setErr(null);
    setEditing(t ? t.id : "new");
    setName(t?.name || "");
    setCeiling(t?.daily_ceiling_minutes ? String(t.daily_ceiling_minutes) : "");
    setRate(t?.hourly_rate != null ? String(t.hourly_rate) : "");
  }

  async function save() {
    setErr(null);
    setBusy(true);
    const ceil = rateOrNull(ceiling);
    const body = { name: name.trim(), daily_ceiling_minutes: ceil ? Math.round(ceil) : null, hourly_rate: rateOrNull(rate) };
    try {
      if (editing === "new") await api("/v1/admin/teams", { body });
      else await api(`/v1/admin/teams/${editing}`, { method: "PUT", body });
      onMsg(`Saved team ${body.name}.`);
      setEditing(null);
      reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const form = (
    <tr>
      <td><input aria-label="Team name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team name" autoFocus /></td>
      <td colSpan={2} />
      <td><input aria-label="Daily ceiling (minutes)" type="number" min={1} value={ceiling} onChange={(e) => setCeiling(e.target.value)} placeholder="No ceiling" /></td>
      <td><input aria-label="Hourly rate" type="number" min={0} step="0.01" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="Organisation rate" /></td>
      <td className="btn-row" style={{ justifyContent: "flex-end" }}>
        <button className="btn small primary" onClick={save} disabled={busy || !name.trim()}>Save</button>
        <button className="btn small" onClick={() => setEditing(null)}>Cancel</button>
      </td>
    </tr>
  );

  return (
    <div className="card" style={{ padding: 0, overflowX: "auto" }}>
      <div className="page-head" style={{ padding: "16px 16px 0", marginBottom: 8 }}>
        <div>
          <h2 style={{ margin: 0 }}>Teams</h2>
          <div className="sub">
            Workflow stages route to a team. The daily ceiling caps the whole team's review minutes. The hourly rate is used in cost
            reports for members without their own rate.
          </div>
        </div>
        {canManage && <button className="btn small" onClick={() => start(null)}>Add team</button>}
      </div>
      {err ? <div style={{ padding: "0 16px" }}><ErrorBox error={err} /></div> : null}
      <table>
        <thead><tr><th>Team</th><th>Reviewers</th><th>Members</th><th>Daily ceiling</th><th>Hourly rate</th><th /></tr></thead>
        <tbody>
          {teams.map((t) => editing === t.id ? <Fragment key={t.id}>{form}</Fragment> : (
            <tr key={t.id}>
              <td style={{ fontWeight: 600 }}>{t.name}</td>
              <td>{t.reviewers}</td>
              <td>{t.members}</td>
              <td>{t.daily_ceiling_minutes ? `${t.daily_ceiling_minutes} min` : <span className="sub">none</span>}</td>
              <td>{t.hourly_rate != null ? rateText(t.hourly_rate) : <span className="sub">organisation rate</span>}</td>
              <td style={{ textAlign: "right" }}>{canManage && <button className="btn small" onClick={() => start(t)}>Edit</button>}</td>
            </tr>
          ))}
          {editing === "new" && form}
          {!teams.length && editing !== "new" && <tr><td colSpan={6}><Empty>No teams yet.</Empty></td></tr>}
        </tbody>
      </table>
    </div>
  );
}


export default function Users() {
  const { me } = useAuth();
  // The page is also open to oversight leads, read-only: every change is admin-only on the server.
  const canManage = !!me?.roles.includes("admin");
  const users = useLoad(() => api<User[]>("/v1/admin/users"));
  const teams = useLoad(() => api<Team[]>("/v1/admin/teams"));
  const [filter, setFilter] = useState<FilterKey>("all");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [msg, setMsg] = useState("");

  const all = users.data || [];
  const teamName = useMemo(() => Object.fromEntries((teams.data || []).map((t) => [t.id, t.name])), [teams.data]);
  const active = all.filter((u) => u.active);
  const counts = {
    admin: active.filter((u) => u.kind === "admin").length,
    reviewer: active.filter((u) => u.kind === "reviewer").length,
    staff: active.filter((u) => u.kind === "staff" || u.kind === "none").length,
    inactive: all.length - active.length,
  };
  const rows = all.filter((u) => {
    if (filter === "inactive" ? u.active : !u.active) return false;
    if (filter === "admin" && u.kind !== "admin") return false;
    if (filter === "reviewer" && u.kind !== "reviewer") return false;
    if (filter === "staff" && !(u.kind === "staff" || u.kind === "none")) return false;
    return !q || `${u.name} ${u.email}`.toLowerCase().includes(q.toLowerCase());
  });

  function saved(m: string) {
    setEditing(null);
    setMsg(m);
    users.reload();
    teams.reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Users &amp; teams</h1>
          <div className="sub">Who can sign in to {me?.tenant?.name}, and what they can do.</div>
        </div>
        {canManage ? (
          <button className="btn primary" onClick={() => { setMsg(""); setEditing("new"); }}>Add user</button>
        ) : <Badge>read-only</Badge>}
      </div>
      {!canManage && (
        <div className="notice">You can see users and teams because you're an oversight lead. Only admins can add, change, sign out or erase users.</div>
      )}

      {msg && <div className="success">{msg}</div>}
      <ErrorBox error={users.error || teams.error} />

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat label="Admins" value={counts.admin} hint="Run the platform. Never assigned reviews." />
        <Stat label="Reviewers" value={counts.reviewer} hint={`${active.reduce((n, u) => n + (u.stats?.open_tasks || 0), 0)} open tasks between them`} />
        <Stat label="Other staff" value={counts.staff} hint="Submitters, oversight, audit, integrations" />
        <Stat label="Deactivated" value={counts.inactive} hint="Kept for the audit trail" />
      </div>

      <div className="page-head" style={{ marginBottom: 10 }}>
        <div className="segmented">
          {FILTERS.map((f) => (
            <button key={f.key} className={filter === f.key ? "on" : ""} onClick={() => setFilter(f.key)}>{f.name}</button>
          ))}
        </div>
        <input placeholder="Search name or email" aria-label="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} />
      </div>

      <div className="card" style={{ padding: 0, marginBottom: 20, overflowX: "auto" }}>
        {users.loading && !users.data ? <Empty>Loading…</Empty> : !rows.length ? <Empty>No users match.</Empty> : (
          <table>
            <thead>
              <tr><th>User</th><th>Access</th><th>Teams</th><th>Review workload</th><th>Last sign-in</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className="clickable" onClick={() => { setMsg(""); setEditing(u); }} style={u.active ? undefined : { opacity: 0.6 }}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{u.name || u.email}{u.id === me?.id && <span className="sub"> (you)</span>}</div>
                    <div className="sub">
                      {u.erased ? "personal data erased" : <>{u.email} · {u.password_set ? "password" : "SSO only"}</>}
                      {u.hourly_rate != null && <> · {rateText(u.hourly_rate)}</>}
                    </div>
                  </td>
                  <td>
                    <div className="btn-row" style={{ gap: 4 }}>
                      {u.roles.map((r) => <RoleBadge key={r} role={r} />)}
                      {u.erased ? <Badge tone="high">erased</Badge> : !u.active && <Badge>Deactivated</Badge>}
                      {u.locked && <Badge>Locked</Badge>}
                    </div>
                  </td>
                  <td className="sub">{u.team_ids.map((t) => teamName[t] || "?").join(", ") || "–"}</td>
                  <td>
                    {u.stats ? (
                      <div style={{ fontSize: 13 }}>
                        {u.stats.open_tasks} open · {u.stats.minutes_today} min today
                        <div className="sub" style={{ fontSize: 12 }}>
                          Catch rate {u.stats.canaries_scored ? `${pct(u.stats.catch_rate)} (${u.stats.canaries_scored})` : "–"} · {u.stats.completed_30d} done in 30 days
                        </div>
                      </div>
                    ) : <span className="sub">Not a reviewer</span>}
                  </td>
                  <td className="sub">{u.last_login_at ? when(u.last_login_at) : "Never"}</td>
                  <td style={{ textAlign: "right" }}><button className="btn small">{canManage && !u.erased ? "Edit" : "View"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Teams teams={teams.data || []} canManage={canManage} reload={teams.reload} onMsg={setMsg} />

      {editing && (
        <UserEditor key={editing === "new" ? "new" : editing.id} user={editing === "new" ? null : editing} teams={teams.data || []}
          selfId={me?.id || ""} canManage={canManage}
          onClose={() => { setEditing(null); users.reload(); }} onSaved={saved} />
      )}
    </>
  );
}
