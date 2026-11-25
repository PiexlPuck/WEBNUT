import { useEffect, useState } from "react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout, type NavTab } from "./components/Layout";
import { useLiveTelemetry } from "./hooks/useLiveTelemetry";
import { DashboardPage } from "./pages/DashboardPage";
import { EventsPage } from "./pages/EventsPage";
import { HostsPage } from "./pages/HostsPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { NutClientsPage } from "./pages/NutClientsPage";
import { NutConfigPage } from "./pages/NutConfigPage";
import { RecordedConfigsPage } from "./pages/RecordedConfigsPage";
import { RepeaterLinkPage } from "./pages/RepeaterLinkPage";
import { RepeatersPage } from "./pages/RepeatersPage";
import { UpsControlPage } from "./pages/UpsControlPage";
import type { RecordedClientConfigSummary } from "./types";

const REPEATER_ALLOWED_TABS: NavTab[] = ["repeater_link", "nut", "events"];

export function App() {
  const [activeTab, setActiveTab] = useState<NavTab>("dashboard");
  const [clientSetupHostId, setClientSetupHostId] = useState<number | null>(null);
  const [selectedRecordedConfig, setSelectedRecordedConfig] =
    useState<RecordedClientConfigSummary | null>(null);
  const live = useLiveTelemetry();

  const role = live.systemInfo?.role ?? "main";

  useEffect(() => {
    if (role === "repeater" && !REPEATER_ALLOWED_TABS.includes(activeTab)) {
      setActiveTab("repeater_link");
    }
  }, [role, activeTab]);

  const effectiveTab: NavTab =
    role === "repeater" && !REPEATER_ALLOWED_TABS.includes(activeTab)
      ? "repeater_link"
      : activeTab;

  const openNutClientSetup = (hostId: number) => {
    setClientSetupHostId(hostId);
    setActiveTab("clients");
  };

  const handleLoadRecordedConfig = (rec: RecordedClientConfigSummary) => {
    setSelectedRecordedConfig(rec);
    setActiveTab("clients");
  };

  return (
    <Layout
      role={role}
      activeTab={effectiveTab}
      onSelectTab={setActiveTab}
      wsConnected={live.wsConnected}
      telemetry={live.telemetry}
      orchestrator={live.orchestrator}
      services={live.services}
      repeaterLink={live.repeaterLink}
      versionStatus={live.versionStatus}
      checkingUpdates={live.checkingUpdates}
      onCheckUpdates={live.checkUpdatesNow}
    >
      <ErrorBoundary key={effectiveTab}>
        {effectiveTab === "repeater_link" && (
          <RepeaterLinkPage
            linkStatus={live.repeaterLink}
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
            onNavigateUpsSetup={() => setActiveTab("nut")}
          />
        )}
        {effectiveTab === "dashboard" && (
          <DashboardPage
            telemetry={live.telemetry}
            hosts={live.hosts}
            orchestrator={live.orchestrator}
            recentEvents={live.recentEvents}
            onNavigateNut={() => setActiveTab("nut")}
            onNavigateHosts={() => setActiveTab("hosts")}
          />
        )}
        {effectiveTab === "repeaters" && (
          <RepeatersPage
            nodes={live.repeaterNodes}
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
          />
        )}
        {effectiveTab === "hosts" && (
          <HostsPage
            hosts={live.hosts}
            orchestrator={live.orchestrator}
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
            onOpenNutClientSetup={openNutClientSetup}
          />
        )}
        {effectiveTab === "clients" && (
          <NutClientsPage
            hosts={live.hosts}
            telemetry={live.telemetry}
            preselectedHostId={clientSetupHostId}
            onClearPreselectedHost={() => setClientSetupHostId(null)}
            preselectedConfig={selectedRecordedConfig}
            onClearPreselectedConfig={() => setSelectedRecordedConfig(null)}
            onOpenSavedConfigs={() => setActiveTab("recorded_configs")}
          />
        )}
        {effectiveTab === "recorded_configs" && (
          <RecordedConfigsPage
            onLoadInClientSetup={handleLoadRecordedConfig}
            onNavigateClientSetup={() => setActiveTab("clients")}
          />
        )}
        {effectiveTab === "integrations" && (
          <IntegrationsPage
            telemetry={live.telemetry}
            hosts={live.hosts}
            orchestrator={live.orchestrator}
          />
        )}
        {effectiveTab === "nut" && (
          <NutConfigPage
            services={live.services}
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
            repeaterMode={role === "repeater"}
          />
        )}
        {effectiveTab === "control" && (
          <UpsControlPage
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
          />
        )}
        {effectiveTab === "notifications" && <NotificationsPage />}
        {effectiveTab === "events" && <EventsPage />}
      </ErrorBoundary>
    </Layout>
  );
}

export default App;
