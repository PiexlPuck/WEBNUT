import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Link2,
  PlugZap,
  Radio,
  RefreshCw,
  Settings2,
  Unlink,
  Wifi,
  WifiOff,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { UpsStatusBadge } from "../components/StatusBadge";
import type { RepeaterLinkStatus, TelemetrySnapshot } from "../types";

interface RepeaterLinkPageProps {
  linkStatus: RepeaterLinkStatus | null;
  telemetry: TelemetrySnapshot;
  onRefresh: () => Promise<void>;
  onNavigateUpsSetup: () => void;
}

export function RepeaterLinkPage({
  linkStatus,
  telemetry,
  onRefresh,
  onNavigateUpsSetup,
}: RepeaterLinkPageProps) {
  const [form, setForm] = useState({
    main_url: "",
    join_token: "",
    node_name: "",
    public_url: "",
  });
  const [pairing, setPairing] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [unpairModalOpen, setUnpairModalOpen] = useState(false);
  const [unpairing, setUnpairing] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const handlePair = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairing(true);
    setFeedback(null);
    try {
      await api.pairRepeaterLink({
        main_url: form.main_url.trim(),
        join_token: form.join_token.trim(),
        node_name: form.node_name.trim(),
        public_url: form.public_url.trim(),
      });
      await onRefresh();
      setFeedback({ ok: true, msg: "Paired with Main WebNUT and starting live tunnel." });
      setForm({ main_url: "", join_token: "", node_name: "", public_url: "" });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to pair with Main WebNUT",
      });
    } finally {
      setPairing(false);
    }
  };

  const handleReconnect = async () => {
    setReconnecting(true);
    setFeedback(null);
    try {
      await api.reconnectRepeaterLink();
      await onRefresh();
      setFeedback({ ok: true, msg: "Triggered WebSocket tunnel reconnect." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Reconnect request failed",
      });
    } finally {
      setReconnecting(false);
    }
  };

  const confirmUnpair = async () => {
    setUnpairing(true);
    setFeedback(null);
    try {
      await api.unpairRepeaterLink();
      setUnpairModalOpen(false);
      await onRefresh();
      setFeedback({ ok: true, msg: "Unpaired from Main WebNUT." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to unpair",
      });
    } finally {
      setUnpairing(false);
    }
  };

  const paired = Boolean(linkStatus?.paired);
  const connected = Boolean(linkStatus?.connected);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <Radio className="h-6 w-6 text-sky-400" />
            Main WebNUT Link
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            Pair this Repeater node to Main WebNUT so local UPSes stream securely over an outbound tunnel
          </p>
        </div>
        <button
          onClick={() => void onRefresh()}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm text-slate-200 self-start sm:self-auto"
        >
          <RefreshCw className="h-4 w-4" />
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

      {paired && linkStatus ? (
        <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2.5">
                <h3 className="text-lg font-semibold text-white">{linkStatus.node_name}</h3>
                <span className="font-mono text-xs px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-indigo-300">
                  {linkStatus.node_slug}_*
                </span>
                <span
                  className={`inline-flex items-center gap-1 text-[11px] px-2.5 py-0.5 rounded-full border font-semibold uppercase ${
                    connected
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                      : "bg-amber-500/15 text-amber-300 border-amber-500/40"
                  }`}
                >
                  {connected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                  {connected ? "Connected" : "Reconnecting"}
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Streaming to{" "}
                <span className="font-mono text-slate-200">{linkStatus.main_url}</span>
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {linkStatus.main_url && (
                <a
                  href={linkStatus.main_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-indigo-300"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Open Main WebNUT
                </a>
              )}
              <button
                onClick={() => void handleReconnect()}
                disabled={reconnecting}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${reconnecting ? "animate-spin" : ""}`} />
                Reconnect
              </button>
              <button
                onClick={() => setUnpairModalOpen(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-xs font-medium text-rose-300"
              >
                <Unlink className="h-3.5 w-3.5" />
                Unpair
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
            <div className="rounded-xl bg-surface-950/70 border border-slate-800 p-3.5">
              <div className="text-slate-400">Tunnel Latency</div>
              <div className="text-sm font-mono font-semibold text-white mt-1">
                {linkStatus.latency_ms !== null ? `${linkStatus.latency_ms.toFixed(1)} ms` : "—"}
              </div>
            </div>
            <div className="rounded-xl bg-surface-950/70 border border-slate-800 p-3.5">
              <div className="text-slate-400">Last Telemetry Push</div>
              <div className="text-sm font-mono font-semibold text-white mt-1">
                {linkStatus.last_push_at
                  ? new Date(linkStatus.last_push_at).toLocaleTimeString()
                  : "—"}
              </div>
            </div>
            <div className="rounded-xl bg-surface-950/70 border border-slate-800 p-3.5">
              <div className="text-slate-400">Advertised Node UI</div>
              <div className="text-sm font-mono text-slate-200 truncate mt-1">
                {linkStatus.public_url || "Auto"}
              </div>
            </div>
          </div>

          {linkStatus.last_error && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
              Tunnel status note: {linkStatus.last_error}
            </div>
          )}
        </div>
      ) : (
        <form
          onSubmit={(e) => void handlePair(e)}
          className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4"
        >
          <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
            <Link2 className="h-5 w-5 text-indigo-400" />
            <div>
              <h3 className="text-base font-semibold text-white">Pair with Main WebNUT</h3>
              <p className="text-xs text-slate-400">
                Create a Join Token in your Main WebNUT UI under <strong>Repeaters</strong> and enter it here
              </p>
            </div>
          </div>

          {linkStatus?.last_error && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
              {linkStatus.last_error}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">
                Main WebNUT URL *
              </label>
              <input
                required
                value={form.main_url}
                onChange={(e) => setForm({ ...form, main_url: e.target.value })}
                placeholder="http://192.168.1.10:8765"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">
                Join Token *
              </label>
              <input
                required
                value={form.join_token}
                onChange={(e) => setForm({ ...form, join_token: e.target.value })}
                placeholder="wnjt_..."
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">
                Node Name *
              </label>
              <input
                required
                value={form.node_name}
                onChange={(e) => setForm({ ...form, node_name: e.target.value })}
                placeholder="garage-pi"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">
                Repeater UI URL (Optional)
              </label>
              <input
                value={form.public_url}
                onChange={(e) => setForm({ ...form, public_url: e.target.value })}
                placeholder="Auto-detected if blank (e.g., http://192.168.1.50:8765)"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm font-mono text-white"
              />
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={pairing}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white disabled:opacity-50"
            >
              <Link2 className="h-4 w-4" />
              {pairing ? "Pairing..." : "Pair Node"}
            </button>
          </div>
        </form>
      )}

      {/* Local UPSes Forwarded Preview */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div>
            <h3 className="text-sm font-semibold text-white">Local UPS Devices</h3>
            <p className="text-xs text-slate-400">
              Hardware configured locally on this Repeater node and forwarded to Main WebNUT
            </p>
          </div>
          <button
            onClick={onNavigateUpsSetup}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white self-start sm:self-auto"
          >
            <Settings2 className="h-3.5 w-3.5" />
            Configure UPS Hardware
          </button>
        </div>

        {!telemetry.connected || telemetry.ups_list.length === 0 ? (
          <div className="rounded-xl border border-slate-800 bg-surface-950/60 p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-amber-300 text-sm font-semibold">
                <PlugZap className="h-4 w-4" />
                <span>No local UPS detected yet</span>
              </div>
              <p className="text-xs text-slate-400">
                {telemetry.error ||
                  "Open UPS Setup to scan USB/Serial/SNMP UPS hardware on this machine."}
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {telemetry.ups_list.map((u) => {
              const preview = linkStatus?.ups_preview.find((p) => p.local_name === u.name);
              const mainName = preview?.main_name || u.name;
              return (
                <div
                  key={u.name}
                  className="rounded-xl border border-slate-800 bg-surface-950/70 p-4 space-y-2.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <div className="font-mono font-semibold text-white text-sm">{u.name}</div>
                      {paired && (
                        <div className="text-[11px] text-slate-400">
                          Main ID: <code className="text-indigo-300">{mainName}</code>
                        </div>
                      )}
                    </div>
                    <UpsStatusBadge status={u.status} />
                  </div>
                  <div className="text-xs text-slate-400">
                    {[u.ups_mfr, u.ups_model, u.description].filter(Boolean).join(" • ") ||
                      "Local UPS"}
                  </div>
                  <div className="grid grid-cols-3 gap-2 pt-1 text-xs">
                    <div>
                      <span className="text-slate-500 block">Battery</span>
                      <span className="font-mono text-white">
                        {u.battery_charge !== null ? `${u.battery_charge.toFixed(0)}%` : "—"}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Load</span>
                      <span className="font-mono text-white">
                        {u.ups_load !== null ? `${u.ups_load.toFixed(0)}%` : "—"}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Input V</span>
                      <span className="font-mono text-white">
                        {u.input_voltage !== null ? `${u.input_voltage.toFixed(1)}V` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={unpairModalOpen}
        title="Unpair from Main WebNUT"
        message="Stop streaming UPS telemetry and clear saved Main WebNUT pairing credentials on this Repeater node?"
        confirmLabel="Unpair Node"
        variant="warning"
        busy={unpairing}
        onConfirm={confirmUnpair}
        onCancel={() => setUnpairModalOpen(false)}
      />
    </div>
  );
}
