import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Badge, Empty, Modal, num, Stat, useLoad, when } from "../components/ui";
import { Confirm, Err, Ok, useAction } from "../components/admin/common";
import "./admin.css";

type Org = {
  id: string; name: string; region: string; active: boolean; created_at: string; active_users: number; admins: string[];
  submissions: number; open_tasks: number; source_apps: number; sso: boolean;
};

const REGIONS = ["uk", "eu", "us"];
const MIN_PASSWORD = 12;

export default function Tenants() {
  const { me } = useAuth();
  const orgs = useLoad(() => api<Org[]>("/v1/platform/tenants"));
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<Org | null>(null);
  const [toggling, setToggling] = useState<Org | null>(null);
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(true);
  const msg = useAction();

  const list = (orgs.data ?? []).filter((o) =>
    (showInactive || o.active) && (!search.trim() || `${o.name} ${o.admins.join(" ")} ${o.id}`.toLowerCase().includes(search.trim().toLowerCase())));
  const all = orgs.data ?? [];

  return (
    <div className="admin-page">
      <div className="page-head">
        <div>
          <h1>Organisations</h1>
          <div className="sub">Every organisation on this platform. Each has its own people, workflows, data and settings.</div>
        </div>
        <button className="btn primary" onClick={() => { msg.clear(); setCreating(true); }}>New organisation</button>
      </div>

      {orgs.data && (
        <div className="grid grid-4 block-gap">
          <Stat label="Organisations" value={num(all.length)} hint={`${all.filter((o) => !o.active).length} deactivated`} />
          <Stat label="Active people" value={num(all.reduce((s, o) => s + o.active_users, 0))} />
          <Stat label="Submissions" value={num(all.reduce((s, o) => s + o.submissions, 0))} />
          <Stat label="Open review tasks" value={num(all.reduce((s, o) => s + o.open_tasks, 0))} />
        </div>
      )}

      <div className="toolbar">
        <div>
          <label htmlFor="org-search">Search</label>
          <input id="org-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name, admin email or ID" />
        </div>
        <label className="check toolbar-check">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show deactivated
        </label>
        <button className="btn small" onClick={orgs.reload} disabled={orgs.loading}>Refresh</button>
      </div>

      <Err error={orgs.error} />
      <Ok>{msg.ok}</Ok>

      <div className="card flush">
        {orgs.loading && !orgs.data ? <div className="sub pad">Loading…</div>
          : !list.length ? <Empty>{orgs.error ? "Organisations could not be loaded." : all.length ? "No organisations match." : "No organisations yet."}</Empty>
          : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr><th>Organisation</th><th>Status</th><th>Admins</th><th className="num">People</th><th className="num">Submissions</th>
                    <th className="num">Open tasks</th><th className="num">Source apps</th><th>SSO</th><th /></tr>
                </thead>
                <tbody>
                  {list.map((o) => {
                    const mine = o.id === me?.tenant?.id;
                    return (
                      <tr key={o.id} className={o.active ? undefined : "row-muted"}>
                        <td>
                          <div className="strong">{o.name} {mine && <Badge tone="accent">yours</Badge>}</div>
                          <div className="sub small-text">Region {o.region} · created {when(o.created_at)}</div>
                          <div className="mono sub">{o.id}</div>
                        </td>
                        <td><Badge tone={o.active ? "ok" : "high"}>{o.active ? "active" : "deactivated"}</Badge></td>
                        <td className="small-text">{o.admins.length ? o.admins.map((e) => <div key={e} className="break">{e}</div>) : <span className="text-high">no active admin</span>}</td>
                        <td className="num">{num(o.active_users)}</td>
                        <td className="num">{num(o.submissions)}</td>
                        <td className="num">{num(o.open_tasks)}</td>
                        <td className="num">{num(o.source_apps)}</td>
                        <td>{o.sso ? <Badge tone="ok">set up</Badge> : <span className="sub">–</span>}</td>
                        <td>
                          <div className="btn-row nowrap">
                            <button className="btn small" onClick={() => { msg.clear(); setRenaming(o); }}>Rename</button>
                            {o.active
                              ? <button className="btn small danger" disabled={mine} title={mine ? "You can't deactivate your own organisation" : undefined}
                                  onClick={() => { msg.clear(); setToggling(o); }}>Deactivate</button>
                              : <button className="btn small primary" onClick={() => { msg.clear(); setToggling(o); }}>Activate</button>}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {creating && <CreateOrg onClose={() => setCreating(false)} onCreated={(o, email, withPassword) => {
        setCreating(false);
        orgs.reload();
        msg.setOk(`${o.name} created. ${email} is its first admin${withPassword
          ? " and can sign in with the password you set. Share it with them securely."
          : ": they sign in with single sign-on once it is set up, or with a password reset link."}`);
      }} />}
      {renaming && <RenameOrg org={renaming} onClose={() => setRenaming(null)} onSaved={(o) => { setRenaming(null); orgs.reload(); msg.setOk(`Renamed to ${o.name}.`); }} />}
      {toggling && (
        <Confirm title={`${toggling.active ? "Deactivate" : "Activate"} ${toggling.name}`} confirmLabel={toggling.active ? "Deactivate" : "Activate"}
          danger={toggling.active} onClose={() => setToggling(null)}
          onConfirm={async () => {
            const o = await api<Org>(`/v1/platform/tenants/${toggling.id}`, { method: "PATCH", body: { active: !toggling.active } });
            orgs.reload();
            msg.setOk(`${o.name} is ${o.active ? "active" : "deactivated"}.`);
          }}>
          {toggling.active ? (
            <>
              <p>Its {num(toggling.active_users)} {toggling.active_users === 1 ? "person" : "people"} can no longer sign in, and requests from its
                {" "}{num(toggling.source_apps)} source app{toggling.source_apps === 1 ? "" : "s"} are refused at once.
                {toggling.open_tasks > 0 && <> {num(toggling.open_tasks)} open review task{toggling.open_tasks === 1 ? "" : "s"} will stop progressing.</>}</p>
              <p className="sub">Nothing is deleted: activate it again to restore access.</p>
            </>
          ) : <p>Its people can sign in again and its source apps can make requests again.</p>}
        </Confirm>
      )}
    </div>
  );
}

function CreateOrg({ onClose, onCreated }: { onClose: () => void; onCreated: (o: Org, email: string, withPassword: boolean) => void }) {
  const [name, setName] = useState("");
  const [region, setRegion] = useState("uk");
  const [email, setEmail] = useState("");
  const [adminName, setAdminName] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const a = useAction();

  const errors: Record<string, string> = {};
  if (!name.trim()) errors.name = "Required.";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) errors.email = "Enter a valid email address.";
  if (!region.trim()) errors.region = "Required.";
  if (password && password.length < MIN_PASSWORD) errors.password = `At least ${MIN_PASSWORD} characters (or leave empty).`;
  const err = (k: string) => (touched && errors[k] ? <div className="field-error">{errors[k]}</div> : null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length) return;
    const o = await a.run("create", () => api<Org>("/v1/platform/tenants", {
      body: { name: name.trim(), region: region.trim(), admin_email: email.trim(), admin_name: adminName.trim(), admin_password: password || null },
    }));
    if (o) onCreated(o, email.trim().toLowerCase(), !!password);
  }

  return (
    <Modal title="New organisation" onClose={onClose} width={560}>
      <form onSubmit={create} noValidate>
        <div className="field-row">
          <div>
            <label htmlFor="org-new-name">Organisation name</label>
            <input id="org-new-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoFocus />
            {err("name")}
          </div>
          <div>
            <label htmlFor="org-new-region">Data region</label>
            <input id="org-new-region" list="regions" value={region} maxLength={20} onChange={(e) => setRegion(e.target.value)} />
            <datalist id="regions">{REGIONS.map((r) => <option key={r} value={r} />)}</datalist>
            {err("region")}
          </div>
        </div>
        <h3 className="table-title">First admin</h3>
        <div className="field-row">
          <div>
            <label htmlFor="org-new-email">Email</label>
            <input id="org-new-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
            {err("email")}
          </div>
          <div>
            <label htmlFor="org-new-admin">Name</label>
            <input id="org-new-admin" value={adminName} onChange={(e) => setAdminName(e.target.value)} autoComplete="off" />
          </div>
        </div>
        <label htmlFor="org-new-pw">Password (optional)</label>
        <input id="org-new-pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div className="sub hint">At least {MIN_PASSWORD} characters. Without one, the admin signs in with single sign-on (once set up) or a password reset link.</div>
        {err("password")}
        <Err error={a.error} />
        <div className="btn-row modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!!a.busy}>{a.busy ? "Creating…" : "Create organisation"}</button>
        </div>
      </form>
    </Modal>
  );
}

function RenameOrg({ org, onClose, onSaved }: { org: Org; onClose: () => void; onSaved: (o: Org) => void }) {
  const [name, setName] = useState(org.name);
  const a = useAction();
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim() === org.name) return onClose();
    const o = await a.run("save", () => api<Org>(`/v1/platform/tenants/${org.id}`, { method: "PATCH", body: { name: name.trim() } }));
    if (o) onSaved(o);
  }
  return (
    <Modal title={`Rename ${org.name}`} onClose={onClose}>
      <form onSubmit={save}>
        <label htmlFor="org-rename">Organisation name</label>
        <input id="org-rename" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoFocus required />
        <Err error={a.error} />
        <div className="btn-row modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!name.trim() || !!a.busy}>{a.busy ? "Saving…" : "Rename"}</button>
        </div>
      </form>
    </Modal>
  );
}
