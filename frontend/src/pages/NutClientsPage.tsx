import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  FileText,
  KeyRound,
  Link2,
  RefreshCw,
  Server,
  Terminal,
  Trash2,
  Upload,
  UserCheck,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { Modal } from "../components/Modal";
import type {
  HostRecord,
  NutClientConfigRequest,
  NutClientConfigResponse,
  NutClientPlatform,
  NutClientsOverview,
  RecordedClientConfigDetail,
  RecordedClientConfigSummary,
  SshDeployResponse,
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
  const [newNotes, setNewNotes] = useState("");
  const [creatingUser, setCreatingUser] = useState(false);
  const [revealedPasswords, setRevealedPasswords] = useState<Record<string, string>>({});
  const [loadingAccount, setLoadingAccount] = useState<string | null>(null);
  const [userFeedback, setUserFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  // One-button SSH deploy state
  const [sshForm, setSshForm] = useState({
    ssh_port: 22,
    ssh_username: "root",
    ssh_password: "",
    ssh_sudo_password: "",
    save_credentials: true,
  });
  const [deployingSsh, setDeployingSsh] = useState(false);
  const [sshResult, setSshResult] = useState<SshDeployResponse | null>(null);

  // Recorded client configs vault state
  const [deviceName, setDeviceName] = useState("");
  const [recordedConfigs, setRecordedConfigs] = useState<RecordedClientConfigSummary[]>([]);
  const [viewingConfig, setViewingConfig] = useState<RecordedClientConfigDetail | null>(null);
  const [loadingConfigId, setLoadingConfigId] = useState<number | null>(null);

  const loadOverview = useCallback(async () => {
    setLoadingOverview(true);
    try {
      const [data, configs] = await Promise.all([
        api.getNutClients(),
        api.getRecordedClientConfigs(),
      ]);
      setOverview(data);
      setRecordedConfigs(configs);
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

  const applyHostSelection = useCallback(
    (h: HostRecord) => {
      setSelectedHostId(String(h.id));
      setDeviceName(h.name);
      setForm((prev) => ({
        ...prev,
        platform: h.nut_client_platform || prev.platform,
        ups_name: h.virtual_ups_name || h.nut_ups_name || prev.ups_name,
        username: h.nut_client_user || prev.username,
      }));
      setSshForm({
        ssh_port: h.ssh_port || 22,
        ssh_username: h.ssh_username || "root",
        ssh_password: h.ssh_password || "",
        ssh_sudo_password: h.ssh_sudo_password || "",
        save_credentials: true,
      });
    },
    []
  );

  useEffect(() => {
    if (preselectedHostId === null) return;
    const h = hosts.find((item) => item.id === preselectedHostId);
    if (h) applyHostSelection(h);
    onClearPreselectedHost();
  }, [preselectedHostId, hosts, onClearPreselectedHost, applyHostSelection]);

  const handleSelectHost = (hostIdStr: string) => {
    setSelectedHostId(hostIdStr);
    setSshResult(null);
    if (!hostIdStr) return;
    const h = hosts.find((item) => item.id === Number(hostIdStr));
    if (h) applyHostSelection(h);
  };

  const handleCreateUser = async () => {
    if (!newUser.trim()) return;
    setCreatingUser(true);
    setUserFeedback(null);
    try {
      const res = await api.createSecondaryNutUser(
        newUser.trim(),
        newPass.trim(),
        newNotes.trim()
      );
      setForm((prev) => ({ ...prev, username: res.username, password: res.password }));
      setRevealedPasswords((prev) => ({ ...prev, [res.username]: res.password }));
      setNewPass(res.password);
      setUserFeedback({
        ok: true,
        msg: `Saved monitor account '${res.username}' encrypted in WebNUT. Pre-filled below.`,
      });
      await loadOverview();
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save secondary NUT user",
      });
    } finally {
      setCreatingUser(false);
    }
  };

  const fetchAccountPassword = async (username: string): Promise<string> => {
    if (revealedPasswords[username] !== undefined) {
      return revealedPasswords[username];
    }
    const res = await api.revealMonitorAccount(username);
    return res.password;
  };

  const handleToggleRevealAccount = async (username: string) => {
    if (revealedPasswords[username] !== undefined) {
      setRevealedPasswords((prev) => {
        const next = { ...prev };
        delete next[username];
        return next;
      });
      return;
    }
    setLoadingAccount(username);
    try {
      const res = await api.revealMonitorAccount(username);
      setRevealedPasswords((prev) => ({ ...prev, [username]: res.password }));
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to reveal password for '${username}'`,
      });
    } finally {
      setLoadingAccount(null);
    }
  };

  const handleUseSavedAccount = async (username: string) => {
    if (!username) return;
    setLoadingAccount(username);
    try {
      const password = await fetchAccountPassword(username);
      setForm((prev) => ({ ...prev, username, password }));
      setUserFeedback({
        ok: true,
        msg: `Loaded monitor account '${username}' into client configuration below.`,
      });
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to load account '${username}'`,
      });
    } finally {
      setLoadingAccount(null);
    }
  };

  const handleCopyAccountPassword = async (username: string) => {
    setLoadingAccount(username);
    try {
      const password = await fetchAccountPassword(username);
      await navigator.clipboard.writeText(password);
      setCopiedKey(`acct-${username}`);
      window.setTimeout(() => setCopiedKey(null), 2000);
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to copy password for '${username}'`,
      });
    } finally {
      setLoadingAccount(null);
    }
  };

  const handleDeleteSavedAccount = async (username: string) => {
    setLoadingAccount(username);
    setUserFeedback(null);
    try {
      await api.deleteMonitorAccount(username);
      setRevealedPasswords((prev) => {
        const next = { ...prev };
        delete next[username];
        return next;
      });
      setUserFeedback({
        ok: true,
        msg: `Deleted monitor account '${username}'.`,
      });
      await loadOverview();
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to delete account '${username}'`,
      });
    } finally {
      setLoadingAccount(null);
    }
  };

  const handleOneButtonSshDeploy = async () => {
    const hid = Number(selectedHostId);
    if (!hid) {
      setUserFeedback({
        ok: false,
        msg: "Select a Managed Host first to deploy via SSH/SFTP.",
      });
      return;
    }
    setDeployingSsh(true);
    setSshResult(null);
    try {
      const res = await api.deployHostClient(hid, {
        ...sshForm,
        nut_username: form.username,
        nut_password: form.password,
        final_delay: form.final_delay,
        shutdown_cmd: form.shutdown_cmd,
      });
      setSshResult(res);
      await loadOverview();
    } catch (err) {
      setSshResult({
        ok: false,
        steps: [],
        error: err instanceof ApiError ? err.message : "SSH deployment failed",
        virtual_ups_name: "",
        server_host: "",
        nut_port: form.nut_port,
      });
    } finally {
      setDeployingSsh(false);
    }
  };

  const handleGenerate = async () => {
    setUserFeedback(null);
    if (!form.password.trim()) {
      setUserFeedback({
        ok: false,
        msg: "Enter a password (or save/select a monitor account above).",
      });
      return;
    }
    try {
      const res = await api.generateNutClientConfig({
        ...form,
        host_id: selectedHostId ? Number(selectedHostId) : null,
        device_name: deviceName.trim(),
        save_record: true,
      });
      setGenerated(res);
      const configs = await api.getRecordedClientConfigs();
      setRecordedConfigs(configs);
      setUserFeedback({
        ok: true,
        msg: `Generated and recorded client configuration for '${deviceName.trim() || form.ups_name}'.`,
      });
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to generate client config",
      });
    }
  };

  const handleViewRecordedConfig = async (id: number) => {
    setLoadingConfigId(id);
    try {
      const detail = await api.getRecordedClientConfig(id);
      setViewingConfig(detail);
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to load recorded config",
      });
    } finally {
      setLoadingConfigId(null);
    }
  };

  const handleLoadRecordedConfig = async (rec: RecordedClientConfigSummary) => {
    if (rec.host_id) {
      const h = hosts.find((item) => item.id === rec.host_id);
      if (h) applyHostSelection(h);
      else setSelectedHostId(String(rec.host_id));
    } else {
      setSelectedHostId("");
    }
    setDeviceName(rec.device_name);
    let pw = revealedPasswords[rec.username] ?? "";
    if (!pw && overview?.saved_accounts.some((a) => a.username === rec.username)) {
      try {
        pw = await fetchAccountPassword(rec.username);
      } catch {
        pw = "";
      }
    }
    setForm((prev) => ({
      ...prev,
      platform: rec.platform,
      server_host: rec.server_host,
      nut_port: rec.nut_port,
      ups_name: rec.ups_name,
      username: rec.username,
      password: pw || prev.password,
      final_delay: rec.final_delay,
      poll_freq: rec.poll_freq,
      deadtime: rec.deadtime,
      shutdown_cmd: rec.shutdown_cmd,
    }));
    setUserFeedback({
      ok: true,
      msg: `Loaded recorded config for '${rec.device_name}' into Client Setup.`,
    });
  };

  const handleDeleteRecordedConfig = async (id: number, name: string) => {
    setLoadingConfigId(id);
    try {
      await api.deleteRecordedClientConfig(id);
      setRecordedConfigs((prev) => prev.filter((item) => item.id !== id));
      if (viewingConfig?.id === id) setViewingConfig(null);
      setUserFeedback({ ok: true, msg: `Deleted recorded config for '${name}'.` });
    } catch (err) {
      setUserFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to delete recorded config",
      });
    } finally {
      setLoadingConfigId(null);
    }
  };

  const copyText = async (key: string, text: string) => {
    await navigator.clipboard.writeText(text);
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey(null), 2000);
  };

  const liveClientPairs = useMemo(() => {
    const ipToHost = new Map(hosts.map((h) => [h.ip_or_host.trim(), h]));
    const pairs: { upsName: string; ip: string; host?: HostRecord }[] = [];
    const seen = new Set<string>();
    for (const c of overview?.connected_clients ?? []) {
      const k = `${c.ups_name}:${c.client_ip}`;
      if (!seen.has(k)) {
        seen.add(k);
        pairs.push({
          upsName: c.ups_name,
          ip: c.client_ip,
          host: hosts.find((h) => h.id === c.matched_host_id) ?? ipToHost.get(c.client_ip),
        });
      }
    }
    for (const u of telemetry.ups_list) {
      for (const ip of u.connected_clients ?? []) {
        const k = `${u.name}:${ip}`;
        if (!seen.has(k)) {
          seen.add(k);
          pairs.push({ upsName: u.name, ip, host: ipToHost.get(ip) });
        }
      }
    }
    return pairs;
  }, [telemetry.ups_list, overview, hosts]);

  const connectedIpSet = useMemo(() => new Set(liveClientPairs.map((p) => p.ip)), [liveClientPairs]);
  const linkedHosts = useMemo(() => hosts.filter((h) => h.nut_client_enabled), [hosts]);
  const missingLinkedHosts = useMemo(
    () => linkedHosts.filter((h) => !connectedIpSet.has(h.ip_or_host.trim())),
    [linkedHosts, connectedIpSet]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            Client Setup &amp; Smart NUT Repeater
          </h2>
          <p className="text-sm text-slate-400">
            All clients connect to WebNUT&apos;s Repeater stream so per-host shutdown &amp;
            disconnect rules stream live from the UI
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

      {/* Pipeline Bar */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-4">
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-3">
          WebNUT Repeater &amp; Recovery Pipeline
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-stretch auto-rows-fr text-xs">
          <div className="h-full flex flex-col justify-between rounded-xl bg-surface-950 border border-slate-800 p-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-amber-300">1. Upstream UPS Event</span>
              <ArrowRight className="h-3.5 w-3.5 text-slate-500 hidden md:block shrink-0" />
            </div>
            <div className="text-slate-400 mt-1.5">
              USB, Serial, SNMP, or External NUT Server polled live
            </div>
          </div>
          <div className="h-full flex flex-col justify-between rounded-xl bg-surface-950 border border-indigo-500/30 p-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-indigo-300">2. Virtual UPS Stream</span>
              <ArrowRight className="h-3.5 w-3.5 text-slate-500 hidden md:block shrink-0" />
            </div>
            <div className="text-slate-300 mt-1.5 font-mono">
              {liveClientPairs.length} connected • {linkedHosts.length} linked
            </div>
          </div>
          <div className="h-full flex flex-col justify-between rounded-xl bg-surface-950 border border-slate-800 p-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-emerald-300">3. Per-Host Shutdown</span>
              <ArrowRight className="h-3.5 w-3.5 text-slate-500 hidden md:block shrink-0" />
            </div>
            <div className="text-slate-400 mt-1.5">
              Streams OB LB FSD or holds grace timer per host
            </div>
          </div>
          <div className="h-full flex flex-col justify-between rounded-xl bg-surface-950 border border-slate-800 p-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-sky-300">4. Staged Battery WoL</span>
            </div>
            <div className="text-slate-400 mt-1.5">
              Wakes each host at its own OL battery % threshold
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

      {/* Live Clients + Quick User */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-emerald-400" />
              <h3 className="text-sm font-semibold text-white">Live Connected NUT Clients</h3>
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
                  <div className="text-slate-400">Stream: {c.upsName}</div>
                </div>
                {c.host ? (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                    <Server className="h-3 w-3" />
                    {c.host.name}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                    Connected Client
                  </span>
                )}
              </div>
            ))}
            {liveClientPairs.length === 0 && (
              <div className="p-6 text-center text-xs text-slate-500">
                No external `upsmon` clients connected to WebNUT Repeater yet.
              </div>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
          <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
            <KeyRound className="h-4 w-4 text-indigo-400" />
            <div>
              <h3 className="text-sm font-semibold text-white">
                Saved Monitor Accounts (`upsd.users`)
              </h3>
              <p className="text-xs text-slate-400">
                Create and save encrypted monitor credentials for quick reuse on new clients
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            <input
              value={newUser}
              onChange={(e) => setNewUser(e.target.value)}
              placeholder="Username (e.g. proxmox_mon)"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white font-mono"
            />
            <input
              type="text"
              value={newPass}
              onChange={(e) => setNewPass(e.target.value)}
              placeholder="Password (blank = auto)"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white font-mono"
            />
            <input
              value={newNotes}
              onChange={(e) => setNewNotes(e.target.value)}
              placeholder="Note (e.g. Proxmox / TrueNAS)"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
            />
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">
              {overview?.saved_accounts.length || 0} saved account(s)
            </span>
            <button
              onClick={handleCreateUser}
              disabled={creatingUser || !newUser.trim()}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50 shrink-0"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {creatingUser ? "Saving..." : "Save Monitor Account"}
            </button>
          </div>

          {/* Saved Monitor Accounts List */}
          <div className="rounded-xl border border-slate-800 bg-surface-950 divide-y divide-slate-800/70 max-h-56 overflow-y-auto">
            {(overview?.saved_accounts ?? []).map((acc) => {
              const revealed = revealedPasswords[acc.username];
              const isBusy = loadingAccount === acc.username;
              return (
                <div
                  key={acc.username}
                  className="px-3.5 py-2.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-semibold text-white">{acc.username}</span>
                      <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] text-slate-300">
                        {acc.role}
                      </span>
                      {acc.notes && (
                        <span className="text-[11px] text-slate-400 truncate">{acc.notes}</span>
                      )}
                    </div>
                    {revealed !== undefined && (
                      <div className="mt-1 font-mono text-[11px] text-emerald-300 bg-surface-900 border border-emerald-500/30 rounded px-2 py-0.5 inline-block select-all">
                        {revealed}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => void handleUseSavedAccount(acc.username)}
                      disabled={isBusy}
                      title="Load username & password into client setup"
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/40 text-indigo-300 font-medium disabled:opacity-50"
                    >
                      <UserCheck className="h-3 w-3" />
                      Use
                    </button>
                    <button
                      onClick={() => void handleToggleRevealAccount(acc.username)}
                      disabled={isBusy}
                      title={revealed !== undefined ? "Hide password" : "Reveal password"}
                      className="p-1.5 rounded bg-surface-800 hover:bg-surface-700 text-slate-300 disabled:opacity-50"
                    >
                      {revealed !== undefined ? (
                        <EyeOff className="h-3.5 w-3.5" />
                      ) : (
                        <Eye className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      onClick={() => void handleCopyAccountPassword(acc.username)}
                      disabled={isBusy}
                      title="Copy password"
                      className="p-1.5 rounded bg-surface-800 hover:bg-surface-700 text-slate-300 disabled:opacity-50"
                    >
                      {copiedKey === `acct-${acc.username}` ? (
                        <Check className="h-3.5 w-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" />
                      )}
                    </button>
                    {acc.username !== "admin" && (
                      <button
                        onClick={() => void handleDeleteSavedAccount(acc.username)}
                        disabled={isBusy}
                        title="Delete monitor account"
                        className="p-1.5 rounded bg-surface-800 hover:bg-rose-500/20 text-slate-400 hover:text-rose-300 disabled:opacity-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            {(overview?.saved_accounts.length ?? 0) === 0 && (
              <div className="p-4 text-center text-xs text-slate-500">
                No saved monitor accounts yet. Create one above so its password stays easy to reuse.
              </div>
            )}
          </div>

          {missingLinkedHosts.length > 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 flex items-start gap-2.5 text-xs text-amber-200">
              <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
              <div>
                <strong>{missingLinkedHosts.length} linked host(s)</strong> not currently
                connected:{" "}
                <span className="font-mono">
                  {missingLinkedHosts.map((h) => h.name).join(", ")}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Configure New Client & Manual Generator */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-indigo-400" />
            <h3 className="text-base font-semibold text-white">
              Configure New Client
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(overview?.saved_accounts.length ?? 0) > 0 && (
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value) void handleUseSavedAccount(e.target.value);
                }}
                className="rounded-lg bg-surface-950 border border-indigo-500/40 px-3 py-1.5 text-xs text-indigo-200"
              >
                <option value="">Load Saved Account...</option>
                {overview?.saved_accounts.map((acc) => (
                  <option key={acc.username} value={acc.username}>
                    {acc.username}
                    {acc.notes ? ` — ${acc.notes}` : ""}
                  </option>
                ))}
              </select>
            )}
            <select
              value={selectedHostId}
              onChange={(e) => handleSelectHost(e.target.value)}
              className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs text-slate-200"
            >
              <option value="">Select Managed Host (recommended)...</option>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name} ({h.ip_or_host}) → {h.virtual_ups_name || h.name}
                </option>
              ))}
            </select>
          </div>
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
            <label className="block text-xs text-slate-400 mb-1">
              Device Name (saved in registry)
            </label>
            <input
              value={deviceName}
              onChange={(e) => setDeviceName(e.target.value)}
              placeholder={form.ups_name || "e.g. proxmox-01 or TrueNAS"}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">WebNUT Repeater IP</label>
            <input
              value={form.server_host}
              onChange={(e) => setForm({ ...form, server_host: e.target.value })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Repeater Port</label>
            <input
              type="number"
              value={form.nut_port}
              onChange={(e) => setForm({ ...form, nut_port: Number(e.target.value) })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Virtual / UPS Name</label>
            <input
              value={form.ups_name}
              onChange={(e) => setForm({ ...form, ups_name: e.target.value })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Username</label>
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Password</label>
            <input
              type="text"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="Password"
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
            Generate Manual Config / Script
          </button>
        </div>

        {generated && (
          <div className="pt-4 border-t border-slate-800 space-y-4">
            <h4 className="text-sm font-semibold text-white">{generated.title}</h4>

            {generated.gui_fields.length > 0 && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden">
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
                          className="p-1 rounded bg-surface-800 text-slate-300"
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
                    One-Click Setup Script
                  </span>
                  <button
                    onClick={() => void copyText("one-liner", generated.one_liner!)}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 text-xs text-slate-200"
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
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 text-xs text-slate-200"
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
          </div>
        )}

        {/* One-Button SSH Deploy Strip (Underneath client configuration & generator) */}
        <div className="pt-4 border-t border-slate-800">
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <div className="text-xs font-semibold text-emerald-300">
                  One-Button Remote SSH/SFTP Deploy (Linux / Proxmox)
                </div>
                <div className="text-[11px] text-slate-400 mt-0.5">
                  {selectedHostId
                    ? "Installs nut-client, uploads nut.conf & upsmon.conf with the credentials above, and starts nut-monitor."
                    : "Select a Managed Host in the dropdown above to enable remote SSH/SFTP deployment."}
                </div>
              </div>
              <button
                onClick={() => void handleOneButtonSshDeploy()}
                disabled={deployingSsh || !selectedHostId || !sshForm.ssh_password.trim()}
                className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-medium text-white disabled:opacity-50 shrink-0"
              >
                {deployingSsh ? "Deploying..." : "One-Button SSH Deploy"}
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
              <input
                value={sshForm.ssh_username}
                onChange={(e) => setSshForm({ ...sshForm, ssh_username: e.target.value })}
                placeholder="SSH username (e.g. root)"
                className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
              />
              <input
                type="password"
                value={sshForm.ssh_password}
                onChange={(e) => setSshForm({ ...sshForm, ssh_password: e.target.value })}
                placeholder="SSH password"
                className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
              />
              <input
                type="number"
                value={sshForm.ssh_port}
                onChange={(e) => setSshForm({ ...sshForm, ssh_port: Number(e.target.value) })}
                placeholder="22"
                className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
              />
              <label className="inline-flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={sshForm.save_credentials}
                  onChange={(e) =>
                    setSshForm({ ...sshForm, save_credentials: e.target.checked })
                  }
                  className="rounded border-slate-600 bg-surface-950 text-emerald-500"
                />
                Save encrypted password
              </label>
            </div>

            {sshResult && (
              <div
                className={`rounded-lg border p-3 text-xs space-y-1 ${
                  sshResult.ok
                    ? "border-emerald-500/40 bg-surface-950 text-emerald-300"
                    : "border-rose-500/40 bg-surface-950 text-rose-300"
                }`}
              >
                <div className="font-semibold">
                  {sshResult.ok
                    ? `Deployed to ${sshResult.virtual_ups_name}@${sshResult.server_host}:${sshResult.nut_port}`
                    : sshResult.error}
                </div>
                {sshResult.steps.map((s, i) => (
                  <div key={i} className="font-mono text-slate-300">
                    • {s}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Recorded Device Client Configs Registry */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-sky-400" />
            <div>
              <h3 className="text-sm font-semibold text-white">
                Recorded Device Client Configs
              </h3>
              <p className="text-xs text-slate-400">
                Every generated manual config and SSH-deployed setup is encrypted and saved per
                device
              </p>
            </div>
          </div>
          <span className="text-xs font-mono text-slate-400">
            {recordedConfigs.length} recorded
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-950/60 text-[11px] uppercase text-slate-400 border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Device</th>
                <th className="px-4 py-3">Platform</th>
                <th className="px-4 py-3">Repeater Stream</th>
                <th className="px-4 py-3">Account</th>
                <th className="px-4 py-3">Delivery &amp; Status</th>
                <th className="px-4 py-3">Updated</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {recordedConfigs.map((rec) => {
                const platLabel =
                  PLATFORMS.find((p) => p.id === rec.platform)?.label ?? rec.platform;
                const isBusy = loadingConfigId === rec.id;
                return (
                  <tr key={rec.id} className="hover:bg-surface-800/40">
                    <td className="px-4 py-3 font-medium text-white">{rec.device_name}</td>
                    <td className="px-4 py-3 text-slate-300">{platLabel}</td>
                    <td className="px-4 py-3 font-mono text-sky-300">
                      {rec.ups_name}@{rec.server_host}:{rec.nut_port}
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-300">{rec.username}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border ${
                          rec.deploy_status === "deployed"
                            ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                            : rec.deploy_status === "failed"
                            ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
                            : "bg-indigo-500/15 text-indigo-300 border-indigo-500/30"
                        }`}
                      >
                        {rec.delivery_method === "ssh_deploy"
                          ? rec.deploy_status === "deployed"
                            ? "SSH Deployed"
                            : "SSH Failed"
                          : "Manual Config"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-400">
                      {new Date(rec.updated_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap space-x-1">
                      <button
                        onClick={() => void handleViewRecordedConfig(rec.id)}
                        disabled={isBusy}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200 disabled:opacity-50"
                      >
                        <Eye className="h-3 w-3" />
                        View Config
                      </button>
                      <button
                        onClick={() => void handleLoadRecordedConfig(rec)}
                        disabled={isBusy}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/40 text-indigo-300 disabled:opacity-50"
                      >
                        <Upload className="h-3 w-3" />
                        Load
                      </button>
                      <button
                        onClick={() => void handleDeleteRecordedConfig(rec.id, rec.device_name)}
                        disabled={isBusy}
                        title="Delete recorded config"
                        className="p-1.5 rounded bg-surface-800 hover:bg-rose-500/20 text-slate-400 hover:text-rose-300 disabled:opacity-50"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {recordedConfigs.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-6 text-center text-slate-500">
                    No device client configs recorded yet. Generate a manual config or run
                    One-Button SSH Deploy above to record it automatically.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recorded Config Detail Modal */}
      <Modal
        open={viewingConfig !== null}
        title={
          viewingConfig
            ? `Recorded NUT Client Config — ${viewingConfig.device_name}`
            : "Recorded Config"
        }
        onClose={() => setViewingConfig(null)}
        maxWidth="max-w-3xl"
      >
        {viewingConfig && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400 border-b border-slate-800 pb-3">
              <div>
                Stream:{" "}
                <span className="font-mono text-sky-300">
                  {viewingConfig.ups_name}@{viewingConfig.server_host}:{viewingConfig.nut_port}
                </span>{" "}
                • Account: <span className="font-mono text-white">{viewingConfig.username}</span>
              </div>
              <span>Updated {new Date(viewingConfig.updated_at).toLocaleString()}</span>
            </div>

            {viewingConfig.config.gui_fields.length > 0 && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 divide-y divide-slate-800/70">
                {viewingConfig.config.gui_fields.map((gf) => (
                  <div
                    key={gf.label}
                    className="px-4 py-2 flex items-center justify-between text-xs"
                  >
                    <span className="text-slate-400">{gf.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-white">{gf.value}</span>
                      <button
                        onClick={() => void copyText(`rec-gf-${gf.label}`, gf.value)}
                        className="p-1 rounded bg-surface-800 text-slate-300"
                      >
                        {copiedKey === `rec-gf-${gf.label}` ? (
                          <Check className="h-3 w-3 text-emerald-400" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {viewingConfig.config.one_liner && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden">
                <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs font-semibold text-indigo-300">
                    One-Click Setup Script
                  </span>
                  <button
                    onClick={() =>
                      void copyText("rec-one-liner", viewingConfig.config.one_liner!)
                    }
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 text-xs text-slate-200"
                  >
                    {copiedKey === "rec-one-liner" ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    Copy Script
                  </button>
                </div>
                <pre className="p-3.5 text-xs font-mono text-slate-200 overflow-x-auto whitespace-pre-wrap">
                  {viewingConfig.config.one_liner}
                </pre>
              </div>
            )}

            {viewingConfig.config.files.map((f) => (
              <div
                key={f.path}
                className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden"
              >
                <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-300">{f.path}</span>
                  <button
                    onClick={() => void copyText(`rec-file-${f.path}`, f.content)}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 text-xs text-slate-200"
                  >
                    {copiedKey === `rec-file-${f.path}` ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    Copy
                  </button>
                </div>
                <pre className="p-3.5 text-xs font-mono text-slate-300 overflow-x-auto whitespace-pre-wrap">
                  {f.content}
                </pre>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  );
}
