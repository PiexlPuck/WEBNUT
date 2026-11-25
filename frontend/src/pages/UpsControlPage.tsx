import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BatteryCharging,
  CalendarClock,
  CheckCircle2,
  Download,
  Edit3,
  Gauge,
  Play,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sliders,
  Square,
  Trash2,
  Volume2,
  VolumeX,
  Wrench,
  Zap,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { Modal } from "../components/Modal";
import { UpsStatusBadge } from "../components/StatusBadge";
import type {
  SelfTestFrequency,
  SelfTestReportRecord,
  SelfTestReportsResponse,
  SelfTestSchedulePayload,
  SelfTestScheduleRecord,
  TelemetrySnapshot,
  UpsCapabilitiesResponse,
  UpsCommandInfo,
  UpsWritableVarInfo,
} from "../types";

interface UpsControlPageProps {
  telemetry: TelemetrySnapshot;
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

const DEFAULT_TEST_COMMANDS = [
  "test.battery.start.quick",
  "test.battery.start.deep",
  "test.battery.start",
  "calibrate.start",
];

function formatDurationSec(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (hrs > 0) return `${hrs}h ${mins}m`;
  if (mins > 0) return `${mins}m ${rem}s`;
  return `${rem}s`;
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function ReportStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    passed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    failed: "bg-rose-500/15 text-rose-300 border-rose-500/40",
    warning: "bg-amber-500/15 text-amber-300 border-amber-500/40",
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

function CategoryBadge({ category }: { category: UpsCommandInfo["category"] }) {
  const map: Record<UpsCommandInfo["category"], string> = {
    selftest: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
    beeper: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
    calibration: "bg-violet-500/15 text-violet-300 border-violet-500/30",
    power: "bg-rose-500/15 text-rose-300 border-rose-500/30",
    other: "bg-slate-800 text-slate-300 border-slate-700",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] font-medium ${map[category]}`}>
      {category}
    </span>
  );
}

export function UpsControlPage({ telemetry, onRefresh }: UpsControlPageProps) {
  const upsNames = useMemo(
    () => telemetry.ups_list.map((u) => u.name),
    [telemetry.ups_list]
  );
  const [selectedUps, setSelectedUps] = useState<string>("");

  useEffect(() => {
    if (!selectedUps && upsNames.length > 0) {
      setSelectedUps(upsNames[0]);
    } else if (selectedUps && upsNames.length > 0 && !upsNames.includes(selectedUps)) {
      setSelectedUps(upsNames[0]);
    }
  }, [selectedUps, upsNames]);

  const currentUps = useMemo(
    () => telemetry.ups_list.find((u) => u.name === selectedUps) ?? telemetry.ups_list[0],
    [telemetry.ups_list, selectedUps]
  );

  const [capabilities, setCapabilities] = useState<UpsCapabilitiesResponse | null>(null);
  const [loadingCaps, setLoadingCaps] = useState(false);
  const [capsError, setCapsError] = useState<string | null>(null);

  const [varDrafts, setVarDrafts] = useState<Record<string, string>>({});
  const [savingVar, setSavingVar] = useState<string | null>(null);
  const [runningCmd, setRunningCmd] = useState<string | null>(null);

  const [dangerModalCmd, setDangerModalCmd] = useState<UpsCommandInfo | null>(null);

  const [schedules, setSchedules] = useState<SelfTestScheduleRecord[]>([]);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [editingScheduleId, setEditingScheduleId] = useState<number | null>(null);
  const [scheduleForm, setScheduleForm] = useState<SelfTestSchedulePayload>({
    ups_name: "",
    test_command: "test.battery.start.quick",
    frequency: "weekly",
    day_of_week: 0,
    day_of_month: 1,
    time_hhmm: "03:00",
    min_battery_pct: 80,
    require_online: true,
    notify_on_complete: true,
    notify_on_fail: true,
    enabled: true,
  });
  const [savingSchedule, setSavingSchedule] = useState(false);

  const [reportsData, setReportsData] = useState<SelfTestReportsResponse>({
    summary: {
      total: 0,
      passed: 0,
      failed: 0,
      warning: 0,
      skipped: 0,
      running: 0,
      pass_rate_pct: null,
      last_passed_at: null,
      avg_battery_drop: null,
    },
    reports: [],
  });
  const [reportUpsFilter, setReportUpsFilter] = useState<string>("all");
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const loadCapabilities = useCallback(async (upsName: string) => {
    if (!upsName) {
      setCapabilities(null);
      return;
    }
    setLoadingCaps(true);
    setCapsError(null);
    try {
      const caps = await api.getUpsCapabilities(upsName);
      setCapabilities(caps);
      const nextDrafts: Record<string, string> = {};
      for (const v of caps.writable_vars) {
        nextDrafts[v.name] = v.value;
      }
      setVarDrafts(nextDrafts);
    } catch (err) {
      setCapsError(
        err instanceof ApiError ? err.message : "Failed to load UPS hardware capabilities"
      );
      setCapabilities(null);
    } finally {
      setLoadingCaps(false);
    }
  }, []);

  const loadSchedules = useCallback(async () => {
    try {
      const items = await api.getSelfTestSchedules();
      setSchedules(items);
    } catch {
      // ignore transient errors
    }
  }, []);

  const loadReports = useCallback(async () => {
    try {
      const upsParam = reportUpsFilter === "all" ? undefined : reportUpsFilter;
      const res = await api.getSelfTestReports(upsParam, 100);
      setReportsData(res);
    } catch {
      // ignore transient errors
    }
  }, [reportUpsFilter]);

  useEffect(() => {
    void loadCapabilities(selectedUps);
  }, [selectedUps, loadCapabilities]);

  useEffect(() => {
    void loadSchedules();
  }, [loadSchedules]);

  useEffect(() => {
    void loadReports();
  }, [loadReports]);

  // Poll reports every 4s while any report is still running
  useEffect(() => {
    const hasRunning = reportsData.reports.some((r) => r.status === "running");
    if (!hasRunning) return;
    const timer = window.setInterval(() => {
      void loadReports();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [reportsData.reports, loadReports]);

  const handleRefreshAll = async () => {
    await Promise.all([
      onRefresh(),
      loadCapabilities(selectedUps),
      loadSchedules(),
      loadReports(),
    ]);
  };

  const supportedCmdMap = useMemo(() => {
    const map = new Map<string, UpsCommandInfo>();
    for (const c of capabilities?.commands ?? []) {
      map.set(c.name, c);
    }
    return map;
  }, [capabilities]);

  const safeCommands = useMemo(
    () => (capabilities?.commands ?? []).filter((c) => c.risk !== "dangerous"),
    [capabilities]
  );
  const dangerousCommands = useMemo(
    () => (capabilities?.commands ?? []).filter((c) => c.risk === "dangerous"),
    [capabilities]
  );

  const beeperCommand = useMemo(() => {
    if (supportedCmdMap.has("beeper.toggle")) return "beeper.toggle";
    if (supportedCmdMap.has("beeper.mute")) return "beeper.mute";
    if (supportedCmdMap.has("beeper.disable")) return "beeper.disable";
    if (supportedCmdMap.has("beeper.enable")) return "beeper.enable";
    return "beeper.toggle";
  }, [supportedCmdMap]);

  const executeCommand = async (cmdName: string, confirmDangerous = false) => {
    if (!selectedUps) return;
    setRunningCmd(cmdName);
    setFeedback(null);
    try {
      const res = await api.runUpsCommand(selectedUps, cmdName, confirmDangerous);
      setFeedback({ ok: res.ok, msg: res.message || `${cmdName}: ${res.ok ? "OK" : "Failed"}` });
      await Promise.all([onRefresh(), loadReports()]);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Command '${cmdName}' failed`,
      });
    } finally {
      setRunningCmd(null);
    }
  };

  const handleCommandClick = (cmd: UpsCommandInfo) => {
    if (cmd.risk === "dangerous") {
      setDangerModalCmd(cmd);
      return;
    }
    void executeCommand(cmd.name, false);
  };

  const handleApplyVar = async (v: UpsWritableVarInfo) => {
    if (!selectedUps) return;
    const val = varDrafts[v.name] ?? v.value;
    setSavingVar(v.name);
    setFeedback(null);
    try {
      const res = await api.setUpsVar(selectedUps, v.name, val);
      setFeedback({ ok: res.ok, msg: `${v.name}: ${res.message}` });
      await Promise.all([onRefresh(), loadCapabilities(selectedUps)]);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to update ${v.name}`,
      });
    } finally {
      setSavingVar(null);
    }
  };

  const openCreateSchedule = () => {
    const firstTestCmd =
      (capabilities?.commands ?? []).find((c) => c.category === "selftest")?.name ??
      "test.battery.start.quick";
    setEditingScheduleId(null);
    setScheduleForm({
      ups_name: selectedUps || upsNames[0] || "",
      test_command: firstTestCmd,
      frequency: "weekly",
      day_of_week: 0,
      day_of_month: 1,
      time_hhmm: "03:00",
      min_battery_pct: 80,
      require_online: true,
      notify_on_complete: true,
      notify_on_fail: true,
      enabled: true,
    });
    setScheduleModalOpen(true);
  };

  const openEditSchedule = (sched: SelfTestScheduleRecord) => {
    setEditingScheduleId(sched.id);
    setScheduleForm({
      ups_name: sched.ups_name,
      test_command: sched.test_command,
      frequency: sched.frequency,
      day_of_week: sched.day_of_week,
      day_of_month: sched.day_of_month,
      time_hhmm: sched.time_hhmm,
      min_battery_pct: sched.min_battery_pct,
      require_online: sched.require_online,
      notify_on_complete: sched.notify_on_complete,
      notify_on_fail: sched.notify_on_fail,
      enabled: sched.enabled,
    });
    setScheduleModalOpen(true);
  };

  const handleSaveSchedule = async () => {
    setSavingSchedule(true);
    setFeedback(null);
    try {
      if (editingScheduleId === null) {
        await api.createSelfTestSchedule(scheduleForm);
        setFeedback({ ok: true, msg: "Created self-test schedule." });
      } else {
        await api.updateSelfTestSchedule(editingScheduleId, scheduleForm);
        setFeedback({ ok: true, msg: "Updated self-test schedule." });
      }
      setScheduleModalOpen(false);
      await loadSchedules();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save schedule",
      });
    } finally {
      setSavingSchedule(false);
    }
  };

  const handleToggleSchedule = async (sched: SelfTestScheduleRecord) => {
    setFeedback(null);
    try {
      await api.updateSelfTestSchedule(sched.id, {
        ups_name: sched.ups_name,
        test_command: sched.test_command,
        frequency: sched.frequency,
        day_of_week: sched.day_of_week,
        day_of_month: sched.day_of_month,
        time_hhmm: sched.time_hhmm,
        min_battery_pct: sched.min_battery_pct,
        require_online: sched.require_online,
        notify_on_complete: sched.notify_on_complete,
        notify_on_fail: sched.notify_on_fail,
        enabled: !sched.enabled,
      });
      await loadSchedules();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to toggle schedule",
      });
    }
  };

  const handleDeleteSchedule = async (id: number) => {
    try {
      await api.deleteSelfTestSchedule(id);
      await loadSchedules();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to delete schedule",
      });
    }
  };

  const handleRunScheduleNow = async (id: number) => {
    setFeedback(null);
    try {
      const rep = await api.runSelfTestScheduleNow(id);
      setFeedback({
        ok: rep.status !== "failed" && rep.status !== "skipped",
        msg: rep.message || `Self-test triggered (${rep.status})`,
      });
      await Promise.all([loadSchedules(), loadReports()]);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to run self-test",
      });
    }
  };

  const handleDeleteReport = async (id: number) => {
    try {
      await api.deleteSelfTestReport(id);
      await loadReports();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to delete report",
      });
    }
  };

  const handleClearReports = async () => {
    try {
      const upsParam = reportUpsFilter === "all" ? undefined : reportUpsFilter;
      await api.clearSelfTestReports(upsParam);
      await loadReports();
      setFeedback({ ok: true, msg: "Cleared self-test reports." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to clear reports",
      });
    }
  };

  const handleExportCsv = () => {
    const headers = [
      "id",
      "ups_name",
      "test_command",
      "trigger_type",
      "status",
      "started_at",
      "completed_at",
      "duration_sec",
      "battery_before",
      "battery_after",
      "runtime_before",
      "runtime_after",
      "load_pct",
      "ups_test_result",
      "message",
    ];
    const escapeCell = (val: unknown) => {
      const s = val === null || val === undefined ? "" : String(val);
      return `"${s.replace(/"/g, '""')}"`;
    };
    const lines = [
      headers.join(","),
      ...reportsData.reports.map((r: SelfTestReportRecord) =>
        headers.map((h) => escapeCell((r as unknown as Record<string, unknown>)[h])).join(",")
      ),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `webnut-selftest-reports-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const quickButtons: {
    label: string;
    cmd: string;
    desc: string;
    icon: typeof Play;
  }[] = [
    {
      label: "Quick Battery Test",
      cmd: "test.battery.start.quick",
      desc: "Short battery health check",
      icon: BatteryCharging,
    },
    {
      label: "Deep Battery Test",
      cmd: "test.battery.start.deep",
      desc: "Full discharge calibration test",
      icon: Activity,
    },
    {
      label: "Stop Test",
      cmd: "test.battery.stop",
      desc: "Abort running battery test",
      icon: Square,
    },
    {
      label:
        beeperCommand === "beeper.mute"
          ? "Mute Beeper"
          : beeperCommand === "beeper.disable"
          ? "Disable Beeper"
          : beeperCommand === "beeper.enable"
          ? "Enable Beeper"
          : "Toggle Beeper",
      cmd: beeperCommand,
      desc: "Control UPS audible alarm",
      icon:
        currentUps?.raw_vars["ups.beeper.status"] === "muted" ||
        currentUps?.raw_vars["ups.beeper.status"] === "disabled"
          ? VolumeX
          : Volume2,
    },
    {
      label: "Panel Test",
      cmd: "test.panel.start",
      desc: "Test front-panel LEDs & buzzer",
      icon: Zap,
    },
  ];

  const scheduleTestOptions = useMemo(() => {
    const hwTests = (capabilities?.commands ?? [])
      .filter((c) => c.category === "selftest" || c.category === "calibration")
      .map((c) => c.name);
    const combined = Array.from(new Set([...hwTests, ...DEFAULT_TEST_COMMANDS]));
    return combined;
  }, [capabilities]);

  const lastTestResult = currentUps?.raw_vars["ups.test.result"] ?? "—";
  const beeperStatus = currentUps?.raw_vars["ups.beeper.status"] ?? "—";

  return (
    <div className="space-y-6">
      {/* Header + UPS Selector */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white flex items-center gap-2.5">
            <Sliders className="h-6 w-6 text-indigo-400" />
            UPS Control &amp; Self-Test Scheduler
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            Execute NUT instant commands, tune writable UPS settings, and automate battery self-tests.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <select
            value={selectedUps}
            onChange={(e) => setSelectedUps(e.target.value)}
            disabled={upsNames.length === 0}
            className="bg-surface-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500"
          >
            {upsNames.length === 0 ? (
              <option value="">No UPS Connected</option>
            ) : (
              upsNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))
            )}
          </select>
          <button
            onClick={() => void handleRefreshAll()}
            disabled={loadingCaps}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm text-slate-200 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loadingCaps ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`p-3.5 rounded-xl border text-sm flex items-center gap-2.5 ${
            feedback.ok
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
              : "bg-rose-500/10 border-rose-500/30 text-rose-300"
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

      {/* Credential Warning Banner */}
      {capabilities && !capabilities.credentials_configured && (
        <div className="p-4 rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-200 text-sm flex items-start gap-3">
          <ShieldAlert className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-amber-100">
              NUT Control Credentials Not Configured
            </div>
            <p className="text-xs text-amber-200/80 mt-0.5">
              Reading capabilities works unauthenticated, but running instant commands or modifying variables requires a NUT user with{" "}
              <code className="font-mono bg-black/30 px-1 rounded">instcmds = ALL</code> and{" "}
              <code className="font-mono bg-black/30 px-1 rounded">actions = SET</code> in{" "}
              <span className="font-semibold">UPS Setup &rarr; upsd.users</span> (or External NUT credentials).
            </p>
          </div>
        </div>
      )}

      {/* Live Status Strip */}
      <div className="bg-surface-900 border border-slate-800 rounded-2xl p-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 items-center">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Status
            </div>
            <div className="mt-1">
              <UpsStatusBadge
                status={currentUps?.status ?? (telemetry.connected ? "NO UPS" : "DISCONNECTED")}
              />
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Battery
            </div>
            <div className="mt-1 text-lg font-bold text-white font-mono">
              {currentUps?.battery_charge !== null && currentUps?.battery_charge !== undefined
                ? `${currentUps.battery_charge.toFixed(0)}%`
                : "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Runtime
            </div>
            <div className="mt-1 text-lg font-bold text-white font-mono">
              {formatDurationSec(currentUps?.battery_runtime)}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Load
            </div>
            <div className="mt-1 text-lg font-bold text-white font-mono">
              {currentUps?.ups_load !== null && currentUps?.ups_load !== undefined
                ? `${currentUps.ups_load.toFixed(0)}%`
                : "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Last Test Result
            </div>
            <div className="mt-1 text-xs font-mono text-slate-200 truncate" title={lastTestResult}>
              {lastTestResult}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Beeper Status
            </div>
            <div className="mt-1 text-xs font-mono text-slate-200 uppercase">
              {beeperStatus}
            </div>
          </div>
        </div>
      </div>

      {/* Quick Controls */}
      <div className="bg-surface-900 border border-slate-800 rounded-2xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Gauge className="h-4 w-4 text-indigo-400" />
            Quick Controls
          </h3>
          {capsError && (
            <span className="text-xs text-rose-400">{capsError}</span>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {quickButtons.map((btn) => {
            const Icon = btn.icon;
            const supported = supportedCmdMap.has(btn.cmd);
            const busy = runningCmd === btn.cmd;
            return (
              <button
                key={btn.cmd}
                onClick={() => void executeCommand(btn.cmd, false)}
                disabled={!supported || !selectedUps || busy}
                title={supported ? btn.cmd : `${btn.cmd} is unsupported by hardware`}
                className={`p-3.5 rounded-xl border text-left transition-colors flex flex-col justify-between gap-2 ${
                  supported
                    ? "bg-surface-800/80 hover:bg-surface-800 border-slate-700 hover:border-indigo-500/50 text-white"
                    : "bg-surface-950/50 border-slate-800/60 text-slate-500 cursor-not-allowed opacity-70"
                }`}
              >
                <div className="flex items-center justify-between">
                  <Icon className={`h-4 w-4 ${supported ? "text-indigo-400" : "text-slate-600"}`} />
                  <span className="text-[10px] font-mono text-slate-500">
                    {busy ? "Running..." : supported ? "Ready" : "Unsupported"}
                  </span>
                </div>
                <div>
                  <div className="text-sm font-semibold">{btn.label}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {supported ? btn.desc : `Unsupported (${btn.cmd})`}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* All Supported Hardware Commands */}
      <div className="bg-surface-900 border border-slate-800 rounded-2xl p-5 space-y-5">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Wrench className="h-4 w-4 text-indigo-400" />
            All Supported Hardware Commands
          </h3>
          <span className="text-xs text-slate-400">
            {capabilities?.commands.length ?? 0} command(s) reported by driver
          </span>
        </div>

        {!capabilities || capabilities.commands.length === 0 ? (
          <p className="text-sm text-slate-400">
            {loadingCaps
              ? "Querying LIST CMD from upsd..."
              : "No instant commands reported for this UPS."}
          </p>
        ) : (
          <div className="space-y-5">
            {safeCommands.length > 0 && (
              <div className="space-y-2.5">
                <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
                  Safe Commands ({safeCommands.length})
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                  {safeCommands.map((cmd) => (
                    <div
                      key={cmd.name}
                      className="flex items-center justify-between gap-3 p-3 rounded-xl bg-surface-800/60 border border-slate-800"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <code className="text-xs font-mono font-semibold text-white truncate">
                            {cmd.name}
                          </code>
                          <CategoryBadge category={cmd.category} />
                        </div>
                        <p className="text-xs text-slate-400 mt-1 truncate">
                          {cmd.description || "No driver description"}
                        </p>
                      </div>
                      <button
                        onClick={() => handleCommandClick(cmd)}
                        disabled={runningCmd === cmd.name}
                        className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                      >
                        <Play className="h-3 w-3" />
                        {runningCmd === cmd.name ? "Running..." : "Run"}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {dangerousCommands.length > 0 && (
              <div className="space-y-2.5 pt-2 border-t border-rose-500/20">
                <div className="text-xs uppercase tracking-wider text-rose-400 font-semibold flex items-center gap-1.5">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  Danger Zone ({dangerousCommands.length}) — Power / Load / Reset Commands
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                  {dangerousCommands.map((cmd) => (
                    <div
                      key={cmd.name}
                      className="flex items-center justify-between gap-3 p-3 rounded-xl bg-rose-950/20 border border-rose-500/30"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <code className="text-xs font-mono font-semibold text-rose-200 truncate">
                            {cmd.name}
                          </code>
                          <CategoryBadge category={cmd.category} />
                        </div>
                        <p className="text-xs text-rose-200/70 mt-1 truncate">
                          {cmd.description || "Potentially disruptive power command"}
                        </p>
                      </div>
                      <button
                        onClick={() => handleCommandClick(cmd)}
                        disabled={runningCmd === cmd.name}
                        className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-xs font-medium text-white disabled:opacity-50"
                      >
                        <AlertTriangle className="h-3 w-3" />
                        Run
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Writable UPS Settings (SET VAR) */}
      <div className="bg-surface-900 border border-slate-800 rounded-2xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-white">
            Writable UPS Settings (<code className="font-mono text-indigo-300 text-sm">SET VAR</code>)
          </h3>
          <span className="text-xs text-slate-400">
            {capabilities?.writable_vars.length ?? 0} writable variable(s)
          </span>
        </div>

        {!capabilities || capabilities.writable_vars.length === 0 ? (
          <p className="text-sm text-slate-400">
            {loadingCaps
              ? "Querying LIST RW from upsd..."
              : "No writable (RW) variables reported by this UPS driver."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs uppercase text-slate-400">
                  <th className="py-2.5 pr-4">Variable</th>
                  <th className="py-2.5 pr-4">Description</th>
                  <th className="py-2.5 pr-4">Constraints</th>
                  <th className="py-2.5 pr-4">Value</th>
                  <th className="py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {capabilities.writable_vars.map((v) => {
                  const draftVal = varDrafts[v.name] ?? v.value;
                  const isNumericRange =
                    v.enum_values.length === 0 &&
                    (v.range_min !== null || v.range_max !== null);
                  return (
                    <tr key={v.name}>
                      <td className="py-3 pr-4 font-mono text-xs text-indigo-300 whitespace-nowrap">
                        {v.name}
                      </td>
                      <td className="py-3 pr-4 text-xs text-slate-300">
                        {v.description || "—"}
                      </td>
                      <td className="py-3 pr-4 text-xs font-mono text-slate-400 whitespace-nowrap">
                        {v.enum_values.length > 0
                          ? `ENUM (${v.enum_values.length})`
                          : v.range_min !== null || v.range_max !== null
                          ? `${v.range_min ?? "?"} .. ${v.range_max ?? "?"}`
                          : v.var_type || "STRING"}
                      </td>
                      <td className="py-3 pr-4">
                        {v.enum_values.length > 0 ? (
                          <select
                            value={draftVal}
                            onChange={(e) =>
                              setVarDrafts((prev) => ({ ...prev, [v.name]: e.target.value }))
                            }
                            className="bg-surface-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
                          >
                            {!v.enum_values.includes(draftVal) && draftVal && (
                              <option value={draftVal}>{draftVal}</option>
                            )}
                            {v.enum_values.map((ev) => (
                              <option key={ev} value={ev}>
                                {ev}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type={isNumericRange ? "number" : "text"}
                            min={v.range_min ?? undefined}
                            max={v.range_max ?? undefined}
                            value={draftVal}
                            onChange={(e) =>
                              setVarDrafts((prev) => ({ ...prev, [v.name]: e.target.value }))
                            }
                            className="w-40 bg-surface-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-indigo-500"
                          />
                        )}
                      </td>
                      <td className="py-3 text-right">
                        <button
                          onClick={() => void handleApplyVar(v)}
                          disabled={savingVar === v.name}
                          className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                        >
                          {savingVar === v.name ? "Applying..." : "Apply"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Self-Test Scheduler */}
      <div className="bg-surface-900 border border-slate-800 rounded-2xl p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-white flex items-center gap-2">
              <CalendarClock className="h-4 w-4 text-indigo-400" />
              Self-Test Scheduler
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Schedule recurring battery self-tests with online &amp; minimum battery safety guards (UTC times).
            </p>
          </div>
          <button
            onClick={openCreateSchedule}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold text-white"
          >
            <Plus className="h-4 w-4" />
            Add Schedule
          </button>
        </div>

        {schedules.length === 0 ? (
          <p className="text-sm text-slate-400">No self-test schedules configured yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs uppercase text-slate-400">
                  <th className="py-2.5 pr-3">UPS</th>
                  <th className="py-2.5 pr-3">Command</th>
                  <th className="py-2.5 pr-3">Cadence</th>
                  <th className="py-2.5 pr-3">Safety Guards</th>
                  <th className="py-2.5 pr-3">Next Run</th>
                  <th className="py-2.5 pr-3">Status</th>
                  <th className="py-2.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {schedules.map((s) => {
                  const dayLabel =
                    s.frequency === "weekly" || s.frequency === "biweekly"
                      ? ` on ${WEEKDAYS.find((d) => d.value === s.day_of_week)?.label ?? "Mon"}`
                      : s.frequency === "monthly"
                      ? ` on day ${s.day_of_month}`
                      : "";
                  return (
                    <tr key={s.id}>
                      <td className="py-3 pr-3 font-mono text-xs text-white">{s.ups_name}</td>
                      <td className="py-3 pr-3 font-mono text-xs text-indigo-300">{s.test_command}</td>
                      <td className="py-3 pr-3 text-xs text-slate-300 capitalize">
                        {s.frequency}
                        {dayLabel} at <span className="font-mono">{s.time_hhmm} UTC</span>
                      </td>
                      <td className="py-3 pr-3 text-xs text-slate-400">
                        {s.require_online ? "OL required" : "Any status"} · Min {s.min_battery_pct}%
                      </td>
                      <td className="py-3 pr-3 text-xs text-slate-300 whitespace-nowrap">
                        {formatDateTime(s.next_run_at)}
                      </td>
                      <td className="py-3 pr-3">
                        <button
                          onClick={() => void handleToggleSchedule(s)}
                          className={`px-2.5 py-0.5 rounded-full border text-[11px] font-semibold ${
                            s.enabled
                              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                              : "bg-slate-800 text-slate-400 border-slate-700"
                          }`}
                        >
                          {s.enabled ? "Enabled" : "Disabled"}
                        </button>
                      </td>
                      <td className="py-3 text-right whitespace-nowrap space-x-1.5">
                        <button
                          onClick={() => void handleRunScheduleNow(s.id)}
                          title="Run Now"
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
                        >
                          <Play className="h-3 w-3 text-emerald-400" />
                          Run Now
                        </button>
                        <button
                          onClick={() => openEditSchedule(s)}
                          title="Edit"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-300"
                        >
                          <Edit3 className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => void handleDeleteSchedule(s.id)}
                          title="Delete"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-rose-500/20 border border-slate-700 text-rose-400"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Self-Test Reports & Analytics */}
      <div className="bg-surface-900 border border-slate-800 rounded-2xl p-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-white">
              Self-Test Reports &amp; Analytics
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Historical battery test outcomes, before/after capacity deltas, and hardware diagnostics.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={reportUpsFilter}
              onChange={(e) => setReportUpsFilter(e.target.value)}
              className="bg-surface-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white"
            >
              <option value="all">All UPS Units</option>
              {upsNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <button
              onClick={handleExportCsv}
              disabled={reportsData.reports.length === 0}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200 disabled:opacity-50"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </button>
            <button
              onClick={() => void handleClearReports()}
              disabled={reportsData.reports.length === 0}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-xs text-rose-300 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear
            </button>
          </div>
        </div>

        {/* KPI Strip */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="bg-surface-950/70 border border-slate-800 rounded-xl p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Total Tests
            </div>
            <div className="text-2xl font-bold text-white mt-1 font-mono">
              {reportsData.summary.total}
            </div>
          </div>
          <div className="bg-surface-950/70 border border-slate-800 rounded-xl p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Pass Rate
            </div>
            <div className="text-2xl font-bold text-emerald-400 mt-1 font-mono">
              {reportsData.summary.pass_rate_pct !== null
                ? `${reportsData.summary.pass_rate_pct.toFixed(1)}%`
                : "—"}
            </div>
          </div>
          <div className="bg-surface-950/70 border border-slate-800 rounded-xl p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Last Passed
            </div>
            <div className="text-xs font-medium text-slate-200 mt-2">
              {formatDateTime(reportsData.summary.last_passed_at)}
            </div>
          </div>
          <div className="bg-surface-950/70 border border-slate-800 rounded-xl p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              Avg Battery Drop
            </div>
            <div className="text-2xl font-bold text-indigo-300 mt-1 font-mono">
              {reportsData.summary.avg_battery_drop !== null
                ? `${reportsData.summary.avg_battery_drop.toFixed(1)}%`
                : "—"}
            </div>
          </div>
        </div>

        {reportsData.reports.length === 0 ? (
          <p className="text-sm text-slate-400">No self-test reports recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs uppercase text-slate-400">
                  <th className="py-2.5 pr-3">Started</th>
                  <th className="py-2.5 pr-3">UPS</th>
                  <th className="py-2.5 pr-3">Command</th>
                  <th className="py-2.5 pr-3">Trigger</th>
                  <th className="py-2.5 pr-3">Status</th>
                  <th className="py-2.5 pr-3">Battery</th>
                  <th className="py-2.5 pr-3">Runtime</th>
                  <th className="py-2.5 pr-3">Duration</th>
                  <th className="py-2.5 pr-3">Hardware Result</th>
                  <th className="py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {reportsData.reports.map((r) => (
                  <tr key={r.id}>
                    <td className="py-3 pr-3 text-xs text-slate-300 whitespace-nowrap">
                      {formatDateTime(r.started_at)}
                    </td>
                    <td className="py-3 pr-3 font-mono text-xs text-white">{r.ups_name}</td>
                    <td className="py-3 pr-3 font-mono text-xs text-indigo-300">{r.test_command}</td>
                    <td className="py-3 pr-3 text-xs text-slate-400 capitalize">{r.trigger_type}</td>
                    <td className="py-3 pr-3">
                      <ReportStatusBadge status={r.status} />
                    </td>
                    <td className="py-3 pr-3 font-mono text-xs text-slate-300 whitespace-nowrap">
                      {r.battery_before !== null ? `${r.battery_before.toFixed(0)}%` : "—"} &rarr;{" "}
                      {r.battery_after !== null ? `${r.battery_after.toFixed(0)}%` : "—"}
                    </td>
                    <td className="py-3 pr-3 font-mono text-xs text-slate-300 whitespace-nowrap">
                      {formatDurationSec(r.runtime_before)} &rarr; {formatDurationSec(r.runtime_after)}
                    </td>
                    <td className="py-3 pr-3 font-mono text-xs text-slate-300 whitespace-nowrap">
                      {formatDurationSec(r.duration_sec)}
                    </td>
                    <td className="py-3 pr-3 text-xs text-slate-300 max-w-xs truncate" title={r.message || r.ups_test_result}>
                      {r.ups_test_result || r.message || "—"}
                    </td>
                    <td className="py-3 text-right">
                      <button
                        onClick={() => void handleDeleteReport(r.id)}
                        title="Delete report"
                        className="p-1.5 rounded-lg bg-surface-800 hover:bg-rose-500/20 border border-slate-700 text-rose-400"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Dangerous Command Confirmation Modal */}
      <Modal
        open={dangerModalCmd !== null}
        title="Confirm Dangerous UPS Command"
        onClose={() => setDangerModalCmd(null)}
        maxWidth="max-w-lg"
      >
        {dangerModalCmd && (
          <div className="space-y-4">
            <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-200 text-sm flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-rose-400 shrink-0 mt-0.5" />
              <div>
                <div className="font-semibold">
                  Execute <code className="font-mono">{dangerModalCmd.name}</code> on{" "}
                  <code className="font-mono">{selectedUps}</code>?
                </div>
                <p className="text-xs text-rose-200/80 mt-1">
                  {dangerModalCmd.description ||
                    "This command may immediately cut outlet power, initiate UPS shutdown, reboot, or reset settings."}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2.5">
              <button
                onClick={() => setDangerModalCmd(null)}
                className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm text-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  const cmd = dangerModalCmd.name;
                  setDangerModalCmd(null);
                  void executeCommand(cmd, true);
                }}
                className="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-sm font-semibold text-white"
              >
                Confirm &amp; Execute
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Add / Edit Self-Test Schedule Modal */}
      <Modal
        open={scheduleModalOpen}
        title={editingScheduleId === null ? "Add Self-Test Schedule" : "Edit Self-Test Schedule"}
        onClose={() => setScheduleModalOpen(false)}
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">UPS Unit</label>
              <select
                value={scheduleForm.ups_name}
                onChange={(e) => setScheduleForm((f) => ({ ...f, ups_name: e.target.value }))}
                className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white"
              >
                {upsNames.length === 0 && scheduleForm.ups_name && (
                  <option value={scheduleForm.ups_name}>{scheduleForm.ups_name}</option>
                )}
                {upsNames.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Test Command</label>
              <select
                value={scheduleForm.test_command}
                onChange={(e) => setScheduleForm((f) => ({ ...f, test_command: e.target.value }))}
                className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white font-mono"
              >
                {scheduleTestOptions.map((cmd) => (
                  <option key={cmd} value={cmd}>
                    {cmd}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Frequency</label>
              <select
                value={scheduleForm.frequency}
                onChange={(e) =>
                  setScheduleForm((f) => ({
                    ...f,
                    frequency: e.target.value as SelfTestFrequency,
                  }))
                }
                className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="biweekly">Biweekly (Every 2 Weeks)</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>

            {(scheduleForm.frequency === "weekly" || scheduleForm.frequency === "biweekly") && (
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Day of Week</label>
                <select
                  value={scheduleForm.day_of_week}
                  onChange={(e) =>
                    setScheduleForm((f) => ({ ...f, day_of_week: Number(e.target.value) }))
                  }
                  className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white"
                >
                  {WEEKDAYS.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {scheduleForm.frequency === "monthly" && (
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Day of Month</label>
                <input
                  type="number"
                  min={1}
                  max={31}
                  value={scheduleForm.day_of_month}
                  onChange={(e) =>
                    setScheduleForm((f) => ({ ...f, day_of_month: Number(e.target.value) }))
                  }
                  className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white"
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Time (UTC HH:MM)</label>
              <input
                type="time"
                value={scheduleForm.time_hhmm}
                onChange={(e) => setScheduleForm((f) => ({ ...f, time_hhmm: e.target.value }))}
                className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white font-mono"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Minimum Battery Charge (%)
              </label>
              <input
                type="number"
                min={0}
                max={100}
                value={scheduleForm.min_battery_pct}
                onChange={(e) =>
                  setScheduleForm((f) => ({ ...f, min_battery_pct: Number(e.target.value) }))
                }
                className="w-full bg-surface-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white font-mono"
              />
            </div>
          </div>

          <div className="space-y-2 pt-2 border-t border-slate-800 text-sm text-slate-200">
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={scheduleForm.require_online}
                onChange={(e) =>
                  setScheduleForm((f) => ({ ...f, require_online: e.target.checked }))
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              <span>Require UPS to be Online (OL) before running</span>
            </label>
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={scheduleForm.notify_on_complete}
                onChange={(e) =>
                  setScheduleForm((f) => ({ ...f, notify_on_complete: e.target.checked }))
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              <span>Send Pushover notification when self-test completes</span>
            </label>
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={scheduleForm.notify_on_fail}
                onChange={(e) =>
                  setScheduleForm((f) => ({ ...f, notify_on_fail: e.target.checked }))
                }
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              <span>Send Pushover alert if self-test fails</span>
            </label>
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={scheduleForm.enabled}
                onChange={(e) => setScheduleForm((f) => ({ ...f, enabled: e.target.checked }))}
                className="rounded border-slate-600 bg-surface-950 text-indigo-500"
              />
              <span>Schedule enabled</span>
            </label>
          </div>

          <div className="flex justify-end gap-2.5 pt-2">
            <button
              onClick={() => setScheduleModalOpen(false)}
              className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm text-slate-200"
            >
              Cancel
            </button>
            <button
              onClick={() => void handleSaveSchedule()}
              disabled={savingSchedule || !scheduleForm.ups_name}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-semibold text-white disabled:opacity-50"
            >
              {savingSchedule ? "Saving..." : "Save Schedule"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
