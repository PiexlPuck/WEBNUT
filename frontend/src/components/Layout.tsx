import { useState, type ReactNode } from "react";
import {
  Activity,
  ArrowUpCircle,
  Bell,
  Check,
  Copy,
  FileText,
  LayoutGrid,
  Link2,
  Power,
  RefreshCw,
  Server,
  Settings2,
  Sliders,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import type {
  AppVersionStatus,
  NutServiceStatus,
  OrchestratorState,
  TelemetrySnapshot,
} from "../types";

export type NavTab =
  | "dashboard"
  | "hosts"
  | "clients"
  | "integrations"
  | "nut"
  | "control"
  | "notifications"
  | "events";

interface LayoutProps {
  activeTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  wsConnected: boolean;
  telemetry: TelemetrySnapshot;
  orchestrator: OrchestratorState;
  services: NutServiceStatus[];
  versionStatus: AppVersionStatus | null;
  checkingUpdates: boolean;
  onCheckUpdates: () => Promise<void>;
  children: ReactNode;
}

const NAV_ITEMS: { id: NavTab; label: string; icon: typeof Activity }[] = [
  { id: "dashboard", label: "Overview", icon: Activity },
  { id: "nut", label: "UPS Setup", icon: Settings2 },
  { id: "control", label: "UPS Control", icon: Sliders },
  { id: "hosts", label: "Managed Hosts", icon: Server },
  { id: "clients", label: "Client Setup", icon: Link2 },
  { id: "integrations", label: "Integrations", icon: LayoutGrid },
  { id: "notifications", label: "Alerts", icon: Bell },
  { id: "events", label: "Activity Log", icon: FileText },
];

export function Layout({
  activeTab,
  onSelectTab,
  wsConnected,
  telemetry,
  orchestrator,
  services,
  versionStatus,
  checkingUpdates,
  onCheckUpdates,
  children,
}: LayoutProps) {
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [copiedCmd, setCopiedCmd] = useState(false);

  const updateCmd =
    "curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash -s -- --update";
  const copyUpdateCmd = async () => {
    await navigator.clipboard.writeText(updateCmd);
    setCopiedCmd(true);
    window.setTimeout(() => setCopiedCmd(false), 2000);
  };
  const primaryUps = telemetry.ups_list[0];
  const upsStatus = primaryUps?.status ?? (telemetry.connected ? "NO UPS" : "DISCONNECTED");
  const isOb = upsStatus.includes("OB");
  const isOl = upsStatus.includes("OL");

  const statusClasses = !telemetry.connected
    ? "bg-slate-800 text-slate-300 border-slate-700"
    : isOb
    ? "bg-amber-500/15 text-amber-300 border-amber-500/40"
    : isOl
    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
    : "bg-sky-500/15 text-sky-300 border-sky-500/40";

  return (
    <div className="min-h-screen bg-surface-950 text-slate-100 flex flex-col lg:flex-row">
      {/* Sidebar */}
      <aside className="w-full lg:w-64 bg-surface-900 border-b lg:border-b-0 lg:border-r border-slate-800/80 flex flex-col shrink-0">
        <div className="px-5 py-4 border-b border-slate-800/80 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-lg bg-indigo-500/20 border border-indigo-400/30 flex items-center justify-center text-indigo-400">
              <Zap className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-base font-bold tracking-tight text-white">WebNUT</h1>
              <p className="text-[11px] text-slate-400">UPS &amp; Power Orchestrator</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setBannerDismissed(false);
                void onCheckUpdates();
              }}
              disabled={checkingUpdates}
              title="Check for WebNUT updates"
              className="lg:hidden inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700/80 text-[11px] text-slate-300 disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${checkingUpdates ? "animate-spin" : ""}`} />
              <span>v{versionStatus?.current_version ?? "2.2.1"}</span>
            </button>
            <span
              title={wsConnected ? "Live WebSocket Connected" : "Reconnecting..."}
              className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border ${
                wsConnected
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                  : "bg-rose-500/10 text-rose-400 border-rose-500/30"
              }`}
            >
              {wsConnected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
              {wsConnected ? "Live" : "Off"}
            </span>
          </div>
        </div>

        <nav className="p-3 flex lg:flex-col gap-1 overflow-x-auto">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectTab(item.id)}
                className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
                  active
                    ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/30"
                    : "text-slate-400 hover:text-slate-100 hover:bg-surface-800/70 border border-transparent"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="hidden lg:block mt-auto p-4 border-t border-slate-800/80 space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">UPS Status</span>
            <span className={`px-2 py-0.5 rounded border font-mono font-semibold ${statusClasses}`}>
              {upsStatus}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-slate-400">Recovery State</span>
            <span className="font-mono text-slate-200">{orchestrator.state}</span>
          </div>
          <div className="pt-2 border-t border-slate-800/60 space-y-1.5">
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              NUT Daemons
            </div>
            {services.map((svc) => (
              <div key={svc.name} className="flex items-center justify-between">
                <span className="text-slate-400 font-mono">{svc.name}</span>
                <span
                  className={`inline-flex items-center gap-1 ${
                    svc.status === "running"
                      ? "text-emerald-400"
                      : svc.status === "error"
                      ? "text-rose-400"
                      : "text-slate-400"
                  }`}
                >
                  <Power className="h-3 w-3" />
                  {svc.status}
                </span>
              </div>
            ))}
          </div>

          <div className="pt-2 border-t border-slate-800/60 space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <span className="text-slate-400 font-mono">
                  v{versionStatus?.current_version ?? "2.2.1"}
                </span>
                {versionStatus?.update_available && (
                  <span
                    title={`Update available: v${versionStatus.latest_version}`}
                    className="h-2 w-2 rounded-full bg-amber-400 animate-pulse"
                  />
                )}
              </div>
              <button
                onClick={() => {
                  setBannerDismissed(false);
                  void onCheckUpdates();
                }}
                disabled={checkingUpdates}
                title="Check for WebNUT updates"
                className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700/80 text-[11px] text-slate-300 disabled:opacity-50"
              >
                <RefreshCw className={`h-3 w-3 ${checkingUpdates ? "animate-spin" : ""}`} />
                {checkingUpdates ? "Checking..." : "Update"}
              </button>
            </div>

            {checkingUpdates ? (
              <div className="text-[11px] text-indigo-300">Checking GitHub for updates...</div>
            ) : versionStatus?.last_error ? (
              <div
                title={versionStatus.last_error}
                className="text-[11px] text-rose-400 truncate"
              >
                Check failed: {versionStatus.last_error}
              </div>
            ) : versionStatus?.update_available && versionStatus.latest_version ? (
              <button
                onClick={() => setBannerDismissed(false)}
                className="text-[11px] text-amber-300 hover:text-amber-200 underline text-left"
              >
                v{versionStatus.latest_version} available — view update command
              </button>
            ) : versionStatus?.last_checked_at ? (
              <div className="text-[11px] text-emerald-400/90">
                Up to date • {new Date(versionStatus.last_checked_at).toLocaleTimeString()}
              </div>
            ) : null}
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 p-4 sm:p-6 lg:p-8 max-w-7xl w-full mx-auto">
        {versionStatus?.update_available && !bannerDismissed && (
          <div className="mb-6 rounded-xl border border-indigo-500/40 bg-indigo-500/10 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-start gap-3">
              <ArrowUpCircle className="h-5 w-5 text-indigo-400 shrink-0 mt-0.5" />
              <div className="text-xs space-y-1">
                <div className="text-sm font-semibold text-white">
                  WEBNUT Update Available: v{versionStatus.latest_version}
                  <span className="ml-2 text-xs font-normal text-slate-400">
                    (running v{versionStatus.current_version})
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-slate-300">
                  <span>Run on host:</span>
                  <code className="px-2 py-0.5 rounded bg-surface-950 border border-slate-700 font-mono text-[11px] text-indigo-300">
                    {updateCmd}
                  </code>
                  <button
                    onClick={() => void copyUpdateCmd()}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-800 hover:bg-surface-700 text-[11px] text-slate-200"
                  >
                    {copiedCmd ? (
                      <Check className="h-3 w-3 text-emerald-400" />
                    ) : (
                      <Copy className="h-3 w-3" />
                    )}
                    {copiedCmd ? "Copied" : "Copy"}
                  </button>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {versionStatus.release_url && (
                <a
                  href={versionStatus.release_url}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
                >
                  View on GitHub
                </a>
              )}
              <button
                onClick={() => setBannerDismissed(true)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-surface-800"
                title="Dismiss"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
