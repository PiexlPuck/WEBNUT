import { AlertTriangle, Clock, Loader2 } from "lucide-react";
import type { OrchestratorState } from "../types";

export function RecoveryBanner({ orchestrator }: { orchestrator: OrchestratorState }) {
  if (orchestrator.state === "NORMAL") {
    return null;
  }

  if (orchestrator.state === "ON_BATTERY") {
    return (
      <div className="mb-6 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
        <div>
          <h3 className="text-sm font-semibold text-amber-200">
            UPS On Battery — Pre-Outage Host Snapshot Frozen
          </h3>
          <p className="text-xs text-amber-300/80 mt-0.5">
            Utility power loss detected ({orchestrator.last_ups_status}). Only hosts running prior to
            the outage (or marked &ldquo;Always Wake&rdquo;) will be woken when power stabilizes.
          </p>
        </div>
      </div>
    );
  }

  if (orchestrator.state === "STABILIZING") {
    return (
      <div className="mb-6 rounded-xl border border-sky-500/40 bg-sky-500/10 p-4 flex items-start gap-3">
        <Clock className="h-5 w-5 text-sky-400 shrink-0 mt-0.5 animate-pulse" />
        <div>
          <h3 className="text-sm font-semibold text-sky-200">
            Power Restored — Stability Hold Active ({orchestrator.stability_remaining_sec}s remaining)
          </h3>
          <p className="text-xs text-sky-300/80 mt-0.5">
            Verifying steady utility power and safe battery charge before dispatching Wake-on-LAN
            magic packets.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mb-6 rounded-xl border border-indigo-500/40 bg-indigo-500/10 p-4 flex items-start gap-3">
      <Loader2 className="h-5 w-5 text-indigo-400 shrink-0 mt-0.5 animate-spin" />
      <div>
        <h3 className="text-sm font-semibold text-indigo-200">
          {orchestrator.is_dry_run
            ? "Simulated Recovery Sequence Running (Dry-Run)"
            : "WoL Recovery Sequence Active"}
        </h3>
        <p className="text-xs text-indigo-300/80 mt-0.5">
          Dispatching staggered Wake-on-LAN packets and verifying host reachability with auto-retry.
        </p>
      </div>
    </div>
  );
}
