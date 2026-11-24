import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw, Search, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { SeverityBadge } from "../components/StatusBadge";
import type { EventLogEntry } from "../types";

export function EventsPage() {
  const [items, setItems] = useState<EventLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [category, setCategory] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const limit = 30;

  const loadEvents = useCallback(async () => {
    const res = await api.getEvents({ category, severity, search, limit, offset });
    setItems(res.items);
    setTotal(res.total);
  }, [category, severity, search, offset]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  const handleClear = async () => {
    await api.clearEvents();
    setOffset(0);
    await loadEvents();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">Event &amp; Audit Log</h2>
          <p className="text-sm text-slate-400">
            Timeline of UPS power transitions, NUT config reloads, WoL packets, and notifications
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void loadEvents()}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
          <button
            onClick={handleClear}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-xs font-medium text-rose-300"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear Log
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-slate-800 bg-surface-900 p-4 grid grid-cols-1 sm:grid-cols-4 gap-3">
        <div className="sm:col-span-2 relative">
          <Search className="h-4 w-4 text-slate-500 absolute left-3 top-2.5" />
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setOffset(0);
              setSearch(e.target.value);
            }}
            placeholder="Search messages, event types, details..."
            className="w-full rounded-lg bg-surface-950 border border-slate-700 pl-9 pr-3 py-2 text-sm text-white"
          />
        </div>
        <select
          value={category}
          onChange={(e) => {
            setOffset(0);
            setCategory(e.target.value);
          }}
          className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-slate-200"
        >
          <option value="all">All Categories</option>
          <option value="ups">UPS</option>
          <option value="orchestrator">Orchestrator</option>
          <option value="wol">Wake-on-LAN</option>
          <option value="nut_config">NUT Config</option>
          <option value="nut_service">NUT Services</option>
          <option value="notification">Notifications</option>
          <option value="hosts">Hosts</option>
          <option value="system">System</option>
        </select>
        <select
          value={severity}
          onChange={(e) => {
            setOffset(0);
            setSeverity(e.target.value);
          }}
          className="rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-slate-200"
        >
          <option value="all">All Severities</option>
          <option value="info">Info</option>
          <option value="success">Success</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
        </select>
      </div>

      {/* Table */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="divide-y divide-slate-800/70">
          {items.map((ev) => {
            const expanded = expandedId === ev.id;
            return (
              <div key={ev.id} className="p-4 hover:bg-surface-800/30">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 min-w-0">
                    <button
                      onClick={() => setExpandedId(expanded ? null : ev.id)}
                      className="mt-0.5 text-slate-500 hover:text-slate-200"
                      disabled={!ev.details}
                    >
                      {ev.details ? (
                        expanded ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )
                      ) : (
                        <span className="inline-block w-4" />
                      )}
                    </button>
                    <SeverityBadge severity={ev.severity} />
                    <div className="min-w-0">
                      <p className="text-sm text-slate-100">{ev.message}</p>
                      <p className="text-xs text-slate-500 font-mono mt-0.5">
                        {ev.category} • {ev.event_type}
                      </p>
                    </div>
                  </div>
                  <span className="text-xs text-slate-400 font-mono shrink-0">
                    {new Date(ev.timestamp).toLocaleString()}
                  </span>
                </div>
                {expanded && ev.details && (
                  <pre className="mt-3 ml-7 p-3 rounded-lg bg-surface-950 border border-slate-800 text-xs text-slate-300 font-mono overflow-x-auto">
                    {JSON.stringify(ev.details, null, 2)}
                  </pre>
                )}
              </div>
            );
          })}
          {items.length === 0 && (
            <div className="p-8 text-center text-sm text-slate-500">
              No matching events found.
            </div>
          )}
        </div>

        {total > limit && (
          <div className="px-4 py-3 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
            <span>
              Showing {offset + 1}–{Math.min(offset + limit, total)} of {total}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setOffset(Math.max(0, offset - limit))}
                disabled={offset === 0}
                className="px-3 py-1 rounded bg-surface-800 border border-slate-700 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                onClick={() => setOffset(offset + limit)}
                disabled={offset + limit >= total}
                className="px-3 py-1 rounded bg-surface-800 border border-slate-700 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
