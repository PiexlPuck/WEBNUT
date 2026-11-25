import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertOctagon,
  Cable,
  CheckCircle2,
  Cpu,
  Globe,
  HardDrive,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Usb,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { Modal } from "../components/Modal";
import type {
  HardwareScanResponse,
  NutConfData,
  NutConfigFile,
  NutConnectionConfigPayload,
  NutConnectionTestResponse,
  NutServiceStatus,
  ScannedUsbDevice,
  SerialDeviceInfo,
  UpsConfData,
  UpsdConfData,
  UpsdUsersData,
  UpsmonConfData,
} from "../types";

interface NutConfigPageProps {
  services: NutServiceStatus[];
  onRefresh: () => Promise<void>;
}

const CONFIG_TABS: NutConfigFile[] = [
  "nut.conf",
  "ups.conf",
  "upsd.conf",
  "upsd.users",
  "upsmon.conf",
];

export function NutConfigPage({ services, onRefresh }: NutConfigPageProps) {
  const [activeFile, setActiveFile] = useState<NutConfigFile>("ups.conf");
  const [loadedFile, setLoadedFile] = useState<NutConfigFile | null>(null);
  const [parsedData, setParsedData] = useState<Record<string, unknown> | null>(null);
  const [rawPreview, setRawPreview] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [restartingSvc, setRestartingSvc] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string; stderr?: string } | null>(
    null
  );
  const loadSeqRef = useRef(0);

  // NUT Server Source (Local Built-in vs External Existing NUT Server)
  const [connForm, setConnForm] = useState<NutConnectionConfigPayload>({
    mode: "local",
    host: "127.0.0.1",
    port: 3493,
    username: "",
    password: "",
  });
  const [testingConn, setTestingConn] = useState(false);
  const [savingConn, setSavingConn] = useState(false);
  const [connTestResult, setConnTestResult] = useState<NutConnectionTestResponse | null>(null);

  // Hardware Scanner Modal (USB + Serial + lsusb)
  const [scanOpen, setScanOpen] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanResult, setScanResult] = useState<HardwareScanResponse | null>(null);
  const [serialDriverChoices, setSerialDriverChoices] = useState<Record<string, string>>({});

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await api.getNutConnection();
        setConnForm({
          mode: cfg.mode,
          host: cfg.host,
          port: cfg.port,
          username: cfg.username,
          password: cfg.password,
        });
      } catch {
        // Ignore initial connection fetch error
      }
    })();
  }, []);

  const handleTestConnection = async () => {
    setTestingConn(true);
    setConnTestResult(null);
    try {
      const res = await api.testNutConnection(connForm.host, connForm.port);
      setConnTestResult(res);
    } catch (err) {
      setConnTestResult({
        ok: false,
        message: err instanceof ApiError ? err.message : "Connection test failed",
        ups_list: [],
      });
    } finally {
      setTestingConn(false);
    }
  };

  const handleSaveConnection = async (overrideMode?: "local" | "external") => {
    setSavingConn(true);
    setFeedback(null);
    try {
      const nextPayload = overrideMode ? { ...connForm, mode: overrideMode } : connForm;
      const saved = await api.saveNutConnection(nextPayload);
      setConnForm({
        mode: saved.mode,
        host: saved.host,
        port: saved.port,
        username: saved.username,
        password: saved.password,
      });
      setFeedback({
        ok: true,
        msg:
          saved.mode === "external"
            ? `Connected WEBNUT to existing NUT server at ${saved.host}:${saved.port}.`
            : "Switched to built-in local NUT server.",
      });
      await onRefresh();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save NUT connection mode",
      });
    } finally {
      setSavingConn(false);
    }
  };

  const loadFile = useCallback(async (file: NutConfigFile) => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setParsedData(null);
    setLoadedFile(null);
    try {
      const res = await api.getNutConfig<Record<string, unknown>>(file);
      if (seq !== loadSeqRef.current) return;
      setParsedData(res.parsed ?? {});
      setLoadedFile(file);
      setRawPreview(res.raw ?? "");
    } catch (err) {
      if (seq !== loadSeqRef.current) return;
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to load ${file}`,
      });
    } finally {
      if (seq === loadSeqRef.current) {
        setLoading(false);
      }
    }
  }, []);

  const handleSelectFile = (file: NutConfigFile) => {
    setFeedback(null);
    setLoading(true);
    setParsedData(null);
    setLoadedFile(null);
    setActiveFile(file);
  };

  useEffect(() => {
    void loadFile(activeFile);
  }, [activeFile, loadFile]);

  const updateDataAndPreview = async (next: Record<string, unknown>) => {
    setParsedData(next);
    try {
      const prev = await api.previewNutConfig(activeFile, next);
      setRawPreview(prev.raw);
    } catch {
      // Preview validation errors surface on Save
    }
  };

  const handleSave = async (applyReload: boolean) => {
    if (!parsedData) return;
    setSaving(true);
    setFeedback(null);
    try {
      const res = await api.saveNutConfig(activeFile, parsedData, applyReload);
      setRawPreview(res.raw);
      setFeedback({ ok: true, msg: `${res.filename}: ${res.message}` });
      await onRefresh();
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail !== null) {
        const d = err.detail as { message?: string; stderr?: string; rolled_back?: boolean };
        setFeedback({
          ok: false,
          msg: d.message || err.message,
          stderr: d.stderr,
        });
        await loadFile(activeFile);
      } else {
        setFeedback({
          ok: false,
          msg: err instanceof ApiError ? err.message : "Failed to save config",
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleRestartService = async (name: "upsd" | "upsmon" | "drivers") => {
    setRestartingSvc(name);
    setFeedback(null);
    try {
      const res = await api.restartService(name);
      setFeedback({ ok: true, msg: `Service '${name}': ${res.detail}` });
      await onRefresh();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : `Failed to restart ${name}`,
      });
    } finally {
      setRestartingSvc(null);
    }
  };

  const handleScanHardware = async () => {
    setScanOpen(true);
    setScanning(true);
    setScanError(null);
    setScanResult(null);
    try {
      const res = await api.scanHardware();
      setScanResult(res);
      const defaults: Record<string, string> = {};
      for (const sd of res.serial_devices) {
        const mfrLower = `${sd.manufacturer ?? ""} ${sd.product ?? ""}`.toLowerCase();
        defaults[sd.path] = mfrLower.includes("apc")
          ? "apcsmart"
          : res.serial_drivers[0] || "nutdrv_qx";
      }
      setSerialDriverChoices(defaults);
    } catch (err) {
      setScanError(err instanceof ApiError ? err.message : "Hardware scan failed");
    } finally {
      setScanning(false);
    }
  };

  const _appendUpsBlock = async (block: UpsConfData["ups_list"][number]) => {
    let targetData = parsedData as UpsConfData | null;
    if (activeFile !== "ups.conf" || !targetData) {
      const loaded = await api.getNutConfig<UpsConfData>("ups.conf");
      targetData = loaded.parsed;
      setActiveFile("ups.conf");
    }
    const existingNames = new Set(targetData.ups_list.map((u) => u.name));
    let name = block.name;
    let idx = 2;
    while (existingNames.has(name)) {
      name = `${block.name}_${idx++}`;
    }

    const next: UpsConfData = {
      ...targetData,
      ups_list: [...targetData.ups_list, { ...block, name }],
    };
    setScanOpen(false);
    setParsedData(next as unknown as Record<string, unknown>);
    const prev = await api.previewNutConfig("ups.conf", next);
    setRawPreview(prev.raw);
  };

  const handleAddScannedDevice = async (dev: ScannedUsbDevice) => {
    const directives = [];
    if (dev.vendorid) directives.push({ key: "vendorid", value: dev.vendorid });
    if (dev.productid) directives.push({ key: "productid", value: dev.productid });
    if (dev.serial) directives.push({ key: "serial", value: dev.serial });

    await _appendUpsBlock({
      name: dev.suggested_name,
      driver: dev.driver,
      port: dev.port || "auto",
      desc: [dev.vendor, dev.product].filter(Boolean).join(" ") || "USB UPS",
      directives,
    });
  };

  const handleAddSerialDevice = async (dev: SerialDeviceInfo) => {
    const driver = serialDriverChoices[dev.path] || "nutdrv_qx";
    await _appendUpsBlock({
      name: dev.suggested_name,
      driver,
      port: dev.path,
      desc: [dev.manufacturer, dev.product].filter(Boolean).join(" ") || `Serial UPS (${dev.path})`,
      directives: [],
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">NUT Configurator</h2>
          <p className="text-sm text-slate-400">
            Graphical editor with atomic writes, automatic rollback, and USB UPS discovery
          </p>
        </div>
        <button
          onClick={handleScanHardware}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium text-white"
        >
          <Search className="h-4 w-4" />
          Scan Hardware (USB &amp; Serial)
        </button>
      </div>

      {/* NUT Server Source Card (Local vs Join Existing NUT Server) */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-white">NUT Server Source</h3>
            <p className="text-xs text-slate-400">
              Run WEBNUT&apos;s built-in NUT server or join an existing NUT server (`upsd`) on your
              network/host
            </p>
          </div>
          <div className="inline-flex rounded-lg bg-surface-950 border border-slate-800 p-1">
            <button
              type="button"
              onClick={() => {
                setConnTestResult(null);
                setConnForm((prev) => ({ ...prev, mode: "local" }));
              }}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                connForm.mode === "local"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <HardDrive className="h-3.5 w-3.5" />
              Built-in Local Server
            </button>
            <button
              type="button"
              onClick={() => {
                setConnTestResult(null);
                setConnForm((prev) => ({
                  ...prev,
                  mode: "external",
                  host: prev.host === "127.0.0.1" ? "" : prev.host,
                }));
              }}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                connForm.mode === "external"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Globe className="h-3.5 w-3.5" />
              Join Existing NUT Server
            </button>
          </div>
        </div>

        {connForm.mode === "external" ? (
          <div className="space-y-3 pt-2 border-t border-slate-800">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Existing NUT Server IP / Host
                </label>
                <input
                  value={connForm.host}
                  onChange={(e) => setConnForm({ ...connForm, host: e.target.value })}
                  placeholder="192.168.1.10 (or 127.0.0.1)"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Port</label>
                <input
                  type="number"
                  value={connForm.port}
                  onChange={(e) => setConnForm({ ...connForm, port: Number(e.target.value) })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Username (Optional)
                </label>
                <input
                  value={connForm.username}
                  onChange={(e) => setConnForm({ ...connForm, username: e.target.value })}
                  placeholder="monuser"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Password (Optional)
                </label>
                <input
                  type="password"
                  value={connForm.password}
                  onChange={(e) => setConnForm({ ...connForm, password: e.target.value })}
                  placeholder="••••••••"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-slate-400">
                Telemetry, OB → OL WoL recovery, Pushover alerts, and live client tracking will all
                use this NUT server. Local `upsd` &amp; `drivers` stay stopped to avoid conflicts.
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handleTestConnection()}
                  disabled={testingConn || !connForm.host.trim()}
                  className="px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
                >
                  {testingConn ? "Testing..." : "Test Connection"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSaveConnection("external")}
                  disabled={savingConn || !connForm.host.trim()}
                  className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                >
                  {savingConn ? "Connecting..." : "Save & Connect"}
                </button>
              </div>
            </div>

            {connTestResult && (
              <div
                className={`rounded-xl border p-3 text-xs space-y-1.5 ${
                  connTestResult.ok
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-300"
                }`}
              >
                <div className="font-medium">{connTestResult.message}</div>
                {connTestResult.ups_list.length > 0 && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {connTestResult.ups_list.map((u) => (
                      <span
                        key={u.name}
                        className="px-2 py-0.5 rounded bg-surface-950 border border-emerald-500/30 font-mono text-[11px]"
                      >
                        {u.name}: {u.status}
                        {u.battery_charge !== null ? ` (${u.battery_charge}%)` : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-between pt-2 border-t border-slate-800 text-xs text-slate-400">
            <span>
              Using local built-in NUT daemons (`127.0.0.1:{connForm.port}`) managed via the
              config files below.
            </span>
            <button
              type="button"
              onClick={() => void handleSaveConnection("local")}
              disabled={savingConn}
              className="px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200 disabled:opacity-50"
            >
              {savingConn ? "Applying..." : "Use Built-in Local Server"}
            </button>
          </div>
        )}
      </div>

      {/* Services Status Strip */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {services.map((svc) => (
          <div
            key={svc.name}
            className="rounded-xl border border-slate-800 bg-surface-900 p-4 flex items-center justify-between gap-3"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm font-semibold text-white">{svc.name}</span>
                <span
                  className={`text-[11px] px-2 py-0.5 rounded border uppercase font-semibold ${
                    svc.status === "running"
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                      : svc.status === "error"
                      ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
                      : "bg-slate-800 text-slate-300 border-slate-700"
                  }`}
                >
                  {svc.status}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 truncate mt-1" title={svc.detail}>
                {svc.detail || "—"}
              </p>
            </div>
            {(svc.name === "upsd" || svc.name === "upsmon" || svc.name === "drivers") && (
              <button
                onClick={() => handleRestartService(svc.name as "upsd" | "upsmon" | "drivers")}
                disabled={restartingSvc === svc.name}
                className="p-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200 disabled:opacity-50 shrink-0"
                title={`Restart ${svc.name}`}
              >
                <RefreshCw
                  className={`h-4 w-4 ${restartingSvc === svc.name ? "animate-spin" : ""}`}
                />
              </button>
            )}
          </div>
        ))}
      </div>

      {feedback && (
        <div
          className={`rounded-xl border p-4 text-sm space-y-2 ${
            feedback.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-rose-500/40 bg-rose-500/10 text-rose-300"
          }`}
        >
          <div className="flex items-center gap-2 font-medium">
            {feedback.ok ? (
              <CheckCircle2 className="h-4 w-4 shrink-0" />
            ) : (
              <AlertOctagon className="h-4 w-4 shrink-0" />
            )}
            <span>{feedback.msg}</span>
          </div>
          {feedback.stderr && (
            <pre className="p-3 rounded-lg bg-surface-950 border border-rose-500/30 text-xs font-mono text-rose-200 overflow-x-auto">
              {feedback.stderr}
            </pre>
          )}
        </div>
      )}

      {/* File Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
        <div className="flex flex-wrap gap-1.5">
          {CONFIG_TABS.map((f) => (
            <button
              key={f}
              onClick={() => handleSelectFile(f)}
              className={`px-3.5 py-2 rounded-lg font-mono text-xs font-medium transition-colors ${
                activeFile === f
                  ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/40"
                  : "bg-surface-900 text-slate-400 hover:text-white border border-slate-800"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => handleSave(false)}
            disabled={saving || loading}
            className="px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs font-medium text-slate-200 disabled:opacity-50"
          >
            Save Only
          </button>
          <button
            onClick={() => handleSave(true)}
            disabled={saving || loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
          >
            <Save className="h-3.5 w-3.5" />
            {saving ? "Applying..." : "Save & Apply"}
          </button>
        </div>
      </div>

      {/* Form Editor + Raw Preview Split */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-7 rounded-2xl border border-slate-800 bg-surface-900 p-5">
          {loading || !parsedData || loadedFile !== activeFile ? (
            <div className="text-sm text-slate-400 py-8 text-center">Loading {activeFile}...</div>
          ) : (
            <NutFormRenderer
              file={activeFile}
              data={parsedData}
              onChange={(next) => void updateDataAndPreview(next)}
            />
          )}
        </div>

        <div className="xl:col-span-5 rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Raw Preview ({activeFile})
            </span>
            <span className="text-[11px] text-slate-500 font-mono">/etc/nut/{activeFile}</span>
          </div>
          <pre className="p-4 text-xs font-mono text-slate-300 bg-surface-950 flex-1 overflow-auto min-h-[320px] whitespace-pre-wrap">
            {rawPreview || "# Empty configuration"}
          </pre>
        </div>
      </div>

      {/* Hardware Scanner Modal (USB + Serial + lsusb) */}
      <Modal
        open={scanOpen}
        title="Hardware Discovery (USB, USB-Serial & Raw lsusb)"
        onClose={() => setScanOpen(false)}
        maxWidth="max-w-3xl"
      >
        {scanning ? (
          <div className="py-10 text-center text-sm text-slate-400">
            Scanning USB bus, passed-through serial ports, and lsusb devices...
          </div>
        ) : scanError ? (
          <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">
            {scanError}
          </div>
        ) : !scanResult ? null : (
          <div className="space-y-6">
            {scanResult.warnings.length > 0 && (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200 space-y-1">
                {scanResult.warnings.map((w, i) => (
                  <div key={i}>{w}</div>
                ))}
              </div>
            )}

            {/* Section 1: NUT Auto-Detected USB UPS */}
            <div className="space-y-2.5">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                  <Cpu className="h-3.5 w-3.5 text-indigo-400" />
                  NUT Auto-Detected USB UPS ({scanResult.nut_usb_devices.length})
                </h4>
              </div>
              {scanResult.nut_usb_devices.length === 0 ? (
                <p className="text-xs text-slate-500 rounded-lg border border-slate-800 bg-surface-950 p-3">
                  No native USB HID UPS detected by nut-scanner. If using a USB-Serial adapter,
                  check the Serial Ports section below.
                </p>
              ) : (
                scanResult.nut_usb_devices.map((dev, idx) => (
                  <div
                    key={`${dev.driver}-${idx}`}
                    className="rounded-xl border border-slate-700 bg-surface-950 p-3.5 flex items-center justify-between gap-4"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-white text-sm">
                          {dev.product || dev.suggested_name}
                        </span>
                        <span className="text-xs font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded">
                          {dev.driver}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400 font-mono">
                        {[
                          dev.vendor,
                          dev.vendorid && `VID:${dev.vendorid}`,
                          dev.productid && `PID:${dev.productid}`,
                          dev.serial && `SN:${dev.serial}`,
                        ]
                          .filter(Boolean)
                          .join(" • ")}
                      </p>
                    </div>
                    <button
                      onClick={() => void handleAddScannedDevice(dev)}
                      className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white shrink-0"
                    >
                      Add to ups.conf
                    </button>
                  </div>
                ))
              )}
            </div>

            {/* Section 2: Passed-Through Serial & USB-Serial Ports */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Cable className="h-3.5 w-3.5 text-sky-400" />
                Serial &amp; USB-Serial Ports ({scanResult.serial_devices.length})
              </h4>
              {scanResult.serial_devices.length === 0 ? (
                <div className="text-xs text-slate-400 rounded-lg border border-slate-800 bg-surface-950 p-3">
                  No passed-through serial devices found (`/dev/webnut-ups-*`, `/dev/ttyUSB*`,
                  `/dev/ttyACM*`). Run <code className="text-indigo-300">./scripts/add-usb-serial.sh</code>{" "}
                  on your Docker host to attach a USB-Serial adapter.
                </div>
              ) : (
                scanResult.serial_devices.map((sd) => (
                  <div
                    key={sd.path}
                    className="rounded-xl border border-slate-700 bg-surface-950 p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-sm font-semibold text-white">
                          {sd.path}
                        </span>
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded border ${
                            sd.accessible
                              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                              : "bg-rose-500/15 text-rose-300 border-rose-500/30"
                          }`}
                        >
                          {sd.accessible ? "R/W OK" : "Check Perms"}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400 font-mono truncate">
                        {[
                          sd.manufacturer,
                          sd.product,
                          sd.vendor_id && sd.product_id && `${sd.vendor_id}:${sd.product_id}`,
                          sd.serial && `SN:${sd.serial}`,
                          sd.real_path !== sd.path && `→ ${sd.real_path}`,
                        ]
                          .filter(Boolean)
                          .join(" • ") || "Serial TTY device"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <select
                        value={serialDriverChoices[sd.path] || "nutdrv_qx"}
                        onChange={(e) =>
                          setSerialDriverChoices((prev) => ({
                            ...prev,
                            [sd.path]: e.target.value,
                          }))
                        }
                        className="rounded-lg bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                      >
                        {scanResult.serial_drivers.map((drv) => (
                          <option key={drv} value={drv}>
                            {drv}
                          </option>
                        ))}
                      </select>
                      <button
                        onClick={() => void handleAddSerialDevice(sd)}
                        className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
                      >
                        Add to ups.conf
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* Section 3: All Container USB Devices (lsusb) */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Usb className="h-3.5 w-3.5 text-emerald-400" />
                All Container USB Devices — lsusb ({scanResult.raw_usb_devices.length})
              </h4>
              {scanResult.raw_usb_devices.length === 0 ? (
                <p className="text-xs text-slate-500 rounded-lg border border-slate-800 bg-surface-950 p-3">
                  No raw USB devices returned by `lsusb`.
                </p>
              ) : (
                <div className="rounded-xl border border-slate-800 bg-surface-950 divide-y divide-slate-800/70 max-h-48 overflow-y-auto">
                  {scanResult.raw_usb_devices.map((u, idx) => (
                    <div
                      key={`${u.bus}-${u.device}-${idx}`}
                      className="px-3.5 py-2 flex items-center justify-between gap-3 text-xs"
                    >
                      <span className="text-slate-200 truncate">{u.description}</span>
                      <span className="font-mono text-slate-400 shrink-0">
                        Bus {u.bus}:{u.device} • {u.vendor_id}:{u.product_id}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <p className="text-[11px] text-slate-500">
                Tip: If a USB-Serial chip (FTDI, CH340, Prolific, CP210x) appears in `lsusb` above
                without a port under Serial Ports, run{" "}
                <code className="text-slate-300">./scripts/add-usb-serial.sh</code> on your host
                to pass the TTY node into Docker.
              </p>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function NutFormRenderer({
  file,
  data,
  onChange,
}: {
  file: NutConfigFile;
  data: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  if (file === "nut.conf") {
    const d = data as unknown as Partial<NutConfData>;
    return (
      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium text-slate-300 mb-1.5">NUT Mode</label>
          <select
            value={d.mode ?? "netserver"}
            onChange={(e) =>
              onChange({
                extra_lines: d.extra_lines ?? [],
                mode: e.target.value as NutConfData["mode"],
              } as unknown as Record<string, unknown>)
            }
            className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
          >
            <option value="netserver">netserver (Local UPS + accept network clients)</option>
            <option value="standalone">standalone (Local UPS only)</option>
            <option value="netclient">netclient (Remote UPS monitor only)</option>
            <option value="none">none (NUT disabled)</option>
          </select>
        </div>
      </div>
    );
  }

  if (file === "ups.conf") {
    const d = data as unknown as Partial<UpsConfData>;
    const upsList = d.ups_list ?? [];
    const updateUps = (idx: number, patch: Partial<UpsConfData["ups_list"][number]>) => {
      const next = [...upsList];
      next[idx] = { ...next[idx], ...patch };
      onChange({
        global_directives: d.global_directives ?? [],
        ups_list: next,
      } as unknown as Record<string, unknown>);
    };
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-white">UPS Definitions</h4>
          <button
            onClick={() =>
              onChange({
                global_directives: d.global_directives ?? [],
                ups_list: [
                  ...upsList,
                  {
                    name: `ups${upsList.length + 1}`,
                    driver: "usbhid-ups",
                    port: "auto",
                    desc: "Local UPS",
                    directives: [],
                  },
                ],
              } as unknown as Record<string, unknown>)
            }
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 text-xs text-indigo-300"
          >
            <Plus className="h-3.5 w-3.5" />
            Add UPS Block
          </button>
        </div>

        {upsList.length === 0 && (
          <p className="text-xs text-slate-400">
            No UPS blocks defined. Use &ldquo;Scan Hardware (USB &amp; Serial)&rdquo; or add one
            manually.
          </p>
        )}

        {upsList.map((ups, idx) => (
          <div
            key={idx}
            className="rounded-xl border border-slate-800 bg-surface-950 p-4 space-y-3"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs font-semibold text-indigo-300">[{ups.name}]</span>
              <button
                onClick={() =>
                  onChange({
                    global_directives: d.global_directives ?? [],
                    ups_list: upsList.filter((_, i) => i !== idx),
                  } as unknown as Record<string, unknown>)
                }
                className="text-rose-400 hover:text-rose-300"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Section Name</label>
                <input
                  value={ups.name ?? ""}
                  onChange={(e) => updateUps(idx, { name: e.target.value })}
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Driver</label>
                <input
                  value={ups.driver ?? ""}
                  onChange={(e) => updateUps(idx, { driver: e.target.value })}
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Port</label>
                <input
                  value={ups.port ?? ""}
                  onChange={(e) => updateUps(idx, { port: e.target.value })}
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Description</label>
                <input
                  value={ups.desc ?? ""}
                  onChange={(e) => updateUps(idx, { desc: e.target.value })}
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs text-white"
                />
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (file === "upsd.conf") {
    const d = data as unknown as Partial<UpsdConfData>;
    const listens = d.listens ?? [];
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-white">LISTEN Endpoints</h4>
          <button
            onClick={() =>
              onChange({
                extra_lines: d.extra_lines ?? [],
                maxage: d.maxage ?? null,
                maxconn: d.maxconn ?? null,
                statepath: d.statepath ?? null,
                listens: [...listens, { address: "0.0.0.0", port: 3493 }],
              } as unknown as Record<string, unknown>)
            }
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-800 text-xs text-indigo-300"
          >
            <Plus className="h-3.5 w-3.5" />
            Add LISTEN
          </button>
        </div>

        {listens.map((l, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <input
              value={l.address ?? ""}
              onChange={(e) => {
                const next = [...listens];
                next[idx] = { ...next[idx], address: e.target.value };
                onChange({ ...d, listens: next } as unknown as Record<string, unknown>);
              }}
              className="flex-1 rounded bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
            />
            <input
              type="number"
              value={l.port ?? 3493}
              onChange={(e) => {
                const next = [...listens];
                next[idx] = { ...next[idx], port: Number(e.target.value) };
                onChange({ ...d, listens: next } as unknown as Record<string, unknown>);
              }}
              className="w-24 rounded bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
            />
            <button
              onClick={() =>
                onChange({
                  ...d,
                  listens: listens.filter((_, i) => i !== idx),
                } as unknown as Record<string, unknown>)
              }
              className="p-2 text-rose-400"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
          <div>
            <label className="block text-xs text-slate-400 mb-1">MAXAGE (sec)</label>
            <input
              type="number"
              value={d.maxage ?? ""}
              onChange={(e) =>
                onChange({
                  ...d,
                  maxage: e.target.value ? Number(e.target.value) : null,
                } as unknown as Record<string, unknown>)
              }
              placeholder="15"
              className="w-full rounded bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">MAXCONN</label>
            <input
              type="number"
              value={d.maxconn ?? ""}
              onChange={(e) =>
                onChange({
                  ...d,
                  maxconn: e.target.value ? Number(e.target.value) : null,
                } as unknown as Record<string, unknown>)
              }
              placeholder="1024"
              className="w-full rounded bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
            />
          </div>
        </div>
      </div>
    );
  }

  if (file === "upsd.users") {
    const d = data as unknown as Partial<UpsdUsersData>;
    const users = d.users ?? [];
    const updateUser = (idx: number, patch: Partial<UpsdUsersData["users"][number]>) => {
      const next = [...users];
      next[idx] = { ...next[idx], ...patch };
      onChange({ users: next } as unknown as Record<string, unknown>);
    };
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-white">NUT Users</h4>
          <button
            onClick={() =>
              onChange({
                users: [
                  ...users,
                  {
                    username: `user${users.length + 1}`,
                    password: "",
                    actions: [],
                    instcmds: [],
                    upsmon_role: "secondary",
                  },
                ],
              } as unknown as Record<string, unknown>)
            }
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-800 text-xs text-indigo-300"
          >
            <Plus className="h-3.5 w-3.5" />
            Add User
          </button>
        </div>

        {users.map((u, idx) => (
          <div
            key={idx}
            className="rounded-xl border border-slate-800 bg-surface-950 p-4 space-y-3"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs font-semibold text-indigo-300">
                [{u.username}]
              </span>
              <button
                onClick={() =>
                  onChange({
                    users: users.filter((_, i) => i !== idx),
                  } as unknown as Record<string, unknown>)
                }
                className="text-rose-400"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Username</label>
                <input
                  value={u.username ?? ""}
                  onChange={(e) => updateUser(idx, { username: e.target.value })}
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Password</label>
                <input
                  type="password"
                  value={u.password ?? ""}
                  onChange={(e) => updateUser(idx, { password: e.target.value })}
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">upsmon Role</label>
                <select
                  value={u.upsmon_role ?? ""}
                  onChange={(e) =>
                    updateUser(idx, {
                      upsmon_role: e.target.value as "" | "primary" | "secondary",
                    })
                  }
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs text-white"
                >
                  <option value="">None</option>
                  <option value="primary">primary</option>
                  <option value="secondary">secondary</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Actions (space-separated)
                </label>
                <input
                  value={(u.actions ?? []).join(" ")}
                  onChange={(e) =>
                    updateUser(idx, {
                      actions: e.target.value.split(/\s+/).filter(Boolean),
                    })
                  }
                  placeholder="SET"
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="block text-[11px] text-slate-400 mb-1">
                  Instant Commands (space-separated)
                </label>
                <input
                  value={(u.instcmds ?? []).join(" ")}
                  onChange={(e) =>
                    updateUser(idx, {
                      instcmds: e.target.value.split(/\s+/).filter(Boolean),
                    })
                  }
                  placeholder="ALL"
                  className="w-full rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  // upsmon.conf
  const d = data as unknown as Partial<UpsmonConfData>;
  const monitors = d.monitors ?? [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-[11px] text-slate-400 mb-1">POLLFREQ (sec)</label>
          <input
            type="number"
            value={d.pollfreq ?? 5}
            onChange={(e) =>
              onChange({ ...d, pollfreq: Number(e.target.value) } as unknown as Record<string, unknown>)
            }
            className="w-full rounded bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs text-white"
          />
        </div>
        <div>
          <label className="block text-[11px] text-slate-400 mb-1">DEADTIME (sec)</label>
          <input
            type="number"
            value={d.deadtime ?? 15}
            onChange={(e) =>
              onChange({ ...d, deadtime: Number(e.target.value) } as unknown as Record<string, unknown>)
            }
            className="w-full rounded bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs text-white"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="block text-[11px] text-slate-400 mb-1">SHUTDOWNCMD</label>
          <input
            value={d.shutdowncmd ?? "/bin/true"}
            onChange={(e) =>
              onChange({ ...d, shutdowncmd: e.target.value } as unknown as Record<string, unknown>)
            }
            className="w-full rounded bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="block text-[11px] text-slate-400 mb-1">NOTIFYCMD (optional)</label>
          <input
            value={d.notifycmd ?? ""}
            onChange={(e) =>
              onChange({ ...d, notifycmd: e.target.value } as unknown as Record<string, unknown>)
            }
            className="w-full rounded bg-surface-950 border border-slate-700 px-3 py-1.5 text-xs font-mono text-white"
          />
        </div>
      </div>

      <div className="pt-2 border-t border-slate-800 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-white">MONITOR Entries</h4>
          <button
            onClick={() =>
              onChange({
                ...d,
                monitors: [
                  ...monitors,
                  {
                    ups_spec: "ups1@127.0.0.1",
                    power_value: 1,
                    username: "monuser",
                    password: "",
                    role: "primary",
                  },
                ],
              } as unknown as Record<string, unknown>)
            }
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-800 text-xs text-indigo-300"
          >
            <Plus className="h-3.5 w-3.5" />
            Add MONITOR
          </button>
        </div>

        {monitors.map((m, idx) => (
          <div
            key={idx}
            className="rounded-xl border border-slate-800 bg-surface-950 p-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5"
          >
            <input
              value={m.ups_spec ?? ""}
              placeholder="ups@host"
              onChange={(e) => {
                const next = [...monitors];
                next[idx] = { ...next[idx], ups_spec: e.target.value };
                onChange({ ...d, monitors: next } as unknown as Record<string, unknown>);
              }}
              className="rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
            />
            <div className="flex gap-2">
              <input
                value={m.username ?? ""}
                placeholder="username"
                onChange={(e) => {
                  const next = [...monitors];
                  next[idx] = { ...next[idx], username: e.target.value };
                  onChange({ ...d, monitors: next } as unknown as Record<string, unknown>);
                }}
                className="flex-1 rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
              />
              <button
                onClick={() =>
                  onChange({
                    ...d,
                    monitors: monitors.filter((_, i) => i !== idx),
                  } as unknown as Record<string, unknown>)
                }
                className="text-rose-400 px-1"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
            <input
              type="password"
              value={m.password ?? ""}
              placeholder="password"
              onChange={(e) => {
                const next = [...monitors];
                next[idx] = { ...next[idx], password: e.target.value };
                onChange({ ...d, monitors: next } as unknown as Record<string, unknown>);
              }}
              className="rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
            />
            <select
              value={m.role ?? "primary"}
              onChange={(e) => {
                const next = [...monitors];
                next[idx] = {
                  ...next[idx],
                  role: e.target.value as "primary" | "secondary",
                };
                onChange({ ...d, monitors: next } as unknown as Record<string, unknown>);
              }}
              className="rounded bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs text-white"
            >
              <option value="primary">primary</option>
              <option value="secondary">secondary</option>
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}
