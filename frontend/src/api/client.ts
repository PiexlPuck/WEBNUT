import type {
  AppVersionStatus,
  ArpLookupResponse,
  CreateSecondaryUserResponse,
  EventLogPage,
  FlowDrillReportRecord,
  FlowDrillReportsResponse,
  FlowDrillRunPayload,
  FlowDrillSchedulePayload,
  FlowDrillScheduleRecord,
  HardwareScanResponse,
  HomeAssistantInfo,
  HomeAssistantState,
  HomepageConfigRequest,
  HomepageConfigResponse,
  HomepageFieldOption,
  HomepageSummary,
  HostPayload,
  HostRecord,
  HostTestRunResponse,
  IntegrationApiKeyResponse,
  IntegrationSettingsUpdatePayload,
  IntegrationSettingsView,
  MonitorAccountRevealResponse,
  NotificationRuleItem,
  NotificationSettings,
  NutClientConfigRequest,
  NutClientConfigResponse,
  NutClientsOverview,
  NutConfigFile,
  NutConnectionConfigPayload,
  NutConnectionConfigView,
  NutConnectionTestResponse,
  NutServiceStatus,
  OnboardingState,
  OnboardingStateUpdatePayload,
  OrchestratorConfig,
  OrchestratorState,
  PrometheusConfigRequest,
  PrometheusConfigResponse,
  PushoverConfigPayload,
  PushoverConfigView,
  RecordedClientConfigDetail,
  RecordedClientConfigSummary,
  RepeaterJoinTokenCreateResponse,
  RepeaterJoinTokenView,
  RepeaterLinkStatus,
  RepeaterNodeView,
  RepeaterStatusResponse,
  RunUpsCommandResponse,
  ScannedSnmpDevice,
  ScannedUsbDevice,
  SelfTestReportRecord,
  SelfTestReportsResponse,
  SelfTestSchedulePayload,
  SelfTestScheduleRecord,
  SetUpsVarResponse,
  SnmpUpsSetupPayload,
  SshDeployRequest,
  SshDeployResponse,
  SystemInfo,
  TelemetrySnapshot,
  UpsCapabilitiesResponse,
} from "../types";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail ?? body ?? res.statusText;
    const msg =
      typeof detail === "string"
        ? detail
        : typeof (detail as { message?: string })?.message === "string"
        ? (detail as { message: string }).message
        : `HTTP ${res.status}`;
    throw new ApiError(msg, res.status, detail);
  }
  return body as T;
}

export const api = {
  // NUT
  getTelemetry: () => request<TelemetrySnapshot>("/api/nut/telemetry"),
  scanUsb: () => request<ScannedUsbDevice[]>("/api/nut/scan", { method: "POST" }),
  scanHardware: () =>
    request<HardwareScanResponse>("/api/nut/scan-hardware", { method: "POST" }),
  scanSnmp: (payload: {
    start_ip: string;
    end_ip: string;
    community: string;
    snmp_version: "v1" | "v2c" | "v3";
  }) =>
    request<ScannedSnmpDevice[]>("/api/nut/scan-snmp", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setupSnmpUps: (payload: SnmpUpsSetupPayload) =>
    request<{ ok: boolean; applied: boolean; message: string; raw: string }>(
      "/api/nut/setup/snmp",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  getRepeaterStatus: () => request<RepeaterStatusResponse>("/api/nut/repeater"),
  getNutConfig: <T>(file: NutConfigFile) =>
    request<{ filename: NutConfigFile; parsed: T; raw: string }>(`/api/nut/config/${file}`),
  previewNutConfig: (file: NutConfigFile, parsed: unknown) =>
    request<{ filename: NutConfigFile; raw: string }>(`/api/nut/config/${file}/preview`, {
      method: "POST",
      body: JSON.stringify({ parsed, apply_reload: false }),
    }),
  saveNutConfig: (file: NutConfigFile, parsed: unknown, applyReload = true) =>
    request<{ filename: NutConfigFile; raw: string; applied: boolean; message: string }>(
      `/api/nut/config/${file}`,
      {
        method: "PUT",
        body: JSON.stringify({ parsed, apply_reload: applyReload }),
      }
    ),
  getServices: () => request<NutServiceStatus[]>("/api/nut/services"),
  getNutConnection: () => request<NutConnectionConfigView>("/api/nut/connection"),
  testNutConnection: (host: string, port = 3493) =>
    request<NutConnectionTestResponse>("/api/nut/connection/test", {
      method: "POST",
      body: JSON.stringify({ host, port }),
    }),
  saveNutConnection: (payload: NutConnectionConfigPayload) =>
    request<NutConnectionConfigView>("/api/nut/connection", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  restartService: (service: "upsd" | "upsmon" | "drivers") =>
    request<{ service: string; ok: boolean; detail: string }>(
      `/api/nut/services/${service}/restart`,
      { method: "POST" }
    ),
  getNutClients: () => request<NutClientsOverview>("/api/nut/clients"),
  createSecondaryNutUser: (username: string, password = "", notes = "") =>
    request<CreateSecondaryUserResponse>("/api/nut/clients/create-user", {
      method: "POST",
      body: JSON.stringify({ username, password, notes }),
    }),
  revealMonitorAccount: (username: string) =>
    request<MonitorAccountRevealResponse>(
      `/api/nut/clients/accounts/${encodeURIComponent(username)}/reveal`
    ),
  deleteMonitorAccount: (username: string) =>
    request<{ deleted: boolean; username: string }>(
      `/api/nut/clients/accounts/${encodeURIComponent(username)}`,
      { method: "DELETE" }
    ),
  generateNutClientConfig: (payload: NutClientConfigRequest) =>
    request<NutClientConfigResponse>("/api/nut/clients/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getRecordedClientConfigs: () =>
    request<RecordedClientConfigSummary[]>("/api/nut/clients/configs"),
  getRecordedClientConfig: (id: number) =>
    request<RecordedClientConfigDetail>(`/api/nut/clients/configs/${id}`),
  deleteRecordedClientConfig: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/nut/clients/configs/${id}`, {
      method: "DELETE",
    }),

  // Hosts
  getHosts: () => request<HostRecord[]>("/api/hosts"),
  createHost: (payload: HostPayload) =>
    request<HostRecord>("/api/hosts", { method: "POST", body: JSON.stringify(payload) }),
  updateHost: (id: number, payload: HostPayload) =>
    request<HostRecord>(`/api/hosts/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteHost: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/hosts/${id}`, { method: "DELETE" }),
  arpLookup: (ipOrHost: string) =>
    request<ArpLookupResponse>("/api/hosts/arp-lookup", {
      method: "POST",
      body: JSON.stringify({ ip_or_host: ipOrHost }),
    }),
  wakeHost: (id: number, trackRetries = true) =>
    request<{ started: boolean; host_id: number }>(`/api/hosts/${id}/wake`, {
      method: "POST",
      body: JSON.stringify({ track_retries: trackRetries }),
    }),
  testRunHost: (id: number, dryRun = true) =>
    request<HostTestRunResponse>(`/api/hosts/${id}/test-run`, {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun }),
    }),
  testHostSsh: (
    id: number,
    payload: { ssh_port: number; ssh_username: string; ssh_password: string }
  ) =>
    request<{ ok: boolean; detail: string }>(`/api/hosts/${id}/test-ssh`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deployHostClient: (id: number, payload: SshDeployRequest) =>
    request<SshDeployResponse>(`/api/hosts/${id}/deploy-client`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Orchestrator
  getOrchestratorStatus: () => request<OrchestratorState>("/api/orchestrator/status"),
  getOrchestratorConfig: () => request<OrchestratorConfig>("/api/orchestrator/config"),
  updateOrchestratorConfig: (cfg: OrchestratorConfig) =>
    request<OrchestratorConfig>("/api/orchestrator/config", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),
  simulateRecovery: (
    dryRun: boolean,
    scope: "bulk" | "single" = "bulk",
    hostIds: number[] = []
  ) =>
    request<OrchestratorState>("/api/orchestrator/simulate-recovery", {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun, scope, host_ids: hostIds }),
    }),

  // Notifications
  getNotificationSettings: () => request<NotificationSettings>("/api/notifications/settings"),
  updatePushover: (payload: PushoverConfigPayload) =>
    request<PushoverConfigView>("/api/notifications/providers/pushover", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  updateNotificationRules: (rules: NotificationRuleItem[]) =>
    request<NotificationRuleItem[]>("/api/notifications/rules", {
      method: "PUT",
      body: JSON.stringify({ rules }),
    }),
  sendTestNotification: (priority = 0) =>
    request<{ success: boolean; provider: string; status_code: number }>(
      "/api/notifications/test",
      {
        method: "POST",
        body: JSON.stringify({ provider_type: "pushover", priority }),
      }
    ),

  // Events
  getEvents: (params: {
    category?: string;
    severity?: string;
    search?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.category && params.category !== "all") qs.set("category", params.category);
    if (params.severity && params.severity !== "all") qs.set("severity", params.severity);
    if (params.search) qs.set("search", params.search);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    return request<EventLogPage>(`/api/events?${qs.toString()}`);
  },
  clearEvents: () => request<{ cleared: number }>("/api/events", { method: "DELETE" }),

  // System / Version / Identity / Onboarding
  getSystemInfo: () => request<SystemInfo>("/api/system/info"),
  updateSystemIdentity: (instanceName: string) =>
    request<SystemInfo>("/api/system/identity", {
      method: "PUT",
      body: JSON.stringify({ instance_name: instanceName }),
    }),
  getOnboardingState: () => request<OnboardingState>("/api/system/onboarding"),
  updateOnboardingState: (payload: OnboardingStateUpdatePayload) =>
    request<OnboardingState>("/api/system/onboarding", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  resetOnboardingState: () =>
    request<OnboardingState>("/api/system/onboarding/reset", {
      method: "POST",
    }),
  getVersionStatus: () => request<AppVersionStatus>("/api/system/version"),
  checkForUpdates: () =>
    request<AppVersionStatus>("/api/system/version/check", { method: "POST" }),

  // Distributed Repeaters (Main) & Main Link (Repeater)
  getRepeaterNodes: () => request<RepeaterNodeView[]>("/api/repeaters/nodes"),
  updateRepeaterNode: (
    id: number,
    payload: { name?: string; ui_url?: string; enabled?: boolean }
  ) =>
    request<RepeaterNodeView>(`/api/repeaters/nodes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteRepeaterNode: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/repeaters/nodes/${id}`, {
      method: "DELETE",
    }),
  getRepeaterTokens: () => request<RepeaterJoinTokenView[]>("/api/repeaters/tokens"),
  createRepeaterToken: (payload: {
    label: string;
    expires_hours: number;
    max_uses: number;
  }) =>
    request<RepeaterJoinTokenCreateResponse>("/api/repeaters/tokens", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  revokeRepeaterToken: (id: number) =>
    request<{ revoked: boolean; id: number }>(`/api/repeaters/tokens/${id}`, {
      method: "DELETE",
    }),
  getRepeaterLink: () => request<RepeaterLinkStatus>("/api/repeaters/link"),
  pairRepeaterLink: (payload: {
    main_url: string;
    join_token: string;
    node_name: string;
    public_url?: string;
  }) =>
    request<RepeaterLinkStatus>("/api/repeaters/link/pair", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  unpairRepeaterLink: () =>
    request<RepeaterLinkStatus>("/api/repeaters/link/unpair", {
      method: "POST",
    }),
  reconnectRepeaterLink: () =>
    request<RepeaterLinkStatus>("/api/repeaters/link/reconnect", {
      method: "POST",
    }),

  // Integrations (Homepage, etc.)
  getIntegrationSettings: () =>
    request<IntegrationSettingsView>("/api/integrations/settings"),
  updateIntegrationSettings: (payload: IntegrationSettingsUpdatePayload) =>
    request<IntegrationSettingsView>("/api/integrations/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  rotateIntegrationApiKey: () =>
    request<IntegrationApiKeyResponse>("/api/integrations/api-key/rotate", {
      method: "POST",
    }),
  revealIntegrationApiKey: () =>
    request<IntegrationApiKeyResponse>("/api/integrations/api-key/reveal"),
  revokeIntegrationApiKey: () =>
    request<{ revoked: boolean }>("/api/integrations/api-key", {
      method: "DELETE",
    }),
  getHomepageFields: () =>
    request<HomepageFieldOption[]>("/api/integrations/homepage/fields"),
  getHomepagePreview: (ups = "") => {
    const qs = ups ? `?ups=${encodeURIComponent(ups)}` : "";
    return request<HomepageSummary>(`/api/integrations/homepage/preview${qs}`);
  },
  testHomepageSummary: (ups = "", apiKey = "") => {
    const qs = ups ? `?ups=${encodeURIComponent(ups)}` : "";
    const headers: Record<string, string> = {};
    if (apiKey.trim()) {
      headers["X-API-Key"] = apiKey.trim();
    }
    return request<HomepageSummary>(`/api/integrations/homepage/summary${qs}`, {
      headers,
    });
  },
  generateHomepageConfig: (payload: HomepageConfigRequest) =>
    request<HomepageConfigResponse>("/api/integrations/homepage/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getHomeAssistantPreview: () =>
    request<HomeAssistantState>("/api/integrations/homeassistant/preview"),
  testHomeAssistantInfo: (apiKey = "") => {
    const headers: Record<string, string> = {};
    if (apiKey.trim()) {
      headers["X-API-Key"] = apiKey.trim();
    }
    return request<HomeAssistantInfo>("/api/integrations/homeassistant/info", {
      headers,
    });
  },
  generatePrometheusConfig: (payload: PrometheusConfigRequest) =>
    request<PrometheusConfigResponse>("/api/integrations/prometheus/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  testPrometheusMetrics: async (apiKey = "") => {
    const headers: Record<string, string> = {};
    if (apiKey.trim()) {
      headers.Authorization = `Bearer ${apiKey.trim()}`;
    }
    const res = await fetch("/api/integrations/prometheus/metrics", { headers });
    const text = await res.text();
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const parsed = JSON.parse(text) as { detail?: string };
        if (parsed?.detail) msg = parsed.detail;
      } catch {
        // keep HTTP status message
      }
      throw new ApiError(msg, res.status, text);
    }
    return text;
  },

  // UPS Control & Self-Test Scheduler
  getUpsCapabilities: (upsName: string) =>
    request<UpsCapabilitiesResponse>(
      `/api/control/${encodeURIComponent(upsName)}/capabilities`
    ),
  runUpsCommand: (upsName: string, command: string, confirmDangerous = false) =>
    request<RunUpsCommandResponse>(`/api/control/${encodeURIComponent(upsName)}/command`, {
      method: "POST",
      body: JSON.stringify({ command, confirm_dangerous: confirmDangerous }),
    }),
  setUpsVar: (upsName: string, varName: string, value: string) =>
    request<SetUpsVarResponse>(`/api/control/${encodeURIComponent(upsName)}/var`, {
      method: "POST",
      body: JSON.stringify({ var_name: varName, value }),
    }),
  getSelfTestSchedules: () =>
    request<SelfTestScheduleRecord[]>("/api/control/schedules"),
  createSelfTestSchedule: (payload: SelfTestSchedulePayload) =>
    request<SelfTestScheduleRecord>("/api/control/schedules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateSelfTestSchedule: (id: number, payload: SelfTestSchedulePayload) =>
    request<SelfTestScheduleRecord>(`/api/control/schedules/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteSelfTestSchedule: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/control/schedules/${id}`, {
      method: "DELETE",
    }),
  runSelfTestScheduleNow: (id: number) =>
    request<SelfTestReportRecord>(`/api/control/schedules/${id}/run-now`, {
      method: "POST",
    }),
  getSelfTestReports: (upsName?: string, limit = 100) => {
    const qs = new URLSearchParams();
    if (upsName) qs.set("ups_name", upsName);
    if (limit) qs.set("limit", String(limit));
    const q = qs.toString();
    return request<SelfTestReportsResponse>(`/api/control/reports${q ? `?${q}` : ""}`);
  },
  deleteSelfTestReport: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/control/reports/${id}`, {
      method: "DELETE",
    }),
  clearSelfTestReports: (upsName?: string) => {
    const qs = upsName ? `?ups_name=${encodeURIComponent(upsName)}` : "";
    return request<{ cleared: number }>(`/api/control/reports${qs}`, {
      method: "DELETE",
    });
  },

  // End-to-End Flow Drills
  runFlowDrill: (payload: FlowDrillRunPayload) =>
    request<FlowDrillReportRecord>("/api/drills/run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  abortFlowDrill: (reportId: number) =>
    request<FlowDrillReportRecord>(`/api/drills/runs/${reportId}/abort`, {
      method: "POST",
    }),
  getFlowDrillSchedules: () =>
    request<FlowDrillScheduleRecord[]>("/api/drills/schedules"),
  createFlowDrillSchedule: (payload: FlowDrillSchedulePayload) =>
    request<FlowDrillScheduleRecord>("/api/drills/schedules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateFlowDrillSchedule: (id: number, payload: FlowDrillSchedulePayload) =>
    request<FlowDrillScheduleRecord>(`/api/drills/schedules/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteFlowDrillSchedule: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/drills/schedules/${id}`, {
      method: "DELETE",
    }),
  runFlowDrillScheduleNow: (id: number) =>
    request<FlowDrillReportRecord>(`/api/drills/schedules/${id}/run-now`, {
      method: "POST",
    }),
  getFlowDrillReports: (limit = 100) =>
    request<FlowDrillReportsResponse>(`/api/drills/reports?limit=${limit}`),
  getFlowDrillReport: (reportId: number) =>
    request<FlowDrillReportRecord>(`/api/drills/reports/${reportId}`),
  deleteFlowDrillReport: (reportId: number) =>
    request<{ deleted: boolean; id: number }>(`/api/drills/reports/${reportId}`, {
      method: "DELETE",
    }),
  clearFlowDrillReports: () =>
    request<{ cleared: number }>("/api/drills/reports", {
      method: "DELETE",
    }),
};
