import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  Copy,
  Eye,
  FileText,
  FolderArchive,
  Link2,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { copyToClipboard } from "../utils/clipboard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import type {
  NutClientPlatform,
  RecordedClientConfigDetail,
  RecordedClientConfigSummary,
} from "../types";

interface RecordedConfigsPageProps {
  onLoadInClientSetup: (rec: RecordedClientConfigSummary) => void;
  onNavigateClientSetup: () => void;
}

const PLATFORM_LABELS: Record<NutClientPlatform, string> = {
  linux_debian: "Debian / Ubuntu / Proxmox",
  linux_rhel: "RHEL / Rocky / Fedora",
  truenas: "TrueNAS SCALE / CORE",
  synology: "Synology DSM",
  windows: "Windows (WinNUT)",
};

export function RecordedConfigsPage({
  onLoadInClientSetup,
  onNavigateClientSetup,
}: RecordedConfigsPageProps) {
  const [records, setRecords] = useState<RecordedClientConfigSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);
  const [viewing, setViewing] = useState<RecordedClientConfigDetail | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RecordedClientConfigSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const loadRecords = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.getRecordedClientConfigs();
      setRecords(list);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to load saved client configs",
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRecords();
  }, [loadRecords]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return records.filter((r) => {
      if (platformFilter !== "all" && r.platform !== platformFilter) return false;
      if (!q) return true;
      return (
        r.device_name.toLowerCase().includes(q) ||
        r.ups_name.toLowerCase().includes(q) ||
        r.server_host.toLowerCase().includes(q) ||
        r.username.toLowerCase().includes(q)
      );
    });
  }, [records, search, platformFilter]);

  const handleViewConfig = async (id: number) => {
    setBusyId(id);
    setFeedback(null);
    try {
      const detail = await api.getRecordedClientConfig(id);
      setViewing(detail);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to load saved config",
      });
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleting(true);
    setFeedback(null);
    try {
      await api.deleteRecordedClientConfig(target.id);
      setRecords((prev) => prev.filter((item) => item.id !== target.id));
      if (viewing?.id === target.id) setViewing(null);
      setDeleteTarget(null);
      setFeedback({ ok: true, msg: `Deleted saved config for '${target.device_name}'.` });
    } catch (err) {
      setDeleteTarget(null);
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to delete '${target.device_name}'`,
      });
    } finally {
      setDeleting(false);
    }
  };

  const copyText = async (key: string, text: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey(null), 2000);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            Saved Device Client Configs
          </h2>
          <p className="text-sm text-slate-400">
            Encrypted registry of every generated and SSH-deployed NUT client setup per device
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onNavigateClientSetup}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
          >
            <Link2 className="h-3.5 w-3.5" />
            Configure New Client
          </button>
          <button
            onClick={() => void loadRecords()}
            disabled={loading}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

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

      {/* Search & Platform Filters */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-4 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
        <div className="relative flex-1">
          <Search className="h-4 w-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by device name, UPS stream, server IP, or account..."
            className="w-full rounded-lg bg-surface-950 border border-slate-700 pl-9 pr-3 py-2 text-xs text-white"
          />
        </div>
        <div className="flex items-center gap-3">
          <select
            value={platformFilter}
            onChange={(e) => setPlatformFilter(e.target.value)}
            className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-slate-200"
          >
            <option value="all">All Platforms</option>
            {Object.entries(PLATFORM_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <span className="text-xs font-mono text-slate-400 shrink-0">
            {filtered.length} / {records.length} recorded
          </span>
        </div>
      </div>

      {/* Saved Configs Table */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FolderArchive className="h-4 w-4 text-sky-400" />
            <h3 className="text-sm font-semibold text-white">Recorded Device Client Configs</h3>
          </div>
          <span className="text-xs font-mono text-slate-400">{records.length} recorded</span>
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
              {filtered.map((rec) => {
                const isBusy = busyId === rec.id;
                return (
                  <tr key={rec.id} className="hover:bg-surface-800/40">
                    <td className="px-4 py-3 font-medium text-white">{rec.device_name}</td>
                    <td className="px-4 py-3 text-slate-300">
                      {PLATFORM_LABELS[rec.platform] ?? rec.platform}
                    </td>
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
                        onClick={() => void handleViewConfig(rec.id)}
                        disabled={isBusy}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200 disabled:opacity-50"
                      >
                        <Eye className="h-3 w-3" />
                        View Config
                      </button>
                      <button
                        onClick={() => onLoadInClientSetup(rec)}
                        disabled={isBusy}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/40 text-indigo-300 disabled:opacity-50"
                      >
                        <Upload className="h-3 w-3" />
                        Load in Setup
                      </button>
                      <button
                        onClick={() => setDeleteTarget(rec)}
                        disabled={isBusy}
                        title="Delete saved config"
                        className="p-1.5 rounded bg-surface-800 hover:bg-rose-500/20 text-slate-400 hover:text-rose-300 disabled:opacity-50"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-400 space-y-3">
                    <div className="flex flex-col items-center gap-2">
                      <FileText className="h-6 w-6 text-slate-600" />
                      <span>
                        {records.length === 0
                          ? "No device client configs recorded yet. Generate a manual config or run One-Button SSH Deploy in Client Setup to record it automatically."
                          : "No saved configs match your current search/filter."}
                      </span>
                      {records.length === 0 && (
                        <button
                          onClick={onNavigateClientSetup}
                          className="mt-1 px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
                        >
                          Open Client Setup
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recorded Config Detail Modal */}
      <Modal
        open={viewing !== null}
        title={
          viewing ? `Saved NUT Client Config — ${viewing.device_name}` : "Saved Client Config"
        }
        onClose={() => setViewing(null)}
        maxWidth="max-w-3xl"
      >
        {viewing && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400 border-b border-slate-800 pb-3">
              <div>
                Stream:{" "}
                <span className="font-mono text-sky-300">
                  {viewing.ups_name}@{viewing.server_host}:{viewing.nut_port}
                </span>{" "}
                • Account: <span className="font-mono text-white">{viewing.username}</span>
              </div>
              <span>Updated {new Date(viewing.updated_at).toLocaleString()}</span>
            </div>

            {viewing.config.gui_fields.length > 0 && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 divide-y divide-slate-800/70">
                {viewing.config.gui_fields.map((gf) => (
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

            {viewing.config.one_liner && (
              <div className="rounded-xl border border-slate-800 bg-surface-950 overflow-hidden">
                <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs font-semibold text-indigo-300">
                    One-Click Setup Script
                  </span>
                  <button
                    onClick={() => void copyText("rec-one-liner", viewing.config.one_liner!)}
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
                  {viewing.config.one_liner}
                </pre>
              </div>
            )}

            {viewing.config.files.map((f) => (
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

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Saved Client Config"
        message={
          deleteTarget
            ? `Delete recorded client config for "${deleteTarget.device_name}" (${deleteTarget.ups_name}@${deleteTarget.server_host})?`
            : ""
        }
        confirmLabel="Delete Config"
        variant="danger"
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
