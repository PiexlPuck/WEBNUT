import { useEffect, useState } from "react";
import { Bell, CheckCircle2, Send, ShieldAlert } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { NotificationRuleItem, PushoverConfigPayload } from "../types";

const EVENT_LABELS: Record<string, { label: string; desc: string }> = {
  ups_on_battery: {
    label: "UPS On Battery (OB)",
    desc: "Utility power lost; UPS switched to battery power",
  },
  ups_power_restored: {
    label: "Power Restored (OL)",
    desc: "Utility power returned while monitoring outage",
  },
  ups_low_battery: {
    label: "UPS Low Battery (LB)",
    desc: "UPS battery charge reached critical low-battery flag",
  },
  ups_comms_lost: {
    label: "UPS Comms Lost",
    desc: "Connection to upsd or UPS driver lost (COMMBAD/NOCOMM)",
  },
  wake_sequence_started: {
    label: "Wake Sequence Initiated",
    desc: "Safe battery threshold & hold met; starting WoL recovery",
  },
  host_wake_succeeded: {
    label: "Host Wake Succeeded",
    desc: "Managed host booted and passed Ping/TCP verification",
  },
  host_wake_retrying: {
    label: "Host Wake Retrying",
    desc: "Host timed out after WoL packet; automatic retry scheduled",
  },
  host_wake_failed: {
    label: "Host Wake Failed (Exhausted)",
    desc: "All WoL retry attempts exhausted; manual intervention needed",
  },
};

const PRIORITIES = [
  { value: -2, label: "-2 Lowest (No alert)" },
  { value: -1, label: "-1 Low (Quiet)" },
  { value: 0, label: "0 Normal" },
  { value: 1, label: "1 High (Bypass quiet hours)" },
  { value: 2, label: "2 Emergency (Requires ACK)" },
];

export function NotificationsPage() {
  const [form, setForm] = useState<PushoverConfigPayload>({
    enabled: false,
    user_key: "",
    api_token: "",
    device: "",
    sound: "",
    emergency_retry_sec: 60,
    emergency_expire_sec: 3600,
  });
  const [rules, setRules] = useState<NotificationRuleItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  useEffect(() => {
    void (async () => {
      const settings = await api.getNotificationSettings();
      setForm({
        enabled: settings.pushover.enabled,
        user_key: settings.pushover.user_key,
        api_token: settings.pushover.api_token,
        device: settings.pushover.device,
        sound: settings.pushover.sound,
        emergency_retry_sec: settings.pushover.emergency_retry_sec,
        emergency_expire_sec: settings.pushover.emergency_expire_sec,
      });
      setRules(settings.rules);
    })();
  }, []);

  const handleSaveAll = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      await api.updatePushover(form);
      await api.updateNotificationRules(rules);
      setFeedback({ ok: true, msg: "Notification settings and event rules saved." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save settings",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setFeedback(null);
    try {
      await api.updatePushover(form);
      await api.sendTestNotification(0);
      setFeedback({ ok: true, msg: "Test notification delivered via Pushover." });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Test notification failed",
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">Notifications</h2>
          <p className="text-sm text-slate-400">
            Configure Pushover credentials, emergency retry parameters, and per-event priorities
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleTest}
            disabled={testing}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-sm font-medium text-slate-200 disabled:opacity-50"
          >
            <Send className="h-4 w-4 text-sky-400" />
            {testing ? "Sending..." : "Send Test Notification"}
          </button>
          <button
            onClick={handleSaveAll}
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white disabled:opacity-50"
          >
            <CheckCircle2 className="h-4 w-4" />
            {saving ? "Saving..." : "Save Settings"}
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

      {/* Pushover Config */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2.5">
            <Bell className="h-5 w-5 text-indigo-400" />
            <div>
              <h3 className="text-base font-semibold text-white">Pushover Provider</h3>
              <p className="text-xs text-slate-400">
                Credentials are stored securely in `/app/data/app.db` and masked after saving
              </p>
            </div>
          </div>
          <label className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="rounded border-slate-600 bg-surface-950 text-indigo-500"
            />
            Enabled
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">User Key</label>
            <input
              type="password"
              value={form.user_key}
              onChange={(e) => setForm({ ...form, user_key: e.target.value })}
              placeholder="Pushover User/Group Key"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">API Token</label>
            <input
              type="password"
              value={form.api_token}
              onChange={(e) => setForm({ ...form, api_token: e.target.value })}
              placeholder="Pushover Application API Token"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              Device (Optional)
            </label>
            <input
              type="text"
              value={form.device}
              onChange={(e) => setForm({ ...form, device: e.target.value })}
              placeholder="All devices if blank"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              Sound (Optional)
            </label>
            <input
              type="text"
              value={form.sound}
              onChange={(e) => setForm({ ...form, sound: e.target.value })}
              placeholder="e.g., pushover, siren, spacealarm"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              Emergency Retry Interval (sec, min 30)
            </label>
            <input
              type="number"
              min={30}
              max={3600}
              value={form.emergency_retry_sec}
              onChange={(e) =>
                setForm({ ...form, emergency_retry_sec: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              Emergency Expire Duration (sec, max 10800)
            </label>
            <input
              type="number"
              min={60}
              max={10800}
              value={form.emergency_expire_sec}
              onChange={(e) =>
                setForm({ ...form, emergency_expire_sec: Number(e.target.value) })
              }
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
        </div>
      </div>

      {/* Event Rules */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-amber-400" />
          <h3 className="text-base font-semibold text-white">Event Toggles &amp; Priorities</h3>
        </div>
        <div className="divide-y divide-slate-800/70">
          {rules.map((rule) => {
            const meta = EVENT_LABELS[rule.event_type] ?? {
              label: rule.event_type,
              desc: "",
            };
            return (
              <div
                key={rule.event_type}
                className="px-5 py-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
              >
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(e) =>
                      setRules((prev) =>
                        prev.map((r) =>
                          r.event_type === rule.event_type
                            ? { ...r, enabled: e.target.checked }
                            : r
                        )
                      )
                    }
                    className="mt-1 rounded border-slate-600 bg-surface-950 text-indigo-500"
                  />
                  <div>
                    <div className="text-sm font-medium text-white">{meta.label}</div>
                    <div className="text-xs text-slate-400">{meta.desc}</div>
                  </div>
                </label>

                <select
                  value={rule.priority}
                  onChange={(e) =>
                    setRules((prev) =>
                      prev.map((r) =>
                        r.event_type === rule.event_type
                          ? { ...r, priority: Number(e.target.value) }
                          : r
                      )
                    )
                  }
                  className="bg-surface-950 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 sm:w-56"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
