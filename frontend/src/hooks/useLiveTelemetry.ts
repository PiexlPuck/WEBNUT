import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  AppVersionStatus,
  EventLogEntry,
  HostRecord,
  NutServiceStatus,
  OrchestratorState,
  TelemetrySnapshot,
} from "../types";

function normalizeHost(raw: Record<string, unknown>): HostRecord {
  return {
    ...(raw as unknown as HostRecord),
    is_online: Boolean(raw.is_online),
    was_online_before_outage: Boolean(raw.was_online_before_outage),
    always_wake: Boolean(raw.always_wake),
    enabled: Boolean(raw.enabled),
    nut_client_enabled: Boolean(raw.nut_client_enabled),
    nut_client_platform: (raw.nut_client_platform as HostRecord["nut_client_platform"]) || "linux_debian",
    nut_ups_name: String(raw.nut_ups_name ?? ""),
    nut_client_user: String(raw.nut_client_user ?? ""),
    virtual_ups_name: String(raw.virtual_ups_name ?? ""),
    wake_min_battery_charge:
      raw.wake_min_battery_charge !== null && raw.wake_min_battery_charge !== undefined
        ? Number(raw.wake_min_battery_charge)
        : null,
    shutdown_mode: (raw.shutdown_mode as HostRecord["shutdown_mode"]) || "nut_stream",
    shutdown_battery_pct:
      raw.shutdown_battery_pct !== null && raw.shutdown_battery_pct !== undefined
        ? Number(raw.shutdown_battery_pct)
        : null,
    shutdown_ob_delay_sec:
      raw.shutdown_ob_delay_sec !== null && raw.shutdown_ob_delay_sec !== undefined
        ? Number(raw.shutdown_ob_delay_sec)
        : null,
    disconnect_policy:
      (raw.disconnect_policy as HostRecord["disconnect_policy"]) || "inherit",
    disconnect_grace_sec:
      raw.disconnect_grace_sec !== null && raw.disconnect_grace_sec !== undefined
        ? Number(raw.disconnect_grace_sec)
        : null,
    ssh_port: Number(raw.ssh_port ?? 22),
    ssh_username: String(raw.ssh_username ?? ""),
    ssh_password: String(raw.ssh_password ?? ""),
    ssh_sudo_password: String(raw.ssh_sudo_password ?? ""),
    ssh_shutdown_cmd: String(raw.ssh_shutdown_cmd ?? "sudo -S shutdown -h now"),
    ssh_password_configured: Boolean(raw.ssh_password_configured),
  };
}

export interface LiveState {
  wsConnected: boolean;
  telemetry: TelemetrySnapshot;
  hosts: HostRecord[];
  orchestrator: OrchestratorState;
  services: NutServiceStatus[];
  recentEvents: EventLogEntry[];
  versionStatus: AppVersionStatus | null;
  checkingUpdates: boolean;
  checkUpdatesNow: () => Promise<void>;
  refreshAll: () => Promise<void>;
}

const DEFAULT_TELEMETRY: TelemetrySnapshot = {
  connected: false,
  error: "Connecting...",
  ups_list: [],
  updated_at: new Date().toISOString(),
};

const DEFAULT_ORCHESTRATOR: OrchestratorState = {
  state: "NORMAL",
  last_ups_status: "UNKNOWN",
  outage_started_at: null,
  stabilizing_started_at: null,
  recovery_started_at: null,
  snapshot_frozen: false,
  is_dry_run: false,
  stability_remaining_sec: 0,
  disconnect_started_at: null,
  disconnect_elapsed_sec: 0,
  updated_at: new Date().toISOString(),
};

export function useLiveTelemetry(): LiveState {
  const [wsConnected, setWsConnected] = useState(false);
  const [telemetry, setTelemetry] = useState<TelemetrySnapshot>(DEFAULT_TELEMETRY);
  const [hosts, setHosts] = useState<HostRecord[]>([]);
  const [orchestrator, setOrchestrator] = useState<OrchestratorState>(DEFAULT_ORCHESTRATOR);
  const [services, setServices] = useState<NutServiceStatus[]>([]);
  const [recentEvents, setRecentEvents] = useState<EventLogEntry[]>([]);
  const [versionStatus, setVersionStatus] = useState<AppVersionStatus | null>(null);
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  const reconnectTimer = useRef<number | null>(null);

  const checkUpdatesNow = useCallback(async () => {
    setCheckingUpdates(true);
    try {
      const status = await api.checkForUpdates();
      setVersionStatus(status);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to reach WebNUT backend";
      setVersionStatus((prev) => ({
        current_version: prev?.current_version ?? "2.1.2",
        latest_version: prev?.latest_version ?? null,
        update_available: prev?.update_available ?? false,
        release_url: prev?.release_url ?? null,
        release_notes: prev?.release_notes ?? null,
        last_checked_at: new Date().toISOString(),
        last_error: msg,
        notified_version: prev?.notified_version ?? null,
        notified_at: prev?.notified_at ?? null,
        github_repo: prev?.github_repo ?? "PiexlPuck/WEBNUT",
      }));
    } finally {
      setCheckingUpdates(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const [tel, h, orch, svc, ev, ver] = await Promise.all([
        api.getTelemetry(),
        api.getHosts(),
        api.getOrchestratorStatus(),
        api.getServices(),
        api.getEvents({ limit: 15, offset: 0 }),
        api.getVersionStatus(),
      ]);
      setTelemetry(tel);
      setHosts(h);
      setOrchestrator(orch);
      setServices(svc);
      setRecentEvents(ev.items);
      setVersionStatus(ver);
    } catch {
      // Ignore transient hydration failures
    }
  }, []);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;

    const connect = () => {
      if (disposed) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws/live`);

      ws.onopen = () => {
        if (!disposed) setWsConnected(true);
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data) as { type: string; data: unknown };
          if (msg.type === "telemetry") {
            setTelemetry(msg.data as TelemetrySnapshot);
          } else if (msg.type === "hosts") {
            const list = (msg.data as Record<string, unknown>[]).map(normalizeHost);
            setHosts(list);
          } else if (msg.type === "orchestrator") {
            setOrchestrator(msg.data as OrchestratorState);
          } else if (msg.type === "services") {
            setServices(msg.data as NutServiceStatus[]);
          } else if (msg.type === "event") {
            const item = msg.data as EventLogEntry;
            setRecentEvents((prev) => [item, ...prev.filter((e) => e.id !== item.id)].slice(0, 25));
          } else if (msg.type === "update_status") {
            setVersionStatus(msg.data as AppVersionStatus);
          }
        } catch {
          // Ignore malformed frame
        }
      };

      ws.onclose = () => {
        if (disposed) return;
        setWsConnected(false);
        reconnectTimer.current = window.setTimeout(connect, 2500);
      };

      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current);
      }
      ws?.close();
    };
  }, []);

  return {
    wsConnected,
    telemetry,
    hosts,
    orchestrator,
    services,
    recentEvents,
    versionStatus,
    checkingUpdates,
    checkUpdatesNow,
    refreshAll,
  };
}
