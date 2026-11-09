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
import { RoleBadge } from "./components/ui";

function Shell() {
  const { me, loading, has } = useAuth();
  const location = useLocation();
  const [account, setAccount] = useState(false);
  if (loading) return <div className="empty">Loading…</div>;
  if (!me) {
    // Come back here after signing in (deep links, expired sessions).
    if (location.pathname !== "/") rememberReturn(location.pathname + location.search);
    return <Navigate to="/login" replace />;
  }
  const isAdmin = me.roles.includes("admin");
  const home = isAdmin ? "/admin/users" : has("reviewer") ? "/queue" : has("submitter") ? "/submissions"
    : has("oversight_lead") ? "/oversight" : has("auditor") ? "/evidence" : has("integration_owner") ? "/integrations"
    : me.platform_admin ? "/platform/tenants" : "/account";
  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">✓</div>
          Proof of Review
        </div>
        {(isAdmin || has("oversight_lead")) && (
          <>
            <div className="nav-section">Administration</div>
            <NavLink to="/admin/users">Users &amp; teams</NavLink>
            {isAdmin && <NavLink to="/admin/settings">Organisation settings</NavLink>}
          </>
        )}
        {me.platform_admin && (
          <>
            <div className="nav-section">Platform</div>
            <NavLink to="/platform/tenants">Organisations</NavLink>
          </>
        )}
        {has("reviewer") && (
          <>
            <div className="nav-section">Review</div>
            <NavLink to="/queue">Review queue</NavLink>
            <NavLink to="/spot-checks">Spot-checks</NavLink>
            <NavLink to="/history">My reviews</NavLink>
          </>
        )}
        {has("submitter") && (
          <>
            <div className="nav-section">Submit</div>
            <NavLink to="/submit">Submit for review</NavLink>
            <NavLink to="/submissions">My submissions</NavLink>
          </>
        )}
        {has("oversight_lead") && (
          <>
            <div className="nav-section">Oversight</div>
            <NavLink to="/oversight">Assurance dashboard</NavLink>
            <NavLink to="/held">Held items</NavLink>
            <NavLink to="/workflows">Workflow settings</NavLink>
          </>
        )}
        {has("oversight_lead", "auditor") && (
          <>
            <div className="nav-section">Reporting</div>
            <NavLink to="/reports">Reports</NavLink>
            {has("oversight_lead") && <NavLink to="/costs">Costs</NavLink>}
          </>
        )}
        {has("auditor", "oversight_lead") && (
          <>
            <div className="nav-section">Audit</div>
            <NavLink to="/evidence">Evidence explorer</NavLink>
            <NavLink to="/audit-log">Audit log</NavLink>
          </>
        )}
        {has("integration_owner") && (
          <>
            <div className="nav-section">Integrations</div>
            <NavLink to="/integrations">Integrations console</NavLink>
          </>
        )}
        <div className="who">
          <div style={{ fontWeight: 600, color: "var(--text)" }}>{me.name || me.email}</div>
          <div>{me.tenant?.name}</div>
          <div className="btn-row" style={{ gap: 4, margin: "6px 0" }}>
            {me.roles.map((r) => <RoleBadge key={r} role={r} />)}
          </div>
          <button className="btn small" onClick={() => setAccount(true)}>Your account</button>
        </div>
      </nav>
      {account && <Account onClose={() => setAccount(false)} />}
      <main className="main" id="main">
        <Routes>
          <Route path="/" element={<Navigate to={home} replace />} />
          <Route path="/account" element={<div className="empty">Your account has no pages yet. Ask an admin for access.</div>} />
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
              <Route path="/workflows" element={<Workflows />} />
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
          {has("integration_owner") && <Route path="/integrations" element={<Integrations />} />}
          {(isAdmin || has("oversight_lead")) && <Route path="/admin/users" element={<Users />} />}
          {isAdmin && <Route path="/admin/settings" element={<TenantSettings />} />}
          {me.platform_admin && <Route path="/platform/tenants" element={<Tenants />} />}
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
