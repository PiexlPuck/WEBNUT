import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Copy,
  Edit2,
  ExternalLink,
  KeyRound,
  Plus,
  Radio,
  RefreshCw,
  Server,
  ShieldOff,
  Trash2,
  Wifi,
  WifiOff,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { copyToClipboard } from "../utils/clipboard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import type {
  RepeaterJoinTokenCreateResponse,
  RepeaterJoinTokenView,
  RepeaterNodeView,
  TelemetrySnapshot,
} from "../types";

interface RepeatersPageProps {
  nodes: RepeaterNodeView[];
  telemetry: TelemetrySnapshot;
  onRefresh: () => Promise<void>;
}

function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "Never";
  try {
    const dt = new Date(iso);
    const diffSec = Math.max(0, Math.round((Date.now() - dt.getTime()) / 1000));
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return dt.toLocaleDateString();
  } catch {
    return iso;
  }
}

export function RepeatersPage({ nodes, telemetry, onRefresh }: RepeatersPageProps) {
  const [tokens, setTokens] = useState<RepeaterJoinTokenView[]>([]);
  const [loadingTokens, setLoadingTokens] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  // Create Join Token Modal
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [tokenForm, setTokenForm] = useState({
    label: "",
    expires_hours: 24,
    max_uses: 1,
  });
  const [creatingToken, setCreatingToken] = useState(false);
  const [createdToken, setCreatedToken] = useState<RepeaterJoinTokenCreateResponse | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  // Edit Node Modal
  const [editNode, setEditNode] = useState<RepeaterNodeView | null>(null);
  const [editForm, setEditForm] = useState({ name: "", ui_url: "" });
  const [savingNode, setSavingNode] = useState(false);

  // Confirm Dialogs
  const [deleteNodeTarget, setDeleteNodeTarget] = useState<RepeaterNodeView | null>(null);
  const [deletingNode, setDeletingNode] = useState(false);
  const [revokeTokenTarget, setRevokeTokenTarget] = useState<RepeaterJoinTokenView | null>(null);
  const [revokingToken, setRevokingToken] = useState(false);

  const loadTokens = useCallback(async () => {
    setLoadingTokens(true);
    try {
      const list = await api.getRepeaterTokens();
      setTokens(list);
    } catch {
      // ignore transient error
    } finally {
      setLoadingTokens(false);
    }
  }, []);

  useEffect(() => {
    void loadTokens();
  }, [loadTokens]);

  const copyText = async (key: string, text: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopiedField(key);
      window.setTimeout(() => setCopiedField(null), 2000);
    }
  };

  const handleOpenAddModal = () => {
    setCreatedToken(null);
    setTokenForm({ label: "", expires_hours: 24, max_uses: 1 });
    setAddModalOpen(true);
  };

  const handleCreateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingToken(true);
    setFeedback(null);
    try {
      const res = await api.createRepeaterToken({
        label: tokenForm.label.trim(),
        expires_hours: tokenForm.expires_hours,
        max_uses: tokenForm.max_uses,
      });
      setCreatedToken(res);
      await loadTokens();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to create join token",
      });
    } finally {
      setCreatingToken(false);
    }
  };

  const handleToggleEnabled = async (node: RepeaterNodeView) => {
    setFeedback(null);
    try {
      await api.updateRepeaterNode(node.id, { enabled: !node.enabled });
      await onRefresh();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to update repeater node",
      });
    }
  };

  const handleOpenEditNode = (node: RepeaterNodeView) => {
    setEditNode(node);
    setEditForm({ name: node.name, ui_url: node.ui_url });
  };

  const handleSaveNode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editNode) return;
    setSavingNode(true);
    setFeedback(null);
    try {
      await api.updateRepeaterNode(editNode.id, {
        name: editForm.name.trim(),
        ui_url: editForm.ui_url.trim(),
      });
      setEditNode(null);
      await onRefresh();
      setFeedback({ ok: true, msg: "Updated repeater node settings." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save node settings",
      });
    } finally {
      setSavingNode(false);
    }
  };

  const confirmDeleteNode = async () => {
    if (!deleteNodeTarget) return;
    setDeletingNode(true);
    setFeedback(null);
    try {
      await api.deleteRepeaterNode(deleteNodeTarget.id);
      setDeleteNodeTarget(null);
      await onRefresh();
      setFeedback({ ok: true, msg: `Removed repeater node '${deleteNodeTarget.name}'.` });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to delete repeater node",
      });
    } finally {
      setDeletingNode(false);
    }
  };

  const confirmRevokeToken = async () => {
    if (!revokeTokenTarget) return;
    setRevokingToken(true);
    try {
      await api.revokeRepeaterToken(revokeTokenTarget.id);
      setRevokeTokenTarget(null);
      await loadTokens();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to revoke join token",
      });
    } finally {
      setRevokingToken(false);
    }
  };

  const onlineNodesCount = nodes.filter((n) => n.enabled && n.online).length;
  const totalRemoteUps = nodes.reduce((acc, n) => acc + n.ups_count, 0);
  const activeTokens = tokens.filter(
    (t) => !t.revoked && !t.expired && t.use_count < t.max_uses
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <Radio className="h-6 w-6 text-indigo-400" />
            Repeater Nodes
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            Pair remote Docker WEBNUT Repeaters to stream USB, Serial, and SNMP UPSes into Main WebNUT
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void Promise.all([onRefresh(), loadTokens()])}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm text-slate-200"
          >
            <RefreshCw className={`h-4 w-4 ${loadingTokens ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            onClick={handleOpenAddModal}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white"
          >
            <Plus className="h-4 w-4" />
            Add Repeater Node
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

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
          <div className="text-xs text-slate-400">Paired Nodes</div>
          <div className="text-2xl font-bold text-white mt-1">{nodes.length}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
          <div className="text-xs text-slate-400">Online Tunnels</div>
          <div className="text-2xl font-bold text-emerald-400 mt-1">{onlineNodesCount}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
          <div className="text-xs text-slate-400">Remote UPS Streams</div>
          <div className="text-2xl font-bold text-indigo-300 mt-1">{totalRemoteUps}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-surface-900 p-4">
          <div className="text-xs text-slate-400">Active Join Tokens</div>
          <div className="text-2xl font-bold text-slate-200 mt-1">{activeTokens.length}</div>
        </div>
      </div>

      {/* Paired Nodes List */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">Paired Repeater Nodes</h3>
            <p className="text-xs text-slate-400">
              Remote UPSes are automatically namespaced as{" "}
              <code className="text-indigo-300">&lt;slug&gt;_&lt;ups&gt;</code> for NUT, WOL, Control, and Home Assistant
            </p>
          </div>
        </div>

        {nodes.length === 0 ? (
          <div className="p-8 text-center space-y-3">
            <Server className="h-8 w-8 text-slate-600 mx-auto" />
            <div className="text-sm text-slate-300 font-medium">No Repeater nodes paired yet</div>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              Click <strong>Add Repeater Node</strong> to generate a join token and copy the{" "}
              <code>docker-compose.repeater.yml</code> config for your remote machine.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-800/80">
            {nodes.map((node) => {
              const nodeUps = telemetry.ups_list.filter((u) => u.source_node_id === node.id);
              return (
                <div
                  key={node.id}
                  className="p-5 flex flex-col lg:flex-row lg:items-center justify-between gap-4 hover:bg-surface-800/30"
                >
                  <div className="space-y-2 min-w-0">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <span className="text-base font-semibold text-white">{node.name}</span>
                      <span className="font-mono text-xs px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-indigo-300">
                        {node.slug}_*
                      </span>
                      {!node.enabled ? (
                        <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border bg-slate-800 text-slate-400 border-slate-700 font-semibold uppercase">
                          Disabled
                        </span>
                      ) : node.online ? (
                        <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border bg-emerald-500/15 text-emerald-300 border-emerald-500/40 font-semibold uppercase">
                          <Wifi className="h-3 w-3" />
                          Online
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border bg-rose-500/15 text-rose-300 border-rose-500/40 font-semibold uppercase">
                          <WifiOff className="h-3 w-3" />
                          Offline
                        </span>
                      )}
                      {node.app_version && (
                        <span className="text-[11px] font-mono text-slate-400">
                          v{node.app_version}
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                      <span>IP: {node.remote_ip || "—"}</span>
                      <span>Last seen: {formatTimeAgo(node.last_seen_at)}</span>
                      {node.latency_ms !== null && (
                        <span>Latency: {node.latency_ms.toFixed(1)} ms</span>
                      )}
                    </div>

                    <div className="flex flex-wrap gap-2 pt-1">
                      {nodeUps.length > 0 ? (
                        nodeUps.map((u) => (
                          <span
                            key={u.name}
                            className="inline-flex items-center gap-1.5 text-xs font-mono px-2.5 py-1 rounded-lg bg-surface-950 border border-slate-800 text-slate-200"
                          >
                            <span
                              className={`h-2 w-2 rounded-full ${
                                u.node_online === false
                                  ? "bg-rose-400"
                                  : u.status.includes("OB")
                                  ? "bg-amber-400"
                                  : "bg-emerald-400"
                              }`}
                            />
                            {u.name}
                            <span className="text-slate-400">({u.status})</span>
                          </span>
                        ))
                      ) : node.ups_names.length > 0 ? (
                        node.ups_names.map((uname) => (
                          <span
                            key={uname}
                            className="text-xs font-mono px-2.5 py-1 rounded-lg bg-surface-950 border border-slate-800 text-slate-400"
                          >
                            {uname}
                          </span>
                        ))
                      ) : (
                        <span className="text-xs text-slate-500">No UPS reported by this node yet</span>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 shrink-0">
                    {node.ui_url && (
                      <a
                        href={node.ui_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-indigo-300"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        Open Node UI
                      </a>
                    )}
                    <button
                      onClick={() => handleOpenEditNode(node)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                      Edit
                    </button>
                    <button
                      onClick={() => void handleToggleEnabled(node)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200"
                    >
                      {node.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      onClick={() => setDeleteNodeTarget(node)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-xs font-medium text-rose-300"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Remove
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Join Tokens List */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-indigo-400" />
            <div>
              <h3 className="text-sm font-semibold text-white">Join Tokens</h3>
              <p className="text-xs text-slate-400">
                Single-use or limited-use tokens for pairing new Repeater containers
              </p>
            </div>
          </div>
        </div>
        {tokens.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">
            No join tokens generated yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-950/60 text-slate-400 uppercase text-[11px]">
                <tr>
                  <th className="px-4 py-3">Label / Prefix</th>
                  <th className="px-4 py-3">Uses</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Expires</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {tokens.map((tok) => {
                  const exhausted = tok.use_count >= tok.max_uses;
                  const active = !tok.revoked && !tok.expired && !exhausted;
                  return (
                    <tr key={tok.id} className="hover:bg-surface-800/30">
                      <td className="px-4 py-3">
                        <div className="font-medium text-white">
                          {tok.label || "Unnamed Repeater Token"}
                        </div>
                        <div className="font-mono text-slate-400">{tok.token_prefix}…</div>
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-300">
                        {tok.use_count} / {tok.max_uses}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`px-2 py-0.5 rounded border text-[11px] uppercase font-semibold ${
                            active
                              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                              : "bg-slate-800 text-slate-400 border-slate-700"
                          }`}
                        >
                          {tok.revoked
                            ? "Revoked"
                            : tok.expired
                            ? "Expired"
                            : exhausted
                            ? "Used"
                            : "Active"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-400">
                        {new Date(tok.expires_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {active && (
                          <button
                            onClick={() => setRevokeTokenTarget(tok)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300"
                          >
                            <ShieldOff className="h-3 w-3" />
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add Repeater Node / Join Token Modal */}
      <Modal
        open={addModalOpen}
        title="Add Repeater Node"
        onClose={() => setAddModalOpen(false)}
        maxWidth="max-w-2xl"
      >
        {!createdToken ? (
          <form onSubmit={(e) => void handleCreateToken(e)} className="space-y-4">
            <p className="text-xs text-slate-400">
              Generate a one-time pairing token and ready-to-run Docker Compose snippet for your remote
              WebNUT Repeater container.
            </p>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Node Label (Optional)
              </label>
              <input
                value={tokenForm.label}
                onChange={(e) => setTokenForm({ ...tokenForm, label: e.target.value })}
                placeholder="e.g., garage-ups or rack-b"
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Expires After (hours)
                </label>
                <input
                  type="number"
                  min={1}
                  max={720}
                  value={tokenForm.expires_hours}
                  onChange={(e) =>
                    setTokenForm({ ...tokenForm, expires_hours: Number(e.target.value) })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Max Uses</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={tokenForm.max_uses}
                  onChange={(e) =>
                    setTokenForm({ ...tokenForm, max_uses: Number(e.target.value) })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-slate-800">
              <button
                type="button"
                onClick={() => setAddModalOpen(false)}
                className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-xs font-medium text-slate-300"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creatingToken}
                className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
              >
                {creatingToken ? "Generating..." : "Generate Join Token"}
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-300">
              Token generated! Copy this token now — it will only be displayed once.
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-[11px] text-slate-400">Main WebNUT URL</label>
                <div className="flex items-center gap-1.5">
                  <input
                    readOnly
                    value={createdToken.main_url}
                    className="flex-1 rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
                  />
                  <button
                    type="button"
                    onClick={() => void copyText("main_url", createdToken.main_url)}
                    className="p-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200"
                  >
                    {copiedField === "main_url" ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] text-slate-400">Join Token</label>
                <div className="flex items-center gap-1.5">
                  <input
                    readOnly
                    value={createdToken.token}
                    className="flex-1 rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-indigo-300"
                  />
                  <button
                    type="button"
                    onClick={() => void copyText("token", createdToken.token)}
                    className="p-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200"
                  >
                    {copiedField === "token" ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[11px] text-slate-400">
                  Docker Compose Snippet (<code>docker-compose.repeater.yml</code>)
                </label>
                <button
                  type="button"
                  onClick={() => void copyText("compose", createdToken.docker_compose_yaml)}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 text-[11px] text-slate-200"
                >
                  {copiedField === "compose" ? (
                    <Check className="h-3 w-3 text-emerald-400" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                  {copiedField === "compose" ? "Copied" : "Copy YAML"}
                </button>
              </div>
              <pre className="p-3 rounded-xl bg-surface-950 border border-slate-800 text-[11px] font-mono text-slate-300 overflow-x-auto max-h-64">
                {createdToken.docker_compose_yaml}
              </pre>
            </div>

            <div className="flex justify-end pt-2 border-t border-slate-800">
              <button
                type="button"
                onClick={() => setAddModalOpen(false)}
                className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
              >
                Done
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Edit Node Modal */}
      <Modal
        open={editNode !== null}
        title="Edit Repeater Node"
        onClose={() => setEditNode(null)}
        maxWidth="max-w-md"
      >
        <form onSubmit={(e) => void handleSaveNode(e)} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">Display Name</label>
            <input
              required
              value={editForm.name}
              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">
              Repeater Web UI URL
            </label>
            <input
              value={editForm.ui_url}
              onChange={(e) => setEditForm({ ...editForm, ui_url: e.target.value })}
              placeholder="http://192.168.1.50:8765"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm font-mono text-white"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2 border-t border-slate-800">
            <button
              type="button"
              onClick={() => setEditNode(null)}
              className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-xs font-medium text-slate-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={savingNode}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
            >
              {savingNode ? "Saving..." : "Save Changes"}
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={deleteNodeTarget !== null}
        title="Remove Repeater Node"
        message={
          deleteNodeTarget
            ? `Remove '${deleteNodeTarget.name}' (${deleteNodeTarget.slug}_*)? Its WS connection will be closed and its remote UPSes will stop appearing in Main WebNUT.`
            : ""
        }
        confirmLabel="Remove Node"
        busy={deletingNode}
        onConfirm={confirmDeleteNode}
        onCancel={() => setDeleteNodeTarget(null)}
      />

      <ConfirmDialog
        open={revokeTokenTarget !== null}
        title="Revoke Join Token"
        message={
          revokeTokenTarget
            ? `Revoke token '${revokeTokenTarget.label || revokeTokenTarget.token_prefix}'? Any unpaired Repeater attempting to use it will be rejected.`
            : ""
        }
        confirmLabel="Revoke Token"
        busy={revokingToken}
        onConfirm={confirmRevokeToken}
        onCancel={() => setRevokeTokenTarget(null)}
      />
    </div>
  );
}
