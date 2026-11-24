import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Edit2,
  FlaskConical,
  Link2,
  Play,
  Plus,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { Modal } from "../components/Modal";
import { RecoveryBanner } from "../components/RecoveryBanner";
import { WakeStateBadge } from "../components/StatusBadge";
import type {
  HostPayload,
  HostRecord,
  NutClientPlatform,
  OrchestratorConfig,
  OrchestratorState,
  TelemetrySnapshot,
} from "../types";

interface HostsPageProps {
  hosts: HostRecord[];
  orchestrator: OrchestratorState;
  telemetry: TelemetrySnapshot;
  onRefresh: () => Promise<void>;
  onOpenNutClientSetup: (hostId: number) => void;
}

const EMPTY_HOST: HostPayload = {
  name: "",
  ip_or_host: "",
  mac_address: "",
  subnet_broadcast: null,
  boot_priority: 10,
  stagger_delay_sec: 15,
  check_method: "icmp",
  check_port: null,
  boot_timeout_sec: 90,
  retry_interval_sec: 15,
  max_retries: 3,
  always_wake: false,
  enabled: true,
  nut_client_enabled: false,
  nut_client_platform: "linux_debian",
  nut_ups_name: "",
  nut_client_user: "",
};

export function HostsPage({
  hosts,
  orchestrator,
  telemetry,
  onRefresh,
  onOpenNutClientSetup,
}: HostsPageProps) {
  const [policy, setPolicy] = useState<OrchestratorConfig>({
    monitored_ups: "",
    min_battery_charge: 25,
    stability_hold_sec: 60,
    host_poll_interval_sec: 30,
    ups_poll_interval_sec: 5,
    auto_recovery_enabled: true,
  });
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<HostPayload>(EMPTY_HOST);
  const [arpLoading, setArpLoading] = useState(false);
  const [arpMsg, setArpMsg] = useState<string | null>(null);
  const [savingHost, setSavingHost] = useState(false);

  const [simModalOpen, setSimModalOpen] = useState(false);
  const [simDryRun, setSimDryRun] = useState(true);

  useEffect(() => {
    void (async () => {
      const cfg = await api.getOrchestratorConfig();
      setPolicy(cfg);
    })();
  }, []);

  const handleSavePolicy = async () => {
    setSavingPolicy(true);
    setFeedback(null);
    try {
      const updated = await api.updateOrchestratorConfig(policy);
      setPolicy(updated);
      setFeedback({ ok: true, msg: "Recovery policy updated." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save policy",
      });
    } finally {
      setSavingPolicy(false);
    }
  };

  const openAddModal = () => {
    setEditingId(null);
    setForm(EMPTY_HOST);
    setArpMsg(null);
    setModalOpen(true);
  };

  const openEditModal = (h: HostRecord) => {
    setEditingId(h.id);
    setForm({
      name: h.name,
      ip_or_host: h.ip_or_host,
      mac_address: h.mac_address,
      subnet_broadcast: h.subnet_broadcast,
      boot_priority: h.boot_priority,
      stagger_delay_sec: h.stagger_delay_sec,
      check_method: h.check_method,
      check_port: h.check_port,
      boot_timeout_sec: h.boot_timeout_sec,
      retry_interval_sec: h.retry_interval_sec,
      max_retries: h.max_retries,
      always_wake: h.always_wake,
      enabled: h.enabled,
      nut_client_enabled: h.nut_client_enabled,
      nut_client_platform: h.nut_client_platform || "linux_debian",
      nut_ups_name: h.nut_ups_name || "",
      nut_client_user: h.nut_client_user || "",
    });
    setArpMsg(null);
    setModalOpen(true);
  };

  const handleArpLookup = async () => {
    if (!form.ip_or_host.trim()) {
      setArpMsg("Enter an IP address or hostname first.");
      return;
    }
    setArpLoading(true);
    setArpMsg(null);
    try {
      const res = await api.arpLookup(form.ip_or_host.trim());
      if (res.found && res.mac_address) {
        setForm((prev) => ({ ...prev, mac_address: res.mac_address! }));
        setArpMsg(`Detected ${res.mac_address} via ${res.source}`);
      } else {
        setArpMsg(
          `No ARP entry found for ${res.resolved_ip ?? form.ip_or_host}. Ensure the host is currently online on the same L2 subnet.`
        );
      }
    } catch (err) {
      setArpMsg(err instanceof ApiError ? err.message : "ARP lookup failed");
    } finally {
      setArpLoading(false);
    }
  };

  const handleSaveHost = async () => {
    setSavingHost(true);
    setArpMsg(null);
    try {
      if (editingId === null) {
        await api.createHost(form);
      } else {
        await api.updateHost(editingId, form);
      }
      setModalOpen(false);
      await onRefresh();
    } catch (err) {
      setArpMsg(err instanceof ApiError ? err.message : "Failed to save host");
    } finally {
      setSavingHost(false);
    }
  };

  const handleDeleteHost = async (h: HostRecord) => {
    if (!window.confirm(`Delete host "${h.name}"?`)) return;
    await api.deleteHost(h.id);
    await onRefresh();
  };

  const handleWakeNow = async (hostId: number) => {
    await api.wakeHost(hostId, true);
  };

  const handleSimulateRecovery = async () => {
    setFeedback(null);
    try {
      await api.simulateRecovery(simDryRun);
      setSimModalOpen(false);
      setFeedback({
        ok: true,
        msg: simDryRun
          ? "Started Dry-Run recovery simulation."
          : "Started Live recovery sequence.",
      });
    } catch (err) {
      setSimModalOpen(false);
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Simulation failed",
      });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            Managed Hosts &amp; WoL Recovery
          </h2>
          <p className="text-sm text-slate-400">
            Configure boot priority, stagger intervals, auto-retry verification, and recovery rules
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSimModalOpen(true)}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm font-medium text-slate-200"
          >
            <FlaskConical className="h-4 w-4 text-amber-400" />
            Simulate Recovery
          </button>
          <button
            onClick={openAddModal}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white"
          >
            <Plus className="h-4 w-4" />
            Add Host
          </button>
        </div>
      </div>

      <RecoveryBanner orchestrator={orchestrator} />

      {feedback && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm ${
            feedback.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-rose-500/40 bg-rose-500/10 text-rose-300"
          }`}
        >
          {feedback.msg}
        </div>
      )}

      {/* Orchestrator Policy Card */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2">
            <Settings className="h-4 w-4 text-indigo-400" />
            <h3 className="text-base font-semibold text-white">Power Recovery Policy</h3>
          </div>
          <button
            onClick={handleSavePolicy}
            disabled={savingPolicy}
            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
          >
            <CheckCircle2 className="h-3.5 w-3.5" />
            {savingPolicy ? "Saving..." : "Save Policy"}
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Monitored UPS Name</label>
            <input
              type="text"
              value={policy.monitored_ups}
              onChange={(e) => setPolicy({ ...policy, monitored_ups: e.target.value })}
              placeholder={telemetry.ups_list[0]?.name || "Auto (first detected UPS)"}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Safe Battery Threshold (%)
            </label>
            <input
              type="number"
              min={0}
              max={100}
              value={policy.min_battery_charge}
              onChange={(e) =>
                setPolicy({ ...policy, min_battery_charge: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Stability Hold Window (sec)
            </label>
            <input
              type="number"
              min={0}
              max={3600}
              value={policy.stability_hold_sec}
              onChange={(e) =>
                setPolicy({ ...policy, stability_hold_sec: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Host Poll Interval (sec)
            </label>
            <input
              type="number"
              min={5}
              max={3600}
              value={policy.host_poll_interval_sec}
              onChange={(e) =>
                setPolicy({ ...policy, host_poll_interval_sec: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              UPS Poll Interval (sec)
            </label>
            <input
              type="number"
              min={1}
              max={300}
              value={policy.ups_poll_interval_sec}
              onChange={(e) =>
                setPolicy({ ...policy, ups_poll_interval_sec: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div className="flex items-end pb-2">
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={policy.auto_recovery_enabled}
                onChange={(e) =>
                  setPolicy({ ...policy, auto_recovery_enabled: e.target.checked })
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Auto-Wake on OB &rarr; OL Recovery
            </label>
          </div>
        </div>
      </div>

      {/* Host Table */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-950/60 text-xs uppercase text-slate-400 border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Priority</th>
                <th className="px-4 py-3">Host / MAC</th>
                <th className="px-4 py-3">Health Check</th>
                <th className="px-4 py-3">Retry Policy</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {hosts.map((h) => (
                <tr key={h.id} className="hover:bg-surface-800/40">
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    <div>#{h.boot_priority}</div>
                    <div className="text-slate-500">+{h.stagger_delay_sec}s</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={`h-2 w-2 rounded-full ${
                          h.is_online ? "bg-emerald-400" : "bg-slate-600"
                        }`}
                      />
                      <span className="font-medium text-white">{h.name}</span>
                      {!h.enabled && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                          Disabled
                        </span>
                      )}
                      {h.always_wake && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300">
                          Always Wake
                        </span>
                      )}
                      {h.nut_client_enabled && (() => {
                        const nutConnected = telemetry.ups_list.some((u) =>
                          (u.connected_clients ?? []).includes(h.ip_or_host.trim())
                        );
                        return (
                          <span
                            className={`text-[10px] px-1.5 py-0.5 rounded border ${
                              nutConnected
                                ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                                : "bg-amber-500/15 text-amber-300 border-amber-500/30"
                            }`}
                          >
                            {nutConnected ? "NUT Connected" : "NUT Linked"}
                          </span>
                        );
                      })()}
                    </div>
                    <div className="text-xs text-slate-400 font-mono mt-0.5">
                      {h.ip_or_host} • {h.mac_address}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-300 font-mono">
                    {h.check_method.toUpperCase()}
                    {h.check_method === "tcp" && h.check_port ? `:${h.check_port}` : ""}
                    <div className="text-slate-500">
                      pre-outage: {h.was_online_before_outage ? "online" : "offline"}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-300">
                    <div>{h.max_retries} attempts</div>
                    <div className="text-slate-500">
                      {h.boot_timeout_sec}s timeout / {h.retry_interval_sec}s wait
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <WakeStateBadge
                      state={h.wake_state}
                      attempt={h.wake_attempt}
                      maxRetries={h.max_retries}
                    />
                    {h.last_wake_error && (
                      <div className="text-[11px] text-rose-400 mt-1 max-w-xs truncate">
                        {h.last_wake_error}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap space-x-1.5">
                    <button
                      onClick={() => onOpenNutClientSetup(h.id)}
                      title="NUT Client Setup"
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sky-300 text-xs"
                    >
                      <Link2 className="h-3 w-3" />
                      NUT Setup
                    </button>
                    <button
                      onClick={() => handleWakeNow(h.id)}
                      title="Wake Now"
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-indigo-300 text-xs"
                    >
                      <Play className="h-3 w-3" />
                      Wake
                    </button>
                    <button
                      onClick={() => openEditModal(h)}
                      title="Edit"
                      className="p-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 text-slate-300"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleDeleteHost(h)}
                      title="Delete"
                      className="p-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
              {hosts.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-slate-500 text-sm">
                    No hosts configured. Click &ldquo;Add Host&rdquo; to register your servers.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add / Edit Host Modal */}
      <Modal
        open={modalOpen}
        title={editingId === null ? "Add Managed Host" : "Edit Managed Host"}
        onClose={() => setModalOpen(false)}
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-300 mb-1">Friendly Name</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Proxmox Node 1"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">IP Address / Hostname</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={form.ip_or_host}
                  onChange={(e) => setForm({ ...form, ip_or_host: e.target.value })}
                  placeholder="192.168.1.50"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
                />
                <button
                  type="button"
                  onClick={handleArpLookup}
                  disabled={arpLoading}
                  className="px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-sky-300 whitespace-nowrap flex items-center gap-1"
                >
                  <Search className="h-3.5 w-3.5" />
                  {arpLoading ? "..." : "ARP MAC"}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">MAC Address</label>
              <input
                type="text"
                value={form.mac_address}
                onChange={(e) => setForm({ ...form, mac_address: e.target.value })}
                placeholder="AA:BB:CC:DD:EE:FF"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">
                Subnet Broadcast (Optional)
              </label>
              <input
                type="text"
                value={form.subnet_broadcast ?? ""}
                onChange={(e) =>
                  setForm({ ...form, subnet_broadcast: e.target.value || null })
                }
                placeholder="192.168.1.255"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">
                Boot Priority (lower = earlier)
              </label>
              <input
                type="number"
                value={form.boot_priority}
                onChange={(e) => setForm({ ...form, boot_priority: Number(e.target.value) })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">Stagger Delay (sec)</label>
              <input
                type="number"
                min={0}
                value={form.stagger_delay_sec}
                onChange={(e) =>
                  setForm({ ...form, stagger_delay_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">Health Check Method</label>
              <select
                value={form.check_method}
                onChange={(e) =>
                  setForm({
                    ...form,
                    check_method: e.target.value as "icmp" | "tcp",
                    check_port: e.target.value === "tcp" ? form.check_port || 22 : null,
                  })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              >
                <option value="icmp">ICMP Ping</option>
                <option value="tcp">TCP Port</option>
              </select>
            </div>
            {form.check_method === "tcp" && (
              <div>
                <label className="block text-xs text-slate-300 mb-1">Target TCP Port</label>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={form.check_port ?? 22}
                  onChange={(e) => setForm({ ...form, check_port: Number(e.target.value) })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                />
              </div>
            )}
            <div>
              <label className="block text-xs text-slate-300 mb-1">Boot Timeout (sec)</label>
              <input
                type="number"
                min={5}
                value={form.boot_timeout_sec}
                onChange={(e) =>
                  setForm({ ...form, boot_timeout_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">Retry Interval (sec)</label>
              <input
                type="number"
                min={1}
                value={form.retry_interval_sec}
                onChange={(e) =>
                  setForm({ ...form, retry_interval_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">Max Retry Attempts</label>
              <input
                type="number"
                min={1}
                max={20}
                value={form.max_retries}
                onChange={(e) => setForm({ ...form, max_retries: Number(e.target.value) })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-6 pt-1">
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={form.always_wake}
                onChange={(e) => setForm({ ...form, always_wake: e.target.checked })}
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Always wake regardless of pre-outage state
            </label>
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Enabled
            </label>
          </div>

          <div className="pt-3 border-t border-slate-800 space-y-3">
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={form.nut_client_enabled}
                onChange={(e) => setForm({ ...form, nut_client_enabled: e.target.checked })}
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Link as Secondary NUT Client (Graceful Shutdown on OB/LB)
            </label>
            {form.nut_client_enabled && (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Client Platform</label>
                  <select
                    value={form.nut_client_platform}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        nut_client_platform: e.target.value as NutClientPlatform,
                      })
                    }
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
                  >
                    <option value="linux_debian">Debian / Ubuntu / Proxmox</option>
                    <option value="linux_rhel">RHEL / Rocky / Fedora</option>
                    <option value="truenas">TrueNAS SCALE / CORE</option>
                    <option value="synology">Synology DSM</option>
                    <option value="windows">Windows (WinNUT)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Monitored UPS Name</label>
                  <input
                    type="text"
                    value={form.nut_ups_name}
                    onChange={(e) => setForm({ ...form, nut_ups_name: e.target.value })}
                    placeholder={telemetry.ups_list[0]?.name || "ups1"}
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs font-mono text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">NUT Client User</label>
                  <input
                    type="text"
                    value={form.nut_client_user}
                    onChange={(e) => setForm({ ...form, nut_client_user: e.target.value })}
                    placeholder="wolnut_client"
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs font-mono text-white"
                  />
                </div>
              </div>
            )}
          </div>

          {arpMsg && (
            <div className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-sky-300">
              {arpMsg}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              onClick={() => setModalOpen(false)}
              className="px-4 py-2 rounded-lg bg-surface-800 text-sm text-slate-300"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveHost}
              disabled={savingHost}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white disabled:opacity-50"
            >
              {savingHost ? "Saving..." : "Save Host"}
            </button>
          </div>
        </div>
      </Modal>

      {/* Simulate Recovery Modal */}
      <Modal
        open={simModalOpen}
        title="Simulate Power Recovery Sequence"
        onClose={() => setSimModalOpen(false)}
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            Test your host boot priority and stagger configuration. In <strong>Dry-Run</strong>{" "}
            mode, WEBNUT evaluates eligible hosts and logs every wake step without broadcasting real
            WoL magic packets.
          </p>
          <label className="flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={simDryRun}
              onChange={(e) => setSimDryRun(e.target.checked)}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Dry-Run Mode (do not send real UDP magic packets)
          </label>
          <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              onClick={() => setSimModalOpen(false)}
              className="px-4 py-2 rounded-lg bg-surface-800 text-sm text-slate-300"
            >
              Cancel
            </button>
            <button
              onClick={handleSimulateRecovery}
              className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-sm font-medium text-white"
            >
              Start Simulation
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
