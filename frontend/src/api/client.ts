import type {
  AppVersionStatus,
  ArpLookupResponse,
  CreateSecondaryUserResponse,
  EventLogPage,
  HardwareScanResponse,
  HostPayload,
  HostRecord,
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
  OrchestratorConfig,
  OrchestratorState,
  PushoverConfigPayload,
  PushoverConfigView,
  ScannedUsbDevice,
  TelemetrySnapshot,
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
  createSecondaryNutUser: (username: string, password = "") =>
    request<CreateSecondaryUserResponse>("/api/nut/clients/create-user", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  generateNutClientConfig: (payload: NutClientConfigRequest) =>
    request<NutClientConfigResponse>("/api/nut/clients/generate", {
      method: "POST",
      body: JSON.stringify(payload),
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

  // Orchestrator
  getOrchestratorStatus: () => request<OrchestratorState>("/api/orchestrator/status"),
  getOrchestratorConfig: () => request<OrchestratorConfig>("/api/orchestrator/config"),
  updateOrchestratorConfig: (cfg: OrchestratorConfig) =>
    request<OrchestratorConfig>("/api/orchestrator/config", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),
  simulateRecovery: (dryRun: boolean) =>
    request<OrchestratorState>("/api/orchestrator/simulate-recovery", {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun }),
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

  // System / Version
  getVersionStatus: () => request<AppVersionStatus>("/api/system/version"),
  checkForUpdates: () =>
    request<AppVersionStatus>("/api/system/version/check", { method: "POST" }),
};
