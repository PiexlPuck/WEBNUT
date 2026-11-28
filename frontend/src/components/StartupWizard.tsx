import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Bell,
  CheckCircle2,
  Compass,
  ExternalLink,
  FlaskConical,
  LayoutGrid,
  Link2,
  Radio,
  Send,
  Server,
  Settings2,
  Sliders,
  Sparkles,
  X,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { Modal } from "./Modal";
import type { NavTab } from "./Layout";
import type { OnboardingState, TelemetrySnapshot } from "../types";

interface StartupWizardProps {
  open: boolean;
  minimized: boolean;
  role: "main" | "repeater";
  onboarding: OnboardingState | null;
  telemetry: TelemetrySnapshot;
  hostsCount: number;
  repeatersCount: number;
  onMinimize: (minimized: boolean) => void;
  onNavigateTab: (tab: NavTab) => void;
  onRenameInstance: (name: string) => Promise<void>;
  onUpdateOnboarding: (payload: {
    instance_name?: string;
    completed?: boolean;
    skipped?: boolean;
    last_step?: number;
  }) => Promise<void>;
  onRefresh: () => Promise<void>;
}

interface WizardStepDef {
  id: string;
  title: string;
  subtitle: string;
  targetTab?: NavTab;
  targetLabel?: string;
}

const MAIN_STEPS: WizardStepDef[] = [
  {
    id: "welcome",
    title: "Welcome & Name This WebNUT",
    subtitle: "Give this WebNUT server a friendly name and review your initial setup checklist.",
  },
  {
    id: "ups",
    title: "UPS & NUT Hardware Setup",
    subtitle: "Detect your USB, Serial, or SNMP UPS or connect an existing NUT server.",
    targetTab: "nut",
    targetLabel: "Open UPS Setup",
  },
  {
    id: "repeaters",
    title: "Remote Repeater Nodes (Optional)",
    subtitle: "Join secondary Raspberry Pis or servers running WEBNUT_ROLE=repeater over a secure tunnel.",
    targetTab: "repeaters",
    targetLabel: "Open Repeaters",
  },
  {
    id: "hosts",
    title: "Managed Hosts & Client Setup",
    subtitle: "Register servers for graceful UPS shutdown streaming and Wake-on-LAN auto-recovery.",
    targetTab: "hosts",
    targetLabel: "Open Managed Hosts",
  },
  {
    id: "alerts",
    title: "Alerts & Update Notifications",
    subtitle: "Configure Pushover so WebNUT alerts you on outages, self-tests, flow drills, and new releases.",
    targetTab: "notifications",
    targetLabel: "Open Alerts Page",
  },
  {
    id: "finish",
    title: "Control, Flow Drills & Integrations",
    subtitle: "Explore UPS commands, scheduled Flow Drills, and Home Assistant / Homepage integrations.",
    targetTab: "drills",
    targetLabel: "Open Flow Drills",
  },
];

const REPEATER_STEPS: WizardStepDef[] = [
  {
    id: "rep_welcome",
    title: "Welcome & Name This Repeater",
    subtitle: "Identify this Repeater node's Web UI and inspect local UPS forwarding status.",
  },
  {
    id: "rep_ups",
    title: "Local UPS Hardware Setup",
    subtitle: "Scan and configure the USB, Serial, or SNMP UPS connected directly to this machine.",
    targetTab: "nut",
    targetLabel: "Open UPS Setup",
  },
  {
    id: "rep_pair",
    title: "Pair with Main WebNUT",
    subtitle: "Enter your Main WebNUT URL and Join Token so telemetry & controls stream automatically.",
    targetTab: "repeater_link",
    targetLabel: "Open Main Link",
  },
];

export function StartupWizard({
  open,
  minimized,
  role,
  onboarding,
  telemetry,
  hostsCount,
  repeatersCount,
  onMinimize,
  onNavigateTab,
  onRenameInstance,
  onUpdateOnboarding,
  onRefresh,
}: StartupWizardProps) {
  const steps = role === "repeater" ? REPEATER_STEPS : MAIN_STEPS;
  const [stepIdx, setStepIdx] = useState(0);
  const [nameInput, setNameInput] = useState(onboarding?.instance_name || "WebNUT");
  const [savingName, setSavingName] = useState(false);

  // Inline Pushover quick config (Step 5 on Main)
  const [poForm, setPoForm] = useState({
    enabled: false,
    user_key: "",
    api_token: "",
  });
  const [poSaving, setPoSaving] = useState(false);
  const [poFeedback, setPoFeedback] = useState<{ ok: boolean; msg: string } | null>(null);
  const [busyAction, setBusyAction] = useState(false);

  useEffect(() => {
    if (!open) return;
    setNameInput(onboarding?.instance_name || "WebNUT");
    const savedIdx = onboarding?.last_step ?? 0;
    if (savedIdx >= 0 && savedIdx < steps.length) {
      setStepIdx(savedIdx);
    } else {
      setStepIdx(0);
    }
  }, [open, onboarding?.instance_name, onboarding?.last_step, steps.length]);

  useEffect(() => {
    if (!open || role !== "main") return;
    void (async () => {
      try {
        const res = await api.getNotificationSettings();
        setPoForm({
          enabled: res.pushover.enabled,
          user_key: res.pushover.user_key,
          api_token: res.pushover.api_token,
        });
      } catch {
        // ignore if unreachable
      }
    })();
  }, [open, role]);

  if (!open) {
    return null;
  }

  const currentStep = steps[stepIdx] ?? steps[0];
  const isFirst = stepIdx === 0;
  const isLast = stepIdx === steps.length - 1;

  const persistNameIfChanged = async () => {
    const trimmed = nameInput.trim();
    if (trimmed && trimmed !== (onboarding?.instance_name || "WebNUT")) {
      setSavingName(true);
      try {
        await onRenameInstance(trimmed);
      } finally {
        setSavingName(false);
      }
    }
  };

  const goToStep = async (nextIdx: number) => {
    const clamped = Math.max(0, Math.min(steps.length - 1, nextIdx));
    if (stepIdx === 0) {
      await persistNameIfChanged();
    }
    setStepIdx(clamped);
    void onUpdateOnboarding({ last_step: clamped });
  };

  const handleOpenTargetPage = async (tab: NavTab) => {
    if (stepIdx === 0) {
      await persistNameIfChanged();
    }
    onNavigateTab(tab);
    onMinimize(true);
  };

  const handleSkip = async () => {
    setBusyAction(true);
    try {
      await onUpdateOnboarding({ skipped: true, last_step: stepIdx });
    } finally {
      setBusyAction(false);
    }
  };

  const handleFinish = async () => {
    setBusyAction(true);
    try {
      await persistNameIfChanged();
      await onUpdateOnboarding({
        completed: true,
        skipped: false,
        last_step: steps.length - 1,
      });
      onNavigateTab(role === "repeater" ? "repeater_link" : "dashboard");
    } finally {
      setBusyAction(false);
    }
  };

  const handleSaveAndTestPushover = async () => {
    setPoSaving(true);
    setPoFeedback(null);
    try {
      const existing = await api.getNotificationSettings();
      await api.updatePushover({
        enabled: true,
        user_key: poForm.user_key.trim(),
        api_token: poForm.api_token.trim(),
        device: existing.pushover.device,
        sound: existing.pushover.sound,
        emergency_retry_sec: existing.pushover.emergency_retry_sec,
        emergency_expire_sec: existing.pushover.emergency_expire_sec,
      });
      await api.sendTestNotification(0);
      setPoForm((prev) => ({ ...prev, enabled: true }));
      setPoFeedback({ ok: true, msg: "Pushover saved and test notification delivered!" });
      await onRefresh();
    } catch (err) {
      setPoFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to send Pushover test",
      });
    } finally {
      setPoSaving(false);
    }
  };

  // Floating minimized guide bar while configuring an actual page
  if (minimized) {
    return (
      <div className="fixed bottom-4 right-4 z-50 max-w-md rounded-2xl border border-indigo-500/40 bg-surface-900/95 backdrop-blur shadow-2xl p-3.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <Compass className="h-4 w-4 text-indigo-400 shrink-0" />
          <div className="text-xs min-w-0">
            <div className="font-semibold text-white truncate">
              Step {stepIdx + 1}/{steps.length}: {currentStep.title}
            </div>
            <div className="text-[11px] text-slate-400">Configure this page, then continue</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            onClick={() => onMinimize(false)}
            className="px-2.5 py-1 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-[11px] font-medium text-slate-200"
          >
            Back to Wizard
          </button>
          {!isLast ? (
            <button
              type="button"
              onClick={() => {
                onMinimize(false);
                void goToStep(stepIdx + 1);
              }}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-[11px] font-medium text-white"
            >
              Next
              <ArrowRight className="h-3 w-3" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void handleFinish()}
              className="px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-[11px] font-medium text-white"
            >
              Finish
            </button>
          )}
          <button
            type="button"
            onClick={() => void handleSkip()}
            disabled={busyAction}
            className="px-2 py-1 rounded-lg text-[11px] text-slate-400 hover:text-white"
            title="Skip setup wizard"
          >
            Skip
          </button>
        </div>
      </div>
    );
  }

  return (
    <Modal
      open={open}
      title={`${onboarding?.instance_name || "WebNUT"} Setup Wizard (${stepIdx + 1} of ${steps.length})`}
      onClose={() => void handleSkip()}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-5">
        {/* Progress bar */}
        <div className="flex items-center gap-1.5">
          {steps.map((s, idx) => (
            <button
              key={s.id}
              type="button"
              onClick={() => void goToStep(idx)}
              title={s.title}
              className={`h-1.5 flex-1 rounded-full transition-colors ${
                idx < stepIdx
                  ? "bg-emerald-500"
                  : idx === stepIdx
                  ? "bg-indigo-500"
                  : "bg-slate-800 hover:bg-slate-700"
              }`}
            />
          ))}
        </div>

        {/* Step Heading */}
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <h3 className="text-base font-semibold text-white flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-indigo-400" />
              {currentStep.title}
            </h3>
            <p className="text-xs text-slate-400">{currentStep.subtitle}</p>
          </div>
          <button
            type="button"
            onClick={() => void handleSkip()}
            disabled={busyAction}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-300 shrink-0"
          >
            <X className="h-3.5 w-3.5" />
            Skip Setup
          </button>
        </div>

        {/* Step Body */}
        {currentStep.id === "welcome" || currentStep.id === "rep_welcome" ? (
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-2.5">
              <label className="block text-xs font-medium text-slate-200">
                WebNUT Instance Name
              </label>
              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  maxLength={64}
                  placeholder="e.g., Home Rack WebNUT"
                  className="flex-1 rounded-lg bg-surface-900 border border-slate-700 px-3 py-2 text-sm text-white"
                />
                <button
                  type="button"
                  onClick={() => void persistNameIfChanged()}
                  disabled={savingName || !nameInput.trim()}
                  className="px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
                >
                  {savingName ? "Saving..." : "Save Name"}
                </button>
              </div>
              <p className="text-[11px] text-slate-400">
                Shown in the sidebar header, browser tab title, Home Assistant, and notification titles.
              </p>
            </div>

            {role === "main" ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
                {[
                  {
                    label: "UPS Configured / Connected",
                    done: Boolean(onboarding?.has_ups_configured || onboarding?.has_ups_polling),
                    detail: `${telemetry.ups_list.length} active stream(s)`,
                  },
                  {
                    label: "Managed Hosts Added",
                    done: Boolean(onboarding?.has_hosts),
                    detail: `${hostsCount} host(s)`,
                  },
                  {
                    label: "Pushover Alerts Ready",
                    done: Boolean(onboarding?.has_pushover),
                    detail: onboarding?.has_pushover ? "Enabled" : "Not configured",
                  },
                  {
                    label: "Remote Repeaters (Optional)",
                    done: Boolean(onboarding?.has_repeaters),
                    detail: `${repeatersCount} node(s)`,
                  },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="rounded-xl border border-slate-800 bg-surface-950/60 p-3 flex items-center justify-between gap-2"
                  >
                    <div>
                      <div className="font-medium text-white">{item.label}</div>
                      <div className="text-[11px] text-slate-400">{item.detail}</div>
                    </div>
                    <span
                      className={`px-2 py-0.5 rounded border text-[10px] font-semibold uppercase ${
                        item.done
                          ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                          : "bg-slate-800 text-slate-400 border-slate-700"
                      }`}
                    >
                      {item.done ? "Ready" : "Pending"}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-slate-800 bg-surface-950/60 p-4 text-xs text-slate-300 space-y-2">
                <div className="font-semibold text-white">Repeater Mode Overview</div>
                <p>
                  This container runs with <code>WEBNUT_ROLE=repeater</code>. Use{" "}
                  <strong>UPS Setup</strong> to detect attached UPSes, then use{" "}
                  <strong>Main Link</strong> to pair with your Main WebNUT server.
                </p>
              </div>
            )}
          </div>
        ) : null}

        {(currentStep.id === "ups" || currentStep.id === "rep_ups") && (
          <div className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-white">
                <Settings2 className="h-4 w-4 text-indigo-400" />
                <span>Current UPS Telemetry</span>
              </div>
              <span
                className={`px-2 py-0.5 rounded border font-semibold ${
                  telemetry.ups_list.length > 0
                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                    : "bg-amber-500/15 text-amber-300 border-amber-500/40"
                }`}
              >
                {telemetry.ups_list.length > 0
                  ? `${telemetry.ups_list.length} UPS Active`
                  : "No UPS Active"}
              </span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              In <strong>UPS Setup</strong>, click <strong>Scan Hardware (USB &amp; Serial)</strong>{" "}
              to auto-detect plugged-in UPSes, configure an <strong>SNMP Network UPS</strong>, or{" "}
              {role === "main"
                ? "mirror an existing NUT server."
                : "verify local NUT daemons before streaming to Main."}
            </p>
            <button
              type="button"
              onClick={() => void handleOpenTargetPage("nut")}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open UPS Setup &amp; Configure
            </button>
          </div>
        )}

        {currentStep.id === "repeaters" && (
          <div className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-white">
                <Radio className="h-4 w-4 text-sky-400" />
                <span>Remote Repeater Nodes</span>
              </div>
              <span className="font-mono text-slate-300">{repeatersCount} paired</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              If you have UPS units connected to other Raspberry Pis or servers, deploy{" "}
              <code>docker-compose.repeater.yml</code> there and generate a Join Token on the{" "}
              <strong>Repeaters</strong> page. All remote UPSes appear automatically across Dashboard,
              Hosts, Self-Tests, and Home Assistant.
            </p>
            <button
              type="button"
              onClick={() => void handleOpenTargetPage("repeaters")}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open Repeaters Page
            </button>
          </div>
        )}

        {currentStep.id === "hosts" && (
          <div className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-white">
                <Server className="h-4 w-4 text-indigo-400" />
                <span>Managed Hosts &amp; Client Setup</span>
              </div>
              <span className="font-mono text-slate-300">{hostsCount} host(s)</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              Add your Proxmox, TrueNAS, Synology, Linux, or Windows machines under{" "}
              <strong>Managed Hosts</strong> with their MAC address and shutdown rules, then use{" "}
              <strong>Client Setup</strong> to one-click deploy <code>nut-client</code> via SSH or
              copy ready-made configs.
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleOpenTargetPage("hosts")}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
              >
                <Server className="h-3.5 w-3.5" />
                Open Managed Hosts
              </button>
              <button
                type="button"
                onClick={() => void handleOpenTargetPage("clients")}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200"
              >
                <Link2 className="h-3.5 w-3.5" />
                Open Client Setup
              </button>
            </div>
          </div>
        )}

        {currentStep.id === "alerts" && (
          <div className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-white">
                <Bell className="h-4 w-4 text-amber-400" />
                <span>Pushover Quick Setup</span>
              </div>
              <button
                type="button"
                onClick={() => void handleOpenTargetPage("notifications")}
                className="text-indigo-300 hover:text-indigo-200 underline"
              >
                Open Full Alerts Page
              </button>
            </div>

            {poFeedback && (
              <div
                className={`rounded-lg border px-3 py-2 flex items-center gap-2 ${
                  poFeedback.ok
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-300"
                }`}
              >
                {poFeedback.ok ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                )}
                <span>{poFeedback.msg}</span>
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Pushover User Key</label>
                <input
                  type="password"
                  value={poForm.user_key}
                  onChange={(e) => setPoForm({ ...poForm, user_key: e.target.value })}
                  placeholder="User / Group key"
                  className="w-full rounded-lg bg-surface-900 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Pushover API Token</label>
                <input
                  type="password"
                  value={poForm.api_token}
                  onChange={(e) => setPoForm({ ...poForm, api_token: e.target.value })}
                  placeholder="Application API token"
                  className="w-full rounded-lg bg-surface-900 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] text-slate-400">
                WebNUT alerts once per new GitHub release, on power outages, and on drill failures.
              </span>
              <button
                type="button"
                onClick={() => void handleSaveAndTestPushover()}
                disabled={poSaving || !poForm.user_key.trim() || !poForm.api_token.trim()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" />
                {poSaving ? "Sending..." : "Save & Send Test"}
              </button>
            </div>
          </div>
        )}

        {currentStep.id === "finish" && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <button
              type="button"
              onClick={() => void handleOpenTargetPage("control")}
              className="text-left rounded-xl border border-slate-800 hover:border-indigo-500/40 bg-surface-950/70 p-3.5 space-y-1.5"
            >
              <div className="flex items-center gap-2 font-semibold text-white">
                <Sliders className="h-4 w-4 text-indigo-400" />
                UPS Control
              </div>
              <p className="text-slate-400">
                Run instant commands and schedule recurring UPS battery self-tests.
              </p>
            </button>
            <button
              type="button"
              onClick={() => void handleOpenTargetPage("drills")}
              className="text-left rounded-xl border border-slate-800 hover:border-indigo-500/40 bg-surface-950/70 p-3.5 space-y-1.5"
            >
              <div className="flex items-center gap-2 font-semibold text-white">
                <FlaskConical className="h-4 w-4 text-sky-400" />
                Flow Drills
              </div>
              <p className="text-slate-400">
                Test the full shutdown &rarr; WoL recovery cycle (one-off or scheduled) with reports.
              </p>
            </button>
            <button
              type="button"
              onClick={() => void handleOpenTargetPage("integrations")}
              className="text-left rounded-xl border border-slate-800 hover:border-indigo-500/40 bg-surface-950/70 p-3.5 space-y-1.5"
            >
              <div className="flex items-center gap-2 font-semibold text-white">
                <LayoutGrid className="h-4 w-4 text-emerald-400" />
                Integrations
              </div>
              <p className="text-slate-400">
                Connect Home Assistant (HACS), Homepage widgets, and Prometheus/Grafana.
              </p>
            </button>
          </div>
        )}

        {currentStep.id === "rep_pair" && (
          <div className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-white">
                <Activity className="h-4 w-4 text-sky-400" />
                <span>Main WebNUT Pairing</span>
              </div>
              <span
                className={`px-2 py-0.5 rounded border font-semibold ${
                  onboarding?.repeater_paired
                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                    : "bg-slate-800 text-slate-300 border-slate-700"
                }`}
              >
                {onboarding?.repeater_paired ? "Paired" : "Not Paired Yet"}
              </span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              Generate a Join Token on your Main WebNUT server under <strong>Repeaters</strong>, then
              paste your Main URL and token into <strong>Main Link</strong>.
            </p>
            <button
              type="button"
              onClick={() => void handleOpenTargetPage("repeater_link")}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open Main Link &amp; Pair
            </button>
          </div>
        )}

        {/* Footer navigation */}
        <div className="flex items-center justify-between gap-2 pt-3 border-t border-slate-800">
          <button
            type="button"
            onClick={() => void goToStep(stepIdx - 1)}
            disabled={isFirst || busyAction}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-40"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back
          </button>

          <div className="flex items-center gap-2">
            {!isLast ? (
              <button
                type="button"
                onClick={() => void goToStep(stepIdx + 1)}
                disabled={busyAction}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
              >
                Next Step
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void handleFinish()}
                disabled={busyAction}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-medium text-white"
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                Finish Setup
              </button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
