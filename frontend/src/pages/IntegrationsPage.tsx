import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BatteryCharging,
  Check,
  CheckCircle2,
  Code2,
  Copy,
  Download,
  Eye,
  EyeOff,
  KeyRound,
  LayoutGrid,
  LineChart,
  Play,
  Power,
  RefreshCw,
  Server,
  Shield,
  Trash2,
  Zap,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { copyToClipboard } from "../utils/clipboard";
import type {
  HomeAssistantState,
  HomepageConfigRequest,
  HomepageConfigResponse,
  HomepageDisplayMode,
  HomepageFieldOption,
  HomepagePreset,
  HomepageSummary,
  HostRecord,
  IntegrationSettingsView,
  OrchestratorState,
  PrometheusConfigRequest,
  PrometheusConfigResponse,
  TelemetrySnapshot,
} from "../types";

interface IntegrationsPageProps {
  instanceName: string;
  telemetry: TelemetrySnapshot;
  hosts: HostRecord[];
  orchestrator: OrchestratorState;
}

type IntegrationTab = "homepage" | "homeassistant" | "grafana";

const PRESETS: { id: HomepagePreset; label: string; desc: string; defaultDisplay: HomepageDisplayMode }[] = [
  { id: "power", label: "Power & Battery", desc: "Status, Battery, Load, Runtime", defaultDisplay: "block" },
  { id: "homelab", label: "Homelab Overview", desc: "Status, Battery, Load, Hosts Online", defaultDisplay: "block" },
  { id: "recovery", label: "Power & Recovery", desc: "Status, Recovery, Hosts, Clients", defaultDisplay: "block" },
  { id: "detailed", label: "Detailed List", desc: "9-metric vertical list view", defaultDisplay: "list" },
  { id: "custom", label: "Custom", desc: "Pick your own metrics", defaultDisplay: "block" },
];

const PRESET_FIELDS: Record<Exclude<HomepagePreset, "custom">, string[]> = {
  power: ["ups_status_label", "battery_charge", "ups_load", "battery_runtime_sec"],
  homelab: ["ups_status_label", "battery_charge", "ups_load", "hosts_summary"],
  recovery: ["ups_status_label", "orchestrator_state_label", "hosts_summary", "clients_connected"],
  detailed: [
    "ups_status_label",
    "battery_charge",
    "ups_load",
    "battery_runtime_sec",
    "realpower_watts",
    "input_voltage",
    "orchestrator_state_label",
    "hosts_summary",
    "clients_connected",
  ],
};

function formatHomepageValue(summary: HomepageSummary, option: HomepageFieldOption): string {
  const raw = (summary as unknown as Record<string, unknown>)[option.field];
  if (raw === null || raw === undefined || raw === "") {
    return "—";
  }
  if (option.format === "duration") {
    const totalSec = Math.max(0, Math.round(Number(raw)));
    const hrs = Math.floor(totalSec / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    const secs = totalSec % 60;
    if (hrs > 0) return `${hrs}h ${mins}m`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  }
  if (option.format === "percent") {
    return `${Number(raw).toFixed(0)}%`;
  }
  if (option.format === "float") {
    return `${Number(raw).toFixed(1)}${option.suffix ?? ""}`;
  }
  if (option.format === "number") {
    return `${Math.round(Number(raw))}${option.suffix ?? ""}`;
  }
  return `${String(raw)}${option.suffix ?? ""}`;
}

interface ApiKeyBarProps {
  settings: IntegrationSettingsView | null;
  revealedKey: string;
  showKeyText: boolean;
  keyBusy: boolean;
  copiedId: string | null;
  onRotate: () => void;
  onToggleReveal: () => void;
  onCopy: (id: string, text: string) => void;
  onRevoke: () => void;
}

function ApiKeyBar({
  settings,
  revealedKey,
  showKeyText,
  keyBusy,
  copiedId,
  onRotate,
  onToggleReveal,
  onCopy,
  onRevoke,
}: ApiKeyBarProps) {
  return (
    <div className="rounded-xl bg-surface-950 border border-slate-800 p-4 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
      <div className="flex items-center gap-3 min-w-0">
        <KeyRound className="h-4 w-4 text-amber-400 shrink-0" />
        <div className="min-w-0">
          <div className="text-xs font-medium text-slate-300">Shared Integration API Key</div>
          <div className="text-xs font-mono text-slate-400 truncate mt-0.5">
            {showKeyText && revealedKey
              ? revealedKey
              : settings?.api_key_configured
              ? settings.api_key_preview
              : "No API key generated yet (optional for trusted LANs)"}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={onRotate}
          disabled={keyBusy}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/40 text-xs font-medium text-indigo-200 disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${keyBusy ? "animate-spin" : ""}`} />
          {settings?.api_key_configured ? "Rotate Key" : "Generate Key"}
        </button>
        {settings?.api_key_configured && (
          <>
            <button
              onClick={onToggleReveal}
              disabled={keyBusy}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
            >
              {showKeyText ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              {showKeyText ? "Hide" : "Reveal"}
            </button>
            {revealedKey && (
              <button
                onClick={() => onCopy("api_key", revealedKey)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
              >
                {copiedId === "api_key" ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                {copiedId === "api_key" ? "Copied" : "Copy Key"}
              </button>
            )}
            <button
              onClick={onRevoke}
              disabled={keyBusy}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-xs text-rose-300"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Revoke
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function IntegrationsPage({ instanceName, telemetry, hosts, orchestrator }: IntegrationsPageProps) {
  const [activeIntegration, setActiveIntegration] = useState<IntegrationTab>("homepage");
  const [settings, setSettings] = useState<IntegrationSettingsView | null>(null);
  const [fields, setFields] = useState<HomepageFieldOption[]>([]);
  const [preview, setPreview] = useState<HomepageSummary | null>(null);
  const [generated, setGenerated] = useState<HomepageConfigResponse | null>(null);
  const [haPreview, setHaPreview] = useState<HomeAssistantState | null>(null);
  const [promGenerated, setPromGenerated] = useState<PrometheusConfigResponse | null>(null);

  // Settings form state
  const [homepageEnabled, setHomepageEnabled] = useState(true);
  const [homeassistantEnabled, setHomeassistantEnabled] = useState(true);
  const [prometheusEnabled, setPrometheusEnabled] = useState(true);
  const [apiKeyEnabled, setApiKeyEnabled] = useState(false);
  const [defaultUps, setDefaultUps] = useState("");
  const [defaultRefreshMs, setDefaultRefreshMs] = useState(10000);

  // API key state
  const [revealedKey, setRevealedKey] = useState<string>("");
  const [showKeyText, setShowKeyText] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const [testingEndpoint, setTestingEndpoint] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testingHaEndpoint, setTestingHaEndpoint] = useState(false);
  const [haTestResult, setHaTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testingPromEndpoint, setTestingPromEndpoint] = useState(false);
  const [promTestResult, setPromTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const lastAutoServiceName = useRef(instanceName || "WebNUT");

  // Generator form state
  const [form, setForm] = useState<HomepageConfigRequest>({
    service_name: instanceName || "WebNUT",
    description: "UPS & Power Orchestrator",
    icon: "mdi-battery-charging",
    base_url: typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8765",
    href_url: "",
    ups_name: "",
    preset: "power",
    display: "block",
    selected_fields: PRESET_FIELDS.power,
    refresh_interval_ms: 10000,
    reveal_api_key: false,
  });
  const [promForm, setPromForm] = useState<PrometheusConfigRequest>({
    job_name: "webnut",
    target_host: "",
    scheme: "http",
    scrape_interval_sec: 15,
    metrics_path: "/metrics",
    reveal_api_key: false,
  });
  const [showFieldPicker, setShowFieldPicker] = useState(false);
  const [showRawJson, setShowRawJson] = useState(false);
  const [showHaRawJson, setShowHaRawJson] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const upsChoices = useMemo(() => {
    const set = new Set<string>(settings?.ups_names ?? []);
    for (const u of telemetry.ups_list) {
      if (u.name) set.add(u.name);
    }
    return Array.from(set);
  }, [settings?.ups_names, telemetry.ups_list]);

  const haHostAddress = useMemo(() => {
    if (settings?.server_url) {
      try {
        return new URL(settings.server_url).hostname;
      } catch {
        // fallback below
      }
    }
    return typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";
  }, [settings?.server_url]);

  const loadAll = useCallback(async () => {
    try {
      const [cfg, catalog] = await Promise.all([
        api.getIntegrationSettings(),
        api.getHomepageFields(),
      ]);
      setSettings(cfg);
      setFields(catalog);
      setHomepageEnabled(cfg.homepage_enabled);
      setHomeassistantEnabled(cfg.homeassistant_enabled);
      setPrometheusEnabled(cfg.prometheus_enabled);
      setApiKeyEnabled(cfg.api_key_enabled);
      setDefaultUps(cfg.homepage_default_ups);
      setDefaultRefreshMs(cfg.homepage_refresh_ms);
      setForm((prev) => ({
        ...prev,
        base_url:
          prev.base_url && !prev.base_url.includes("localhost")
            ? prev.base_url
            : cfg.server_url || prev.base_url,
        refresh_interval_ms: cfg.homepage_refresh_ms,
      }));
      if (cfg.server_url) {
        try {
          const parsed = new URL(cfg.server_url);
          setPromForm((prev) => ({
            ...prev,
            target_host: prev.target_host || `${parsed.hostname}:${parsed.port || cfg.web_port}`,
            scheme: parsed.protocol === "https:" ? "https" : "http",
          }));
        } catch {
          // ignore
        }
      }
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to load integration settings",
      });
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const refreshPreviewAndYaml = useCallback(async (req: HomepageConfigRequest) => {
    try {
      const [prevPayload, genPayload] = await Promise.all([
        api.getHomepagePreview(req.ups_name),
        api.generateHomepageConfig(req),
      ]);
      setPreview(prevPayload);
      setGenerated(genPayload);
    } catch {
      // Keep previous preview if transient error occurs while typing
    }
  }, []);

  const refreshHaPreview = useCallback(async () => {
    try {
      const payload = await api.getHomeAssistantPreview();
      setHaPreview(payload);
    } catch {
      // Keep previous preview if transient error occurs
    }
  }, []);

  const refreshPromPreview = useCallback(async (req: PrometheusConfigRequest) => {
    try {
      const payload = await api.generatePrometheusConfig(req);
      setPromGenerated(payload);
    } catch {
      // Keep previous preview if transient error occurs
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshPreviewAndYaml(form);
    }, 150);
    return () => window.clearTimeout(timer);
  }, [form, refreshPreviewAndYaml, telemetry.updated_at, hosts.length, orchestrator.state]);

  useEffect(() => {
    const clean = instanceName || "WebNUT";
    setForm((prev) => {
      if (prev.service_name === lastAutoServiceName.current || !prev.service_name.trim()) {
        return { ...prev, service_name: clean };
      }
      return prev;
    });
    lastAutoServiceName.current = clean;
  }, [instanceName]);

  useEffect(() => {
    if (activeIntegration === "homeassistant") {
      void refreshHaPreview();
    }
  }, [activeIntegration, refreshHaPreview, telemetry.updated_at, hosts.length, orchestrator.state, instanceName]);

  useEffect(() => {
    if (activeIntegration !== "grafana") return;
    const timer = window.setTimeout(() => {
      void refreshPromPreview(promForm);
    }, 150);
    return () => window.clearTimeout(timer);
  }, [activeIntegration, promForm, refreshPromPreview, telemetry.updated_at, hosts.length, orchestrator.state, instanceName]);

  const copyText = async (id: string, text: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopiedId(id);
      window.setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 2000);
    }
  };

  const resolveApiKeyIfEnabled = async (): Promise<string> => {
    if (!settings?.api_key_enabled) return "";
    if (revealedKey) return revealedKey;
    const revealed = await api.revealIntegrationApiKey();
    setRevealedKey(revealed.api_key);
    return revealed.api_key;
  };

  const handleSaveSettings = async () => {
    setSavingSettings(true);
    setFeedback(null);
    try {
      const updated = await api.updateIntegrationSettings({
        api_key_enabled: apiKeyEnabled,
        homepage_enabled: homepageEnabled,
        homepage_default_ups: defaultUps,
        homepage_refresh_ms: defaultRefreshMs,
        homeassistant_enabled: homeassistantEnabled,
        prometheus_enabled: prometheusEnabled,
      });
      setSettings(updated);
      setApiKeyEnabled(updated.api_key_enabled);
      setHomepageEnabled(updated.homepage_enabled);
      setHomeassistantEnabled(updated.homeassistant_enabled);
      setPrometheusEnabled(updated.prometheus_enabled);
      if (!updated.api_key_configured) {
        setRevealedKey("");
        setShowKeyText(false);
      }
      setFeedback({ ok: true, msg: "Integration settings saved." });
      await Promise.all([
        refreshPreviewAndYaml(form),
        refreshHaPreview(),
        refreshPromPreview(promForm),
      ]);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save integration settings",
      });
    } finally {
      setSavingSettings(false);
    }
  };

  const handleRotateKey = async () => {
    setKeyBusy(true);
    setFeedback(null);
    try {
      const res = await api.rotateIntegrationApiKey();
      setRevealedKey(res.api_key);
      setShowKeyText(true);
      setApiKeyEnabled(res.api_key_enabled);
      const updated = await api.getIntegrationSettings();
      setSettings(updated);
      setFeedback({
        ok: true,
        msg: "Generated new integration API key and enabled X-API-Key authentication.",
      });
      await refreshPreviewAndYaml(form);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to rotate API key",
      });
    } finally {
      setKeyBusy(false);
    }
  };

  const handleToggleRevealKey = async () => {
    if (showKeyText && revealedKey) {
      setShowKeyText(false);
      return;
    }
    if (revealedKey) {
      setShowKeyText(true);
      return;
    }
    setKeyBusy(true);
    try {
      const res = await api.revealIntegrationApiKey();
      setRevealedKey(res.api_key);
      setShowKeyText(true);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "No API key configured yet",
      });
    } finally {
      setKeyBusy(false);
    }
  };

  const handleRevokeKey = async () => {
    setKeyBusy(true);
    setFeedback(null);
    try {
      await api.revokeIntegrationApiKey();
      setRevealedKey("");
      setShowKeyText(false);
      setApiKeyEnabled(false);
      setForm((prev) => ({ ...prev, reveal_api_key: false }));
      setPromForm((prev) => ({ ...prev, reveal_api_key: false }));
      const updated = await api.getIntegrationSettings();
      setSettings(updated);
      setFeedback({ ok: true, msg: "API key revoked and authentication disabled." });
      await Promise.all([
        refreshPreviewAndYaml({ ...form, reveal_api_key: false }),
        refreshPromPreview({ ...promForm, reveal_api_key: false }),
      ]);
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to revoke API key",
      });
    } finally {
      setKeyBusy(false);
    }
  };

  const handleTestEndpoint = async () => {
    setTestingEndpoint(true);
    setTestResult(null);
    const started = performance.now();
    try {
      const keyToUse = await resolveApiKeyIfEnabled();
      const res = await api.testHomepageSummary(form.ups_name, keyToUse);
      const ms = Math.round(performance.now() - started);
      setTestResult({
        ok: true,
        msg: `HTTP 200 OK (${ms} ms) — ${res.ups_status_label}${res.battery_charge !== null ? `, Battery ${res.battery_charge}%` : ""}, Hosts ${res.hosts_summary}`,
      });
    } catch (err) {
      const ms = Math.round(performance.now() - started);
      setTestResult({
        ok: false,
        msg: `${err instanceof ApiError ? `HTTP ${err.status}: ${err.message}` : "Endpoint test failed"} (${ms} ms)`,
      });
    } finally {
      setTestingEndpoint(false);
    }
  };

  const handleTestHaEndpoint = async () => {
    setTestingHaEndpoint(true);
    setHaTestResult(null);
    const started = performance.now();
    try {
      const keyToUse = await resolveApiKeyIfEnabled();
      const res = await api.testHomeAssistantInfo(keyToUse);
      const ms = Math.round(performance.now() - started);
      setHaTestResult({
        ok: true,
        msg: `HTTP 200 OK (${ms} ms) — ${res.name || instanceName || "WebNUT"} v${res.version}, Instance ${res.instance_id}, API Key ${res.api_key_required ? "Required" : "Optional"}`,
      });
    } catch (err) {
      const ms = Math.round(performance.now() - started);
      setHaTestResult({
        ok: false,
        msg: `${err instanceof ApiError ? `HTTP ${err.status}: ${err.message}` : "HA endpoint test failed"} (${ms} ms)`,
      });
    } finally {
      setTestingHaEndpoint(false);
    }
  };

  const handleTestPromEndpoint = async () => {
    setTestingPromEndpoint(true);
    setPromTestResult(null);
    const started = performance.now();
    try {
      const keyToUse = await resolveApiKeyIfEnabled();
      const body = await api.testPrometheusMetrics(keyToUse);
      const ms = Math.round(performance.now() - started);
      const sampleCount = body
        .split("\n")
        .filter((line) => line && !line.startsWith("#")).length;
      setPromTestResult({
        ok: true,
        msg: `HTTP 200 OK (${ms} ms) — ${sampleCount} Prometheus metric sample(s) exposed`,
      });
    } catch (err) {
      const ms = Math.round(performance.now() - started);
      setPromTestResult({
        ok: false,
        msg: `${err instanceof ApiError ? `HTTP ${err.status}: ${err.message}` : "Prometheus /metrics test failed"} (${ms} ms)`,
      });
    } finally {
      setTestingPromEndpoint(false);
    }
  };

  const selectPreset = (presetId: HomepagePreset) => {
    const presetMeta = PRESETS.find((p) => p.id === presetId);
    setForm((prev) => ({
      ...prev,
      preset: presetId,
      display: presetMeta ? presetMeta.defaultDisplay : prev.display,
      selected_fields:
        presetId === "custom"
          ? prev.selected_fields.length > 0
            ? prev.selected_fields
            : PRESET_FIELDS.power
          : PRESET_FIELDS[presetId],
    }));
    if (presetId === "custom") {
      setShowFieldPicker(true);
    }
  };

  const toggleField = (fieldKey: string) => {
    setForm((prev) => {
      const exists = prev.selected_fields.includes(fieldKey);
      const nextFields = exists
        ? prev.selected_fields.filter((f) => f !== fieldKey)
        : [...prev.selected_fields, fieldKey];
      return {
        ...prev,
        preset: "custom",
        selected_fields: nextFields,
      };
    });
  };

  const activeMappings = useMemo(() => {
    if (generated?.mappings?.length) return generated.mappings;
    const keys = form.preset === "custom" ? form.selected_fields : PRESET_FIELDS[form.preset];
    return keys
      .map((k) => fields.find((f) => f.field === k))
      .filter((f): f is HomepageFieldOption => Boolean(f));
  }, [generated?.mappings, form.preset, form.selected_fields, fields]);

  const activeSummary = preview ?? generated?.sample_payload ?? null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">Integrations</h2>
          <p className="text-sm text-slate-400">
            Connect WebNUT telemetry and host recovery status to external homelab dashboards &amp; view tools
          </p>
        </div>
        <button
          onClick={() => void loadAll()}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 self-start sm:self-auto"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
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

      {/* Integration Selector Strip */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <button
          type="button"
          onClick={() => setActiveIntegration("homepage")}
          className={`text-left rounded-2xl border p-4 flex items-start justify-between gap-3 transition-colors ${
            activeIntegration === "homepage"
              ? "border-indigo-500/60 bg-indigo-500/10"
              : "border-slate-800 bg-surface-900 hover:border-slate-700"
          }`}
        >
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-xl bg-indigo-500/20 border border-indigo-400/30 flex items-center justify-center text-indigo-300 shrink-0">
              <LayoutGrid className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white">Homepage (gethomepage)</h3>
                <span
                  className={`text-[11px] px-2 py-0.5 rounded-full border font-medium ${
                    homepageEnabled
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                      : "bg-slate-800 text-slate-400 border-slate-700"
                  }`}
                >
                  {homepageEnabled ? "Active" : "Disabled"}
                </span>
              </div>
              <p className="text-xs text-slate-300 mt-1">
                Flat <code className="text-indigo-300">customapi</code> summary endpoint + live widget preview &amp;{" "}
                <code className="text-indigo-300">services.yaml</code> generator
              </p>
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={() => setActiveIntegration("homeassistant")}
          className={`text-left rounded-2xl border p-4 flex items-start justify-between gap-3 transition-colors ${
            activeIntegration === "homeassistant"
              ? "border-indigo-500/60 bg-indigo-500/10"
              : "border-slate-800 bg-surface-900 hover:border-slate-700"
          }`}
        >
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-xl bg-surface-950 border border-indigo-500/30 flex items-center justify-center shrink-0 overflow-hidden p-1">
              <img src="/favicon.svg" alt="WebNUT" className="h-full w-full" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white">Home Assistant</h3>
                <span
                  className={`text-[11px] px-2 py-0.5 rounded-full border font-medium ${
                    homeassistantEnabled
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                      : "bg-slate-800 text-slate-400 border-slate-700"
                  }`}
                >
                  {homeassistantEnabled ? "Active" : "Disabled"}
                </span>
              </div>
              <p className="text-xs text-slate-300 mt-1">
                Custom integration with bundled WebNUT favicon brand icons, UPS sensors, managed hosts &amp; WoL buttons
              </p>
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={() => setActiveIntegration("grafana")}
          className={`text-left rounded-2xl border p-4 flex items-start justify-between gap-3 transition-colors ${
            activeIntegration === "grafana"
              ? "border-indigo-500/60 bg-indigo-500/10"
              : "border-slate-800 bg-surface-900 hover:border-slate-700"
          }`}
        >
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-xl bg-amber-500/15 border border-amber-400/30 flex items-center justify-center text-amber-300 shrink-0">
              <LineChart className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white">Grafana &amp; Prometheus</h3>
                <span
                  className={`text-[11px] px-2 py-0.5 rounded-full border font-medium ${
                    prometheusEnabled
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                      : "bg-slate-800 text-slate-400 border-slate-700"
                  }`}
                >
                  {prometheusEnabled ? "Active" : "Disabled"}
                </span>
              </div>
              <p className="text-xs text-slate-300 mt-1">
                Prometheus <code className="text-indigo-300">/metrics</code> exposition, scrape config generator &amp; Grafana dashboard JSON
              </p>
            </div>
          </div>
        </button>
      </div>

      {activeIntegration === "homepage" ? (
        <>
          {/* Widget API & Access Settings Card */}
          <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
              <div className="flex items-center gap-2.5">
                <Shield className="h-5 w-5 text-indigo-400" />
                <div>
                  <h3 className="text-base font-semibold text-white">Homepage Widget API &amp; Access</h3>
                  <p className="text-xs text-slate-400">
                    Read-only endpoint <code className="text-slate-300">/api/integrations/homepage/summary</code> with optional encrypted API key auth
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => void handleTestEndpoint()}
                  disabled={testingEndpoint}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
                >
                  <Play className="h-3.5 w-3.5 text-emerald-400" />
                  {testingEndpoint ? "Testing..." : "Test Endpoint"}
                </button>
                <button
                  onClick={() => void handleSaveSettings()}
                  disabled={savingSettings}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {savingSettings ? "Saving..." : "Save Settings"}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="flex items-start gap-3 p-3.5 rounded-xl bg-surface-950 border border-slate-800 cursor-pointer">
                <input
                  type="checkbox"
                  checked={homepageEnabled}
                  onChange={(e) => setHomepageEnabled(e.target.checked)}
                  className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">Enable Homepage Widget Endpoint</div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Serves flat UPS, orchestrator, and host counts at{" "}
                    <code className="text-indigo-300">/api/integrations/homepage/summary</code>
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-3 p-3.5 rounded-xl bg-surface-950 border border-slate-800 cursor-pointer">
                <input
                  type="checkbox"
                  checked={apiKeyEnabled}
                  onChange={(e) => setApiKeyEnabled(e.target.checked)}
                  className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">Require API Key (X-API-Key / Bearer)</div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Protects external integration endpoints with a Fernet-encrypted token when enabled
                  </div>
                </div>
              </label>
            </div>

            <ApiKeyBar
              settings={settings}
              revealedKey={revealedKey}
              showKeyText={showKeyText}
              keyBusy={keyBusy}
              copiedId={copiedId}
              onRotate={() => void handleRotateKey()}
              onToggleReveal={() => void handleToggleRevealKey()}
              onCopy={(id, text) => void copyText(id, text)}
              onRevoke={() => void handleRevokeKey()}
            />

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  Default Target UPS (when <code className="text-slate-400">?ups=</code> is omitted)
                </label>
                <select
                  value={defaultUps}
                  onChange={(e) => setDefaultUps(e.target.value)}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                >
                  <option value="">Auto (Orchestrator Monitored / First UPS)</option>
                  {upsChoices.map((u) => (
                    <option key={u} value={u}>
                      {u}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  Default Homepage Refresh Interval (ms)
                </label>
                <input
                  type="number"
                  min={1000}
                  max={300000}
                  step={1000}
                  value={defaultRefreshMs}
                  onChange={(e) => setDefaultRefreshMs(Number(e.target.value) || 10000)}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
                />
              </div>
            </div>

            {testResult && (
              <div
                className={`rounded-xl border px-4 py-2.5 text-xs font-mono ${
                  testResult.ok
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-300"
                }`}
              >
                {testResult.msg}
              </div>
            )}
          </div>

          {/* Live Preview + YAML Generator Grid */}
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
            {/* Live Homepage Widget Preview Card */}
            <div className="xl:col-span-5 space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-semibold text-white">Live Homepage Widget Preview</h3>
                    <p className="text-xs text-slate-400">
                      Simulated rendering of your Homepage service card ({form.display === "list" ? "Vertical List" : "Block Grid"})
                    </p>
                  </div>
                  <span
                    className={`text-[11px] px-2 py-0.5 rounded-full border font-mono ${
                      activeSummary?.connected
                        ? activeSummary.on_battery
                          ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
                          : "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                        : "bg-slate-800 text-slate-400 border-slate-700"
                    }`}
                  >
                    {activeSummary?.ups_status_label ?? "Loading..."}
                  </span>
                </div>

                {/* Simulated Homepage Card */}
                <div className="rounded-xl border border-slate-700/80 bg-[#121620] p-4 shadow-lg space-y-3">
                  <div className="flex items-center gap-3">
                    <div className="h-9 w-9 rounded-lg bg-indigo-500/20 border border-indigo-400/30 flex items-center justify-center text-indigo-300 shrink-0">
                      <Zap className="h-4 w-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-white truncate">
                        {form.service_name || instanceName || "WebNUT"}
                      </div>
                      {form.description && (
                        <div className="text-xs text-slate-400 truncate">{form.description}</div>
                      )}
                    </div>
                  </div>

                  {activeSummary && (
                    form.display === "list" ? (
                      <div className="divide-y divide-slate-800/80 rounded-lg bg-slate-900/70 border border-slate-800/80 px-3">
                        {activeMappings.map((m) => (
                          <div key={m.field} className="py-1.5 flex items-center justify-between text-xs">
                            <span className="text-slate-400 uppercase tracking-wider text-[10px] font-semibold">
                              {m.label}
                            </span>
                            <span className="font-mono font-semibold text-slate-100">
                              {formatHomepageValue(activeSummary, m)}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                        {activeMappings.map((m) => (
                          <div
                            key={m.field}
                            className="rounded-lg bg-slate-900/90 border border-slate-800/80 px-2.5 py-2 text-center"
                          >
                            <div className="text-xs font-mono font-semibold text-slate-100 truncate">
                              {formatHomepageValue(activeSummary, m)}
                            </div>
                            <div className="text-[10px] uppercase tracking-wider text-slate-400 mt-0.5 truncate">
                              {m.label}
                            </div>
                          </div>
                        ))}
                      </div>
                    )
                  )}
                </div>

                {/* Collapsible Raw JSON Payload */}
                <div className="border-t border-slate-800 pt-3">
                  <button
                    onClick={() => setShowRawJson((v) => !v)}
                    className="inline-flex items-center gap-1.5 text-xs text-indigo-300 hover:text-indigo-200 font-medium"
                  >
                    <Code2 className="h-3.5 w-3.5" />
                    {showRawJson ? "Hide Raw JSON Payload" : "View Raw JSON Payload"}
                  </button>
                  {showRawJson && activeSummary && (
                    <pre className="mt-2.5 rounded-xl bg-surface-950 border border-slate-800 p-3 text-[11px] font-mono text-slate-300 overflow-x-auto max-h-72">
                      {JSON.stringify(activeSummary, null, 2)}
                    </pre>
                  )}
                </div>
              </div>
            </div>

            {/* Homepage services.yaml Generator Card */}
            <div className="xl:col-span-7 rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-4">
                <div>
                  <h3 className="text-base font-semibold text-white">Homepage <code className="text-indigo-300">services.yaml</code> Generator</h3>
                  <p className="text-xs text-slate-400">
                    Customize the widget preset, URL, and metrics, then paste directly into Homepage
                  </p>
                </div>
                {generated && (
                  <button
                    onClick={() => void copyText("yaml", generated.yaml)}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white shrink-0"
                  >
                    {copiedId === "yaml" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copiedId === "yaml" ? "Copied YAML" : "Copy services.yaml"}
                  </button>
                )}
              </div>

              {/* Preset Buttons */}
              <div>
                <div className="text-xs font-medium text-slate-300 mb-2">Widget Preset</div>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
                  {PRESETS.map((p) => {
                    const active = form.preset === p.id;
                    return (
                      <button
                        key={p.id}
                        onClick={() => selectPreset(p.id)}
                        className={`text-left rounded-xl border p-2.5 transition-colors ${
                          active
                            ? "border-indigo-500 bg-indigo-600/20 text-white"
                            : "border-slate-800 bg-surface-950 text-slate-300 hover:border-slate-700"
                        }`}
                      >
                        <div className="text-xs font-semibold">{p.label}</div>
                        <div className="text-[10px] text-slate-400 mt-0.5 leading-tight">{p.desc}</div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">Service Name</label>
                  <input
                    type="text"
                    value={form.service_name}
                    onChange={(e) => setForm({ ...form, service_name: e.target.value })}
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">Icon (Homepage / MDI)</label>
                  <input
                    type="text"
                    value={form.icon}
                    onChange={(e) => setForm({ ...form, icon: e.target.value })}
                    placeholder="mdi-battery-charging"
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white font-mono"
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs font-medium text-slate-300 mb-1">Description</label>
                  <input
                    type="text"
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">
                    WebNUT Base URL (for Homepage widget)
                  </label>
                  <input
                    type="text"
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    placeholder="http://192.168.1.10:8765"
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">
                    Click-Through <code className="text-slate-400">href</code> URL (Optional)
                  </label>
                  <input
                    type="text"
                    value={form.href_url}
                    onChange={(e) => setForm({ ...form, href_url: e.target.value })}
                    placeholder="Defaults to Base URL"
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">Target UPS</label>
                  <select
                    value={form.ups_name}
                    onChange={(e) => setForm({ ...form, ups_name: e.target.value })}
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white"
                  >
                    <option value="">Default UPS</option>
                    {upsChoices.map((u) => (
                      <option key={u} value={u}>
                        {u}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs font-medium text-slate-300 mb-1">Display Mode</label>
                    <select
                      value={form.display}
                      onChange={(e) =>
                        setForm({ ...form, display: e.target.value as HomepageDisplayMode })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-sm text-white"
                    >
                      <option value="block">Block Grid</option>
                      <option value="list">Vertical List</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-300 mb-1">Refresh (ms)</label>
                    <input
                      type="number"
                      min={1000}
                      max={300000}
                      step={1000}
                      value={form.refresh_interval_ms}
                      onChange={(e) =>
                        setForm({ ...form, refresh_interval_ms: Number(e.target.value) || 10000 })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-sm text-white"
                    />
                  </div>
                </div>
              </div>

              {/* Field Picker Toggle & Checkboxes */}
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <button
                    onClick={() => setShowFieldPicker((v) => !v)}
                    className="text-xs text-indigo-300 hover:text-indigo-200 font-medium"
                  >
                    {showFieldPicker ? "Hide Metric Field Picker" : "Customize Metric Fields"} (
                    {activeMappings.length} selected)
                  </button>
                  {settings?.api_key_enabled && (
                    <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={form.reveal_api_key}
                        onChange={(e) => setForm({ ...form, reveal_api_key: e.target.checked })}
                        className="rounded border-slate-600 bg-surface-950 text-indigo-500"
                      />
                      Embed actual API key in YAML
                    </label>
                  )}
                </div>

                {(showFieldPicker || form.preset === "custom") && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 rounded-xl bg-surface-950 border border-slate-800 p-3">
                    {fields.map((f) => {
                      const checked = form.selected_fields.includes(f.field);
                      return (
                        <label
                          key={f.field}
                          className="flex items-start gap-2 text-xs cursor-pointer p-1.5 rounded hover:bg-surface-900"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleField(f.field)}
                            className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                          />
                          <div>
                            <span className="font-medium text-slate-200">{f.label}</span>{" "}
                            <code className="text-[10px] text-indigo-300">{f.field}</code>
                            <div className="text-[11px] text-slate-400">{f.description}</div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Generated YAML Output */}
              {generated && (
                <div className="space-y-2.5">
                  <pre className="rounded-xl bg-surface-950 border border-slate-800 p-4 text-xs font-mono text-emerald-300 overflow-x-auto leading-relaxed">
                    {generated.yaml}
                  </pre>
                  {generated.notes.length > 0 && (
                    <ul className="space-y-1 text-xs text-slate-400 list-disc pl-5">
                      {generated.notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      ) : activeIntegration === "homeassistant" ? (
        <>
          {/* Home Assistant Access & Download Card */}
          <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
              <div className="flex items-center gap-2.5">
                <Shield className="h-5 w-5 text-indigo-400" />
                <div>
                  <h3 className="text-base font-semibold text-white">Home Assistant Integration &amp; Access</h3>
                  <p className="text-xs text-slate-400">
                    Serves UPS telemetry, orchestrator status, and Wake-on-LAN actions at{" "}
                    <code className="text-slate-300">/api/integrations/homeassistant/*</code>
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href="/api/integrations/homeassistant/package.zip"
                  download="webnut-homeassistant.zip"
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/40 text-xs font-medium text-emerald-200"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download Integration ZIP
                </a>
                <button
                  onClick={() => void handleTestHaEndpoint()}
                  disabled={testingHaEndpoint}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
                >
                  <Play className="h-3.5 w-3.5 text-emerald-400" />
                  {testingHaEndpoint ? "Testing..." : "Test HA Endpoint"}
                </button>
                <button
                  onClick={() => void handleSaveSettings()}
                  disabled={savingSettings}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {savingSettings ? "Saving..." : "Save Settings"}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="flex items-start gap-3 p-3.5 rounded-xl bg-surface-950 border border-slate-800 cursor-pointer">
                <input
                  type="checkbox"
                  checked={homeassistantEnabled}
                  onChange={(e) => setHomeassistantEnabled(e.target.checked)}
                  className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">Enable Home Assistant Integration API</div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Allows Home Assistant to poll UPS/host state and trigger Wake-on-LAN buttons
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-3 p-3.5 rounded-xl bg-surface-950 border border-slate-800 cursor-pointer">
                <input
                  type="checkbox"
                  checked={apiKeyEnabled}
                  onChange={(e) => setApiKeyEnabled(e.target.checked)}
                  className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">Require API Key (X-API-Key / Bearer)</div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Recommended when Home Assistant connects across VLANs or reverse proxies
                  </div>
                </div>
              </label>
            </div>

            <ApiKeyBar
              settings={settings}
              revealedKey={revealedKey}
              showKeyText={showKeyText}
              keyBusy={keyBusy}
              copiedId={copiedId}
              onRotate={() => void handleRotateKey()}
              onToggleReveal={() => void handleToggleRevealKey()}
              onCopy={(id, text) => void copyText(id, text)}
              onRevoke={() => void handleRevokeKey()}
            />

            {haTestResult && (
              <div
                className={`rounded-xl border px-4 py-2.5 text-xs font-mono ${
                  haTestResult.ok
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-300"
                }`}
              >
                {haTestResult.msg}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
            {/* Setup & Connection Details Card */}
            <div className="xl:col-span-5 space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
                <div className="flex items-center gap-3 border-b border-slate-800 pb-4">
                  <div className="h-12 w-12 rounded-xl bg-surface-950 border border-indigo-500/40 p-1.5 shrink-0">
                    <img src="/favicon.svg" alt="WebNUT" className="h-full w-full" />
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-white">Setup &amp; Connection Details</h3>
                    <p className="text-xs text-slate-400">
                      Bundled <code className="text-indigo-300">brand/icon.png</code> &amp;{" "}
                      <code className="text-indigo-300">icon@2x.png</code> match this WebNUT favicon in HA 2026.3+
                    </p>
                  </div>
                </div>

                <div className="space-y-2.5">
                  {[
                    { id: "ha_name", label: "Instance Name", value: settings?.instance_name || instanceName || "WebNUT" },
                    { id: "ha_host", label: "Host / IP Address", value: haHostAddress },
                    { id: "ha_port", label: "Web UI / API Port", value: String(settings?.web_port ?? 8765) },
                    { id: "ha_instance", label: "Instance ID", value: settings?.instance_id || "—" },
                  ].map((item) => (
                    <div
                      key={item.id}
                      className="flex items-center justify-between gap-2 rounded-xl bg-surface-950 border border-slate-800 px-3.5 py-2.5"
                    >
                      <div className="min-w-0">
                        <div className="text-[11px] text-slate-400">{item.label}</div>
                        <div className="text-xs font-mono text-white truncate">{item.value}</div>
                      </div>
                      <button
                        onClick={() => void copyText(item.id, item.value)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-[11px] text-slate-200 shrink-0"
                      >
                        {copiedId === item.id ? (
                          <Check className="h-3 w-3 text-emerald-400" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                        {copiedId === item.id ? "Copied" : "Copy"}
                      </button>
                    </div>
                  ))}
                  <div className="flex items-center justify-between rounded-xl bg-surface-950 border border-slate-800 px-3.5 py-2.5">
                    <div>
                      <div className="text-[11px] text-slate-400">API Key Requirement</div>
                      <div className="text-xs text-slate-200">
                        {settings?.api_key_enabled
                          ? `Required (${settings.api_key_preview})`
                          : "Optional (leave blank in HA setup)"}
                      </div>
                    </div>
                  </div>
                  <p className="text-[11px] text-slate-400 px-1">
                    Home Assistant automatically syncs this Instance Name to its server hub device &amp; integration title on poll.
                  </p>
                </div>

                <div className="rounded-xl bg-surface-950 border border-slate-800 p-4 space-y-2">
                  <div className="text-xs font-semibold text-white">Installation (Marketplace or Manual ZIP)</div>
                  <ol className="text-xs text-slate-300 space-y-1.5 list-decimal pl-4">
                    <li>
                      <strong>Marketplace (Recommended — HA 2026.11+):</strong> Open{" "}
                      <strong>Settings → Marketplace</strong>, add{" "}
                      <code className="text-indigo-300">https://github.com/PiexlPuck/WEBNUT</code> as a custom
                      repository (metadata via <code className="text-indigo-300">hacs.json</code>), install{" "}
                      <strong>WebNUT</strong>, and restart Home Assistant.
                    </li>
                    <li>
                      <strong>Manual ZIP:</strong> Click <strong>Download Integration ZIP</strong>, extract{" "}
                      <code className="text-indigo-300">custom_components/webnut</code> into your Home Assistant{" "}
                      <code className="text-indigo-300">/config</code> directory, and restart Home Assistant.
                    </li>
                    <li>
                      Open <strong>Settings → Devices &amp; Services → Add Integration → WebNUT</strong> and enter
                      the host, port, and API key (if enabled) above.
                    </li>
                  </ol>
                </div>
              </div>
            </div>

            {/* Live Entities Preview Card */}
            <div className="xl:col-span-7 rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                <div>
                  <h3 className="text-base font-semibold text-white">Live Entities Preview</h3>
                  <p className="text-xs text-slate-400">
                    Devices and entities Home Assistant will register from <code className="text-slate-300">/api/integrations/homeassistant/state</code>
                  </p>
                </div>
                <span
                  className={`text-[11px] px-2 py-0.5 rounded-full border font-mono ${
                    haPreview?.connected
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                      : "bg-slate-800 text-slate-400 border-slate-700"
                  }`}
                >
                  {haPreview?.connected ? "Connected" : "Waiting"}
                </span>
              </div>

              {haPreview && (
                <div className="space-y-4">
                  {/* WebNUT Hub */}
                  <div className="rounded-xl bg-surface-950 border border-slate-800 p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-sm font-semibold text-white">
                        <Activity className="h-4 w-4 text-indigo-400" />
                        {haPreview.instance_name || instanceName || "WebNUT"} Hub
                      </div>
                      <span className="text-[11px] font-mono text-slate-400">v{haPreview.version}</span>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs">
                      <div className="rounded-lg bg-surface-900 border border-slate-800 p-2">
                        <div className="text-[10px] text-slate-400 uppercase">Orchestrator</div>
                        <div className="font-mono text-white mt-0.5">{haPreview.orchestrator.state_label}</div>
                      </div>
                      <div className="rounded-lg bg-surface-900 border border-slate-800 p-2">
                        <div className="text-[10px] text-slate-400 uppercase">UPS Link</div>
                        <div className="font-mono text-white mt-0.5">{haPreview.connected ? "Connected" : "Offline"}</div>
                      </div>
                      <div className="rounded-lg bg-surface-900 border border-slate-800 p-2">
                        <div className="text-[10px] text-slate-400 uppercase">Repeater</div>
                        <div className="font-mono text-white mt-0.5">{haPreview.repeater_running ? "Running" : "Stopped"}</div>
                      </div>
                      <div className="rounded-lg bg-surface-900 border border-slate-800 p-2">
                        <div className="text-[10px] text-slate-400 uppercase">NUT Clients</div>
                        <div className="font-mono text-white mt-0.5">{haPreview.clients_connected}</div>
                      </div>
                      <div className="rounded-lg bg-surface-900 border border-slate-800 p-2">
                        <div className="text-[10px] text-slate-400 uppercase">Hosts Online</div>
                        <div className="font-mono text-white mt-0.5">
                          {haPreview.hosts_online}/{haPreview.hosts_total}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* UPS Devices */}
                  <div className="rounded-xl bg-surface-950 border border-slate-800 p-4 space-y-2.5">
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      <BatteryCharging className="h-4 w-4 text-emerald-400" />
                      UPS Devices ({haPreview.ups.length})
                    </div>
                    {haPreview.ups.length === 0 ? (
                      <div className="text-xs text-slate-400">No UPS units currently reported by telemetry.</div>
                    ) : (
                      <div className="space-y-2">
                        {haPreview.ups.map((u) => (
                          <div
                            key={u.name}
                            className="rounded-lg bg-surface-900 border border-slate-800 p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs"
                          >
                            <div>
                              <div className="font-semibold text-white">
                                {u.description || u.name}{" "}
                                <span className="font-mono text-[11px] text-indigo-300">({u.name})</span>
                              </div>
                              <div className="text-[11px] text-slate-400">
                                {[u.manufacturer, u.model].filter(Boolean).join(" ") || "NUT UPS"}
                              </div>
                            </div>
                            <div className="flex flex-wrap gap-2 font-mono text-[11px]">
                              <span className="px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-slate-200">
                                {u.status_label}
                              </span>
                              {u.battery_charge !== null && (
                                <span className="px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-emerald-300">
                                  {u.battery_charge}%
                                </span>
                              )}
                              {u.ups_load !== null && (
                                <span className="px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-sky-300">
                                  Load {u.ups_load}%
                                </span>
                              )}
                              {u.realpower_watts !== null && (
                                <span className="px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-amber-300">
                                  {u.realpower_watts} W
                                </span>
                              )}
                              {u.input_voltage !== null && (
                                <span className="px-2 py-0.5 rounded bg-surface-950 border border-slate-700 text-slate-300">
                                  {u.input_voltage} V
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Managed Host Devices */}
                  <div className="rounded-xl bg-surface-950 border border-slate-800 p-4 space-y-2.5">
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      <Server className="h-4 w-4 text-sky-400" />
                      Managed Host Devices ({haPreview.hosts.length})
                    </div>
                    {haPreview.hosts.length === 0 ? (
                      <div className="text-xs text-slate-400">No managed hosts configured yet.</div>
                    ) : (
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        {haPreview.hosts.map((h) => (
                          <div
                            key={h.id}
                            className="rounded-lg bg-surface-900 border border-slate-800 px-3 py-2 flex items-center justify-between gap-2 text-xs"
                          >
                            <div className="min-w-0">
                              <div className="font-medium text-white truncate">{h.name}</div>
                              <div className="text-[11px] font-mono text-slate-400 truncate">
                                {h.ip_or_host} • {h.wake_state}
                              </div>
                            </div>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <span
                                className={`px-2 py-0.5 rounded-full text-[10px] border font-medium ${
                                  h.is_online
                                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                                    : "bg-slate-800 text-slate-400 border-slate-700"
                                }`}
                              >
                                {h.is_online ? "Online" : "Offline"}
                              </span>
                              <span
                                title="Includes Wake-on-LAN button entity in Home Assistant"
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-indigo-500/15 border border-indigo-500/30 text-[10px] text-indigo-300"
                              >
                                <Power className="h-2.5 w-2.5" />
                                WoL
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Collapsible Raw JSON Payload */}
              <div className="border-t border-slate-800 pt-3">
                <button
                  onClick={() => setShowHaRawJson((v) => !v)}
                  className="inline-flex items-center gap-1.5 text-xs text-indigo-300 hover:text-indigo-200 font-medium"
                >
                  <Code2 className="h-3.5 w-3.5" />
                  {showHaRawJson ? "Hide Raw JSON Payload" : "View Raw JSON Payload"}
                </button>
                {showHaRawJson && haPreview && (
                  <pre className="mt-2.5 rounded-xl bg-surface-950 border border-slate-800 p-3 text-[11px] font-mono text-slate-300 overflow-x-auto max-h-72">
                    {JSON.stringify(haPreview, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          </div>
        </>
      ) : (
        <>
          {/* Prometheus & Grafana Access Card */}
          <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
              <div className="flex items-center gap-2.5">
                <Shield className="h-5 w-5 text-indigo-400" />
                <div>
                  <h3 className="text-base font-semibold text-white">Prometheus &amp; Grafana Access</h3>
                  <p className="text-xs text-slate-400">
                    Exposes OpenMetrics telemetry at <code className="text-slate-300">/metrics</code> and{" "}
                    <code className="text-slate-300">/api/integrations/prometheus/metrics</code>
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href="/api/integrations/grafana/dashboard.json"
                  download="webnut-grafana-dashboard.json"
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-400/40 text-xs font-medium text-amber-200"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download Grafana Dashboard JSON
                </a>
                <button
                  onClick={() => void handleTestPromEndpoint()}
                  disabled={testingPromEndpoint}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
                >
                  <Play className="h-3.5 w-3.5 text-emerald-400" />
                  {testingPromEndpoint ? "Testing..." : "Test /metrics Endpoint"}
                </button>
                <button
                  onClick={() => void handleSaveSettings()}
                  disabled={savingSettings}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {savingSettings ? "Saving..." : "Save Settings"}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="flex items-start gap-3 p-3.5 rounded-xl bg-surface-950 border border-slate-800 cursor-pointer">
                <input
                  type="checkbox"
                  checked={prometheusEnabled}
                  onChange={(e) => setPrometheusEnabled(e.target.checked)}
                  className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">Enable Prometheus /metrics Endpoint</div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Allows Prometheus or Grafana Alloy to scrape UPS, orchestrator, and host metrics
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-3 p-3.5 rounded-xl bg-surface-950 border border-slate-800 cursor-pointer">
                <input
                  type="checkbox"
                  checked={apiKeyEnabled}
                  onChange={(e) => setApiKeyEnabled(e.target.checked)}
                  className="mt-0.5 rounded border-slate-600 bg-surface-900 text-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">Require API Key (Bearer / X-API-Key)</div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Prometheus scrapes send <code className="text-indigo-300">Authorization: Bearer</code> when enabled
                  </div>
                </div>
              </label>
            </div>

            <ApiKeyBar
              settings={settings}
              revealedKey={revealedKey}
              showKeyText={showKeyText}
              keyBusy={keyBusy}
              copiedId={copiedId}
              onRotate={() => void handleRotateKey()}
              onToggleReveal={() => void handleToggleRevealKey()}
              onCopy={(id, text) => void copyText(id, text)}
              onRevoke={() => void handleRevokeKey()}
            />

            {promTestResult && (
              <div
                className={`rounded-xl border px-4 py-2.5 text-xs font-mono ${
                  promTestResult.ok
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-300"
                }`}
              >
                {promTestResult.msg}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
            {/* Scrape Config Generator */}
            <div className="xl:col-span-6 rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                <div>
                  <h3 className="text-base font-semibold text-white">
                    <code className="text-indigo-300">prometheus.yml</code> Scrape Config
                  </h3>
                  <p className="text-xs text-slate-400">
                    Add this job to Prometheus or Grafana Alloy, then import the Grafana dashboard JSON
                  </p>
                </div>
                {promGenerated && (
                  <button
                    onClick={() => void copyText("prom_yaml", promGenerated.scrape_yaml)}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white shrink-0"
                  >
                    {copiedId === "prom_yaml" ? (
                      <Check className="h-3.5 w-3.5" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    {copiedId === "prom_yaml" ? "Copied YAML" : "Copy Scrape YAML"}
                  </button>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">Job Name</label>
                  <input
                    type="text"
                    value={promForm.job_name}
                    onChange={(e) => setPromForm({ ...promForm, job_name: e.target.value })}
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">
                    Target Host (<code className="text-slate-400">ip:port</code>)
                  </label>
                  <input
                    type="text"
                    value={promForm.target_host}
                    onChange={(e) => setPromForm({ ...promForm, target_host: e.target.value })}
                    placeholder="192.168.1.10:8765"
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">Metrics Path</label>
                  <select
                    value={promForm.metrics_path}
                    onChange={(e) =>
                      setPromForm({
                        ...promForm,
                        metrics_path: e.target.value as "/metrics" | "/api/integrations/prometheus/metrics",
                      })
                    }
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-1.5 text-sm text-white font-mono"
                  >
                    <option value="/metrics">/metrics</option>
                    <option value="/api/integrations/prometheus/metrics">
                      /api/integrations/prometheus/metrics
                    </option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs font-medium text-slate-300 mb-1">Scheme</label>
                    <select
                      value={promForm.scheme}
                      onChange={(e) =>
                        setPromForm({ ...promForm, scheme: e.target.value as "http" | "https" })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-sm text-white"
                    >
                      <option value="http">http</option>
                      <option value="https">https</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-300 mb-1">Interval (s)</label>
                    <input
                      type="number"
                      min={5}
                      max={300}
                      value={promForm.scrape_interval_sec}
                      onChange={(e) =>
                        setPromForm({
                          ...promForm,
                          scrape_interval_sec: Number(e.target.value) || 15,
                        })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-sm text-white"
                    />
                  </div>
                </div>
              </div>

              {settings?.api_key_enabled && (
                <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={promForm.reveal_api_key}
                    onChange={(e) => setPromForm({ ...promForm, reveal_api_key: e.target.checked })}
                    className="rounded border-slate-600 bg-surface-950 text-indigo-500"
                  />
                  Embed actual API key in YAML
                </label>
              )}

              {promGenerated && (
                <pre className="rounded-xl bg-surface-950 border border-slate-800 p-4 text-xs font-mono text-emerald-300 overflow-x-auto leading-relaxed">
                  {promGenerated.scrape_yaml}
                </pre>
              )}

              <div className="rounded-xl bg-surface-950 border border-slate-800 p-4 space-y-2">
                <div className="text-xs font-semibold text-white">3-Step Grafana Setup</div>
                <ol className="text-xs text-slate-300 space-y-1.5 list-decimal pl-4">
                  <li>Add the scrape job above to Prometheus (or Grafana Alloy) and reload its config.</li>
                  <li>
                    Click <strong>Download Grafana Dashboard JSON</strong> to save{" "}
                    <code className="text-indigo-300">webnut-grafana-dashboard.json</code>.
                  </li>
                  <li>
                    In Grafana, open <strong>Dashboards → New → Import</strong>, upload the JSON, and select your
                    Prometheus datasource.
                  </li>
                </ol>
              </div>
            </div>

            {/* Live /metrics Preview */}
            <div className="xl:col-span-6 rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                <div>
                  <h3 className="text-base font-semibold text-white">Live <code className="text-indigo-300">/metrics</code> Exposition Preview</h3>
                  <p className="text-xs text-slate-400">
                    Real-time Prometheus text format output from your WebNUT instance
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {promGenerated && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full border border-slate-700 bg-surface-950 font-mono text-slate-300">
                      {
                        promGenerated.sample_metrics
                          .split("\n")
                          .filter((l) => l && !l.startsWith("#")).length
                      }{" "}
                      samples
                    </span>
                  )}
                  {promGenerated && (
                    <button
                      onClick={() => void copyText("prom_sample", promGenerated.sample_metrics)}
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
                    >
                      {copiedId === "prom_sample" ? (
                        <Check className="h-3.5 w-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" />
                      )}
                      {copiedId === "prom_sample" ? "Copied" : "Copy"}
                    </button>
                  )}
                </div>
              </div>

              <pre className="rounded-xl bg-surface-950 border border-slate-800 p-4 text-[11px] font-mono text-slate-300 overflow-x-auto max-h-[34rem] leading-relaxed">
                {promGenerated?.sample_metrics || "Loading metrics preview..."}
              </pre>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
