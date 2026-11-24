import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  KeyRound,
  Link2,
  RefreshCw,
  Server,
  Terminal,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type {
  HostRecord,
  NutClientConfigRequest,
  NutClientConfigResponse,
  NutClientPlatform,
  NutClientsOverview,
  TelemetrySnapshot,
} from "../types";

interface NutClientsPageProps {
  hosts: HostRecord[];
  telemetry: TelemetrySnapshot;
  preselectedHostId: number | null;
  onClearPreselectedHost: () => void;
}

const PLATFORMS: { id: NutClientPlatform; label: string }[] = [
  { id: "linux_debian", label: "Debian / Ubuntu / Proxmox" },
  { id: "linux_rhel", label: "RHEL / Rocky / Fedora" },
  { id: "truenas", label: "TrueNAS SCALE / CORE" },
  { id: "synology", label: "Synology DSM" },
  { id: "windows", label: "Windows (WinNUT)" },
];

export function NutClientsPage({
  hosts,
  telemetry,
  preselectedHostId,
  onClearPreselectedHost,
}: NutClientsPageProps) {
  const [overview, setOverview] = useState<NutClientsOverview | null>(null);
  const [selectedHostId, setSelectedHostId] = useState<string>("");
  const [form, setForm] = useState<NutClientConfigRequest>({
    platform: "linux_debian",
    server_host: "",
    nut_port: 3493,
    ups_name: "ups1",
    username: "monuser",
    password: "",
    final_delay: 5,
    poll_freq: 5,
    deadtime: 15,
    shutdown_cmd: "/sbin/shutdown -h +0",
  });
  const [generated, setGenerated] = useState<NutClientConfigResponse | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [loadingOverview, setLoadingOverview] = useState(false);

  // Secondary user creator state
  const [newUser, setNewUser] = useState("wolnut_client");
  const [newPass, setNewPass] = useState("");
  const [creatingUser, setCreatingUser] = useState(false);
  const [userFeedback, setUserFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const loadOverview = useCallback(async () => {
    setLoadingOverview(true);
    try {
      const data = await api.getNutClients();
      setOverview(data);
      setForm((prev) => ({
        ...prev,
        server_host: prev.server_host || data.server_ip,
        nut_port: data.nut_port || prev.nut_port,
        ups_name:
          prev.ups_name && prev.ups_name !== "ups1"
            ? prev.ups_name
            : data.ups_names[0] || "ups1",
        username:
          prev.username && prev.username !== "monuser"
            ? prev.username
            : data.available_users.find((u) => u !== "admin") || data.available_users[0] || "monuser",
      }));
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  // Pre-fill when navigated from a specific Managed Host
  useEffect(() => {
    if (preselectedHostId === null) return;
    const h = hosts.find((item) => item.id === preselectedHostId);
    if (h) {
      setSelectedHostId(String(h.id));
      setForm((prev) => ({
        ...prev,
        platform: h.nut_client_platform || prev.platform,
        ups_name: h.nut_ups_name || prev.ups_name,
        username: h.nut_client_user || prev.username,
      }));
    }
    onClearPreselectedHost();
  }, [preselectedHostId, hosts, onClearPreselectedHost]);

  const handleSelectHost = (hostIdStr: string) => {
    setSelectedHostId(hostIdStr);
    if (!hostIdStr) return;
    const h = hosts.find((item) => item.id === Number(hostIdStr));
    if (!h) return;
    setForm((prev) => ({
      ...prev,
      platform: h.nut_client_platform || prev.platform,
      ups_name: h.nut_ups_name || overview?.ups_names[0] || prev.ups_name,
      username: h.nut_client_user || prev.username,
    }));
  };

  const handleCreateUser = async () => {
    if (!newUser.trim()) return;
    setCreatingUser(true);
    setUserFeedback(null);
    try {
      const res = await api.createSecondaryNutUser(newUser.trim(), newPass.trim());
      setForm((prev) => ({
        ...prev,
        username: res.username,
        password: res.password,
      }));
      setNewPass(res.password);
      setUserFeedback({
        ok: true,
        msg: `Created/updated secondary NUT user '${res.username}' and applied upsd.users. Password pre-filled below.`,
      });
      await loadOverview();
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to create secondary NUT user",
      });
    } finally {
      setCreatingUser(false);
    }
  };

  const handleGenerate = async () => {
    setUserFeedback(null);
    if (!form.password.trim()) {
      setUserFeedback({
        ok: false,
        msg: "Enter the NUT user password (or click 'Create Secondary User' above to generate one).",
      });
      return;
    }
    try {
      const res = await api.generateNutClientConfig(form);
      setGenerated(res);
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to generate client config",
      });
    }
  };

  const copyText = async (key: string, text: string) => {
    await navigator.clipboard.writeText(text);
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey(null), 2000);
  };

  // Live connected client IPs directly from WebSocket telemetry (real-time) + REST fallback
  const liveClientPairs = useMemo(() => {
    const ipToHost = new Map(hosts.map((h) => [h.ip_or_host.trim(), h]));
    const pairs: { upsName: string; ip: string; host?: HostRecord }[] = [];
    for (const u of telemetry.ups_list) {
      for (const ip of u.connected_clients ?? []) {
        pairs.push({ upsName: u.name, ip, host: ipToHost.get(ip) });
      }
    }
    if (pairs.length === 0 && overview?.connected_clients.length) {
      for (const c of overview.connected_clients) {
        pairs.push({
          upsName: c.ups_name,
          ip: c.client_ip,
          host: hosts.find((h) => h.id === c.matched_host_id),
        });
      }
    }
    return pairs;
  }, [telemetry.ups_list, overview, hosts]);

  const connectedIpSet = useMemo(
    () => new Set(liveClientPairs.map((p) => p.ip)),
    [liveClientPairs]
  );

  const linkedHosts = useMemo(
    () => hosts.filter((h) => h.nut_client_enabled),
    [hosts]
  );

  const missingLinkedHosts = useMemo(
    () => linkedHosts.filter((h) => !connectedIpSet.has(h.ip_or_host.trim())),
    [linkedHosts, connectedIpSet]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            NUT Client Configurator &amp; Pipeline Link
          </h2>
          <p className="text-sm text-slate-400">
            Configure secondary `upsmon` clients on your servers so they shut down gracefully on
            `OB`/`LB` before WEBNUT wakes them on `OL`
          </p>
        </div>
        <button
          onClick={() => void loadOverview()}
          disabled={loadingOverview}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loadingOverview ? "animate-spin" : ""}`} />
          Refresh Clients
        </button>
      </div>

      {/* End-to-End Outage -> Shutdown -> Recovery Pipeline Banner */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-4">
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-3">
          Unified Outage &amp; Recovery Pipeline
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-center text-xs">
          <div className="rounded-xl bg-surface-950 border border-slate-800 p-3">
            <div className="font-semibold text-amber-300">1. UPS On Battery (OB)</div>
            <div className="text-slate-400 mt-1">
              WEBNUT freezes pre-outage snapshot &amp; notifies `upsd` clients
            </div>
          </div>
          <div className="rounded-xl bg-surface-950 border border-indigo-500/30 p-3">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-indigo-300">2. Client Graceful Shutdown</span>
              <ArrowRight className="h-3.5 w-3.5 text-slate-500 hidden md:block" />
            </div>
            <div className="text-slate-300 mt-1 font-mono">
              {liveClientPairs.length} connected • {linkedHosts.length} linked host(s)
            </div>
          </div>
          <div className="rounded-xl bg-surface-950 border border-slate-800 p-3">
            <div className="font-semibold text-emerald-300">3. Power Restored (OL)</div>
            <div className="text-slate-400 mt-1">
              Battery meets safe threshold &amp; stability hold timer completes
            </div>
          </div>
          <div className="rounded-xl bg-surface-950 border border-slate-800 p-3">
            <div className="font-semibold text-sky-300">4. Staggered WoL + Retry</div>
            <div className="text-slate-400 mt-1">
              Wakes hosts in priority order &amp; verifies Ping/TCP reachability
            </div>
          </div>
        </div>
      </div>

      {userFeedback && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm ${
            userFeedback.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-rose-500/40 bg-rose-500/10 text-rose-300"
          }`}
        >
          {userFeedback.msg}
        </div>
      )}

      {/* Live Connected NUT Clients + Linked Host Mismatches */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-emerald-400" />
              <h3 className="text-sm font-semibold text-white">
                Live Connected NUT Clients (`upsd`)
              </h3>
            </div>
            <span className="text-xs font-mono text-slate-400">
              {liveClientPairs.length} active
            </span>
          </div>
          <div className="divide-y divide-slate-800/70">
            {liveClientPairs.map((c, idx) => (
              <div
                key={`${c.upsName}-${c.ip}-${idx}`}
                className="px-5 py-3 flex items-center justify-between text-xs"
              >
                <div>
                  <div className="font-mono text-white font-medium">{c.ip}</div>
                  <div className="text-slate-400">UPS: {c.upsName}</div>
                </div>
                {c.host ? (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                    <Server className="h-3 w-3" />
                    {c.host.name}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                    Unregistered Client
                  </span>
                )}
              </div>
            ))}
            {liveClientPairs.length === 0 && (
              <div className="p-6 text-center text-xs text-slate-500">
                No external `upsmon` clients currently reported by `upsd` (`LIST CLIENT`).
              </div>
            )}
          </div>
        </div>

        {/* Quick Secondary User Creator */}
        <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
          <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
            <KeyRound className="h-4 w-4 text-indigo-400" />
            <div>
              <h3 className="text-sm font-semibold text-white">
                Quick Secondary NUT User (`upsd.users`)
              </h3>
              <p className="text-xs text-slate-400">
                Create or rotate a `secondary` client account and reload `upsd` automatically
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Username</label>
              <input
                value={newUser}
                onChange={(e) => setNewUser(e.target.value)}
                placeholder="wolnut_client"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Password (blank = auto-generate)
              </label>
              <input
                type="text"
                value={newPass}
                onChange={(e) => setNewPass(e.target.value)}
                placeholder="Auto-generated strong secret"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">
              Existing users: {overview?.available_users.join(", ") || "—"}
            </span>
            <button
              onClick={handleCreateUser}
              disabled={creatingUser}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {creatingUser ? "Applying..." : "Create Secondary User"}
            </button>
          </div>

          {missingLinkedHosts.length > 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 flex items-start gap-2.5 text-xs text-amber-200">
              <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
              <div>
                <strong>{missingLinkedHosts.length} linked host(s)</strong> marked for NUT client
                shutdown are not currently connected to `upsd`:{" "}
                <span className="font-mono">
                  {missingLinkedHosts.map((h) => `${h.name} (${h.ip_or_host})`).join(", ")}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Config Generator */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-sky-400" />
            <h3 className="text-base font-semibold text-white">
              Client Configuration &amp; Setup Script Generator
            </h3>
          </div>
          <select
            value={selectedHostId}
            onChange={(e) => handleSelectHost(e.target.value)}
            className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs text-slate-200"
          >
            <option value="">Load defaults from Managed Host...</option>
            {hosts.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name} ({h.ip_or_host})
              </option>
            ))}
          </select>
        </div>

        {/* Platform Tabs */}
        <div className="flex flex-wrap gap-1.5">
          {PLATFORMS.map((p) => (
            <button
              key={p.id}
              onClick={() => setForm({ ...form, platform: p.id })}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                form.platform === p.id
                  ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/40"
                  : "bg-surface-950 text-slate-400 border-slate-800 hover:text-white"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">NUT Server IP / Host</label>
            <input
              value={form.server_host}
              onChange={(e) => setForm({ ...form, server_host: e.target.value })}
              placeholder="192.168.1.10"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">NUT Port</label>
            <input
              type="number"
              value={form.nut_port}
              onChange={(e) => setForm({ ...form, nut_port: Number(e.target.value) })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">UPS Name</label>
            <input
              value={form.ups_name}
              onChange={(e) => setForm({ ...form, ups_name: e.target.value })}
              placeholder="ups1"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">NUT Client Username</label>
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">NUT Client Password</label>
            <input
              type="text"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="Password from upsd.users"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">FINALDELAY (sec)</label>
            <input
              type="number"
              min={0}
              value={form.final_delay}
              onChange={(e) => setForm({ ...form, final_delay: Number(e.target.value) })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
        </div>

        <div className="flex justify-end">
          <button
            onClick={() => void handleGenerate()}
            className="px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white"
          >
            Generate Client Configuration
          </button>
        </div>

        {generated && (
          <div className="pt-4 border-t border-slate-800 space-y-4">
            <h4 className="text-sm font-semibold text-white">{generated.title}</h4>

            {generated.gui_fields.length > 0 && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden">
                <div className="px-4 py-2.5 border-b border-slate-800 text-xs font-semibold text-slate-300">
                  GUI Settings
                </div>
                <div className="divide-y divide-slate-800/70">
                  {generated.gui_fields.map((gf) => (
                    <div
                      key={gf.label}
                      className="px-4 py-2.5 flex items-center justify-between text-xs"
                    >
                      <span className="text-slate-400">{gf.label}</span>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-white">{gf.value}</span>
                        <button
                          onClick={() => void copyText(`gf-${gf.label}`, gf.value)}
                          className="p-1 rounded bg-surface-800 text-slate-300 hover:text-white"
                        >
                          {copiedKey === `gf-${gf.label}` ? (
                            <Check className="h-3 w-3 text-emerald-400" />
                          ) : (
                            <Copy className="h-3 w-3" />
                          )}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {generated.one_liner && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden">
                <div className="px-4 py-2.5 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs font-semibold text-indigo-300">
                    One-Click Setup Script (Run on Target Host)
                  </span>
                  <button
                    onClick={() => void copyText("one-liner", generated.one_liner!)}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 text-xs text-slate-200 hover:bg-surface-700"
                  >
                    {copiedKey === "one-liner" ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    Copy Script
                  </button>
                </div>
                <pre className="p-4 text-xs font-mono text-slate-200 overflow-x-auto whitespace-pre-wrap">
                  {generated.one_liner}
                </pre>
              </div>
            )}

            {generated.files.map((f) => (
              <div
                key={f.path}
                className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden"
              >
                <div className="px-4 py-2.5 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-300">{f.path}</span>
                  <button
                    onClick={() => void copyText(`file-${f.path}`, f.content)}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 text-xs text-slate-200 hover:bg-surface-700"
                  >
                    {copiedKey === `file-${f.path}` ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    Copy
                  </button>
                </div>
                <pre className="p-4 text-xs font-mono text-slate-300 overflow-x-auto whitespace-pre-wrap">
                  {f.content}
                </pre>
              </div>
            ))}

            {generated.notes.length > 0 && (
              <ul className="list-disc pl-5 space-y-1 text-xs text-slate-400">
                {generated.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
