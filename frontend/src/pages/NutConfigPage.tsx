import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertOctagon,
  Cable,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cpu,
  Edit2,
  Globe,
  HardDrive,
  Network,
  Plus,
  Radio,
  RefreshCw,
  Save,
  Search,
  ShieldAlert,
  Trash2,
  Usb,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import type {
  HardwareScanResponse,
  NutConfData,
  NutConfigFile,
  NutConnectionConfigPayload,
  NutConnectionTestResponse,
  NutServiceStatus,
  OrchestratorConfig,
  RepeaterStatusResponse,
  ScannedSnmpDevice,
  ScannedUsbDevice,
  SerialDeviceInfo,
  SnmpUpsSetupPayload,
  TelemetrySnapshot,
  UpsConfData,
  UpsdConfData,
  UpsdUsersData,
  UpsmonConfData,
} from "../types";

interface NutConfigPageProps {
  services: NutServiceStatus[];
  telemetry: TelemetrySnapshot;
  onRefresh: () => Promise<void>;
}

function sanitizeUpsId(raw: string, fallback = "ups1"): string {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/^[_\-.]+|[_\-.]+$/g, "");
  return (cleaned || fallback).slice(0, 48);
}

function suggestFriendlyUpsName(dev: ScannedUsbDevice): string {
  const base = (dev.product || dev.vendor || dev.suggested_name || "ups1")
    .replace(/fw:.*$/i, "")
    .replace(/\bid=\S+/gi, "")
    .trim();
  const words = base.split(/\s+/).filter(Boolean).slice(0, 3).join("_");
  return sanitizeUpsId(words || dev.suggested_name, "ups1");
}

function getUpsDirective(ups: UpsConfData["ups_list"][number], key: string): string {
  const found = (ups.directives || []).find(
    (d) => d.key.toLowerCase() === key.toLowerCase()
  );
  return (found?.value || "").trim();
}

function findMatchingConfiguredUpsForUsb(
  dev: ScannedUsbDevice,
  configured: UpsConfData["ups_list"]
): UpsConfData["ups_list"][number] | undefined {
  const devSerial = (dev.serial || "").trim().toLowerCase();
  const devVid = (dev.vendorid || "").trim().toLowerCase();
  const devPid = (dev.productid || "").trim().toLowerCase();

  return configured.find((u) => {
    const uSerial = getUpsDirective(u, "serial").toLowerCase();
    if (devSerial && uSerial && devSerial === uSerial) return true;
    const uVid = getUpsDirective(u, "vendorid").toLowerCase();
    const uPid = getUpsDirective(u, "productid").toLowerCase();
    if (devVid && devPid && uVid === devVid && uPid === devPid) {
      if (devSerial && uSerial && devSerial !== uSerial) return false;
      return true;
    }
    return false;
  });
}

function findMatchingConfiguredUpsForSerial(
  sd: SerialDeviceInfo,
  configured: UpsConfData["ups_list"]
): UpsConfData["ups_list"][number] | undefined {
  return configured.find((u) => u.port.trim() === sd.path.trim());
}

const CONFIG_TABS: NutConfigFile[] = [
  "nut.conf",
  "ups.conf",
  "upsd.conf",
  "upsd.users",
  "upsmon.conf",
];

const SNMP_MIBS = [
  "auto",
  "apcc",
  "ietf",
  "pw",
  "cyberpower",
  "eaton_ats",
  "raritan",
  "tripplite",
  "bestpower",
  "netvision",
];

export function NutConfigPage({ services, telemetry, onRefresh }: NutConfigPageProps) {
  const [sourceTab, setSourceTab] = useState<"usb" | "snmp" | "external">("usb");
  const [repeater, setRepeater] = useState<RepeaterStatusResponse | null>(null);
  const [policy, setPolicy] = useState<OrchestratorConfig | null>(null);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [configuredUps, setConfiguredUps] = useState<UpsConfData["ups_list"]>([]);
  const [upsGlobalDirectives, setUpsGlobalDirectives] = useState<
    UpsConfData["global_directives"]
  >([]);
  const [removingUpsName, setRemovingUpsName] = useState<string | null>(null);
  const [removeUpsTarget, setRemoveUpsTarget] = useState<{
    name: string;
    keepScanModalOpen: boolean;
  } | null>(null);
  const [renameTarget, setRenameTarget] = useState<UpsConfData["ups_list"][number] | null>(null);
  const [renameForm, setRenameForm] = useState({ name: "", desc: "" });
  const [renaming, setRenaming] = useState(false);

  // External NUT Server form
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

  // One-Page SNMP UPS Setup form
  const [snmpForm, setSnmpForm] = useState<SnmpUpsSetupPayload>({
    name: "snmp_ups",
    host: "",
    snmp_version: "v2c",
    mibs: "auto",
    community: "public",
    sec_level: "authPriv",
    sec_name: "",
    auth_protocol: "SHA",
    auth_password: "",
    priv_protocol: "AES",
    priv_password: "",
    desc: "SNMP Network UPS",
    pollfreq: 15,
  });
  const [savingSnmp, setSavingSnmp] = useState(false);
  const [snmpScanOpen, setSnmpScanOpen] = useState(false);
  const [snmpScanRange, setSnmpScanRange] = useState({
    start_ip: "192.168.1.1",
    end_ip: "192.168.1.254",
    community: "public",
    snmp_version: "v2c" as "v1" | "v2c" | "v3",
  });
  const [scanningSnmp, setScanningSnmp] = useState(false);
  const [scannedSnmp, setScannedSnmp] = useState<ScannedSnmpDevice[]>([]);
  const [snmpScanErr, setSnmpScanErr] = useState<string | null>(null);

  // Advanced 5-file NUT Configurator
  const [showAdvanced, setShowAdvanced] = useState(false);
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

  // Hardware Scanner Modal (USB + Serial + lsusb)
  const [scanOpen, setScanOpen] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanStderr, setScanStderr] = useState<string | null>(null);
  const [scanResult, setScanResult] = useState<HardwareScanResponse | null>(null);
  const [serialDriverChoices, setSerialDriverChoices] = useState<Record<string, string>>({});
  const [usbNameChoices, setUsbNameChoices] = useState<Record<number, string>>({});
  const [serialNameChoices, setSerialNameChoices] = useState<Record<string, string>>({});
  const [addingDevice, setAddingDevice] = useState<string | null>(null);

  const loadTopState = useCallback(async () => {
    try {
      const [cfg, rep, orch, upsCfg] = await Promise.all([
        api.getNutConnection(),
        api.getRepeaterStatus(),
        api.getOrchestratorConfig(),
        api.getNutConfig<UpsConfData>("ups.conf"),
      ]);
      setConnForm({
        mode: cfg.mode,
        host: cfg.host,
        port: cfg.port,
        username: cfg.username,
        password: cfg.password,
      });
      if (cfg.mode === "external") {
        setSourceTab("external");
      }
      setRepeater(rep);
      setPolicy(orch);
      setConfiguredUps(upsCfg.parsed?.ups_list ?? []);
      setUpsGlobalDirectives(upsCfg.parsed?.global_directives ?? []);
    } catch {
      // Ignore initial load error
    }
  }, []);

  useEffect(() => {
    void loadTopState();
  }, [loadTopState]);

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

  useEffect(() => {
    void loadFile(activeFile);
  }, [activeFile, loadFile]);

  const handleSelectFile = (file: NutConfigFile) => {
    setFeedback(null);
    setLoading(true);
    setParsedData(null);
    setLoadedFile(null);
    setActiveFile(file);
  };

  const updateDataAndPreview = async (next: Record<string, unknown>) => {
    setParsedData(next);
    try {
      const prev = await api.previewNutConfig(activeFile, next);
      setRawPreview(prev.raw);
    } catch {
      // Ignore preview validation until Save
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
        const d = err.detail as { message?: string; stderr?: string };
        setFeedback({ ok: false, msg: d.message || err.message, stderr: d.stderr });
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

  const handleSaveDisconnectPolicy = async () => {
    if (!policy) return;
    setSavingPolicy(true);
    setFeedback(null);
    try {
      const updated = await api.updateOrchestratorConfig(policy);
      setPolicy(updated);
      await loadTopState();
      setFeedback({
        ok: true,
        msg: "Saved UPS disconnection grace policy and Smart NUT Repeater settings.",
      });
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to save disconnect policy",
      });
    } finally {
      setSavingPolicy(false);
    }
  };

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

  const handleSaveConnection = async (mode: "local" | "external") => {
    setSavingConn(true);
    setFeedback(null);
    try {
      const saved = await api.saveNutConnection({ ...connForm, mode });
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
            ? `Joined existing NUT server (${saved.host}:${saved.port}). WebNUT Repeater mirrors it for all LAN clients.`
            : "Switched to built-in local NUT server.",
      });
      await onRefresh();
      await loadTopState();
    } catch (err) {
      setFeedback({
        ok: false,
        msg: err instanceof ApiError ? err.message : "Failed to switch NUT server mode",
      });
    } finally {
      setSavingConn(false);
    }
  };

  const handleSaveSnmp = async () => {
    setSavingSnmp(true);
    setFeedback(null);
    try {
      // Ensure local mode is active when configuring SNMP UPS locally
      if (connForm.mode === "external") {
        await api.saveNutConnection({ ...connForm, mode: "local" });
      }
      const res = await api.setupSnmpUps(snmpForm);
      setFeedback({ ok: true, msg: `SNMP UPS '${snmpForm.name}' configured: ${res.message}` });
      await loadFile("ups.conf");
      await loadTopState();
      await onRefresh();
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail !== null) {
        const d = err.detail as { message?: string; stderr?: string };
        setFeedback({ ok: false, msg: d.message || err.message, stderr: d.stderr });
      } else {
        setFeedback({
          ok: false,
          msg: err instanceof ApiError ? err.message : "Failed to configure SNMP UPS",
        });
      }
    } finally {
      setSavingSnmp(false);
    }
  };

  const handleScanSnmpSubnet = async () => {
    setScanningSnmp(true);
    setSnmpScanErr(null);
    setScannedSnmp([]);
    try {
      const res = await api.scanSnmp(snmpScanRange);
      setScannedSnmp(res);
    } catch (err) {
      setSnmpScanErr(err instanceof ApiError ? err.message : "SNMP scan failed");
    } finally {
      setScanningSnmp(false);
    }
  };

  const handleScanHardware = async () => {
    setScanOpen(true);
    setScanning(true);
    setScanError(null);
    setScanStderr(null);
    setScanResult(null);
    try {
      const [res, upsCfg] = await Promise.all([
        api.scanHardware(),
        api.getNutConfig<UpsConfData>("ups.conf"),
      ]);
      const existingUps = upsCfg.parsed?.ups_list ?? [];
      setConfiguredUps(existingUps);
      setUpsGlobalDirectives(upsCfg.parsed?.global_directives ?? []);
      setScanResult(res);

      const usbNames: Record<number, string> = {};
      res.nut_usb_devices.forEach((dev, idx) => {
        const match = findMatchingConfiguredUpsForUsb(dev, existingUps);
        usbNames[idx] = match ? match.name : suggestFriendlyUpsName(dev);
      });
      setUsbNameChoices(usbNames);

      const defaults: Record<string, string> = {};
      const serNames: Record<string, string> = {};
      for (const sd of res.serial_devices) {
        const mfrLower = `${sd.manufacturer ?? ""} ${sd.product ?? ""}`.toLowerCase();
        defaults[sd.path] = mfrLower.includes("apc")
          ? "apcsmart"
          : res.serial_drivers[0] || "nutdrv_qx";
        const match = findMatchingConfiguredUpsForSerial(sd, existingUps);
        serNames[sd.path] = match ? match.name : sanitizeUpsId(sd.suggested_name, "serial_ups");
      }
      setSerialDriverChoices(defaults);
      setSerialNameChoices(serNames);
    } catch (err) {
      setScanError(err instanceof ApiError ? err.message : "Hardware scan failed");
    } finally {
      setScanning(false);
    }
  };

  const handleRemoveUps = (upsName: string, keepScanModalOpen = false) => {
    setRemoveUpsTarget({ name: upsName, keepScanModalOpen });
  };

  const confirmRemoveUps = async () => {
    if (!removeUpsTarget) return;
    const { name: upsName, keepScanModalOpen } = removeUpsTarget;
    setRemovingUpsName(upsName);
    setScanError(null);
    setScanStderr(null);
    setFeedback(null);
    try {
      const loaded = await api.getNutConfig<UpsConfData>("ups.conf");
      const current = loaded.parsed;
      const next: UpsConfData = {
        global_directives: current.global_directives ?? upsGlobalDirectives,
        ups_list: (current.ups_list ?? []).filter((u) => u.name !== upsName),
      };
      const saved = await api.saveNutConfig("ups.conf", next, true);
      setConfiguredUps(next.ups_list);
      if (activeFile === "ups.conf") {
        setParsedData(next as unknown as Record<string, unknown>);
        setLoadedFile("ups.conf");
        setRawPreview(saved.raw);
      }
      setRemoveUpsTarget(null);
      setFeedback({ ok: true, msg: `Removed UPS '${upsName}' cleanly.` });
      await loadTopState();
      await onRefresh();
      if (!keepScanModalOpen) {
        setScanOpen(false);
      }
    } catch (err) {
      let msg = err instanceof Error ? err.message : `Failed to remove UPS '${upsName}'`;
      let stderr: string | undefined;
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail !== null) {
        const d = err.detail as { message?: string; stderr?: string };
        msg = d.message || err.message;
        stderr = d.stderr;
      }
      setRemoveUpsTarget(null);
      setScanError(msg);
      setScanStderr(stderr ?? null);
      setFeedback({ ok: false, msg, stderr });
    } finally {
      setRemovingUpsName(null);
    }
  };

  const openRenameUpsModal = (ups: UpsConfData["ups_list"][number]) => {
    setRenameTarget(ups);
    setRenameForm({ name: ups.name, desc: ups.desc || "" });
  };

  const handleSaveRenameUps = async () => {
    if (!renameTarget) return;
    const cleanName = sanitizeUpsId(renameForm.name, renameTarget.name);
    setRenaming(true);
    setFeedback(null);
    try {
      const loaded = await api.getNutConfig<UpsConfData>("ups.conf");
      const current = loaded.parsed;
      const duplicate = (current.ups_list ?? []).some(
        (u) => u.name === cleanName && u.name !== renameTarget.name
      );
      if (duplicate) {
        setFeedback({ ok: false, msg: `A UPS named '${cleanName}' already exists.` });
        return;
      }
      const next: UpsConfData = {
        global_directives: current.global_directives ?? upsGlobalDirectives,
        ups_list: (current.ups_list ?? []).map((u) =>
          u.name === renameTarget.name
            ? { ...u, name: cleanName, desc: renameForm.desc.trim() }
            : u
        ),
      };
      const saved = await api.saveNutConfig("ups.conf", next, true);
      setConfiguredUps(next.ups_list);
      if (activeFile === "ups.conf") {
        setParsedData(next as unknown as Record<string, unknown>);
        setLoadedFile("ups.conf");
        setRawPreview(saved.raw);
      }
      setRenameTarget(null);
      setFeedback({ ok: true, msg: `Renamed UPS to '${cleanName}'.` });
      await loadTopState();
      await onRefresh();
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail !== null) {
        const d = err.detail as { message?: string; stderr?: string };
        setFeedback({ ok: false, msg: d.message || err.message, stderr: d.stderr });
      } else {
        setFeedback({
          ok: false,
          msg: err instanceof Error ? err.message : "Failed to rename UPS",
        });
      }
    } finally {
      setRenaming(false);
    }
  };

  const _appendUpsBlock = async (
    block: UpsConfData["ups_list"][number],
    autoApply = false,
    deviceKey = "device"
  ) => {
    setAddingDevice(deviceKey);
    setScanError(null);
    setScanStderr(null);
    setFeedback(null);
    try {
      const loaded = await api.getNutConfig<UpsConfData>("ups.conf");
      const targetData = loaded.parsed;
      setActiveFile("ups.conf");
      const cleanRequested = sanitizeUpsId(block.name, "ups1");
      const existingNames = new Set((targetData.ups_list ?? []).map((u) => u.name));
      let name = cleanRequested;
      let idx = 2;
      while (existingNames.has(name)) {
        name = `${cleanRequested}_${idx++}`;
      }

      const next: UpsConfData = {
        global_directives: targetData.global_directives ?? [],
        ups_list: [...(targetData.ups_list ?? []), { ...block, name }],
      };
      if (autoApply) {
        if (connForm.mode === "external") {
          await api.saveNutConnection({ ...connForm, mode: "local" });
        }
        const saved = await api.saveNutConfig("ups.conf", next, true);
        setConfiguredUps(next.ups_list);
        setParsedData(next as unknown as Record<string, unknown>);
        setLoadedFile("ups.conf");
        setRawPreview(saved.raw);
        setScanOpen(false);
        setFeedback({ ok: true, msg: `Added '${name}' and started NUT drivers/upsd.` });
        await loadTopState();
        await onRefresh();
      } else {
        setConfiguredUps(next.ups_list);
        setParsedData(next as unknown as Record<string, unknown>);
        setLoadedFile("ups.conf");
        const prev = await api.previewNutConfig("ups.conf", next);
        setRawPreview(prev.raw);
        setScanOpen(false);
      }
    } catch (err) {
      let msg = err instanceof Error ? err.message : "Failed to add and start UPS";
      let stderr: string | undefined;
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail !== null) {
        const d = err.detail as { message?: string; stderr?: string };
        msg = d.message || err.message;
        stderr = d.stderr;
      }
      setScanError(msg);
      setScanStderr(stderr ?? null);
      setFeedback({ ok: false, msg, stderr });
    } finally {
      setAddingDevice(null);
    }
  };

  const handleAddScannedDevice = async (dev: ScannedUsbDevice, idx: number) => {
    const rawChosen = (usbNameChoices[idx] || "").trim();
    const friendlyId = sanitizeUpsId(rawChosen || suggestFriendlyUpsName(dev), "ups1");
    const directives = [];
    if (dev.vendorid) directives.push({ key: "vendorid", value: dev.vendorid });
    if (dev.productid) directives.push({ key: "productid", value: dev.productid });
    if (dev.serial) directives.push({ key: "serial", value: dev.serial });

    await _appendUpsBlock(
      {
        name: friendlyId,
        driver: dev.driver,
        port: dev.port || "auto",
        desc:
          (rawChosen && rawChosen !== friendlyId ? rawChosen : "") ||
          [dev.vendor, dev.product].filter(Boolean).join(" ") ||
          "USB UPS",
        directives,
      },
      true,
      `usb-${idx}`
    );
  };

  const handleAddSerialDevice = async (dev: SerialDeviceInfo) => {
    const rawChosen = (serialNameChoices[dev.path] || "").trim();
    const friendlyId = sanitizeUpsId(rawChosen || dev.suggested_name, "serial_ups");
    const driver = serialDriverChoices[dev.path] || "nutdrv_qx";
    await _appendUpsBlock(
      {
        name: friendlyId,
        driver,
        port: dev.path,
        desc:
          (rawChosen && rawChosen !== friendlyId ? rawChosen : "") ||
          [dev.manufacturer, dev.product].filter(Boolean).join(" ") ||
          `Serial UPS (${dev.path})`,
        directives: [],
      },
      true,
      `serial-${dev.path}`
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">One-Page UPS Setup</h2>
          <p className="text-sm text-slate-400">
            Configure USB/Serial, SNMP Network, or an Existing NUT Server — WebNUT repeats all
            streams cleanly to your LAN clients
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

      {/* Services & Repeater Status Strip */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Radio className="h-4 w-4 text-indigo-400 shrink-0" />
              <span className="font-mono text-sm font-semibold text-white">repeater</span>
              <span
                className={`text-[11px] px-2 py-0.5 rounded border uppercase font-semibold ${
                  repeater?.running
                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                    : "bg-rose-500/15 text-rose-300 border-rose-500/30"
                }`}
              >
                {repeater?.running ? `:${repeater.port}` : "offline"}
              </span>
            </div>
            <p className="text-[11px] text-slate-400 truncate mt-1" title={repeater?.error || ""}>
              {repeater?.error
                ? repeater.error
                : `${repeater?.mirrored_ups_count ?? 0} mirrored • ${
                    repeater?.virtual_ups_count ?? 0
                  } virtual stream(s)`}
            </p>
          </div>
        </div>

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

      {/* One-Page UPS Connection Source Wizard */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
          <div>
            <h3 className="text-base font-semibold text-white">UPS Connection Wizard</h3>
            <p className="text-xs text-slate-400">
              Choose how WebNUT connects to your UPS hardware
            </p>
          </div>
          <div className="inline-flex flex-wrap rounded-lg bg-surface-950 border border-slate-800 p-1 gap-1">
            <button
              type="button"
              onClick={() => setSourceTab("usb")}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium ${
                sourceTab === "usb"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Usb className="h-3.5 w-3.5" />
              USB / Serial UPS
            </button>
            <button
              type="button"
              onClick={() => setSourceTab("snmp")}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium ${
                sourceTab === "snmp"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Network className="h-3.5 w-3.5" />
              SNMP Network UPS
            </button>
            <button
              type="button"
              onClick={() => setSourceTab("external")}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium ${
                sourceTab === "external"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Globe className="h-3.5 w-3.5" />
              Existing NUT Server (Repeater)
            </button>
          </div>
        </div>

        {sourceTab === "usb" && (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-1 text-xs text-slate-300">
              <p>
                Click <strong>Scan Hardware (USB &amp; Serial)</strong> to detect USB HID UPS units,
                passed-through USB-Serial adapters (<code>/dev/ttyUSB*</code>,{" "}
                <code>/dev/webnut-ups-*</code>), and raw <code>lsusb</code> devices, then add your
                UPS in one click.
              </p>
              <p className="text-slate-500">
                Active mode:{" "}
                <span className="font-mono text-slate-300">
                  {connForm.mode === "local" ? "Built-in Local NUT Server" : "External Repeater"}
                </span>
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {connForm.mode === "external" && (
                <button
                  onClick={() => void handleSaveConnection("local")}
                  disabled={savingConn}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
                >
                  <HardDrive className="h-3.5 w-3.5" />
                  Switch to Local Mode
                </button>
              )}
              <button
                onClick={handleScanHardware}
                className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white"
              >
                Scan &amp; Add USB/Serial UPS
              </button>
            </div>
          </div>
        )}

        {sourceTab === "snmp" && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-slate-400">
                Connect directly to an SNMP-enabled UPS NMC card (APC AP9630/9640, Eaton, CyberPower,
                RFC 1628 IETF) using NUT&apos;s `snmp-ups` driver.
              </p>
              <button
                type="button"
                onClick={() => setSnmpScanOpen(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-sky-300"
              >
                <Search className="h-3.5 w-3.5" />
                Scan Subnet for SNMP UPS
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">UPS Name (ups.conf)</label>
                <input
                  value={snmpForm.name}
                  onChange={(e) => setSnmpForm({ ...snmpForm, name: e.target.value })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">UPS IP / Hostname</label>
                <input
                  value={snmpForm.host}
                  onChange={(e) => setSnmpForm({ ...snmpForm, host: e.target.value })}
                  placeholder="192.168.1.40"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">SNMP Version</label>
                <select
                  value={snmpForm.snmp_version}
                  onChange={(e) =>
                    setSnmpForm({
                      ...snmpForm,
                      snmp_version: e.target.value as "v1" | "v2c" | "v3",
                    })
                  }
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
                >
                  <option value="v2c">SNMP v2c</option>
                  <option value="v1">SNMP v1</option>
                  <option value="v3">SNMP v3</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">MIB Profile</label>
                <select
                  value={snmpForm.mibs}
                  onChange={(e) => setSnmpForm({ ...snmpForm, mibs: e.target.value })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                >
                  {SNMP_MIBS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>

              {snmpForm.snmp_version !== "v3" ? (
                <div>
                  <label className="block text-[11px] text-slate-400 mb-1">Community String</label>
                  <input
                    value={snmpForm.community}
                    onChange={(e) => setSnmpForm({ ...snmpForm, community: e.target.value })}
                    className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                  />
                </div>
              ) : (
                <>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Security Level</label>
                    <select
                      value={snmpForm.sec_level}
                      onChange={(e) =>
                        setSnmpForm({
                          ...snmpForm,
                          sec_level: e.target.value as SnmpUpsSetupPayload["sec_level"],
                        })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
                    >
                      <option value="authPriv">authPriv</option>
                      <option value="authNoPriv">authNoPriv</option>
                      <option value="noAuthNoPriv">noAuthNoPriv</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Security User</label>
                    <input
                      value={snmpForm.sec_name}
                      onChange={(e) => setSnmpForm({ ...snmpForm, sec_name: e.target.value })}
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Auth Password</label>
                    <input
                      type="password"
                      value={snmpForm.auth_password}
                      onChange={(e) =>
                        setSnmpForm({ ...snmpForm, auth_password: e.target.value })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Priv Password</label>
                    <input
                      type="password"
                      value={snmpForm.priv_password}
                      onChange={(e) =>
                        setSnmpForm({ ...snmpForm, priv_password: e.target.value })
                      }
                      className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                    />
                  </div>
                </>
              )}

              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Poll Freq (sec)</label>
                <input
                  type="number"
                  min={2}
                  max={300}
                  value={snmpForm.pollfreq}
                  onChange={(e) => setSnmpForm({ ...snmpForm, pollfreq: Number(e.target.value) })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="block text-[11px] text-slate-400 mb-1">Description</label>
                <input
                  value={snmpForm.desc}
                  onChange={(e) => setSnmpForm({ ...snmpForm, desc: e.target.value })}
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
                />
              </div>
            </div>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => void handleSaveSnmp()}
                disabled={savingSnmp || !snmpForm.host.trim()}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
              >
                <Save className="h-3.5 w-3.5" />
                {savingSnmp ? "Applying SNMP Config..." : "Save & Connect SNMP UPS"}
              </button>
            </div>
          </div>
        )}

        {sourceTab === "external" && (
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">
                  Existing NUT Server IP / Host
                </label>
                <input
                  value={connForm.host}
                  onChange={(e) => setConnForm({ ...connForm, host: e.target.value })}
                  placeholder="192.168.1.10"
                  className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Upstream Port</label>
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
                WebNUT polls this upstream NUT server and{" "}
                <strong>repeats virtual per-host streams</strong> on port{" "}
                <code>{repeater?.port ?? 3493}</code> so all your clients point to WebNUT.
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
        )}
      </div>

      {/* Configured UPS Devices Registry (Easy View, Rename & Remove) */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <HardDrive className="h-4 w-4 text-emerald-400" />
            <div>
              <h3 className="text-sm font-semibold text-white">Configured UPS Devices</h3>
              <p className="text-xs text-slate-400">
                Manage friendly names, inspect USB/Serial/SNMP hardware IDs, or remove a UPS in one
                click
              </p>
            </div>
          </div>
          <span className="text-xs font-mono text-slate-400">
            {configuredUps.length} configured
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-950/60 text-[11px] uppercase text-slate-400 border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Friendly Name / Description</th>
                <th className="px-4 py-3">Driver</th>
                <th className="px-4 py-3">Connection / Hardware ID</th>
                <th className="px-4 py-3">Live Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {configuredUps.map((u) => {
                const liveUps = telemetry.ups_list.find((t) => t.name === u.name);
                const vid = getUpsDirective(u, "vendorid");
                const pid = getUpsDirective(u, "productid");
                const sn = getUpsDirective(u, "serial");
                const hwBits = [
                  `port=${u.port}`,
                  vid && pid ? `USB ${vid}:${pid}` : "",
                  sn ? `SN:${sn}` : "",
                ]
                  .filter(Boolean)
                  .join(" • ");
                return (
                  <tr key={u.name} className="hover:bg-surface-800/40">
                    <td className="px-4 py-3">
                      <div className="font-mono font-semibold text-white">{u.name}</div>
                      {u.desc && <div className="text-slate-400 mt-0.5">{u.desc}</div>}
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
                        {u.driver}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-300">{hwBits}</td>
                    <td className="px-4 py-3">
                      {liveUps ? (
                        <span className="inline-flex items-center gap-1.5 font-mono text-emerald-300">
                          <span className="h-2 w-2 rounded-full bg-emerald-400" />
                          {liveUps.status}
                          {liveUps.battery_charge !== null
                            ? ` • ${liveUps.battery_charge.toFixed(0)}%`
                            : ""}
                        </span>
                      ) : connForm.mode === "external" ? (
                        <span className="text-slate-400">External Repeater Mode</span>
                      ) : (
                        <span className="text-slate-500">Not polling yet</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap space-x-1.5">
                      <button
                        type="button"
                        onClick={() => openRenameUpsModal(u)}
                        disabled={removingUpsName === u.name}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-slate-700 text-slate-200 disabled:opacity-50"
                      >
                        <Edit2 className="h-3 w-3" />
                        Rename
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleRemoveUps(u.name)}
                        disabled={removingUpsName === u.name}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300 disabled:opacity-50"
                      >
                        <Trash2 className="h-3 w-3" />
                        {removingUpsName === u.name ? "Removing..." : "Remove"}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {configuredUps.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-6 text-center text-slate-500">
                    No local UPS devices configured in <code>ups.conf</code> yet. Use{" "}
                    <strong>Scan &amp; Add USB/Serial UPS</strong> or{" "}
                    <strong>SNMP Network UPS</strong> above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Global UPS Disconnect Grace & Repeater Policy Card */}
      {policy && (
        <div className="rounded-2xl border border-slate-800 bg-surface-900 p-5 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-4 w-4 text-amber-400" />
              <div>
                <h3 className="text-sm font-semibold text-white">
                  UPS Disconnection Policy &amp; Repeater Port
                </h3>
                <p className="text-xs text-slate-400">
                  Control what clients do when USB/SNMP/UPS comms drop (e.g., keep services running
                  for 10 minutes before shutting down)
                </p>
              </div>
            </div>
            <button
              onClick={() => void handleSaveDisconnectPolicy()}
              disabled={savingPolicy}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
            >
              <Save className="h-3.5 w-3.5" />
              {savingPolicy ? "Saving..." : "Save Policy"}
            </button>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <label className="block text-[11px] text-slate-400 mb-1">
                Default UPS Disconnect Behavior
              </label>
              <select
                value={policy.disconnect_policy}
                onChange={(e) =>
                  setPolicy({
                    ...policy,
                    disconnect_policy: e.target.value as "keep_running" | "shutdown_after_grace",
                  })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
              >
                <option value="keep_running">Keep Services Running &amp; Alert Only</option>
                <option value="shutdown_after_grace">
                  Keep Running for Grace Time, Then Shut Down
                </option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-slate-400 mb-1">
                Disconnect Grace Time (minutes)
              </label>
              <input
                type="number"
                min={0}
                max={1440}
                value={Math.round(policy.disconnect_grace_sec / 60)}
                onChange={(e) =>
                  setPolicy({
                    ...policy,
                    disconnect_grace_sec: Math.max(0, Number(e.target.value)) * 60,
                  })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
              />
            </div>
            <div>
              <label className="block text-[11px] text-slate-400 mb-1">
                Smart NUT Repeater Port
              </label>
              <input
                type="number"
                min={1}
                max={65535}
                value={policy.repeater_port}
                onChange={(e) => setPolicy({ ...policy, repeater_port: Number(e.target.value) })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div className="flex items-end pb-2">
              <label className="inline-flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={policy.repeater_enabled}
                  onChange={(e) => setPolicy({ ...policy, repeater_enabled: e.target.checked })}
                  className="rounded border-slate-600 bg-surface-950 text-indigo-500"
                />
                Enable Smart NUT Repeater
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Collapsible Advanced 5-File Config Editor */}
      <div className="rounded-2xl border border-slate-800 bg-surface-900 overflow-hidden">
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="w-full px-5 py-3.5 flex items-center justify-between text-left hover:bg-surface-800/40"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            {showAdvanced ? (
              <ChevronDown className="h-4 w-4 text-slate-400" />
            ) : (
              <ChevronRight className="h-4 w-4 text-slate-400" />
            )}
            <span>Advanced NUT Config Files (`/etc/nut/*`)</span>
          </div>
          <span className="text-xs text-slate-400">
            Direct form &amp; raw preview editor with atomic backup/rollback
          </span>
        </button>

        {showAdvanced && (
          <div className="p-5 border-t border-slate-800 space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
              <div className="flex flex-wrap gap-1.5">
                {CONFIG_TABS.map((f) => (
                  <button
                    key={f}
                    onClick={() => handleSelectFile(f)}
                    className={`px-3.5 py-2 rounded-lg font-mono text-xs font-medium transition-colors ${
                      activeFile === f
                        ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/40"
                        : "bg-surface-950 text-slate-400 hover:text-white border border-slate-800"
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

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
              <div className="xl:col-span-7 rounded-xl border border-slate-800 bg-surface-950 p-5">
                {loading || !parsedData || loadedFile !== activeFile ? (
                  <div className="text-sm text-slate-400 py-8 text-center">
                    Loading {activeFile}...
                  </div>
                ) : (
                  <NutFormRenderer
                    file={activeFile}
                    data={parsedData}
                    onChange={(next) => void updateDataAndPreview(next)}
                  />
                )}
              </div>

              <div className="xl:col-span-5 rounded-xl border border-slate-800 bg-surface-950 overflow-hidden flex flex-col">
                <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Raw Preview ({activeFile})
                  </span>
                  <span className="text-[11px] text-slate-500 font-mono">/etc/nut/{activeFile}</span>
                </div>
                <pre className="p-4 text-xs font-mono text-slate-300 flex-1 overflow-auto min-h-[320px] whitespace-pre-wrap">
                  {rawPreview || "# Empty configuration"}
                </pre>
              </div>
            </div>
          </div>
        )}
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
        ) : (
          <div className="space-y-6">
            {scanError && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs text-rose-300 space-y-2">
                <div className="font-semibold flex items-center gap-2">
                  <AlertOctagon className="h-4 w-4 shrink-0" />
                  <span>{scanError}</span>
                </div>
                {scanStderr && (
                  <pre className="p-2.5 rounded-lg bg-surface-950 border border-rose-500/30 font-mono text-rose-200 overflow-x-auto whitespace-pre-wrap">
                    {scanStderr}
                  </pre>
                )}
              </div>
            )}
            {scanResult && (
              <div className="space-y-6">
            {scanResult.warnings.length > 0 && (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200 space-y-1">
                {scanResult.warnings.map((w, i) => (
                  <div key={i}>{w}</div>
                ))}
              </div>
            )}

            <div className="space-y-2.5">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Cpu className="h-3.5 w-3.5 text-indigo-400" />
                NUT Auto-Detected USB UPS ({scanResult.nut_usb_devices.length})
              </h4>
              {scanResult.nut_usb_devices.length === 0 ? (
                <p className="text-xs text-slate-500 rounded-lg border border-slate-800 bg-surface-950 p-3">
                  No native USB HID UPS detected by nut-scanner. Check Serial Ports below if using
                  a USB-Serial adapter.
                </p>
              ) : (
                scanResult.nut_usb_devices.map((dev, idx) => {
                  const matched = findMatchingConfiguredUpsForUsb(dev, configuredUps);
                  return (
                    <div
                      key={`${dev.driver}-${idx}`}
                      className={`rounded-xl border p-3.5 space-y-3 ${
                        matched
                          ? "border-emerald-500/40 bg-emerald-500/5"
                          : "border-slate-700 bg-surface-950"
                      }`}
                    >
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-semibold text-white text-sm">
                              {dev.product || dev.suggested_name}
                            </span>
                            <span className="text-xs font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded">
                              {dev.driver}
                            </span>
                            {matched && (
                              <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                                <CheckCircle2 className="h-3 w-3" />
                                Already Configured as &apos;{matched.name}&apos;
                              </span>
                            )}
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

                        {matched ? (
                          <div className="flex items-center gap-2 shrink-0">
                            <button
                              type="button"
                              onClick={() => openRenameUpsModal(matched)}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
                            >
                              <Edit2 className="h-3 w-3" />
                              Rename
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleRemoveUps(matched.name, true)}
                              disabled={removingUpsName === matched.name}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-xs text-rose-300 disabled:opacity-50"
                            >
                              <Trash2 className="h-3 w-3" />
                              {removingUpsName === matched.name ? "Removing..." : "Remove"}
                            </button>
                          </div>
                        ) : (
                          <div className="flex flex-wrap items-center gap-2 shrink-0">
                            <input
                              value={usbNameChoices[idx] ?? ""}
                              onChange={(e) =>
                                setUsbNameChoices((prev) => ({
                                  ...prev,
                                  [idx]: e.target.value,
                                }))
                              }
                              placeholder="Friendly name (e.g. main_ups)"
                              className="w-44 rounded-lg bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                            />
                            <button
                              onClick={() => void handleAddScannedDevice(dev, idx)}
                              disabled={addingDevice !== null}
                              className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white shrink-0 disabled:opacity-50"
                            >
                              {addingDevice === `usb-${idx}`
                                ? "Adding & Starting..."
                                : "Add & Start"}
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            <div className="space-y-2.5">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Cable className="h-3.5 w-3.5 text-sky-400" />
                Serial &amp; USB-Serial Ports ({scanResult.serial_devices.length})
              </h4>
              {scanResult.serial_devices.length === 0 ? (
                <div className="text-xs text-slate-400 rounded-lg border border-slate-800 bg-surface-950 p-3">
                  No passed-through serial devices found. Run{" "}
                  <code className="text-indigo-300">./scripts/add-usb-serial.sh</code> on your
                  Docker host to attach a USB-Serial adapter.
                </div>
              ) : (
                scanResult.serial_devices.map((sd) => {
                  const matched = findMatchingConfiguredUpsForSerial(sd, configuredUps);
                  return (
                    <div
                      key={sd.path}
                      className={`rounded-xl border p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                        matched
                          ? "border-emerald-500/40 bg-emerald-500/5"
                          : "border-slate-700 bg-surface-950"
                      }`}
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
                          {matched && (
                            <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                              <CheckCircle2 className="h-3 w-3" />
                              Already Configured as &apos;{matched.name}&apos;
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-slate-400 font-mono truncate">
                          {[
                            sd.manufacturer,
                            sd.product,
                            sd.vendor_id && sd.product_id && `${sd.vendor_id}:${sd.product_id}`,
                            sd.serial && `SN:${sd.serial}`,
                          ]
                            .filter(Boolean)
                            .join(" • ") || "Serial TTY device"}
                        </p>
                      </div>
                      {matched ? (
                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            type="button"
                            onClick={() => openRenameUpsModal(matched)}
                            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-slate-200"
                          >
                            <Edit2 className="h-3 w-3" />
                            Rename
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleRemoveUps(matched.name, true)}
                            disabled={removingUpsName === matched.name}
                            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-xs text-rose-300 disabled:opacity-50"
                          >
                            <Trash2 className="h-3 w-3" />
                            {removingUpsName === matched.name ? "Removing..." : "Remove"}
                          </button>
                        </div>
                      ) : (
                        <div className="flex flex-wrap items-center gap-2 shrink-0">
                          <input
                            value={serialNameChoices[sd.path] ?? ""}
                            onChange={(e) =>
                              setSerialNameChoices((prev) => ({
                                ...prev,
                                [sd.path]: e.target.value,
                              }))
                            }
                            placeholder="Friendly name"
                            className="w-36 rounded-lg bg-surface-900 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                          />
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
                            disabled={addingDevice !== null}
                            className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
                          >
                            {addingDevice === `serial-${sd.path}`
                              ? "Adding & Starting..."
                              : "Add & Start"}
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>

            <div className="space-y-2.5">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Usb className="h-3.5 w-3.5 text-emerald-400" />
                All Container USB Devices — lsusb ({scanResult.raw_usb_devices.length})
              </h4>
              {scanResult.raw_usb_devices.length > 0 && (
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
            </div>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* SNMP Subnet Scan Modal */}
      <Modal
        open={snmpScanOpen}
        title="Scan Subnet for SNMP UPS (`nut-scanner -S`)"
        onClose={() => setSnmpScanOpen(false)}
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Start IP</label>
              <input
                value={snmpScanRange.start_ip}
                onChange={(e) => setSnmpScanRange({ ...snmpScanRange, start_ip: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">End IP</label>
              <input
                value={snmpScanRange.end_ip}
                onChange={(e) => setSnmpScanRange({ ...snmpScanRange, end_ip: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Community</label>
              <input
                value={snmpScanRange.community}
                onChange={(e) => setSnmpScanRange({ ...snmpScanRange, community: e.target.value })}
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Version</label>
              <select
                value={snmpScanRange.snmp_version}
                onChange={(e) =>
                  setSnmpScanRange({
                    ...snmpScanRange,
                    snmp_version: e.target.value as "v1" | "v2c" | "v3",
                  })
                }
                className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-xs text-white"
              >
                <option value="v2c">v2c</option>
                <option value="v1">v1</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end">
            <button
              onClick={() => void handleScanSnmpSubnet()}
              disabled={scanningSnmp}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
            >
              {scanningSnmp ? "Scanning Subnet..." : "Start SNMP Scan"}
            </button>
          </div>

          {snmpScanErr && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
              {snmpScanErr}
            </div>
          )}

          {scannedSnmp.length > 0 && (
            <div className="space-y-2">
              {scannedSnmp.map((d, i) => (
                <div
                  key={`${d.port}-${i}`}
                  className="rounded-xl border border-slate-700 bg-surface-950 p-3 flex items-center justify-between gap-3"
                >
                  <div className="text-xs">
                    <div className="font-mono text-white font-semibold">{d.port}</div>
                    <div className="text-slate-400">
                      MIB: {d.mibs} • {d.snmp_version}
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      setSnmpForm((prev) => ({
                        ...prev,
                        name: d.suggested_name,
                        host: d.port,
                        mibs: d.mibs || "auto",
                        community: d.community || prev.community,
                      }));
                      setSnmpScanOpen(false);
                    }}
                    className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs text-white"
                  >
                    Use This UPS
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>

      {/* Rename UPS Modal */}
      <Modal
        open={renameTarget !== null}
        title={renameTarget ? `Rename UPS — ${renameTarget.name}` : "Rename UPS"}
        onClose={() => setRenameTarget(null)}
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              UPS Stream Name (ID in ups.conf &amp; Repeater)
            </label>
            <input
              value={renameForm.name}
              onChange={(e) => setRenameForm({ ...renameForm, name: e.target.value })}
              placeholder="e.g. apc_smart_ups"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm font-mono text-white"
            />
            <p className="text-[11px] text-slate-500 mt-1">
              Normalized to valid NUT ID characters: <code>{sanitizeUpsId(renameForm.name || "ups1")}</code>
            </p>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Description (Optional)</label>
            <input
              value={renameForm.desc}
              onChange={(e) => setRenameForm({ ...renameForm, desc: e.target.value })}
              placeholder="Rack APC Smart-UPS 1000"
              className="w-full rounded-lg bg-surface-950 border border-slate-700 px-3 py-2 text-sm text-white"
            />
          </div>
          <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              type="button"
              onClick={() => setRenameTarget(null)}
              className="px-4 py-2 rounded-lg bg-surface-800 text-xs text-slate-300"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleSaveRenameUps()}
              disabled={renaming || !renameForm.name.trim()}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white disabled:opacity-50"
            >
              {renaming ? "Applying..." : "Save & Restart Drivers"}
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={removeUpsTarget !== null}
        title="Remove Configured UPS"
        message={
          removeUpsTarget
            ? `Remove UPS '${removeUpsTarget.name}' from ups.conf and stop its NUT driver?`
            : ""
        }
        confirmLabel="Remove UPS"
        variant="danger"
        busy={removingUpsName !== null}
        onConfirm={confirmRemoveUps}
        onCancel={() => setRemoveUpsTarget(null)}
      />
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
            className="w-full rounded-lg bg-surface-900 border border-slate-700 px-3 py-2 text-sm text-white"
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

        {upsList.map((ups, idx) => (
          <div key={idx} className="rounded-xl border border-slate-800 bg-surface-900 p-4 space-y-3">
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
                  className="w-full rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Driver</label>
                <input
                  value={ups.driver ?? ""}
                  onChange={(e) => updateUps(idx, { driver: e.target.value })}
                  className="w-full rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Port</label>
                <input
                  value={ups.port ?? ""}
                  onChange={(e) => updateUps(idx, { port: e.target.value })}
                  className="w-full rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Description</label>
                <input
                  value={ups.desc ?? ""}
                  onChange={(e) => updateUps(idx, { desc: e.target.value })}
                  className="w-full rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs text-white"
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
                listens: [...listens, { address: "127.0.0.1", port: 3494 }],
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
              className="flex-1 rounded bg-surface-900 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
            />
            <input
              type="number"
              value={l.port ?? 3494}
              onChange={(e) => {
                const next = [...listens];
                next[idx] = { ...next[idx], port: Number(e.target.value) };
                onChange({ ...d, listens: next } as unknown as Record<string, unknown>);
              }}
              className="w-24 rounded bg-surface-900 border border-slate-700 px-3 py-2 text-xs font-mono text-white"
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
          <div key={idx} className="rounded-xl border border-slate-800 bg-surface-900 p-4 space-y-3">
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
              <input
                value={u.username ?? ""}
                placeholder="username"
                onChange={(e) => updateUser(idx, { username: e.target.value })}
                className="rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
              />
              <input
                type="password"
                value={u.password ?? ""}
                placeholder="password"
                onChange={(e) => updateUser(idx, { password: e.target.value })}
                className="rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
              />
              <select
                value={u.upsmon_role ?? ""}
                onChange={(e) =>
                  updateUser(idx, {
                    upsmon_role: e.target.value as "" | "primary" | "secondary",
                  })
                }
                className="rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs text-white"
              >
                <option value="">None</option>
                <option value="primary">primary</option>
                <option value="secondary">secondary</option>
              </select>
              <input
                value={(u.actions ?? []).join(" ")}
                placeholder="actions (e.g. SET)"
                onChange={(e) =>
                  updateUser(idx, { actions: e.target.value.split(/\s+/).filter(Boolean) })
                }
                className="rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
              />
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
            className="w-full rounded bg-surface-900 border border-slate-700 px-3 py-1.5 text-xs text-white"
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
            className="w-full rounded bg-surface-900 border border-slate-700 px-3 py-1.5 text-xs text-white"
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
                    ups_spec: "ups1@127.0.0.1:3494",
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
            className="rounded-xl border border-slate-800 bg-surface-900 p-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5"
          >
            <input
              value={m.ups_spec ?? ""}
              placeholder="ups@host"
              onChange={(e) => {
                const next = [...monitors];
                next[idx] = { ...next[idx], ups_spec: e.target.value };
                onChange({ ...d, monitors: next } as unknown as Record<string, unknown>);
              }}
              className="rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
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
                className="flex-1 rounded bg-surface-950 border border-slate-700 px-2.5 py-1.5 text-xs font-mono text-white"
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
          </div>
        ))}
      </div>
    </div>
  );
}
