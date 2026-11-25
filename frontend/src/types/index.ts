export type NutConfigFile =
  | "nut.conf"
  | "ups.conf"
  | "upsd.conf"
  | "upsd.users"
  | "upsmon.conf";

export interface NutConfData {
  mode: "none" | "standalone" | "netserver" | "netclient";
  extra_lines: string[];
}

export interface UpsDirective {
  key: string;
  value: string;
}

export interface UpsDefinition {
  name: string;
  driver: string;
  port: string;
  desc: string;
  directives: UpsDirective[];
}

export interface UpsConfData {
  global_directives: UpsDirective[];
  ups_list: UpsDefinition[];
}

export interface UpsdListenEntry {
  address: string;
  port: number;
}

export interface UpsdConfData {
  listens: UpsdListenEntry[];
  maxage: number | null;
  maxconn: number | null;
  statepath: string | null;
  extra_lines: string[];
}

export interface UpsdUserEntry {
  username: string;
  password: string;
  actions: string[];
  instcmds: string[];
  upsmon_role: "" | "primary" | "secondary";
}

export interface UpsdUsersData {
  users: UpsdUserEntry[];
}

export interface UpsmonMonitorEntry {
  ups_spec: string;
  power_value: number;
  username: string;
  password: string;
  role: "primary" | "secondary";
}

export interface UpsmonNotifyFlag {
  event: string;
  flags: string;
}

export interface UpsmonConfData {
  run_as_user: string;
  minsupplies: number;
  shutdowncmd: string;
  notifycmd: string;
  pollfreq: number;
  pollfreqalert: number;
  hostsync: number;
  deadtime: number;
  finaldelay: number;
  powerdownflag: string;
  monitors: UpsmonMonitorEntry[];
  notifyflags: UpsmonNotifyFlag[];
  extra_lines: string[];
}

export interface ScannedUsbDevice {
  suggested_name: string;
  driver: string;
  port: string;
  vendorid?: string | null;
  productid?: string | null;
  product?: string | null;
  serial?: string | null;
  vendor?: string | null;
  bus?: string | null;
  device?: string | null;
}

export interface SerialDeviceInfo {
  path: string;
  real_path: string;
  by_id_path: string | null;
  suggested_name: string;
  vendor_id: string | null;
  product_id: string | null;
  manufacturer: string | null;
  product: string | null;
  serial: string | null;
  accessible: boolean;
}

export interface RawUsbDevice {
  bus: string;
  device: string;
  vendor_id: string;
  product_id: string;
  description: string;
}

export interface HardwareScanResponse {
  nut_usb_devices: ScannedUsbDevice[];
  serial_devices: SerialDeviceInfo[];
  raw_usb_devices: RawUsbDevice[];
  serial_drivers: string[];
  warnings: string[];
}

export interface NutServiceStatus {
  name: string;
  status: "running" | "stopped" | "error" | "unavailable";
  detail: string;
}

export type NutConnectionMode = "local" | "external";

export interface NutConnectionConfigPayload {
  mode: NutConnectionMode;
  host: string;
  port: number;
  username: string;
  password: string;
}

export interface NutConnectionConfigView extends NutConnectionConfigPayload {
  password_configured: boolean;
}

export interface NutConnectionTestResponse {
  ok: boolean;
  message: string;
  ups_list: UpsTelemetry[];
}

export interface UpsTelemetry {
  name: string;
  description: string;
  status: string;
  status_tokens: string[];
  battery_charge: number | null;
  battery_runtime: number | null;
  ups_load: number | null;
  input_voltage: number | null;
  output_voltage: number | null;
  ups_mfr: string | null;
  ups_model: string | null;
  raw_vars: Record<string, string>;
  connected_clients: string[];
}

export interface TelemetrySnapshot {
  connected: boolean;
  error: string | null;
  ups_list: UpsTelemetry[];
  updated_at: string;
}

export type NutClientPlatform =
  | "linux_debian"
  | "linux_rhel"
  | "truenas"
  | "synology"
  | "windows";

export type WakeState =
  | "IDLE"
  | "WAITING_STAGGER"
  | "WAKING"
  | "RETRYING"
  | "WAKE_SUCCEEDED"
  | "WAKE_FAILED";

export interface HostPayload {
  name: string;
  ip_or_host: string;
  mac_address: string;
  subnet_broadcast: string | null;
  boot_priority: number;
  stagger_delay_sec: number;
  check_method: "icmp" | "tcp";
  check_port: number | null;
  boot_timeout_sec: number;
  retry_interval_sec: number;
  max_retries: number;
  always_wake: boolean;
  enabled: boolean;
  nut_client_enabled: boolean;
  nut_client_platform: NutClientPlatform;
  nut_ups_name: string;
  nut_client_user: string;
}

export interface HostRecord extends HostPayload {
  id: number;
  is_online: boolean;
  last_seen_at: string | null;
  was_online_before_outage: boolean;
  wake_state: WakeState;
  wake_attempt: number;
  last_wake_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ArpLookupResponse {
  ip_or_host: string;
  resolved_ip: string | null;
  mac_address: string | null;
  found: boolean;
  source: string;
}

export interface OrchestratorConfig {
  monitored_ups: string;
  min_battery_charge: number;
  stability_hold_sec: number;
  host_poll_interval_sec: number;
  ups_poll_interval_sec: number;
  auto_recovery_enabled: boolean;
}

export interface OrchestratorState {
  state: "NORMAL" | "ON_BATTERY" | "STABILIZING" | "RECOVERING";
  last_ups_status: string;
  outage_started_at: string | null;
  stabilizing_started_at: string | null;
  recovery_started_at: string | null;
  snapshot_frozen: boolean;
  is_dry_run: boolean;
  stability_remaining_sec: number;
  updated_at: string;
}

export interface PushoverConfigView {
  enabled: boolean;
  user_key_configured: boolean;
  api_token_configured: boolean;
  user_key: string;
  api_token: string;
  device: string;
  sound: string;
  emergency_retry_sec: number;
  emergency_expire_sec: number;
}

export interface PushoverConfigPayload {
  enabled: boolean;
  user_key: string;
  api_token: string;
  device: string;
  sound: string;
  emergency_retry_sec: number;
  emergency_expire_sec: number;
}

export interface NotificationRuleItem {
  event_type: string;
  enabled: boolean;
  priority: number;
}

export interface NotificationSettings {
  pushover: PushoverConfigView;
  rules: NotificationRuleItem[];
}

export interface EventLogEntry {
  id: number;
  timestamp: string;
  category: string;
  severity: "info" | "warning" | "error" | "success";
  event_type: string;
  message: string;
  details: Record<string, unknown> | null;
}

export interface EventLogPage {
  items: EventLogEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface ConnectedNutClient {
  ups_name: string;
  client_ip: string;
  matched_host_id: number | null;
  matched_host_name: string | null;
}

export interface NutClientsOverview {
  server_ip: string;
  nut_port: number;
  ups_names: string[];
  available_users: string[];
  connected_clients: ConnectedNutClient[];
}

export interface CreateSecondaryUserResponse {
  username: string;
  password: string;
  upsmon_role: "secondary";
  applied: boolean;
  message: string;
}

export interface NutClientConfigRequest {
  platform: NutClientPlatform;
  server_host: string;
  nut_port: number;
  ups_name: string;
  username: string;
  password: string;
  final_delay: number;
  poll_freq: number;
  deadtime: number;
  shutdown_cmd: string;
}

export interface GeneratedConfigFile {
  path: string;
  content: string;
}

export interface GeneratedGuiField {
  label: string;
  value: string;
}

export interface NutClientConfigResponse {
  platform: NutClientPlatform;
  title: string;
  files: GeneratedConfigFile[];
  one_liner: string | null;
  gui_fields: GeneratedGuiField[];
  notes: string[];
}

export interface AppVersionStatus {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_url: string | null;
  release_notes: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  notified_version: string | null;
  notified_at: string | null;
  github_repo: string;
}
