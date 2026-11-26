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
  Terminal,
  Trash2,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import { RecoveryBanner } from "../components/RecoveryBanner";
import { WakeStateBadge } from "../components/StatusBadge";
import type {
  HostDisconnectPolicy,
  HostPayload,
  HostRecord,
  HostTestRunResponse,
  NutClientPlatform,
  OrchestratorConfig,
  OrchestratorState,
  ShutdownMode,
  SshDeployResponse,
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
  virtual_ups_name: "",
  wake_min_battery_charge: null,
  shutdown_mode: "nut_stream",
  shutdown_battery_pct: null,
  shutdown_ob_delay_sec: null,
  disconnect_policy: "inherit",
  disconnect_grace_sec: null,
  ssh_port: 22,
  ssh_username: "",
  ssh_password: "",
  ssh_sudo_password: "",
  ssh_shutdown_cmd: "sudo -S shutdown -h now",
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
    disconnect_policy: "keep_running",
    disconnect_grace_sec: 600,
    repeater_enabled: true,
    repeater_port: 3493,
    cold_start_boot_grace_sec: 30,
    cold_start_resume_recovery: true,
    cold_start_wake_always_wake: true,
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
  const [simMode, setSimMode] = useState<"single" | "bulk">("bulk");
  const [simHostId, setSimHostId] = useState<number | "">("");
  const [simBulkTarget, setSimBulkTarget] = useState<"eligible" | "selected">("eligible");
  const [simBulkIds, setSimBulkIds] = useState<number[]>([]);
  const [simDryRun, setSimDryRun] = useState(true);
  const [simRunning, setSimRunning] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);
  const [hostTestResult, setHostTestResult] = useState<HostTestRunResponse | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<HostRecord | null>(null);
  const [deletingHost, setDeletingHost] = useState(false);

  // Quick One-Button SSH Deploy Modal
  const [deployHost, setDeployHost] = useState<HostRecord | null>(null);
  const [sshForm, setSshForm] = useState({
    ssh_port: 22,
    ssh_username: "root",
    ssh_password: "",
    ssh_sudo_password: "",
    save_credentials: true,
    nut_username: "monuser",
    nut_password: "",
    final_delay: 5,
    shutdown_cmd: "/sbin/shutdown -h +0",
  });
  const [deploying, setDeploying] = useState(false);
  const [deployResult, setDeployResult] = useState<SshDeployResponse | null>(null);

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
      virtual_ups_name: h.virtual_ups_name || "",
      wake_min_battery_charge: h.wake_min_battery_charge,
      shutdown_mode: h.shutdown_mode || "nut_stream",
      shutdown_battery_pct: h.shutdown_battery_pct,
      shutdown_ob_delay_sec: h.shutdown_ob_delay_sec,
      disconnect_policy: h.disconnect_policy || "inherit",
      disconnect_grace_sec: h.disconnect_grace_sec,
      ssh_port: h.ssh_port || 22,
      ssh_username: h.ssh_username || "",
      ssh_password: h.ssh_password || "",
      ssh_sudo_password: h.ssh_sudo_password || "",
      ssh_shutdown_cmd: h.ssh_shutdown_cmd || "sudo -S shutdown -h now",
    });
    setArpMsg(null);
    setModalOpen(true);
  };

  const openSshDeployModal = (h: HostRecord) => {
    setDeployHost(h);
    setDeployResult(null);
    setSshForm({
      ssh_port: h.ssh_port || 22,
      ssh_username: h.ssh_username || "root",
      ssh_password: h.ssh_password || "",
      ssh_sudo_password: h.ssh_sudo_password || "",
      save_credentials: true,
      nut_username: h.nut_client_user || "monuser",
      nut_password: "",
      final_delay: 5,
      shutdown_cmd: "/sbin/shutdown -h +0",
    });
  };

  const handleRunSshDeploy = async () => {
    if (!deployHost) return;
    setDeploying(true);
    setDeployResult(null);
    try {
      const res = await api.deployHostClient(deployHost.id, sshForm);
      setDeployResult(res);
      await onRefresh();
    } catch (err) {
      setDeployResult({
        ok: false,
        steps: [],
        error: err instanceof ApiError ? err.message : "SSH deployment failed",
        virtual_ups_name: "",
        server_host: "",
        nut_port: 3493,
      });
    } finally {
      setDeploying(false);
    }
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
          `No ARP entry found for ${res.resolved_ip ?? form.ip_or_host}. Ensure the host is currently online.`
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

  const handleDeleteHost = (h: HostRecord) => {
    setDeleteTarget(h);
  };

  const confirmDeleteHost = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeletingHost(true);
    setFeedback(null);
    try {
      await api.deleteHost(target.id);
      setDeleteTarget(null);
      await onRefresh();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to delete host "${target.name}"`,
      });
      setDeleteTarget(null);
    } finally {
      setDeletingHost(false);
    }
  };

  const openBulkTestModal = () => {
    setSimMode("bulk");
    setSimHostId(hosts[0]?.id ?? "");
    setSimBulkTarget("eligible");
    setSimBulkIds([]);
    setSimDryRun(true);
    setSimError(null);
    setHostTestResult(null);
    setSimModalOpen(true);
  };

  const openSingleTestModal = (h: HostRecord) => {
    setSimMode("single");
    setSimHostId(h.id);
    setSimDryRun(true);
    setSimError(null);
    setHostTestResult(null);
    setSimModalOpen(true);
  };

  const toggleBulkHostId = (id: number) => {
    setSimBulkIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleExecuteTestRun = async () => {
    setSimRunning(true);
    setSimError(null);
    try {
      if (simMode === "single") {
        const hid = Number(simHostId);
        if (!hid) {
          setSimError("Select a host to run an individual test.");
          return;
        }
        const res = await api.testRunHost(hid, simDryRun);
        setHostTestResult(res);
        await onRefresh();
      } else {
        if (simBulkTarget === "selected" && simBulkIds.length === 0) {
          setSimError("Select at least one host for a selected bulk test run.");
          return;
        }
        await api.simulateRecovery(
          simDryRun,
          "bulk",
          simBulkTarget === "selected" ? simBulkIds : []
        );
        setSimModalOpen(false);
      }
    } catch (err) {
      setSimError(err instanceof ApiError ? err.message : "Test run failed");
    } finally {
      setSimRunning(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            Managed Hosts &amp; Per-Host Power Rules
          </h2>
          <p className="text-sm text-slate-400">
            Configure individual shutdown battery %, OB timers, staged wake thresholds, and
            one-button SSH client deployment
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={openBulkTestModal}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm font-medium text-slate-200"
          >
            <FlaskConical className="h-4 w-4 text-amber-400" />
            Test Run
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
            <h3 className="text-base font-semibold text-white">
              Global Recovery &amp; Disconnect Defaults
            </h3>
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

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Monitored UPS Name</label>
            <input
              type="text"
              value={policy.monitored_ups}
              onChange={(e) => setPolicy({ ...policy, monitored_ups: e.target.value })}
              placeholder={telemetry.ups_list[0]?.name || "Auto (first UPS)"}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Default Wake Battery (%)
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
              Host / UPS Poll (sec)
            </label>
            <div className="flex gap-2">
              <input
                type="number"
                min={5}
                value={policy.host_poll_interval_sec}
                onChange={(e) =>
                  setPolicy({ ...policy, host_poll_interval_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-sm text-white"
              />
              <input
                type="number"
                min={1}
                value={policy.ups_poll_interval_sec}
                onChange={(e) =>
                  setPolicy({ ...policy, ups_poll_interval_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-sm text-white"
              />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 pt-3 border-t border-slate-800/70">
          <div className="flex flex-wrap items-center gap-5">
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
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={policy.cold_start_resume_recovery}
                onChange={(e) =>
                  setPolicy({ ...policy, cold_start_resume_recovery: e.target.checked })
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Resume Interrupted Recovery on Boot
            </label>
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={policy.cold_start_wake_always_wake}
                onChange={(e) =>
                  setPolicy({ ...policy, cold_start_wake_always_wake: e.target.checked })
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Wake Offline &ldquo;Always Wake&rdquo; Hosts on Cold Start
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <label className="inline-flex items-center gap-2 text-xs text-slate-400">
              <span>Cold-Start Boot Grace (sec)</span>
              <input
                type="number"
                min={0}
                max={600}
                value={policy.cold_start_boot_grace_sec}
                onChange={(e) =>
                  setPolicy({
                    ...policy,
                    cold_start_boot_grace_sec: Math.max(0, Number(e.target.value)),
                  })
                }
                className="w-20 rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-1 text-xs text-white"
              />
            </label>
            <span className="text-xs text-slate-400">
              Disconnect Policy:{" "}
              <strong className="text-slate-200">
                {policy.disconnect_policy === "keep_running"
                  ? "Keep Running & Alert"
                  : `Grace ${Math.round(policy.disconnect_grace_sec / 60)} min then Shutdown`}
              </strong>
            </span>
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
                <th className="px-4 py-3">Host / Stream</th>
                <th className="px-4 py-3">Shutdown &amp; Wake Rules</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {hosts.map((h) => {
                const effWake = h.wake_min_battery_charge ?? policy.min_battery_charge;
                return (
                  <tr key={h.id} className="hover:bg-surface-800/40">
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      <div>#{h.boot_priority}</div>
                      <div className="text-slate-500">+{h.stagger_delay_sec}s</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span
                          className={`h-2 w-2 rounded-full ${
                            h.is_online ? "bg-emerald-400" : "bg-slate-600"
                          }`}
                        />
                        <span className="font-medium text-white">{h.name}</span>
                        {h.virtual_ups_name && (
                          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-950 border border-slate-700 text-sky-300">
                            {h.virtual_ups_name}@webnut
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-slate-400 font-mono mt-0.5">
                        {h.ip_or_host} • {h.mac_address}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-300 space-y-0.5">
                      <div>
                        <span className="text-amber-300">
                          Shutdown:{" "}
                          {h.shutdown_mode === "disabled"
                            ? "Disabled"
                            : h.shutdown_battery_pct !== null
                            ? `≤ ${h.shutdown_battery_pct}%`
                            : h.shutdown_ob_delay_sec !== null
                            ? `${Math.round(h.shutdown_ob_delay_sec / 60)}m OB`
                            : "On LB"}
                        </span>{" "}
                        <span className="text-slate-500">({h.shutdown_mode})</span>
                      </div>
                      <div className="text-emerald-300">
                        Wake: ≥ {effWake}% OL{" "}
                        {h.always_wake && (
                          <span className="text-indigo-300">(Always Wake)</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <WakeStateBadge
                        state={h.wake_state}
                        attempt={h.wake_attempt}
                        maxRetries={h.max_retries}
                      />
                      {h.last_wake_error && (
                        <div className="text-[11px] text-amber-300/90 mt-1 max-w-xs truncate">
                          {h.last_wake_error}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap space-x-1">
                      <button
                        onClick={() => openSingleTestModal(h)}
                        title="Run Individual Host Test"
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-amber-300 text-xs"
                      >
                        <FlaskConical className="h-3 w-3" />
                        Test
                      </button>
                      <button
                        onClick={() => openSshDeployModal(h)}
                        title="One-Button SSH/SFTP Deploy"
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-emerald-300 text-xs"
                      >
                        <Terminal className="h-3 w-3" />
                        SSH Deploy
                      </button>
                      <button
                        onClick={() => onOpenNutClientSetup(h.id)}
                        title="Manual Client Config"
                        className="inline-flex items-center gap-1 px-2 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sky-300 text-xs"
                      >
                        <Link2 className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => void api.wakeHost(h.id, true)}
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
                );
              })}
              {hosts.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-slate-500 text-sm">
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
        title={editingId === null ? "Add Managed Host" : "Edit Managed Host & Power Rules"}
        onClose={() => setModalOpen(false)}
        maxWidth="max-w-3xl"
      >
        <div className="space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-300 mb-1">Friendly Name</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Proxmox 1"
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
                Virtual UPS Stream Name (Repeater)
              </label>
              <input
                type="text"
                value={form.virtual_ups_name}
                onChange={(e) => setForm({ ...form, virtual_ups_name: e.target.value })}
                placeholder="Auto from name (e.g. proxmox_1)"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">
                Boot Priority / Stagger (sec)
              </label>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={form.boot_priority}
                  onChange={(e) => setForm({ ...form, boot_priority: Number(e.target.value) })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                />
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
            </div>
            <div>
              <label className="block text-xs text-slate-300 mb-1">
                Wake at Battery % (blank = global {policy.min_battery_charge}%)
              </label>
              <input
                type="number"
                min={0}
                max={100}
                value={form.wake_min_battery_charge ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    wake_min_battery_charge: e.target.value ? Number(e.target.value) : null,
                  })
                }
                placeholder={`e.g. 10 or 50 (default ${policy.min_battery_charge})`}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
          </div>

          {/* Per-Host Shutdown & Disconnect Grace Rules */}
          <div className="pt-3 border-t border-slate-800 space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-amber-300">
              Per-Host Shutdown &amp; UPS Disconnect Rules (Streamed Live via Repeater)
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Shutdown Method</label>
                <select
                  value={form.shutdown_mode}
                  onChange={(e) =>
                    setForm({ ...form, shutdown_mode: e.target.value as ShutdownMode })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
                >
                  <option value="nut_stream">NUT Repeater Stream</option>
                  <option value="both">NUT Stream + SSH Fallback</option>
                  <option value="ssh">SSH Command Only</option>
                  <option value="disabled">Never Auto-Shutdown</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Shutdown at Battery ≤ (%)
                </label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={form.shutdown_battery_pct ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      shutdown_battery_pct: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                  placeholder="Only on LB"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Or After On-Battery (min)
                </label>
                <input
                  type="number"
                  min={0}
                  value={
                    form.shutdown_ob_delay_sec !== null
                      ? Math.round(form.shutdown_ob_delay_sec / 60)
                      : ""
                  }
                  onChange={(e) =>
                    setForm({
                      ...form,
                      shutdown_ob_delay_sec: e.target.value ? Number(e.target.value) * 60 : null,
                    })
                  }
                  placeholder="None"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  On UPS Disconnect
                </label>
                <select
                  value={form.disconnect_policy}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      disconnect_policy: e.target.value as HostDisconnectPolicy,
                    })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
                >
                  <option value="inherit">Inherit Global</option>
                  <option value="keep_running">Keep Running</option>
                  <option value="shutdown_after_grace">Shutdown After Grace</option>
                </select>
              </div>
            </div>
          </div>

          {/* Optional Saved Encrypted SSH Credentials */}
          <div className="pt-3 border-t border-slate-800 space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Optional Saved SSH Credentials (Encrypted at Rest)
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
              <input
                type="number"
                value={form.ssh_port}
                onChange={(e) => setForm({ ...form, ssh_port: Number(e.target.value) })}
                placeholder="22"
                className="rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs font-mono text-white"
              />
              <input
                value={form.ssh_username}
                onChange={(e) => setForm({ ...form, ssh_username: e.target.value })}
                placeholder="SSH username"
                className="rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs font-mono text-white"
              />
              <input
                type="password"
                value={form.ssh_password}
                onChange={(e) => setForm({ ...form, ssh_password: e.target.value })}
                placeholder="SSH password"
                className="rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs font-mono text-white"
              />
              <input
                value={form.ssh_shutdown_cmd}
                onChange={(e) => setForm({ ...form, ssh_shutdown_cmd: e.target.value })}
                placeholder="sudo -S shutdown -h now"
                className="rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs font-mono text-white"
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
            <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={form.nut_client_enabled}
                onChange={(e) => setForm({ ...form, nut_client_enabled: e.target.checked })}
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Link as Secondary NUT Client
            </label>
            {form.nut_client_enabled && (
              <select
                value={form.nut_client_platform}
                onChange={(e) =>
                  setForm({
                    ...form,
                    nut_client_platform: e.target.value as NutClientPlatform,
                  })
                }
                className="rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-1 text-xs text-white"
              >
                <option value="linux_debian">Debian / Ubuntu / Proxmox</option>
                <option value="linux_rhel">RHEL / Rocky / Fedora</option>
                <option value="truenas">TrueNAS</option>
                <option value="synology">Synology DSM</option>
                <option value="windows">Windows</option>
              </select>
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

      {/* One-Button SSH/SFTP Deploy Modal */}
      <Modal
        open={deployHost !== null}
        title={`One-Button SSH/SFTP Client Setup — ${deployHost?.name ?? ""}`}
        onClose={() => setDeployHost(null)}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-400">
            Installs <code>nut-client</code> and uploads <code>nut.conf</code> +{" "}
            <code>upsmon.conf</code> via SFTP pointing directly at WebNUT&apos;s Repeater stream (
            <code>{deployHost?.virtual_ups_name || deployHost?.name}@webnut</code>).
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">SSH Username</label>
              <input
                value={sshForm.ssh_username}
                onChange={(e) => setSshForm({ ...sshForm, ssh_username: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">SSH Password</label>
              <input
                type="password"
                value={sshForm.ssh_password}
                onChange={(e) => setSshForm({ ...sshForm, ssh_password: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">SSH Port</label>
              <input
                type="number"
                value={sshForm.ssh_port}
                onChange={(e) => setSshForm({ ...sshForm, ssh_port: Number(e.target.value) })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Sudo Password (if different)
              </label>
              <input
                type="password"
                value={sshForm.ssh_sudo_password}
                onChange={(e) => setSshForm({ ...sshForm, ssh_sudo_password: e.target.value })}
                placeholder="Same as SSH password if blank"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
          </div>

          <label className="inline-flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={sshForm.save_credentials}
              onChange={(e) => setSshForm({ ...sshForm, save_credentials: e.target.checked })}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Save encrypted SSH credentials on this host (for SSH fallback shutdown)
          </label>

          {deployResult && (
            <div
              className={`rounded-xl border p-3 text-xs space-y-2 ${
                deployResult.ok
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-rose-500/40 bg-rose-500/10 text-rose-300"
              }`}
            >
              <div className="font-semibold">
                {deployResult.ok
                  ? `Deployed cleanly (${deployResult.virtual_ups_name}@${deployResult.server_host}:${deployResult.nut_port})`
                  : deployResult.error}
              </div>
              {deployResult.steps.length > 0 && (
                <ul className="list-disc pl-5 space-y-0.5 text-slate-200 font-mono">
                  {deployResult.steps.map((s, idx) => (
                    <li key={idx}>{s}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              onClick={() => setDeployHost(null)}
              className="px-4 py-2 rounded-lg bg-surface-800 text-xs text-slate-300"
            >
              Close
            </button>
            <button
              onClick={() => void handleRunSshDeploy()}
              disabled={deploying || !sshForm.ssh_username.trim() || !sshForm.ssh_password.trim()}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-medium text-white disabled:opacity-50"
            >
              {deploying ? "Deploying via SSH/SFTP..." : "Deploy NUT Client Now"}
            </button>
          </div>
        </div>
      </Modal>

      {/* Individual Host & Bulk Test Run Modal */}
      <Modal
        open={simModalOpen}
        title="Test Run — Individual Host or Bulk Recovery"
        onClose={() => setSimModalOpen(false)}
        maxWidth="max-w-xl"
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 rounded-xl bg-surface-950 p-1 border border-slate-800">
            <button
              onClick={() => {
                setSimMode("single");
                setHostTestResult(null);
                setSimError(null);
                if (!simHostId && hosts[0]) setSimHostId(hosts[0].id);
              }}
              className={`py-2 rounded-lg text-xs font-medium transition-colors ${
                simMode === "single"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Individual Host Test
            </button>
            <button
              onClick={() => {
                setSimMode("bulk");
                setHostTestResult(null);
                setSimError(null);
              }}
              className={`py-2 rounded-lg text-xs font-medium transition-colors ${
                simMode === "bulk"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Bulk Recovery Test Run
            </button>
          </div>

          {simMode === "single" ? (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Select Host</label>
                <select
                  value={simHostId}
                  onChange={(e) => {
                    setSimHostId(e.target.value ? Number(e.target.value) : "");
                    setHostTestResult(null);
                  }}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                >
                  <option value="">Choose a host...</option>
                  {hosts.map((h) => (
                    <option key={h.id} value={h.id}>
                      {h.name} ({h.ip_or_host})
                    </option>
                  ))}
                </select>
              </div>
              <p className="text-xs text-slate-400">
                Verifies live reachability, Repeater NUT stream connection, battery threshold,
                shutdown &amp; disconnect rules. Uncheck Dry-Run to transmit a live WoL magic
                packet to this host.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-3 text-xs">
                <label className="inline-flex items-center gap-2 text-slate-200 cursor-pointer">
                  <input
                    type="radio"
                    checked={simBulkTarget === "eligible"}
                    onChange={() => setSimBulkTarget("eligible")}
                  />
                  All Eligible Hosts (pre-outage online or Always Wake)
                </label>
                <label className="inline-flex items-center gap-2 text-slate-200 cursor-pointer">
                  <input
                    type="radio"
                    checked={simBulkTarget === "selected"}
                    onChange={() => setSimBulkTarget("selected")}
                  />
                  Pick Specific Hosts
                </label>
              </div>

              {simBulkTarget === "selected" && (
                <div className="rounded-xl border border-slate-800 bg-surface-950 divide-y divide-slate-800/70 max-h-48 overflow-y-auto">
                  {hosts.map((h) => (
                    <label
                      key={h.id}
                      className="px-3 py-2 flex items-center justify-between text-xs text-slate-200 cursor-pointer hover:bg-surface-900"
                    >
                      <span className="inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={simBulkIds.includes(h.id)}
                          onChange={() => toggleBulkHostId(h.id)}
                          className="rounded border-slate-600 bg-surface-900 text-indigo-500"
                        />
                        <span className="font-medium text-white">{h.name}</span>
                        <span className="font-mono text-slate-400">({h.ip_or_host})</span>
                      </span>
                      <span className="font-mono text-slate-500">#{h.boot_priority}</span>
                    </label>
                  ))}
                  {hosts.length === 0 && (
                    <div className="p-4 text-center text-xs text-slate-500">
                      No managed hosts configured.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <label className="flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={simDryRun}
              onChange={(e) => setSimDryRun(e.target.checked)}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Dry-Run Mode (diagnostics only — do not send real UDP magic packets)
          </label>

          {simError && (
            <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
              {simError}
            </div>
          )}

          {hostTestResult && simMode === "single" && (
            <div className="rounded-xl border border-slate-700 bg-surface-950 p-4 space-y-2.5 text-xs">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <span className="font-semibold text-white">
                  {hostTestResult.host_name} ({hostTestResult.ip_or_host})
                </span>
                <span
                  className={`px-2 py-0.5 rounded font-medium ${
                    hostTestResult.started_live_wake
                      ? "bg-amber-500/20 text-amber-300"
                      : "bg-emerald-500/20 text-emerald-300"
                  }`}
                >
                  {hostTestResult.started_live_wake ? "Live WoL Sent" : "Dry-Run Complete"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span className="text-slate-400">Host Reachability: </span>
                  <strong className={hostTestResult.reachable ? "text-emerald-300" : "text-slate-300"}>
                    {hostTestResult.reachable ? "Online" : "Offline"} ({hostTestResult.check_method})
                  </strong>
                </div>
                <div>
                  <span className="text-slate-400">NUT Repeater ({hostTestResult.virtual_ups_name}): </span>
                  <strong
                    className={
                      hostTestResult.nut_client_connected ? "text-emerald-300" : "text-amber-300"
                    }
                  >
                    {hostTestResult.nut_client_connected ? "Connected" : "Not Connected"}
                  </strong>
                </div>
                <div>
                  <span className="text-slate-400">Wake Threshold: </span>
                  <strong className="text-white">
                    &ge; {hostTestResult.effective_wake_battery_pct}% OL{" "}
                    {hostTestResult.current_battery_charge !== null
                      ? `(UPS: ${hostTestResult.current_battery_charge.toFixed(0)}%)`
                      : ""}
                  </strong>
                </div>
                <div>
                  <span className="text-slate-400">WoL Targets: </span>
                  <span className="font-mono text-slate-200">
                    {hostTestResult.wol_targets.join(", ")}
                  </span>
                </div>
              </div>
              <div className="text-slate-300">
                <span className="text-slate-400">Shutdown Rule:</span>{" "}
                {hostTestResult.shutdown_rule_summary}
              </div>
              <div className="text-slate-300">
                <span className="text-slate-400">Disconnect Grace:</span>{" "}
                {hostTestResult.disconnect_rule_summary}
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              onClick={() => setSimModalOpen(false)}
              className="px-4 py-2 rounded-lg bg-surface-800 text-sm text-slate-300"
            >
              Close
            </button>
            <button
              onClick={() => void handleExecuteTestRun()}
              disabled={simRunning || (simMode === "single" && !simHostId)}
              className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-sm font-medium text-white disabled:opacity-50"
            >
              {simRunning
                ? "Running..."
                : simMode === "single"
                ? simDryRun
                  ? "Run Host Dry-Run"
                  : "Send Live Host WoL Test"
                : simDryRun
                ? "Start Bulk Dry-Run"
                : "Start Live Bulk Test Run"}
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Managed Host"
        message={
          deleteTarget
            ? `Delete host "${deleteTarget.name}" (${deleteTarget.ip_or_host})? Its recorded WoL & shutdown policy settings will be removed.`
            : ""
        }
        confirmLabel="Delete Host"
        variant="danger"
        busy={deletingHost}
        onConfirm={confirmDeleteHost}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
