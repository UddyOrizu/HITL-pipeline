import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";

export type SettingsTab = { to: string; label: string };

// One home for everything that is set up once and changed rarely. Each person sees only the tabs they may use.
export default function Settings({ tabs }: { tabs: SettingsTab[] }) {
  return (
    <div className="settings">
      <div className="settings-eyebrow">Settings</div>
      <nav className="subnav" aria-label="Settings">
        {tabs.map((t) => <NavLink key={t.to} to={t.to}>{t.label}</NavLink>)}
      </nav>
      <Outlet />
    </div>
  );
}

// Old addresses (bookmarks, links in emails) keep working, with their query string.
export function MovedTo({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={to + search} replace />;
}
