import { useState } from "react";
import {
  Activity,
  BatteryCharging,
  Clock,
  Cpu,
  Play,
  PlugZap,
  Server,
  Zap,
} from "lucide-react";
import { api } from "../api/client";
import { RecoveryBanner } from "../components/RecoveryBanner";
import { SeverityBadge, UpsStatusBadge, WakeStateBadge } from "../components/StatusBadge";
import type { EventLogEntry, HostRecord, OrchestratorState, TelemetrySnapshot } from "../types";

interface DashboardPageProps {
  telemetry: TelemetrySnapshot;
  hosts: HostRecord[];
  orchestrator: OrchestratorState;
  recentEvents: EventLogEntry[];
  onNavigateNut: () => void;
  onNavigateHosts: () => void;
}

function formatRuntime(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins >= 60) {
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }
  return `${mins}m ${secs}s`;
}

export function DashboardPage({
  telemetry,
  hosts,
  orchestrator,
  recentEvents,
  onNavigateNut,
  onNavigateHosts,
}: DashboardPageProps) {
  const [selectedUpsIdx, setSelectedUpsIdx] = useState(0);
  const [wakingId, setWakingId] = useState<number | null>(null);

  const ups = telemetry.ups_list[selectedUpsIdx] ?? telemetry.ups_list[0];

  const handleWakeNow = async (hostId: number) => {
    setWakingId(hostId);
    try {
      await api.wakeHost(hostId, true);
    } finally {
      setWakingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">System Telemetry</h2>
          <p className="text-sm text-slate-400">
            Real-time UPS status, pre-outage host snapshot, and WoL recovery matrix
          </p>
        </div>

        {telemetry.ups_list.length > 1 && (
          <select
            value={selectedUpsIdx}
            onChange={(e) => setSelectedUpsIdx(Number(e.target.value))}
            className="bg-surface-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200"
          >
            {telemetry.ups_list.map((u, idx) => (
              <option key={u.name} value={idx}>
                {u.name} ({u.status})
              </option>
            ))}
          </select>
        )}
      </div>

      <RecoveryBanner orchestrator={orchestrator} />

      {/* UPS Telemetry Section */}
      {!telemetry.connected || !ups ? (
        <div className="rounded-2xl border border-slate-800 bg-surface-900 p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-amber-300 font-semibold text-sm">
              <PlugZap className="h-4 w-4" />
              <span>
                {telemetry.connected ? "No UPS Configured in NUT" : "NUT upsd Not Connected"}
              </span>
            </div>
            <p className="text-xs text-slate-400">
              {telemetry.error ||
                "Use USB Auto-Discovery in NUT Config to detect your UPS and start upsd."}
            </p>
          </div>
          <button
            onClick={onNavigateNut}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white shrink-0"
          >
            Configure NUT
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-3">
                <h3 className="text-lg font-semibold text-white font-mono">{ups.name}</h3>
                <UpsStatusBadge status={ups.status} />
              </div>
              <p className="text-xs text-slate-400 mt-1">
                {[ups.ups_mfr, ups.ups_model, ups.description].filter(Boolean).join(" • ") ||
                  "Active UPS"}
              </p>
            </div>
            <div className="text-xs text-slate-400">
              Updated{" "}
              <span className="font-mono text-slate-300">
                {new Date(telemetry.updated_at).toLocaleTimeString()}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
              <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                <span>Battery Charge</span>
                <BatteryCharging className="h-4 w-4 text-emerald-400" />
              </div>
              <div className="text-2xl font-bold text-white">
                {ups.battery_charge !== null ? `${ups.battery_charge.toFixed(0)}%` : "—"}
              </div>
              <div className="mt-3 h-2 rounded-full bg-surface-800 overflow-hidden">
                <div
                  className={`h-full transition-all duration-300 ${
                    (ups.battery_charge ?? 0) >= 50
                      ? "bg-emerald-500"
                      : (ups.battery_charge ?? 0) >= 25
                      ? "bg-amber-500"
                      : "bg-rose-500"
                  }`}
                  style={{ width: `${Math.min(100, Math.max(0, ups.battery_charge ?? 0))}%` }}
                />
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
              <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                <span>UPS Load</span>
                <Cpu className="h-4 w-4 text-indigo-400" />
              </div>
              <div className="text-2xl font-bold text-white">
                {ups.ups_load !== null ? `${ups.ups_load.toFixed(0)}%` : "—"}
              </div>
              <div className="mt-3 h-2 rounded-full bg-surface-800 overflow-hidden">
                <div
                  className="h-full bg-indigo-500 transition-all duration-300"
                  style={{ width: `${Math.min(100, Math.max(0, ups.ups_load ?? 0))}%` }}
                />
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
              <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                <span>Estimated Runtime</span>
                <Clock className="h-4 w-4 text-sky-400" />
              </div>
              <div className="text-2xl font-bold text-white">
                {formatRuntime(ups.battery_runtime)}
              </div>
              <p className="text-xs text-slate-500 mt-2">Based on current load</p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
              <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                <span>Input / Output Voltage</span>
                <Zap className="h-4 w-4 text-amber-400" />
              </div>
              <div className="text-2xl font-bold text-white">
                {ups.input_voltage !== null ? `${ups.input_voltage.toFixed(1)}V` : "—"}
                <span className="text-slate-500 text-base font-normal mx-1.5">/</span>
                {ups.output_voltage !== null ? `${ups.output_voltage.toFixed(1)}V` : "—"}
              </div>
              <p className="text-xs text-slate-500 mt-2">Line in / Inverter out</p>
            </div>
          </div>
        </div>
      )}

      {/* Host Recovery Matrix */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server className="h-4 w-4 text-indigo-400" />
            <h3 className="text-base font-semibold text-white">Host Recovery Matrix</h3>
            {orchestrator.snapshot_frozen && (
              <span className="text-[11px] px-2 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
                Snapshot Frozen
              </span>
            )}
          </div>
          <button
            onClick={onNavigateHosts}
            className="text-xs text-indigo-400 hover:text-indigo-300 font-medium"
          >
            Manage Hosts &rarr;
          </button>
        </div>

        {hosts.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-400">
            No managed hosts registered yet. Add servers in{" "}
            <button onClick={onNavigateHosts} className="text-indigo-400 underline">
              Hosts &amp; Recovery
            </button>
            .
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface-950/60 text-xs uppercase text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="px-4 py-3">Host</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">MAC Address</th>
                  <th className="px-4 py-3">Pre-Outage</th>
                  <th className="px-4 py-3">Wake State</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {hosts.map((host) => (
                  <tr key={host.id} className="hover:bg-surface-800/40">
                    <td className="px-4 py-3">
                      <div className="font-medium text-white">{host.name}</div>
                      <div className="text-xs text-slate-400 font-mono">{host.ip_or_host}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs font-medium ${
                          host.is_online ? "text-emerald-400" : "text-slate-400"
                        }`}
                      >
                        <span
                          className={`h-2 w-2 rounded-full ${
                            host.is_online ? "bg-emerald-400" : "bg-slate-600"
                          }`}
                        />
                        {host.is_online ? "Online" : "Offline"}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      {host.mac_address}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {host.always_wake ? (
                        <span className="text-indigo-300">Always Wake</span>
                      ) : host.was_online_before_outage ? (
                        <span className="text-emerald-400">Was Online</span>
                      ) : (
                        <span className="text-slate-500">Was Offline</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <WakeStateBadge
                        state={host.wake_state}
                        attempt={host.wake_attempt}
                        maxRetries={host.max_retries}
                      />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleWakeNow(host.id)}
                        disabled={wakingId === host.id}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-indigo-300 text-xs font-medium disabled:opacity-50"
                      >
                        <Play className="h-3 w-3" />
                        Wake Now
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent Activity */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
          <Activity className="h-4 w-4 text-sky-400" />
          <h3 className="text-base font-semibold text-white">Recent Events</h3>
        </div>
        <div className="divide-y divide-slate-800/70">
          {recentEvents.slice(0, 8).map((ev) => (
            <div key={ev.id} className="px-5 py-3 flex items-start justify-between gap-4 text-xs">
              <div className="flex items-start gap-2.5 min-w-0">
                <SeverityBadge severity={ev.severity} />
                <div className="min-w-0">
                  <p className="text-slate-200 truncate">{ev.message}</p>
                  <p className="text-slate-500 font-mono mt-0.5">
                    {ev.category} • {ev.event_type}
                  </p>
                </div>
              </div>
              <span className="text-slate-500 font-mono shrink-0">
                {new Date(ev.timestamp).toLocaleTimeString()}
              </span>
            </div>
          ))}
          {recentEvents.length === 0 && (
            <div className="p-6 text-center text-xs text-slate-500">No events recorded yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
