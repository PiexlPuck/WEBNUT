import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Download,
  Edit3,
  Eye,
  FlaskConical,
  Play,
  Plus,
  RefreshCw,
  ShieldAlert,
  Square,
  Trash2,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import type {
  FlowDrillMode,
  FlowDrillReportRecord,
  FlowDrillReportsResponse,
  FlowDrillSchedulePayload,
  FlowDrillScheduleRecord,
  FlowDrillTargetScope,
  HostRecord,
  SelfTestFrequency,
  TelemetrySnapshot,
} from "../types";

interface DrillsPageProps {
  hosts: HostRecord[];
  telemetry: TelemetrySnapshot;
  latestDrillUpdate: FlowDrillReportRecord | null;
  onRefresh: () => Promise<void>;
}

const WEEKDAYS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
];

const EMPTY_SCHEDULE: FlowDrillSchedulePayload = {
  name: "Weekly Dry-Run Flow Drill",
  mode: "dry_run",
  target_scope: "all",
  host_ids: [],
  frequency: "weekly",
  day_of_week: 6,
  day_of_month: 1,
  time_hhmm: "04:00",
  min_battery_pct: 80,
  require_online: true,
  shutdown_timeout_sec: 180,
  restore_delay_sec: 15,
  notify_on_complete: true,
  notify_on_fail: true,
  enabled: true,
};

function formatDurationSec(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  const mins = Math.floor(s / 60);
  const rem = s % 60;
  if (mins >= 60) {
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }
  return mins > 0 ? `${mins}m ${rem}s` : `${rem}s`;
}

function DrillStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    passed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    warning: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    failed: "bg-rose-500/15 text-rose-300 border-rose-500/40",
    aborted: "bg-amber-500/15 text-amber-200 border-amber-500/40",
    skipped: "bg-slate-700/60 text-slate-300 border-slate-600",
    running: "bg-sky-500/15 text-sky-300 border-sky-500/40 animate-pulse",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] uppercase font-semibold ${
        map[status] ?? "bg-slate-800 text-slate-300 border-slate-700"
      }`}
    >
      {status}
    </span>
  );
}

export function DrillsPage({
  hosts,
  telemetry,
  latestDrillUpdate,
  onRefresh,
}: DrillsPageProps) {
  const [schedules, setSchedules] = useState<FlowDrillScheduleRecord[]>([]);
  const [reportsData, setReportsData] = useState<FlowDrillReportsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  // One-Off Runner State
  const [runForm, setRunForm] = useState<{
    mode: FlowDrillMode;
    target_scope: FlowDrillTargetScope;
    host_ids: number[];
    min_battery_pct: number;
    require_online: boolean;
    shutdown_timeout_sec: number;
    restore_delay_sec: number;
    notify_on_complete: boolean;
    notify_on_fail: boolean;
  }>({
    mode: "dry_run",
    target_scope: "all",
    host_ids: [],
    min_battery_pct: 80,
    require_online: true,
    shutdown_timeout_sec: 180,
    restore_delay_sec: 15,
    notify_on_complete: true,
    notify_on_fail: true,
  });
  const [startingDrill, setStartingDrill] = useState(false);
  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);

  // Schedule Modal
  const [schedModalOpen, setSchedModalOpen] = useState(false);
  const [editingSchedId, setEditingSchedId] = useState<number | null>(null);
  const [schedForm, setSchedForm] = useState<FlowDrillSchedulePayload>(EMPTY_SCHEDULE);
  const [savingSched, setSavingSched] = useState(false);

  // Detail & Confirm Dialogs
  const [detailReport, setDetailReport] = useState<FlowDrillReportRecord | null>(null);
  const [abortTarget, setAbortTarget] = useState<FlowDrillReportRecord | null>(null);
  const [aborting, setAborting] = useState(false);
  const [deleteSchedTarget, setDeleteSchedTarget] = useState<FlowDrillScheduleRecord | null>(null);
  const [deletingSched, setDeletingSched] = useState(false);
  const [deleteReportTarget, setDeleteReportTarget] = useState<FlowDrillReportRecord | null>(null);
  const [clearReportsOpen, setClearReportsOpen] = useState(false);
  const [clearingReports, setClearingReports] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [scheds, reps] = await Promise.all([
        api.getFlowDrillSchedules(),
        api.getFlowDrillReports(100),
      ]);
      setSchedules(scheds);
      setReportsData(reps);
    } catch {
      // ignore transient load failure
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Merge live WebSocket drill updates immediately
  useEffect(() => {
    if (!latestDrillUpdate) return;
    setReportsData((prev) => {
      if (!prev) return prev;
      const exists = prev.reports.some((r) => r.id === latestDrillUpdate.id);
      const nextReports = exists
        ? prev.reports.map((r) => (r.id === latestDrillUpdate.id ? latestDrillUpdate : r))
        : [latestDrillUpdate, ...prev.reports];
      return { ...prev, reports: nextReports };
    });
    if (detailReport && detailReport.id === latestDrillUpdate.id) {
      setDetailReport(latestDrillUpdate);
    }
    if (latestDrillUpdate.status !== "running") {
      void loadAll();
    }
  }, [latestDrillUpdate, detailReport, loadAll]);

  const activeDrill = useMemo(
    () => (reportsData?.reports ?? []).find((r) => r.status === "running") ?? null,
    [reportsData]
  );

  const enabledHosts = useMemo(() => hosts.filter((h) => h.enabled), [hosts]);

  const toggleHostId = (
    list: number[],
    id: number,
    setter: (next: number[]) => void
  ) => {
    setter(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  };

  const triggerDrill = async (confirmLive: boolean) => {
    setStartingDrill(true);
    setFeedback(null);
    try {
      const rep = await api.runFlowDrill({
        ...runForm,
        confirm_live: confirmLive,
      });
      setLiveConfirmOpen(false);
      setFeedback({
        ok: rep.status !== "failed" && rep.status !== "skipped",
        msg:
          rep.status === "running"
            ? `Flow drill #${rep.id} (${rep.mode}) started.`
            : rep.summary_message || `Flow drill finished (${rep.status}).`,
      });
      await loadAll();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to start flow drill",
      });
    } finally {
      setStartingDrill(false);
    }
  };

  const handleRunButtonClick = () => {
    if (runForm.target_scope === "selected" && runForm.host_ids.length === 0) {
      setFeedback({ ok: false, msg: "Select at least one target host or choose All Enabled Hosts." });
      return;
    }
    if (runForm.mode === "live") {
      setLiveConfirmOpen(true);
      return;
    }
    void triggerDrill(false);
  };

  const confirmAbort = async () => {
    if (!abortTarget) return;
    setAborting(true);
    try {
      await api.abortFlowDrill(abortTarget.id);
      setAbortTarget(null);
      await loadAll();
      setFeedback({ ok: true, msg: "Flow drill aborted and NUT shutdown overrides cleared." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to abort drill",
      });
    } finally {
      setAborting(false);
    }
  };

  const openCreateSchedule = () => {
    setEditingSchedId(null);
    setSchedForm(EMPTY_SCHEDULE);
    setSchedModalOpen(true);
  };

  const openEditSchedule = (s: FlowDrillScheduleRecord) => {
    setEditingSchedId(s.id);
    setSchedForm({
      name: s.name,
      mode: s.mode,
      target_scope: s.target_scope,
      host_ids: s.host_ids,
      frequency: s.frequency,
      day_of_week: s.day_of_week,
      day_of_month: s.day_of_month,
      time_hhmm: s.time_hhmm,
      min_battery_pct: s.min_battery_pct,
      require_online: s.require_online,
      shutdown_timeout_sec: s.shutdown_timeout_sec,
      restore_delay_sec: s.restore_delay_sec,
      notify_on_complete: s.notify_on_complete,
      notify_on_fail: s.notify_on_fail,
      enabled: s.enabled,
    });
    setSchedModalOpen(true);
  };

  const handleSaveSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (schedForm.target_scope === "selected" && schedForm.host_ids.length === 0) {
      setFeedback({ ok: false, msg: "Select at least one host for selected target scope." });
      return;
    }
    setSavingSched(true);
    setFeedback(null);
    try {
      if (editingSchedId !== null) {
        await api.updateFlowDrillSchedule(editingSchedId, schedForm);
        setFeedback({ ok: true, msg: "Updated scheduled flow drill." });
      } else {
        await api.createFlowDrillSchedule(schedForm);
        setFeedback({ ok: true, msg: "Created scheduled flow drill." });
      }
      setSchedModalOpen(false);
      await loadAll();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save schedule",
      });
    } finally {
      setSavingSched(false);
    }
  };

  const handleToggleSchedule = async (s: FlowDrillScheduleRecord) => {
    try {
      await api.updateFlowDrillSchedule(s.id, {
        name: s.name,
        mode: s.mode,
        target_scope: s.target_scope,
        host_ids: s.host_ids,
        frequency: s.frequency,
        day_of_week: s.day_of_week,
        day_of_month: s.day_of_month,
        time_hhmm: s.time_hhmm,
        min_battery_pct: s.min_battery_pct,
        require_online: s.require_online,
        shutdown_timeout_sec: s.shutdown_timeout_sec,
        restore_delay_sec: s.restore_delay_sec,
        notify_on_complete: s.notify_on_complete,
        notify_on_fail: s.notify_on_fail,
        enabled: !s.enabled,
      });
      await loadAll();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to toggle schedule",
      });
    }
  };

  const handleRunScheduleNow = async (s: FlowDrillScheduleRecord) => {
    setFeedback(null);
    try {
      const rep = await api.runFlowDrillScheduleNow(s.id);
      setFeedback({
        ok: rep.status !== "failed" && rep.status !== "skipped",
        msg: `Triggered scheduled drill '${s.name}' (${rep.mode}).`,
      });
      await loadAll();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to run scheduled drill",
      });
    }
  };

  const confirmDeleteSchedule = async () => {
    if (!deleteSchedTarget) return;
    setDeletingSched(true);
    try {
      await api.deleteFlowDrillSchedule(deleteSchedTarget.id);
      setDeleteSchedTarget(null);
      await loadAll();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to delete schedule",
      });
    } finally {
      setDeletingSched(false);
    }
  };

  const confirmDeleteReport = async () => {
    if (!deleteReportTarget) return;
    try {
      await api.deleteFlowDrillReport(deleteReportTarget.id);
      if (detailReport?.id === deleteReportTarget.id) setDetailReport(null);
      setDeleteReportTarget(null);
      await loadAll();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to delete report",
      });
    }
  };

  const confirmClearReports = async () => {
    setClearingReports(true);
    try {
      await api.clearFlowDrillReports();
      setClearReportsOpen(false);
      await loadAll();
      setFeedback({ ok: true, msg: "Cleared completed flow drill reports." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to clear reports",
      });
    } finally {
      setClearingReports(false);
    }
  };

  const handleExportCsv = () => {
    const rows = reportsData?.reports ?? [];
    const headers = [
      "id",
      "mode",
      "trigger_type",
      "schedule_name",
      "status",
      "ups_name",
      "battery_start",
      "battery_end",
      "hosts_total",
      "hosts_passed",
      "hosts_failed",
      "duration_sec",
      "started_at",
      "completed_at",
      "summary_message",
    ];
    const escape = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [
      headers.join(","),
      ...rows.map((r) =>
        [
          r.id,
          r.mode,
          r.trigger_type,
          r.schedule_name,
          r.status,
          r.ups_name,
          r.battery_start,
          r.battery_end,
          r.hosts_total,
          r.hosts_passed,
          r.hosts_failed,
          r.duration_sec,
          r.started_at,
          r.completed_at,
          r.summary_message,
        ]
          .map(escape)
          .join(",")
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `webnut-flow-drills-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const summary = reportsData?.summary;
  const reports = reportsData?.reports ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <FlaskConical className="h-6 w-6 text-indigo-400" />
            End-to-End Flow Drills
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            Verify the complete outage &rarr; graceful shutdown &rarr; Wake-on-LAN restore pipeline with one-off or scheduled drills
          </p>
        </div>
        <button
          onClick={() => void Promise.all([onRefresh(), loadAll()])}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm text-slate-200 self-start sm:self-auto"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {feedback && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm flex items-center gap-2 ${
            feedback.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-rose-500/40 bg-rose-500/10 text-rose-300"
          }`}
        >
          {feedback.ok ? (
            <CheckCircle2 className="h-4 w-4 shrink-0" />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0" />
          )}
          <span>{feedback.msg}</span>
        </div>
      )}

      {/* Active Drill Banner */}
      {activeDrill && (
        <div className="rounded-2xl border border-sky-500/40 bg-sky-500/10 p-5 space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Activity className="h-5 w-5 text-sky-300 animate-pulse" />
              <div>
                <div className="text-sm font-semibold text-white">
                  Active Flow Drill #{activeDrill.id} ({activeDrill.mode === "live" ? "Live Full Cycle" : "Dry-Run"})
                </div>
                <div className="text-xs text-sky-200/90">
                  Stage: <span className="font-mono uppercase">{activeDrill.current_stage}</span>{" "}
                  • {activeDrill.summary_message}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setDetailReport(activeDrill)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-900/80 hover:bg-surface-800 border border-sky-500/30 text-xs font-medium text-sky-200"
              >
                <Eye className="h-3.5 w-3.5" />
                Live View
              </button>
              <button
                onClick={() => setAbortTarget(activeDrill)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-xs font-medium text-white"
              >
                <Square className="h-3.5 w-3.5" />
                Abort Drill
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {activeDrill.steps.map((st, idx) => (
              <div
                key={`${st.stage}-${idx}`}
                className="inline-flex items-center gap-2 px-2.5 py-1 rounded-lg bg-surface-950/80 border border-slate-800 text-xs"
              >
                <span className="font-mono text-slate-300">{st.stage}</span>
                <DrillStatusBadge status={st.status} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* One-Off Flow Drill Runner */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div>
            <h3 className="text-base font-semibold text-white">One-Off Flow Drill</h3>
            <p className="text-xs text-slate-400">
              Run an immediate readiness check (Dry-Run) or full shutdown &rarr; offline verification &rarr; WoL restore test
            </p>
          </div>
          <button
            onClick={handleRunButtonClick}
            disabled={startingDrill || Boolean(activeDrill) || enabledHosts.length === 0}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50 ${
              runForm.mode === "live"
                ? "bg-amber-600 hover:bg-amber-500"
                : "bg-indigo-600 hover:bg-indigo-500"
            }`}
          >
            <Play className="h-4 w-4" />
            {startingDrill
              ? "Starting..."
              : runForm.mode === "live"
              ? "Run Live Full Drill"
              : "Run Dry-Run Drill"}
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Drill Mode</label>
            <select
              value={runForm.mode}
              onChange={(e) => setRunForm({ ...runForm, mode: e.target.value as FlowDrillMode })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            >
              <option value="dry_run">Safe Dry-Run (Verify Readiness Only)</option>
              <option value="live">Live Full Cycle (Shutdown &rarr; Verify Off &rarr; WoL Restore)</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Target Scope</label>
            <select
              value={runForm.target_scope}
              onChange={(e) =>
                setRunForm({
                  ...runForm,
                  target_scope: e.target.value as FlowDrillTargetScope,
                })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            >
              <option value="all">All Enabled Hosts ({enabledHosts.length})</option>
              <option value="selected">Selected Hosts Only</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Min UPS Battery (%)</label>
            <input
              type="number"
              min={0}
              max={100}
              value={runForm.min_battery_pct}
              onChange={(e) =>
                setRunForm({ ...runForm, min_battery_pct: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Shutdown Timeout (s)</label>
              <input
                type="number"
                min={10}
                max={1800}
                value={runForm.shutdown_timeout_sec}
                onChange={(e) =>
                  setRunForm({ ...runForm, shutdown_timeout_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Restore Delay (s)</label>
              <input
                type="number"
                min={0}
                max={300}
                value={runForm.restore_delay_sec}
                onChange={(e) =>
                  setRunForm({ ...runForm, restore_delay_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-sm text-white"
              />
            </div>
          </div>
        </div>

        {runForm.target_scope === "selected" && (
          <div className="space-y-1.5">
            <label className="block text-xs text-slate-400">Select Target Hosts</label>
            <div className="flex flex-wrap gap-2">
              {enabledHosts.map((h) => {
                const picked = runForm.host_ids.includes(h.id);
                return (
                  <button
                    key={h.id}
                    type="button"
                    onClick={() =>
                      toggleHostId(runForm.host_ids, h.id, (next) =>
                        setRunForm({ ...runForm, host_ids: next })
                      )
                    }
                    className={`px-3 py-1.5 rounded-lg border text-xs font-medium ${
                      picked
                        ? "bg-indigo-600/25 border-indigo-500 text-indigo-200"
                        : "bg-surface-950 border-slate-700 text-slate-400 hover:text-white"
                    }`}
                  >
                    {h.name} ({h.ip_or_host})
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-6 pt-2 border-t border-slate-800/70 text-xs text-slate-300">
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={runForm.require_online}
              onChange={(e) => setRunForm({ ...runForm, require_online: e.target.checked })}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Require UPS Online (OL) before starting
          </label>
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={runForm.notify_on_complete}
              onChange={(e) => setRunForm({ ...runForm, notify_on_complete: e.target.checked })}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Notify on completion
          </label>
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={runForm.notify_on_fail}
              onChange={(e) => setRunForm({ ...runForm, notify_on_fail: e.target.checked })}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Notify on failure / skip
          </label>
          <span className="ml-auto text-slate-400">
            UPS:{" "}
            <strong className="font-mono text-slate-200">
              {telemetry.ups_list[0]?.name || "None"} ({telemetry.ups_list[0]?.status || "DISCONNECTED"})
            </strong>
          </span>
        </div>
      </div>

      {/* Scheduled Flow Drills */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CalendarClock className="h-5 w-5 text-indigo-400" />
            <div>
              <h3 className="text-sm font-semibold text-white">Scheduled Flow Drills</h3>
              <p className="text-xs text-slate-400">
                Automate recurring dry-run checks or maintenance-window live shutdown &amp; WoL tests (UTC times)
              </p>
            </div>
          </div>
          <button
            onClick={openCreateSchedule}
            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Schedule
          </button>
        </div>

        {schedules.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">
            No flow drill schedules configured yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-950/60 text-slate-400 uppercase text-[11px]">
                <tr>
                  <th className="px-4 py-3">Schedule</th>
                  <th className="px-4 py-3">Mode &amp; Scope</th>
                  <th className="px-4 py-3">Cadence (UTC)</th>
                  <th className="px-4 py-3">Next / Last Run</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {schedules.map((s) => (
                  <tr key={s.id} className="hover:bg-surface-800/30">
                    <td className="px-4 py-3">
                      <div className="font-semibold text-white">{s.name}</div>
                      <div className="text-slate-400">Min battery {s.min_battery_pct}%</div>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded border text-[11px] font-semibold uppercase mr-2 ${
                          s.mode === "live"
                            ? "bg-amber-500/15 text-amber-300 border-amber-500/40"
                            : "bg-indigo-500/15 text-indigo-300 border-indigo-500/40"
                        }`}
                      >
                        {s.mode === "live" ? "Live" : "Dry-Run"}
                      </span>
                      <span className="text-slate-300">
                        {s.target_scope === "all"
                          ? "All enabled"
                          : `${s.host_ids.length} selected`}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-300">
                      {s.frequency} @ {s.time_hhmm}
                    </td>
                    <td className="px-4 py-3 text-slate-400">
                      <div>
                        Next:{" "}
                        {s.enabled && s.next_run_at
                          ? new Date(s.next_run_at).toLocaleString()
                          : "Paused"}
                      </div>
                      <div>
                        Last: {s.last_run_at ? new Date(s.last_run_at).toLocaleString() : "Never"}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap space-x-1.5">
                      <button
                        onClick={() => void handleRunScheduleNow(s)}
                        disabled={Boolean(activeDrill)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-600/20 hover:bg-indigo-600/35 border border-indigo-500/30 text-indigo-200 disabled:opacity-50"
                      >
                        <Play className="h-3 w-3" />
                        Run Now
                      </button>
                      <button
                        onClick={() => openEditSchedule(s)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200"
                      >
                        <Edit3 className="h-3 w-3" />
                        Edit
                      </button>
                      <button
                        onClick={() => void handleToggleSchedule(s)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200"
                      >
                        {s.enabled ? "Disable" : "Enable"}
                      </button>
                      <button
                        onClick={() => setDeleteSchedTarget(s)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Reports KPIs & History */}
      <div className="space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
            <div className="text-xs text-slate-400">Total Drills Run</div>
            <div className="text-2xl font-bold text-white mt-1">{summary?.total ?? 0}</div>
          </div>
          <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
            <div className="text-xs text-slate-400">Pass Rate</div>
            <div className="text-2xl font-bold text-emerald-400 mt-1">
              {summary?.pass_rate_pct !== null && summary?.pass_rate_pct !== undefined
                ? `${summary.pass_rate_pct.toFixed(1)}%`
                : "—"}
            </div>
          </div>
          <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
            <div className="text-xs text-slate-400">Avg Host Shutdown</div>
            <div className="text-2xl font-bold text-indigo-300 mt-1">
              {formatDurationSec(summary?.avg_shutdown_sec)}
            </div>
          </div>
          <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
            <div className="text-xs text-slate-400">Avg Host Wake-Up</div>
            <div className="text-2xl font-bold text-sky-300 mt-1">
              {formatDurationSec(summary?.avg_wake_sec)}
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-white">Drill Reports &amp; Audit History</h3>
              <p className="text-xs text-slate-400">
                Per-host shutdown verification, Wake-on-LAN attempts, and timing metrics
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleExportCsv}
                disabled={reports.length === 0}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200 disabled:opacity-50"
              >
                <Download className="h-3.5 w-3.5" />
                Export CSV
              </button>
              <button
                onClick={() => setClearReportsOpen(true)}
                disabled={reports.length === 0}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-xs text-rose-300 disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Clear History
              </button>
            </div>
          </div>

          {reports.length === 0 ? (
            <div className="p-6 text-center text-xs text-slate-500">
              No flow drill reports recorded yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-950/60 text-slate-400 uppercase text-[11px]">
                  <tr>
                    <th className="px-4 py-3">When / Trigger</th>
                    <th className="px-4 py-3">Mode</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Hosts</th>
                    <th className="px-4 py-3">Duration</th>
                    <th className="px-4 py-3">Summary</th>
                    <th className="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {reports.map((rep) => (
                    <tr key={rep.id} className="hover:bg-surface-800/30">
                      <td className="px-4 py-3">
                        <div className="text-white font-medium">
                          {new Date(rep.started_at).toLocaleString()}
                        </div>
                        <div className="text-slate-400">
                          #{rep.id} • {rep.trigger_type}
                          {rep.schedule_name ? ` (${rep.schedule_name})` : ""}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-300">
                        {rep.mode === "live" ? "Live" : "Dry-Run"}
                      </td>
                      <td className="px-4 py-3">
                        <DrillStatusBadge status={rep.status} />
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-300">
                        {rep.hosts_passed}/{rep.hosts_total} passed
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-300">
                        {formatDurationSec(rep.duration_sec)}
                      </td>
                      <td className="px-4 py-3 text-slate-300 max-w-xs truncate">
                        {rep.summary_message}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap space-x-1.5">
                        <button
                          onClick={() => setDetailReport(rep)}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200"
                        >
                          <Eye className="h-3 w-3" />
                          Details
                        </button>
                        {rep.status !== "running" && (
                          <button
                            onClick={() => setDeleteReportTarget(rep)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Live Drill Confirmation Modal */}
      <Modal
        open={liveConfirmOpen}
        title="Confirm Live Shutdown & Restore Drill"
        onClose={() => !startingDrill && setLiveConfirmOpen(false)}
        maxWidth="max-w-lg"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-xl bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-amber-400 shrink-0">
              <ShieldAlert className="h-5 w-5" />
            </div>
            <div className="text-sm text-slate-300 space-y-2">
              <p>
                This will trigger a <strong>real graceful shutdown</strong> (<code>OB LB FSD</code>{" "}
                stream / SSH) on{" "}
                <strong>
                  {runForm.target_scope === "all"
                    ? `${enabledHosts.length} enabled host(s)`
                    : `${runForm.host_ids.length} selected host(s)`}
                </strong>
                , wait for them to go offline, pause{" "}
                <strong>{runForm.restore_delay_sec}s</strong>, and then wake them via{" "}
                <strong>Wake-on-LAN</strong>.
              </p>
              <p className="text-xs text-slate-400">
                Your physical UPS stays online the entire time, and any NUT shutdown signal is
                automatically cleared as soon as each host goes offline or if you abort.
              </p>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              type="button"
              onClick={() => setLiveConfirmOpen(false)}
              disabled={startingDrill}
              className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-xs font-medium text-slate-300"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void triggerDrill(true)}
              disabled={startingDrill}
              className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-xs font-medium text-white disabled:opacity-50"
            >
              {startingDrill ? "Starting..." : "Confirm & Start Live Drill"}
            </button>
          </div>
        </div>
      </Modal>

      {/* Schedule Create/Edit Modal */}
      <Modal
        open={schedModalOpen}
        title={editingSchedId !== null ? "Edit Flow Drill Schedule" : "New Flow Drill Schedule"}
        onClose={() => !savingSched && setSchedModalOpen(false)}
        maxWidth="max-w-xl"
      >
        <form onSubmit={(e) => void handleSaveSchedule(e)} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Schedule Name *
              </label>
              <input
                required
                value={schedForm.name}
                onChange={(e) => setSchedForm({ ...schedForm, name: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Drill Mode</label>
              <select
                value={schedForm.mode}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, mode: e.target.value as FlowDrillMode })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              >
                <option value="dry_run">Safe Dry-Run</option>
                <option value="live">Live Shutdown &amp; WoL Restore</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Frequency</label>
              <select
                value={schedForm.frequency}
                onChange={(e) =>
                  setSchedForm({
                    ...schedForm,
                    frequency: e.target.value as SelfTestFrequency,
                  })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="biweekly">Biweekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>
            {(schedForm.frequency === "weekly" || schedForm.frequency === "biweekly") && (
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Weekday</label>
                <select
                  value={schedForm.day_of_week}
                  onChange={(e) =>
                    setSchedForm({ ...schedForm, day_of_week: Number(e.target.value) })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                >
                  {WEEKDAYS.map((w) => (
                    <option key={w.value} value={w.value}>
                      {w.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {schedForm.frequency === "monthly" && (
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Day of Month
                </label>
                <input
                  type="number"
                  min={1}
                  max={31}
                  value={schedForm.day_of_month}
                  onChange={(e) =>
                    setSchedForm({ ...schedForm, day_of_month: Number(e.target.value) })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                />
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Run Time (UTC HH:MM)
              </label>
              <input
                type="time"
                value={schedForm.time_hhmm}
                onChange={(e) => setSchedForm({ ...schedForm, time_hhmm: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm font-mono text-white"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Target Scope</label>
              <select
                value={schedForm.target_scope}
                onChange={(e) =>
                  setSchedForm({
                    ...schedForm,
                    target_scope: e.target.value as FlowDrillTargetScope,
                  })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
              >
                <option value="all">All Enabled</option>
                <option value="selected">Selected</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Min Battery %</label>
              <input
                type="number"
                min={0}
                max={100}
                value={schedForm.min_battery_pct}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, min_battery_pct: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Shutdown Timeout (s)</label>
              <input
                type="number"
                min={10}
                max={1800}
                value={schedForm.shutdown_timeout_sec}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, shutdown_timeout_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Restore Delay (s)</label>
              <input
                type="number"
                min={0}
                max={300}
                value={schedForm.restore_delay_sec}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, restore_delay_sec: Number(e.target.value) })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-2 text-xs text-white"
              />
            </div>
          </div>

          {schedForm.target_scope === "selected" && (
            <div className="flex flex-wrap gap-2">
              {enabledHosts.map((h) => {
                const picked = schedForm.host_ids.includes(h.id);
                return (
                  <button
                    key={h.id}
                    type="button"
                    onClick={() =>
                      toggleHostId(schedForm.host_ids, h.id, (next) =>
                        setSchedForm({ ...schedForm, host_ids: next })
                      )
                    }
                    className={`px-2.5 py-1 rounded-lg border text-xs ${
                      picked
                        ? "bg-indigo-600/25 border-indigo-500 text-indigo-200"
                        : "bg-surface-950 border-slate-700 text-slate-400"
                    }`}
                  >
                    {h.name}
                  </button>
                );
              })}
            </div>
          )}

          <div className="flex flex-wrap gap-4 text-xs text-slate-300">
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={schedForm.require_online}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, require_online: e.target.checked })
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Require OL
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={schedForm.notify_on_complete}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, notify_on_complete: e.target.checked })
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Notify Complete
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={schedForm.notify_on_fail}
                onChange={(e) =>
                  setSchedForm({ ...schedForm, notify_on_fail: e.target.checked })
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Notify Fail
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={schedForm.enabled}
                onChange={(e) => setSchedForm({ ...schedForm, enabled: e.target.checked })}
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              Enabled
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-slate-800">
            <button
              type="button"
              onClick={() => setSchedModalOpen(false)}
              className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-xs font-medium text-slate-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={savingSched}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
            >
              {savingSched ? "Saving..." : "Save Schedule"}
            </button>
          </div>
        </form>
      </Modal>

      {/* Detailed Report Viewer Modal */}
      <Modal
        open={detailReport !== null}
        title={detailReport ? `Flow Drill Report #${detailReport.id}` : "Report Details"}
        onClose={() => setDetailReport(null)}
        maxWidth="max-w-3xl"
      >
        {detailReport && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <DrillStatusBadge status={detailReport.status} />
                <span className="font-mono text-xs text-slate-300">
                  {detailReport.mode === "live" ? "Live Full Cycle" : "Dry-Run"} • UPS:{" "}
                  {detailReport.ups_name || "N/A"} ({detailReport.ups_status_start || "—"})
                </span>
              </div>
              <span className="text-xs font-mono text-slate-400">
                Duration: {formatDurationSec(detailReport.duration_sec)}
              </span>
            </div>

            <div className="text-xs text-slate-300">{detailReport.summary_message}</div>

            {/* Stage Timeline */}
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Stage Timeline
              </h4>
              <div className="space-y-1.5">
                {detailReport.steps.map((st, idx) => (
                  <div
                    key={`${st.stage}-${idx}`}
                    className="flex items-center justify-between gap-3 p-2.5 rounded-lg bg-surface-950 border border-slate-800 text-xs"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <DrillStatusBadge status={st.status} />
                      <span className="font-mono font-semibold text-white">{st.stage}</span>
                      <span className="text-slate-400 truncate">{st.message}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Per-Host Results */}
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Per-Host Results ({detailReport.host_results.length})
              </h4>
              {detailReport.host_results.length === 0 ? (
                <p className="text-xs text-slate-500">No per-host results recorded.</p>
              ) : (
                <div className="overflow-x-auto rounded-xl border border-slate-800">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-surface-950 text-slate-400 text-[11px] uppercase">
                      <tr>
                        <th className="px-3 py-2">Host</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Shutdown</th>
                        <th className="px-3 py-2">Wake / WoL</th>
                        <th className="px-3 py-2">Notes</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/70">
                      {detailReport.host_results.map((hr) => (
                        <tr key={hr.host_id}>
                          <td className="px-3 py-2">
                            <div className="font-medium text-white">{hr.host_name}</div>
                            <div className="font-mono text-slate-400">{hr.ip_or_host}</div>
                          </td>
                          <td className="px-3 py-2">
                            <DrillStatusBadge status={hr.status} />
                          </td>
                          <td className="px-3 py-2 font-mono text-slate-300">
                            {hr.shutdown_verified_offline
                              ? `Offline (${formatDurationSec(hr.shutdown_duration_sec)})`
                              : hr.shutdown_triggered
                              ? "Triggered"
                              : "—"}
                          </td>
                          <td className="px-3 py-2 font-mono text-slate-300">
                            {hr.wake_verified_online
                              ? `Online (${formatDurationSec(hr.wake_duration_sec)}, ${hr.wake_attempts}x)`
                              : hr.wake_triggered
                              ? `Attempted (${hr.wake_attempts}x)`
                              : "—"}
                          </td>
                          <td className="px-3 py-2 text-slate-300">{hr.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={abortTarget !== null}
        title="Abort Running Flow Drill"
        message="Stop the running flow drill immediately and clear all simulated NUT shutdown signals?"
        confirmLabel="Abort Drill"
        variant="danger"
        busy={aborting}
        onConfirm={confirmAbort}
        onCancel={() => setAbortTarget(null)}
      />

      <ConfirmDialog
        open={deleteSchedTarget !== null}
        title="Delete Flow Drill Schedule"
        message={
          deleteSchedTarget
            ? `Delete scheduled drill '${deleteSchedTarget.name}'?`
            : ""
        }
        confirmLabel="Delete"
        busy={deletingSched}
        onConfirm={confirmDeleteSchedule}
        onCancel={() => setDeleteSchedTarget(null)}
      />

      <ConfirmDialog
        open={deleteReportTarget !== null}
        title="Delete Flow Drill Report"
        message={
          deleteReportTarget ? `Delete Flow Drill Report #${deleteReportTarget.id}?` : ""
        }
        confirmLabel="Delete"
        onConfirm={confirmDeleteReport}
        onCancel={() => setDeleteReportTarget(null)}
      />

      <ConfirmDialog
        open={clearReportsOpen}
        title="Clear All Flow Drill Reports"
        message="Delete all completed flow drill reports? Running drills (if any) will be kept."
        confirmLabel="Clear History"
        busy={clearingReports}
        onConfirm={confirmClearReports}
        onCancel={() => setClearReportsOpen(false)}
      />
    </div>
  );
}
