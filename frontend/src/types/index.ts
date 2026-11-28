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

export interface ScannedSnmpDevice {
  suggested_name: string;
  driver: string;
  port: string;
  mibs: string;
  community: string;
  snmp_version: string;
  desc: string;
}

export interface SnmpUpsSetupPayload {
  name: string;
  host: string;
  snmp_version: "v1" | "v2c" | "v3";
  mibs: string;
  community: string;
  sec_level: "noAuthNoPriv" | "authNoPriv" | "authPriv";
  sec_name: string;
  auth_protocol: "MD5" | "SHA" | "SHA256";
  auth_password: string;
  priv_protocol: "DES" | "AES";
  priv_password: string;
  desc: string;
  pollfreq: number;
}

export interface RepeaterClientSession {
  ups_name: string;
  client_ip: string;
  username: string;
  connected_at: string;
}

export interface RepeaterStatusResponse {
  running: boolean;
  port: number;
  error: string | null;
  mirrored_ups_count: number;
  virtual_ups_count: number;
  connected_clients: RepeaterClientSession[];
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
  source_node_id?: number | null;
  source_node_name?: string | null;
  source_node_slug?: string | null;
  remote_ups_name?: string | null;
  node_online?: boolean;
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
  | "WAITING_BATTERY"
  | "WAKING"
  | "RETRYING"
  | "WAKE_SUCCEEDED"
  | "WAKE_FAILED";

export type ShutdownMode = "nut_stream" | "ssh" | "both" | "disabled";
export type HostDisconnectPolicy = "inherit" | "keep_running" | "shutdown_after_grace";

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
  virtual_ups_name: string;
  wake_min_battery_charge: number | null;
  shutdown_mode: ShutdownMode;
  shutdown_battery_pct: number | null;
  shutdown_ob_delay_sec: number | null;
  disconnect_policy: HostDisconnectPolicy;
  disconnect_grace_sec: number | null;
  ssh_port: number;
  ssh_username: string;
  ssh_password: string;
  ssh_sudo_password: string;
  ssh_shutdown_cmd: string;
}

export interface HostRecord extends HostPayload {
  id: number;
  ssh_password_configured: boolean;
  is_online: boolean;
  last_seen_at: string | null;
  was_online_before_outage: boolean;
  wake_state: WakeState;
  wake_attempt: number;
  last_wake_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface SshDeployRequest {
  ssh_port: number;
  ssh_username: string;
  ssh_password: string;
  ssh_sudo_password: string;
  save_credentials: boolean;
  nut_username: string;
  nut_password: string;
  final_delay: number;
  shutdown_cmd: string;
}

export interface SshDeployResponse {
  ok: boolean;
  steps: string[];
  error: string | null;
  virtual_ups_name: string;
  server_host: string;
  nut_port: number;
}

export interface HostTestRunResponse {
  host_id: number;
  host_name: string;
  ip_or_host: string;
  dry_run: boolean;
  reachable: boolean;
  check_method: string;
  virtual_ups_name: string;
  nut_client_connected: boolean;
  current_battery_charge: number | null;
  effective_wake_battery_pct: number;
  would_wake_now: boolean;
  wol_targets: string[];
  shutdown_rule_summary: string;
  disconnect_rule_summary: string;
  started_live_wake: boolean;
  notes: string[];
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
  disconnect_policy: "keep_running" | "shutdown_after_grace";
  disconnect_grace_sec: number;
  repeater_enabled: boolean;
  repeater_port: number;
  cold_start_boot_grace_sec: number;
  cold_start_resume_recovery: boolean;
  cold_start_wake_always_wake: boolean;
}

export interface OrchestratorState {
  state: "NORMAL" | "ON_BATTERY" | "STABILIZING" | "RECOVERING";
  last_ups_status: string;
  outage_started_at: string | null;
  stabilizing_started_at: string | null;
  recovery_started_at: string | null;
  disconnect_started_at: string | null;
  disconnect_elapsed_sec: number;
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

export interface SavedMonitorAccount {
  username: string;
  role: string;
  notes: string;
  has_password: boolean;
  source: "saved" | "upsd";
  updated_at: string | null;
}

export interface MonitorAccountRevealResponse {
  username: string;
  password: string;
  role: string;
  notes: string;
}

export interface NutClientsOverview {
  server_ip: string;
  nut_port: number;
  ups_names: string[];
  available_users: string[];
  saved_accounts: SavedMonitorAccount[];
  connected_clients: ConnectedNutClient[];
}

export interface CreateSecondaryUserResponse {
  username: string;
  password: string;
  notes?: string;
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
  host_id?: number | null;
  device_name?: string;
  save_record?: boolean;
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
  record_id?: number | null;
}

export interface RecordedClientConfigSummary {
  id: number;
  host_id: number | null;
  device_name: string;
  platform: NutClientPlatform;
  server_host: string;
  nut_port: number;
  ups_name: string;
  username: string;
  final_delay: number;
  poll_freq: number;
  deadtime: number;
  shutdown_cmd: string;
  delivery_method: "manual" | "ssh_deploy";
  deploy_status: "generated" | "deployed" | "failed";
  created_at: string;
  updated_at: string;
}

export interface RecordedClientConfigDetail extends RecordedClientConfigSummary {
  config: NutClientConfigResponse;
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

export type HomepagePreset = "power" | "homelab" | "recovery" | "detailed" | "custom";
export type HomepageDisplayMode = "block" | "list";

export interface IntegrationSettingsView {
  api_key_enabled: boolean;
  api_key_configured: boolean;
  api_key_preview: string;
  homepage_enabled: boolean;
  homepage_default_ups: string;
  homepage_refresh_ms: number;
  homeassistant_enabled: boolean;
  prometheus_enabled: boolean;
  instance_id: string;
  server_url: string;
  web_port: number;
  ups_names: string[];
}

export interface IntegrationSettingsUpdatePayload {
  api_key_enabled: boolean;
  homepage_enabled: boolean;
  homepage_default_ups: string;
  homepage_refresh_ms: number;
  homeassistant_enabled: boolean;
  prometheus_enabled: boolean;
}

export interface IntegrationApiKeyResponse {
  api_key: string;
  api_key_preview: string;
  api_key_enabled: boolean;
}

export interface HomepageSummary {
  connected: boolean;
  ups_name: string;
  ups_model: string;
  ups_status: string;
  ups_status_label: string;
  on_battery: boolean;
  low_battery: boolean;
  battery_charge: number | null;
  ups_load: number | null;
  battery_runtime_sec: number | null;
  battery_runtime_min: number | null;
  input_voltage: number | null;
  output_voltage: number | null;
  realpower_watts: number | null;
  orchestrator_state: string;
  orchestrator_state_label: string;
  hosts_online: number;
  hosts_total: number;
  hosts_summary: string;
  hosts_waking: number;
  repeater_running: boolean;
  clients_connected: number;
  ups_count: number;
  updated_at: string;
}

export interface HomepageFieldOption {
  field: string;
  label: string;
  format: "text" | "number" | "float" | "percent" | "duration";
  suffix?: string | null;
  description: string;
}

export interface HomepageConfigRequest {
  service_name: string;
  description: string;
  icon: string;
  base_url: string;
  href_url: string;
  ups_name: string;
  preset: HomepagePreset;
  display: HomepageDisplayMode;
  selected_fields: string[];
  refresh_interval_ms: number;
  reveal_api_key: boolean;
}

export interface HomepageConfigResponse {
  yaml: string;
  endpoint_url: string;
  requires_api_key: boolean;
  display: HomepageDisplayMode;
  mappings: HomepageFieldOption[];
  sample_payload: HomepageSummary;
  notes: string[];
}

export interface HomeAssistantInfo {
  instance_id: string;
  name: string;
  version: string;
  server_url: string;
  api_key_required: boolean;
  homeassistant_enabled: boolean;
}

export interface HomeAssistantUps {
  name: string;
  description: string;
  manufacturer: string;
  model: string;
  status: string;
  status_label: string;
  on_battery: boolean;
  low_battery: boolean;
  battery_charge: number | null;
  battery_runtime: number | null;
  battery_voltage: number | null;
  ups_load: number | null;
  input_voltage: number | null;
  output_voltage: number | null;
  realpower_watts: number | null;
}

export interface HomeAssistantHost {
  id: number;
  name: string;
  ip_or_host: string;
  enabled: boolean;
  is_online: boolean;
  wake_state: string;
  last_seen_at: string | null;
  virtual_ups_name: string;
}

export interface HomeAssistantOrchestrator {
  state: string;
  state_label: string;
  last_ups_status: string;
  stability_remaining_sec: number;
  disconnect_elapsed_sec: number;
  auto_recovery_enabled: boolean;
}

export interface HomeAssistantState {
  instance_id: string;
  version: string;
  latest_version: string | null;
  update_available: boolean;
  connected: boolean;
  error: string | null;
  orchestrator: HomeAssistantOrchestrator;
  repeater_running: boolean;
  clients_connected: number;
  hosts_online: number;
  hosts_total: number;
  ups: HomeAssistantUps[];
  hosts: HomeAssistantHost[];
  updated_at: string;
}

export interface PrometheusConfigRequest {
  job_name: string;
  target_host: string;
  scheme: "http" | "https";
  scrape_interval_sec: number;
  metrics_path: "/metrics" | "/api/integrations/prometheus/metrics";
  reveal_api_key: boolean;
}

export interface PrometheusConfigResponse {
  scrape_yaml: string;
  metrics_url: string;
  requires_api_key: boolean;
  sample_metrics: string;
  notes: string[];
}

export type UpsCommandRisk = "safe" | "dangerous";
export type UpsCommandCategory = "selftest" | "beeper" | "calibration" | "power" | "other";

export interface UpsCommandInfo {
  name: string;
  description: string;
  risk: UpsCommandRisk;
  category: UpsCommandCategory;
}

export interface UpsWritableVarInfo {
  name: string;
  value: string;
  description: string;
  var_type: string;
  enum_values: string[];
  range_min: number | null;
  range_max: number | null;
}

export interface UpsCapabilitiesResponse {
  ups_name: string;
  commands: UpsCommandInfo[];
  writable_vars: UpsWritableVarInfo[];
  credentials_configured: boolean;
  credential_source: string;
}

export interface RunUpsCommandResponse {
  ok: boolean;
  ups_name: string;
  command: string;
  message: string;
  report_id: number | null;
}

export interface SetUpsVarResponse {
  ok: boolean;
  ups_name: string;
  var_name: string;
  value: string;
  message: string;
}

export type SelfTestFrequency = "daily" | "weekly" | "biweekly" | "monthly";

export interface SelfTestSchedulePayload {
  ups_name: string;
  test_command: string;
  frequency: SelfTestFrequency;
  day_of_week: number;
  day_of_month: number;
  time_hhmm: string;
  min_battery_pct: number;
  require_online: boolean;
  notify_on_complete: boolean;
  notify_on_fail: boolean;
  enabled: boolean;
}

export interface SelfTestScheduleRecord extends SelfTestSchedulePayload {
  id: number;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SelfTestReportRecord {
  id: number;
  schedule_id: number | null;
  ups_name: string;
  test_command: string;
  trigger_type: string;
  status: "running" | "passed" | "failed" | "warning" | "skipped" | string;
  started_at: string;
  completed_at: string | null;
  duration_sec: number | null;
  battery_before: number | null;
  battery_after: number | null;
  runtime_before: number | null;
  runtime_after: number | null;
  load_pct: number | null;
  ups_test_result: string;
  message: string;
}

export interface SelfTestReportsSummary {
  total: number;
  passed: number;
  failed: number;
  warning: number;
  skipped: number;
  running: number;
  pass_rate_pct: number | null;
  last_passed_at: string | null;
  avg_battery_drop: number | null;
}

export interface SelfTestReportsResponse {
  summary: SelfTestReportsSummary;
  reports: SelfTestReportRecord[];
}

export interface SystemInfo {
  role: "main" | "repeater";
  version: string;
  web_port: number;
  nut_port: number;
  node_name: string;
  paired: boolean;
  instance_name: string;
}

export interface OnboardingState {
  instance_name: string;
  completed: boolean;
  skipped: boolean;
  last_step: number;
  completed_at: string | null;
  role: "main" | "repeater";
  has_ups_configured: boolean;
  has_ups_polling: boolean;
  has_hosts: boolean;
  has_pushover: boolean;
  has_repeaters: boolean;
  repeater_paired: boolean;
}

export interface OnboardingStateUpdatePayload {
  instance_name?: string;
  completed?: boolean;
  skipped?: boolean;
  last_step?: number;
}

export interface RepeaterNodeView {
  id: number;
  name: string;
  slug: string;
  ui_url: string;
  remote_ip: string;
  app_version: string;
  enabled: boolean;
  online: boolean;
  latency_ms: number | null;
  last_seen_at: string | null;
  ups_count: number;
  ups_names: string[];
  created_at: string;
  updated_at: string;
}

export interface RepeaterJoinTokenView {
  id: number;
  label: string;
  token_prefix: string;
  expires_at: string;
  max_uses: number;
  use_count: number;
  revoked: boolean;
  expired: boolean;
  created_at: string;
}

export interface RepeaterJoinTokenCreateResponse {
  token: string;
  token_record: RepeaterJoinTokenView;
  main_url: string;
  docker_compose_yaml: string;
}

export interface RepeaterLocalUpsPreview {
  local_name: string;
  main_name: string;
  status: string;
  battery_charge: number | null;
  ups_load: number | null;
  description: string;
}

export interface RepeaterLinkStatus {
  paired: boolean;
  connected: boolean;
  main_url: string;
  node_id: number | null;
  node_name: string;
  node_slug: string;
  public_url: string;
  push_interval_sec: number;
  last_push_at: string | null;
  last_error: string | null;
  latency_ms: number | null;
  ups_preview: RepeaterLocalUpsPreview[];
  updated_at: string | null;
}

export type FlowDrillMode = "dry_run" | "live";
export type FlowDrillTargetScope = "selected" | "all";

export interface FlowDrillStepEntry {
  stage: string;
  status: "running" | "passed" | "warning" | "failed" | "skipped" | "aborted" | string;
  started_at: string;
  completed_at: string | null;
  message: string;
}

export interface FlowDrillHostResult {
  host_id: number;
  host_name: string;
  ip_or_host: string;
  shutdown_mode: string;
  initial_online: boolean;
  nut_client_connected: boolean;
  ssh_configured: boolean;
  shutdown_triggered: boolean;
  shutdown_verified_offline: boolean;
  shutdown_duration_sec: number | null;
  wake_triggered: boolean;
  wake_verified_online: boolean;
  wake_attempts: number;
  wake_duration_sec: number | null;
  status: "passed" | "warning" | "failed" | "skipped" | "aborted" | string;
  message: string;
}

export interface FlowDrillRunPayload {
  mode: FlowDrillMode;
  target_scope: FlowDrillTargetScope;
  host_ids: number[];
  min_battery_pct: number;
  require_online: boolean;
  shutdown_timeout_sec: number;
  restore_delay_sec: number;
  notify_on_complete: boolean;
  notify_on_fail: boolean;
  confirm_live: boolean;
}

export interface FlowDrillSchedulePayload {
  name: string;
  mode: FlowDrillMode;
  target_scope: FlowDrillTargetScope;
  host_ids: number[];
  frequency: SelfTestFrequency;
  day_of_week: number;
  day_of_month: number;
  time_hhmm: string;
  min_battery_pct: number;
  require_online: boolean;
  shutdown_timeout_sec: number;
  restore_delay_sec: number;
  notify_on_complete: boolean;
  notify_on_fail: boolean;
  enabled: boolean;
}

export interface FlowDrillScheduleRecord extends FlowDrillSchedulePayload {
  id: number;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface FlowDrillReportRecord {
  id: number;
  schedule_id: number | null;
  schedule_name: string;
  mode: FlowDrillMode;
  trigger_type: string;
  status: "running" | "passed" | "warning" | "failed" | "skipped" | "aborted" | string;
  current_stage: string;
  ups_name: string;
  ups_status_start: string;
  battery_start: number | null;
  battery_end: number | null;
  hosts_total: number;
  hosts_passed: number;
  hosts_failed: number;
  started_at: string;
  completed_at: string | null;
  duration_sec: number | null;
  summary_message: string;
  steps: FlowDrillStepEntry[];
  host_results: FlowDrillHostResult[];
}

export interface FlowDrillReportsSummary {
  total: number;
  passed: number;
  warning: number;
  failed: number;
  skipped: number;
  aborted: number;
  running: number;
  pass_rate_pct: number | null;
  avg_shutdown_sec: number | null;
  avg_wake_sec: number | null;
  last_passed_at: string | null;
}

export interface FlowDrillReportsResponse {
  summary: FlowDrillReportsSummary;
  reports: FlowDrillReportRecord[];
}
