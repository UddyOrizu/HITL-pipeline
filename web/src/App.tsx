import { useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import Account from "./components/Account";
import { AuthProvider, useAuth } from "./auth";
import { rememberReturn } from "./api";
import Login from "./pages/Login";
import SsoCallback from "./pages/SsoCallback";
import ResetPassword from "./pages/ResetPassword";
import Queue from "./pages/Queue";
import Workspace from "./pages/Workspace";
import History from "./pages/History";
import Dashboard from "./pages/Dashboard";
import Held from "./pages/Held";
import Workflows from "./pages/Workflows";
import Evidence from "./pages/Evidence";
import AuditLog from "./pages/AuditLog";
import Integrations from "./pages/Integrations";
import Users from "./pages/Users";
import TenantSettings from "./pages/TenantSettings";
import Tenants from "./pages/Tenants";
import Reports from "./pages/Reports";
import Costs from "./pages/Costs";
import Submit from "./pages/Submit";
import MySubmissions from "./pages/MySubmissions";
import Settings, { MovedTo, type SettingsTab } from "./pages/Settings";
import { RoleBadge } from "./components/ui";

function Shell() {
  const { me, loading, has, signedOut } = useAuth();
  const location = useLocation();
  const [account, setAccount] = useState(false);
  if (loading) return <div className="empty">Loading…</div>;
  if (!me) {
    // Come back here after signing in (deep links, expired sessions), but not after a deliberate sign-out:
    // the next person to sign in on this browser shouldn't land on the previous person's page.
    if (location.pathname !== "/" && !signedOut) rememberReturn(location.pathname + location.search);
    return <Navigate to="/login" replace />;
  }
  const isAdmin = me.roles.includes("admin");
  // Settings: only the tabs this person may use. No tabs, no Settings link.
  const settingsTabs: SettingsTab[] = [];
  if (isAdmin || has("oversight_lead")) settingsTabs.push({ to: "/settings/people", label: "People & teams" });
  if (isAdmin) settingsTabs.push({ to: "/settings/organisation", label: "Organisation" });
  if (has("oversight_lead")) settingsTabs.push({ to: "/settings/workflows", label: "Review workflows" });
  if (has("integration_owner")) settingsTabs.push({ to: "/settings/connected-apps", label: "Connected apps" });
  if (me.platform_admin) settingsTabs.push({ to: "/settings/all-organisations", label: "All organisations" });
  const settingsHome = settingsTabs[0]?.to;
  const home = has("reviewer") ? "/queue" : has("submitter") ? "/submissions"
    : has("oversight_lead") ? "/oversight" : has("auditor") ? "/evidence" : settingsHome || "/account";
  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">✓</div>
          Proof of Review
        </div>
        {has("reviewer") && (
          <>
            <div className="nav-section">Review</div>
            <NavLink to="/queue">To review</NavLink>
            <NavLink to="/spot-checks">Spot checks</NavLink>
            <NavLink to="/history">My past reviews</NavLink>
          </>
        )}
        {has("submitter") && (
          <>
            <div className="nav-section">Send work</div>
            <NavLink to="/submit">Send for review</NavLink>
            <NavLink to="/submissions">Sent by me</NavLink>
          </>
        )}
        {has("oversight_lead") && (
          <>
            <div className="nav-section">Quality</div>
            <NavLink to="/oversight">Quality overview</NavLink>
            <NavLink to="/held">On hold</NavLink>
          </>
        )}
        {has("oversight_lead", "auditor") && (
          <>
            <div className="nav-section">Reports</div>
            <NavLink to="/reports">Reports</NavLink>
            {has("oversight_lead") && <NavLink to="/costs">Costs</NavLink>}
          </>
        )}
        {has("auditor", "oversight_lead") && (
          <>
            <div className="nav-section">Records</div>
            <NavLink to="/evidence">Review records</NavLink>
            <NavLink to="/audit-log">Activity log</NavLink>
          </>
        )}
        <div className="nav-foot">
          {settingsHome && <NavLink to="/settings">Settings</NavLink>}
          <div className="who">
            <div className="who-name">{me.name || me.email}</div>
            <div>{me.tenant?.name}</div>
            <div className="btn-row" style={{ gap: 4, margin: "6px 0" }}>
              {me.roles.map((r) => <RoleBadge key={r} role={r} />)}
            </div>
            <button className="btn small" onClick={() => setAccount(true)}>Your account</button>
          </div>
        </div>
      </nav>
      {account && <Account onClose={() => setAccount(false)} />}
      <main className="main" id="main">
        <Routes>
          <Route path="/" element={<Navigate to={home} replace />} />
          <Route path="/account" element={<div className="empty">You don't have access to anything yet. Ask your administrator to give you access.</div>} />
          {has("reviewer") && (
            <>
              <Route path="/queue" element={<Queue kind="primary" />} />
              <Route path="/spot-checks" element={<Queue kind="spot_check" />} />
              <Route path="/review/:taskId" element={<Workspace />} />
              <Route path="/history" element={<History />} />
            </>
          )}
          {has("submitter") && (
            <>
              <Route path="/submit" element={<Submit />} />
              <Route path="/submissions" element={<MySubmissions />} />
            </>
          )}
          {has("oversight_lead") && (
            <>
              <Route path="/oversight" element={<Dashboard />} />
              <Route path="/held" element={<Held />} />
              <Route path="/costs" element={<Costs />} />
            </>
          )}
          {has("oversight_lead", "auditor") && <Route path="/reports" element={<Reports />} />}
          {has("auditor", "oversight_lead") && (
            <>
              <Route path="/evidence" element={<Evidence />} />
              <Route path="/audit-log" element={<AuditLog />} />
            </>
          )}
          {settingsHome && (
            <Route path="/settings" element={<Settings tabs={settingsTabs} />}>
              <Route index element={<Navigate to={settingsHome} replace />} />
              {(isAdmin || has("oversight_lead")) && <Route path="people" element={<Users />} />}
              {isAdmin && <Route path="organisation" element={<TenantSettings />} />}
              {has("oversight_lead") && <Route path="workflows" element={<Workflows />} />}
              {has("integration_owner") && <Route path="connected-apps" element={<Integrations />} />}
              {me.platform_admin && <Route path="all-organisations" element={<Tenants />} />}
              <Route path="*" element={<Navigate to={settingsHome} replace />} />
            </Route>
          )}
          {/* Addresses from before Settings existed. */}
          <Route path="/admin/users" element={<MovedTo to="/settings/people" />} />
          <Route path="/admin/settings" element={<MovedTo to="/settings/organisation" />} />
          <Route path="/workflows" element={<MovedTo to="/settings/workflows" />} />
          <Route path="/integrations" element={<MovedTo to="/settings/connected-apps" />} />
          <Route path="/platform/tenants" element={<MovedTo to="/settings/all-organisations" />} />
          <Route path="*" element={<Navigate to={home} replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/login/sso" element={<SsoCallback />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/*" element={<Shell />} />
      </Routes>
    </AuthProvider>
  );
}
