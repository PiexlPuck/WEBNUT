import type { WakeState } from "../types";

export function UpsStatusBadge({ status }: { status: string }) {
  const upper = status.toUpperCase();
  let cls = "bg-slate-800 text-slate-300 border-slate-700";
  if (upper.includes("LB") || upper.includes("COMMBAD") || upper.includes("NOCOMM")) {
    cls = "bg-rose-500/15 text-rose-300 border-rose-500/40";
  } else if (upper.includes("OB")) {
    cls = "bg-amber-500/15 text-amber-300 border-amber-500/40";
  } else if (upper.includes("OL")) {
    cls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/40";
  }
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-md border font-mono text-xs font-semibold ${cls}`}>
      {status}
    </span>
  );
}

export function WakeStateBadge({
  state,
  attempt,
  maxRetries,
}: {
  state: WakeState;
  attempt: number;
  maxRetries: number;
}) {
  const styles: Record<WakeState, { label: string; cls: string }> = {
    IDLE: { label: "Idle", cls: "bg-slate-800/80 text-slate-300 border-slate-700" },
    WAITING_STAGGER: {
      label: "Waiting Stagger",
      cls: "bg-indigo-500/15 text-indigo-300 border-indigo-500/40",
    },
    WAITING_BATTERY: {
      label: "Waiting Battery",
      cls: "bg-cyan-500/15 text-cyan-300 border-cyan-500/40",
    },
    WAKING: {
      label: `Waking (${attempt}/${maxRetries})`,
      cls: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    },
    RETRYING: {
      label: `Retrying (${attempt}/${maxRetries})`,
      cls: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    },
    WAKE_SUCCEEDED: {
      label: "Wake Succeeded",
      cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    },
    WAKE_FAILED: {
      label: "Wake Failed",
      cls: "bg-rose-500/15 text-rose-300 border-rose-500/40",
    },
  };
  const cfg = styles[state] ?? styles.IDLE;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const map: Record<string, string> = {
    info: "bg-sky-500/15 text-sky-300 border-sky-500/30",
    success: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    warning: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    error: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] uppercase font-semibold ${
        map[severity] ?? "bg-slate-800 text-slate-300 border-slate-700"
      }`}
    >
      {severity}
    </span>
  );
}
