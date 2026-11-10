import { useSearchParams } from "react-router-dom";
import { apiAll } from "../api";
import { useLoad } from "../components/ui";
import { SourceApps } from "../components/integrations/SourceApps";
import { Connectors } from "../components/integrations/Connectors";
import { Deliveries } from "../components/integrations/Deliveries";
import type { SourceApp } from "../components/integrations/types";
import "./admin.css";

const TABS = [
  { key: "apps", label: "Source applications" },
  { key: "connectors", label: "Connectors" },
  { key: "deliveries", label: "Delivery log" },
] as const;

type Tab = (typeof TABS)[number]["key"];

export default function Integrations() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = (TABS.find((t) => t.key === params.get("tab"))?.key ?? "apps") as Tab;
  // Apps are shared by every tab (names for connectors and deliveries, filters).
  const apps = useLoad(() => apiAll<SourceApp>("/v1/integrations/apps"));

  return (
    <div className="admin-page">
      <div className="page-head">
        <div>
          <h1>Integrations console</h1>
          <div className="sub">Source applications, API keys, webhooks and connectors.</div>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Integrations">
        {TABS.map((t) => (
          <button key={t.key} role="tab" id={`tab-${t.key}`} aria-controls={`panel-${t.key}`} aria-selected={tab === t.key}
            onClick={() => setParams(t.key === "apps" ? {} : { tab: t.key }, { replace: true })}>
            {t.label}
            {t.key === "apps" && apps.data ? <span className="tab-count">{apps.data.length}</span> : null}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "apps" && <SourceApps apps={apps} />}
        {tab === "connectors" && <Connectors apps={apps} />}
        {tab === "deliveries" && <Deliveries apps={apps} />}
      </div>
    </div>
  );
}
