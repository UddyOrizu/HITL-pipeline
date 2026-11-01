import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Login from "./pages/Login";
import Queue from "./pages/Queue";
import Workspace from "./pages/Workspace";
import History from "./pages/History";
import Dashboard from "./pages/Dashboard";
import Held from "./pages/Held";
import Workflows from "./pages/Workflows";
import Evidence from "./pages/Evidence";
import Integrations from "./pages/Integrations";
import Users from "./pages/Users";
import Submit from "./pages/Submit";
import MySubmissions from "./pages/MySubmissions";
import { RoleBadge } from "./components/ui";

function Shell() {
  const { me, loading, has, signOut } = useAuth();
  if (loading) return <div className="empty">Loading…</div>;
  if (!me) return <Navigate to="/login" replace />;
  const isAdmin = me.roles.includes("admin");
  const home = isAdmin ? "/admin/users" : has("reviewer") ? "/queue" : has("submitter") ? "/submissions"
    : has("oversight_lead") ? "/oversight" : has("auditor") ? "/evidence" : "/integrations";
  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">
          <div className="brand-mark">✓</div>
          Proof of Review
        </div>
        {isAdmin && (
          <>
            <div className="nav-section">Administration</div>
            <NavLink to="/admin/users">Users &amp; teams</NavLink>
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
        {has("auditor", "oversight_lead") && (
          <>
            <div className="nav-section">Audit</div>
            <NavLink to="/evidence">Evidence explorer</NavLink>
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
          <a href="/login" onClick={signOut}>Sign out</a>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to={home} replace />} />
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
            </>
          )}
          {has("auditor", "oversight_lead") && <Route path="/evidence" element={<Evidence />} />}
          {has("integration_owner") && <Route path="/integrations" element={<Integrations />} />}
          {isAdmin && <Route path="/admin/users" element={<Users />} />}
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
        <Route path="/*" element={<Shell />} />
      </Routes>
    </AuthProvider>
  );
}
