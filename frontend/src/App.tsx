import { useState } from "react";
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
import { UpsControlPage } from "./pages/UpsControlPage";
import type { RecordedClientConfigSummary } from "./types";

export function App() {
  const [activeTab, setActiveTab] = useState<NavTab>("dashboard");
  const [clientSetupHostId, setClientSetupHostId] = useState<number | null>(null);
  const [selectedRecordedConfig, setSelectedRecordedConfig] =
    useState<RecordedClientConfigSummary | null>(null);
  const live = useLiveTelemetry();

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
      activeTab={activeTab}
      onSelectTab={setActiveTab}
      wsConnected={live.wsConnected}
      telemetry={live.telemetry}
      orchestrator={live.orchestrator}
      services={live.services}
      versionStatus={live.versionStatus}
      checkingUpdates={live.checkingUpdates}
      onCheckUpdates={live.checkUpdatesNow}
    >
      <ErrorBoundary key={activeTab}>
        {activeTab === "dashboard" && (
          <DashboardPage
            telemetry={live.telemetry}
            hosts={live.hosts}
            orchestrator={live.orchestrator}
            recentEvents={live.recentEvents}
            onNavigateNut={() => setActiveTab("nut")}
            onNavigateHosts={() => setActiveTab("hosts")}
          />
        )}
        {activeTab === "hosts" && (
          <HostsPage
            hosts={live.hosts}
            orchestrator={live.orchestrator}
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
            onOpenNutClientSetup={openNutClientSetup}
          />
        )}
        {activeTab === "clients" && (
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
        {activeTab === "recorded_configs" && (
          <RecordedConfigsPage
            onLoadInClientSetup={handleLoadRecordedConfig}
            onNavigateClientSetup={() => setActiveTab("clients")}
          />
        )}
        {activeTab === "integrations" && (
          <IntegrationsPage
            telemetry={live.telemetry}
            hosts={live.hosts}
            orchestrator={live.orchestrator}
          />
        )}
        {activeTab === "nut" && (
          <NutConfigPage
            services={live.services}
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
          />
        )}
        {activeTab === "control" && (
          <UpsControlPage
            telemetry={live.telemetry}
            onRefresh={live.refreshAll}
          />
        )}
        {activeTab === "notifications" && <NotificationsPage />}
        {activeTab === "events" && <EventsPage />}
      </ErrorBoundary>
    </Layout>
  );
}

export default App;
