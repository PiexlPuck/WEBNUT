"""Pydantic request and response models for WEBNUT."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


SECRET_MASK = "********"

NutConfigFile = Literal[
    "nut.conf",
    "ups.conf",
    "upsd.conf",
    "upsd.users",
    "upsmon.conf",
]


# --- NUT Config Models ---

class NutConfModel(BaseModel):
    mode: Literal["none", "standalone", "netserver", "netclient"] = "netserver"
    extra_lines: list[str] = Field(default_factory=list)


class UpsDirective(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(default="", max_length=256)


class UpsDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    driver: str = Field(min_length=1, max_length=64)
    port: str = Field(default="auto", min_length=1, max_length=128)
    desc: str = Field(default="", max_length=256)
    directives: list[UpsDirective] = Field(default_factory=list)


class UpsConfModel(BaseModel):
    global_directives: list[UpsDirective] = Field(default_factory=list)
    ups_list: list[UpsDefinition] = Field(default_factory=list)


class UpsdListenEntry(BaseModel):
    address: str = Field(default="0.0.0.0", min_length=1, max_length=64)
    port: int = Field(default=3493, ge=1, le=65535)


class UpsdConfModel(BaseModel):
    listens: list[UpsdListenEntry] = Field(default_factory=list)
    maxage: int | None = Field(default=None, ge=1, le=3600)
    maxconn: int | None = Field(default=None, ge=1, le=65535)
    statepath: str | None = Field(default=None, max_length=256)
    extra_lines: list[str] = Field(default_factory=list)


class UpsdUserEntry(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)
    actions: list[str] = Field(default_factory=list)
    instcmds: list[str] = Field(default_factory=list)
    upsmon_role: Literal["", "primary", "secondary"] = ""


class UpsdUsersModel(BaseModel):
    users: list[UpsdUserEntry] = Field(default_factory=list)


class UpsmonMonitorEntry(BaseModel):
    ups_spec: str = Field(min_length=1, max_length=128)
    power_value: int = Field(default=1, ge=0, le=64)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)
    role: Literal["primary", "secondary"] = "primary"


class UpsmonNotifyFlag(BaseModel):
    event: str = Field(min_length=1, max_length=32)
    flags: str = Field(min_length=1, max_length=64)


class UpsmonConfModel(BaseModel):
    run_as_user: str = Field(default="nut", max_length=64)
    minsupplies: int = Field(default=1, ge=0, le=64)
    shutdowncmd: str = Field(default="/bin/true", max_length=256)
    notifycmd: str = Field(default="", max_length=256)
    pollfreq: int = Field(default=5, ge=1, le=300)
    pollfreqalert: int = Field(default=5, ge=1, le=300)
    hostsync: int = Field(default=15, ge=1, le=600)
    deadtime: int = Field(default=15, ge=1, le=600)
    finaldelay: int = Field(default=5, ge=0, le=600)
    powerdownflag: str = Field(default="/etc/killpower", max_length=256)
    monitors: list[UpsmonMonitorEntry] = Field(default_factory=list)
    notifyflags: list[UpsmonNotifyFlag] = Field(default_factory=list)
    extra_lines: list[str] = Field(default_factory=list)


class NutConfigReadResponse(BaseModel):
    filename: NutConfigFile
    parsed: dict[str, Any]
    raw: str


class NutConfigSaveRequest(BaseModel):
    parsed: dict[str, Any]
    apply_reload: bool = True


class NutConfigPreviewResponse(BaseModel):
    filename: NutConfigFile
    raw: str


class NutConfigSaveResponse(BaseModel):
    filename: NutConfigFile
    raw: str
    applied: bool
    message: str


class ScannedUsbDevice(BaseModel):
    suggested_name: str
    driver: str
    port: str
    vendorid: str | None = None
    productid: str | None = None
    product: str | None = None
    serial: str | None = None
    vendor: str | None = None
    bus: str | None = None
    device: str | None = None


class SerialDeviceInfo(BaseModel):
    path: str
    real_path: str
    by_id_path: str | None = None
    suggested_name: str
    vendor_id: str | None = None
    product_id: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial: str | None = None
    accessible: bool = True


class RawUsbDevice(BaseModel):
    bus: str
    device: str
    vendor_id: str
    product_id: str
    description: str


class HardwareScanResponse(BaseModel):
    nut_usb_devices: list[ScannedUsbDevice] = Field(default_factory=list)
    serial_devices: list[SerialDeviceInfo] = Field(default_factory=list)
    raw_usb_devices: list[RawUsbDevice] = Field(default_factory=list)
    serial_drivers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SnmpScanRequest(BaseModel):
    start_ip: str = Field(min_length=1, max_length=64)
    end_ip: str = Field(min_length=1, max_length=64)
    community: str = Field(default="public", max_length=64)
    snmp_version: Literal["v1", "v2c", "v3"] = "v2c"


class ScannedSnmpDevice(BaseModel):
    suggested_name: str
    driver: str = "snmp-ups"
    port: str
    mibs: str = "auto"
    community: str = "public"
    snmp_version: str = "v2c"
    desc: str = ""


class SnmpUpsSetupRequest(BaseModel):
    name: str = Field(default="snmp_ups", min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    snmp_version: Literal["v1", "v2c", "v3"] = "v2c"
    mibs: str = Field(default="auto", min_length=1, max_length=64)
    community: str = Field(default="public", max_length=128)
    sec_level: Literal["noAuthNoPriv", "authNoPriv", "authPriv"] = "authPriv"
    sec_name: str = Field(default="", max_length=128)
    auth_protocol: Literal["MD5", "SHA", "SHA256"] = "SHA"
    auth_password: str = Field(default="", max_length=128)
    priv_protocol: Literal["DES", "AES"] = "AES"
    priv_password: str = Field(default="", max_length=128)
    desc: str = Field(default="SNMP Network UPS", max_length=256)
    pollfreq: int = Field(default=15, ge=2, le=300)


class RepeaterClientSession(BaseModel):
    ups_name: str
    client_ip: str
    username: str = ""
    connected_at: str


class RepeaterStatusResponse(BaseModel):
    running: bool
    port: int
    error: str | None = None
    mirrored_ups_count: int = 0
    virtual_ups_count: int = 0
    connected_clients: list[RepeaterClientSession] = Field(default_factory=list)


class NutServiceStatus(BaseModel):
    name: str
    status: Literal["running", "stopped", "error", "unavailable"]
    detail: str = ""


NutConnectionMode = Literal["local", "external"]


class NutConnectionConfigRequest(BaseModel):
    mode: NutConnectionMode = "local"
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=3493, ge=1, le=65535)
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=256)


class NutConnectionConfigView(BaseModel):
    mode: NutConnectionMode
    host: str
    port: int
    username: str
    password: str
    password_configured: bool


class NutConnectionTestRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3493, ge=1, le=65535)


class NutConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    ups_list: list[UpsTelemetry] = Field(default_factory=list)


class UpsTelemetry(BaseModel):
    name: str
    description: str = ""
    status: str = "UNKNOWN"
    status_tokens: list[str] = Field(default_factory=list)
    battery_charge: float | None = None
    battery_runtime: float | None = None
    ups_load: float | None = None
    input_voltage: float | None = None
    output_voltage: float | None = None
    ups_mfr: str | None = None
    ups_model: str | None = None
    raw_vars: dict[str, str] = Field(default_factory=dict)
    connected_clients: list[str] = Field(default_factory=list)
    source_node_id: int | None = None
    source_node_name: str | None = None
    source_node_slug: str | None = None
    remote_ups_name: str | None = None
    node_online: bool = True


class TelemetrySnapshot(BaseModel):
    connected: bool
    error: str | None = None
    ups_list: list[UpsTelemetry] = Field(default_factory=list)
    updated_at: str


NutClientPlatform = Literal[
    "linux_debian",
    "linux_rhel",
    "truenas",
    "synology",
    "windows",
]


# --- Host & WoL Models ---

class HostBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ip_or_host: str = Field(min_length=1, max_length=255)
    mac_address: str = Field(min_length=11, max_length=32)
    subnet_broadcast: str | None = Field(default=None, max_length=64)
    boot_priority: int = Field(default=10, ge=0, le=1000)
    stagger_delay_sec: int = Field(default=15, ge=0, le=600)
    check_method: Literal["icmp", "tcp"] = "icmp"
    check_port: int | None = Field(default=None, ge=1, le=65535)
    boot_timeout_sec: int = Field(default=90, ge=5, le=1800)
    retry_interval_sec: int = Field(default=15, ge=1, le=600)
    max_retries: int = Field(default=3, ge=1, le=20)
    always_wake: bool = False
    enabled: bool = True
    nut_client_enabled: bool = False
    nut_client_platform: NutClientPlatform = "linux_debian"
    nut_ups_name: str = Field(default="", max_length=64)
    nut_client_user: str = Field(default="", max_length=64)
    virtual_ups_name: str = Field(default="", max_length=64)
    wake_min_battery_charge: float | None = Field(default=None, ge=0.0, le=100.0)
    shutdown_mode: Literal["nut_stream", "ssh", "both", "disabled"] = "nut_stream"
    shutdown_battery_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    shutdown_ob_delay_sec: int | None = Field(default=None, ge=0, le=86400)
    disconnect_policy: Literal["inherit", "keep_running", "shutdown_after_grace"] = "inherit"
    disconnect_grace_sec: int | None = Field(default=None, ge=0, le=86400)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(default="", max_length=64)
    ssh_password: str = Field(default="", max_length=256)
    ssh_sudo_password: str = Field(default="", max_length=256)
    ssh_shutdown_cmd: str = Field(default="sudo -S shutdown -h now", max_length=256)

    @field_validator("subnet_broadcast")
    @classmethod
    def empty_broadcast_to_none(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            return None
        return v.strip() if v else None


class HostCreate(HostBase):
    pass


class HostUpdate(HostBase):
    pass


class HostRecord(HostBase):
    id: int
    ssh_password_configured: bool = False
    is_online: bool
    last_seen_at: str | None = None
    was_online_before_outage: bool
    wake_state: Literal[
        "IDLE",
        "WAITING_STAGGER",
        "WAITING_BATTERY",
        "WAKING",
        "RETRYING",
        "WAKE_SUCCEEDED",
        "WAKE_FAILED",
    ]
    wake_attempt: int
    last_wake_error: str | None = None
    created_at: str
    updated_at: str


class SshTestRequest(BaseModel):
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(min_length=1, max_length=64)
    ssh_password: str = Field(default="", max_length=256)


class SshDeployRequest(BaseModel):
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(min_length=1, max_length=64)
    ssh_password: str = Field(default="", max_length=256)
    ssh_sudo_password: str = Field(default="", max_length=256)
    save_credentials: bool = False
    nut_username: str = Field(default="monuser", min_length=1, max_length=64)
    nut_password: str = Field(default="", max_length=256)
    final_delay: int = Field(default=5, ge=0, le=600)
    shutdown_cmd: str = Field(default="/sbin/shutdown -h +0", max_length=256)


class SshDeployResponse(BaseModel):
    ok: bool
    steps: list[str] = Field(default_factory=list)
    error: str | None = None
    virtual_ups_name: str = ""
    server_host: str = ""
    nut_port: int = 3493


class ArpLookupRequest(BaseModel):
    ip_or_host: str = Field(min_length=1, max_length=255)


class ArpLookupResponse(BaseModel):
    ip_or_host: str
    resolved_ip: str | None = None
    mac_address: str | None = None
    found: bool
    source: str = ""


class ManualWakeRequest(BaseModel):
    track_retries: bool = True


# --- NUT Client Configurator Models ---

class ConnectedNutClient(BaseModel):
    ups_name: str
    client_ip: str
    matched_host_id: int | None = None
    matched_host_name: str | None = None


class SavedMonitorAccount(BaseModel):
    username: str
    role: str = "secondary"
    notes: str = ""
    has_password: bool = True
    source: Literal["saved", "upsd"] = "saved"
    updated_at: str | None = None


class MonitorAccountRevealResponse(BaseModel):
    username: str
    password: str
    role: str = "secondary"
    notes: str = ""


class NutClientsOverview(BaseModel):
    server_ip: str
    nut_port: int
    ups_names: list[str] = Field(default_factory=list)
    available_users: list[str] = Field(default_factory=list)
    saved_accounts: list[SavedMonitorAccount] = Field(default_factory=list)
    connected_clients: list[ConnectedNutClient] = Field(default_factory=list)


class CreateSecondaryUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)
    notes: str = Field(default="", max_length=120)


class CreateSecondaryUserResponse(BaseModel):
    username: str
    password: str
    notes: str = ""
    upsmon_role: Literal["secondary"] = "secondary"
    applied: bool
    message: str


class NutClientConfigRequest(BaseModel):
    platform: NutClientPlatform = "linux_debian"
    server_host: str = Field(min_length=1, max_length=255)
    nut_port: int = Field(default=3493, ge=1, le=65535)
    ups_name: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    final_delay: int = Field(default=5, ge=0, le=600)
    poll_freq: int = Field(default=5, ge=1, le=300)
    deadtime: int = Field(default=15, ge=1, le=600)
    shutdown_cmd: str = Field(default="/sbin/shutdown -h +0", max_length=256)
    host_id: int | None = None
    device_name: str = Field(default="", max_length=100)
    save_record: bool = True


class GeneratedConfigFile(BaseModel):
    path: str
    content: str


class GeneratedGuiField(BaseModel):
    label: str
    value: str


class NutClientConfigResponse(BaseModel):
    platform: NutClientPlatform
    title: str
    files: list[GeneratedConfigFile] = Field(default_factory=list)
    one_liner: str | None = None
    gui_fields: list[GeneratedGuiField] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    record_id: int | None = None


class RecordedClientConfigSummary(BaseModel):
    id: int
    host_id: int | None = None
    device_name: str
    platform: NutClientPlatform
    server_host: str
    nut_port: int
    ups_name: str
    username: str
    final_delay: int
    poll_freq: int
    deadtime: int
    shutdown_cmd: str
    delivery_method: Literal["manual", "ssh_deploy"]
    deploy_status: Literal["generated", "deployed", "failed"]
    created_at: str
    updated_at: str


class RecordedClientConfigDetail(RecordedClientConfigSummary):
    config: NutClientConfigResponse


# --- Orchestrator Models ---

class OrchestratorConfigModel(BaseModel):
    monitored_ups: str = Field(default="", max_length=64)
    min_battery_charge: float = Field(default=25.0, ge=0.0, le=100.0)
    stability_hold_sec: int = Field(default=60, ge=0, le=3600)
    host_poll_interval_sec: int = Field(default=30, ge=5, le=3600)
    ups_poll_interval_sec: int = Field(default=5, ge=1, le=300)
    auto_recovery_enabled: bool = True
    disconnect_policy: Literal["keep_running", "shutdown_after_grace"] = "keep_running"
    disconnect_grace_sec: int = Field(default=600, ge=0, le=86400)
    repeater_enabled: bool = True
    repeater_port: int = Field(default=3493, ge=1, le=65535)
    cold_start_boot_grace_sec: int = Field(default=30, ge=0, le=600)
    cold_start_resume_recovery: bool = True
    cold_start_wake_always_wake: bool = True


class OrchestratorStateModel(BaseModel):
    state: Literal["NORMAL", "ON_BATTERY", "STABILIZING", "RECOVERING"]
    last_ups_status: str
    outage_started_at: str | None = None
    stabilizing_started_at: str | None = None
    recovery_started_at: str | None = None
    disconnect_started_at: str | None = None
    disconnect_elapsed_sec: int = 0
    snapshot_frozen: bool
    is_dry_run: bool
    stability_remaining_sec: int = 0
    updated_at: str


class SimulateRecoveryRequest(BaseModel):
    dry_run: bool = True
    scope: Literal["bulk", "single"] = "bulk"
    host_ids: list[int] = Field(default_factory=list)


class HostTestRunRequest(BaseModel):
    dry_run: bool = True


class HostTestRunResponse(BaseModel):
    host_id: int
    host_name: str
    ip_or_host: str
    dry_run: bool
    reachable: bool
    check_method: str
    virtual_ups_name: str
    nut_client_connected: bool
    current_battery_charge: float | None = None
    effective_wake_battery_pct: float
    would_wake_now: bool
    wol_targets: list[str] = Field(default_factory=list)
    shutdown_rule_summary: str
    disconnect_rule_summary: str
    started_live_wake: bool = False
    notes: list[str] = Field(default_factory=list)


# --- Notification Models ---

class PushoverConfigRequest(BaseModel):
    enabled: bool = False
    user_key: str = Field(default="", max_length=128)
    api_token: str = Field(default="", max_length=128)
    device: str = Field(default="", max_length=64)
    sound: str = Field(default="", max_length=64)
    emergency_retry_sec: int = Field(default=60, ge=30, le=3600)
    emergency_expire_sec: int = Field(default=3600, ge=60, le=10800)


class PushoverConfigView(BaseModel):
    enabled: bool
    user_key_configured: bool
    api_token_configured: bool
    user_key: str
    api_token: str
    device: str
    sound: str
    emergency_retry_sec: int
    emergency_expire_sec: int


class NotificationRuleItem(BaseModel):
    event_type: str
    enabled: bool
    priority: int = Field(ge=-2, le=2)


class NotificationRulesUpdateRequest(BaseModel):
    rules: list[NotificationRuleItem]


class NotificationSettingsResponse(BaseModel):
    pushover: PushoverConfigView
    rules: list[NotificationRuleItem]


class NotificationTestRequest(BaseModel):
    provider_type: str = "pushover"
    priority: int = Field(default=0, ge=-2, le=2)


# --- Event Logs ---

class EventLogEntry(BaseModel):
    id: int
    timestamp: str
    category: str
    severity: Literal["info", "warning", "error", "success"]
    event_type: str
    message: str
    details: dict[str, Any] | None = None


class EventLogPage(BaseModel):
    items: list[EventLogEntry]
    total: int
    limit: int
    offset: int


# --- System & Version Status ---

class AppVersionStatus(BaseModel):
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    release_notes: str | None = None
    last_checked_at: str | None = None
    last_error: str | None = None
    notified_version: str | None = None
    notified_at: str | None = None
    github_repo: str = "PiexlPuck/WEBNUT"


# --- External Integrations (Homepage, etc.) ---

HomepagePreset = Literal["power", "homelab", "recovery", "detailed", "custom"]
HomepageDisplayMode = Literal["block", "list"]


class IntegrationSettingsView(BaseModel):
    api_key_enabled: bool
    api_key_configured: bool
    api_key_preview: str = ""
    homepage_enabled: bool
    homepage_default_ups: str = ""
    homepage_refresh_ms: int = 10000
    homeassistant_enabled: bool = True
    prometheus_enabled: bool = True
    instance_id: str = ""
    server_url: str
    web_port: int = 8765
    ups_names: list[str] = Field(default_factory=list)


class IntegrationSettingsUpdateRequest(BaseModel):
    api_key_enabled: bool = False
    homepage_enabled: bool = True
    homepage_default_ups: str = Field(default="", max_length=64)
    homepage_refresh_ms: int = Field(default=10000, ge=1000, le=300000)
    homeassistant_enabled: bool = True
    prometheus_enabled: bool = True


class IntegrationApiKeyResponse(BaseModel):
    api_key: str
    api_key_preview: str
    api_key_enabled: bool


class HomepageSummaryResponse(BaseModel):
    connected: bool
    ups_name: str
    ups_model: str
    ups_status: str
    ups_status_label: str
    on_battery: bool
    low_battery: bool
    battery_charge: float | None = None
    ups_load: float | None = None
    battery_runtime_sec: int | None = None
    battery_runtime_min: float | None = None
    input_voltage: float | None = None
    output_voltage: float | None = None
    realpower_watts: float | None = None
    orchestrator_state: str
    orchestrator_state_label: str
    hosts_online: int
    hosts_total: int
    hosts_summary: str
    hosts_waking: int
    repeater_running: bool
    clients_connected: int
    ups_count: int
    updated_at: str


class HomepageFieldOption(BaseModel):
    field: str
    label: str
    format: Literal["text", "number", "float", "percent", "duration"] = "text"
    suffix: str | None = None
    description: str = ""


class HomepageConfigRequest(BaseModel):
    service_name: str = Field(default="WebNUT", min_length=1, max_length=64)
    description: str = Field(default="UPS & Power Orchestrator", max_length=120)
    icon: str = Field(default="mdi-battery-charging", max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    href_url: str = Field(default="", max_length=255)
    ups_name: str = Field(default="", max_length=64)
    preset: HomepagePreset = "power"
    display: HomepageDisplayMode = "block"
    selected_fields: list[str] = Field(default_factory=list)
    refresh_interval_ms: int = Field(default=10000, ge=1000, le=300000)
    reveal_api_key: bool = False


class HomepageConfigResponse(BaseModel):
    yaml: str
    endpoint_url: str
    requires_api_key: bool
    display: HomepageDisplayMode
    mappings: list[HomepageFieldOption]
    sample_payload: HomepageSummaryResponse
    notes: list[str] = Field(default_factory=list)


# --- UPS Control & Self-Test Scheduler Models ---

class UpsCommandInfo(BaseModel):
    name: str
    description: str = ""
    risk: Literal["safe", "dangerous"] = "safe"
    category: Literal["selftest", "beeper", "calibration", "power", "other"] = "other"


class UpsWritableVarInfo(BaseModel):
    name: str
    value: str = ""
    description: str = ""
    var_type: str = ""
    enum_values: list[str] = Field(default_factory=list)
    range_min: float | None = None
    range_max: float | None = None


class UpsCapabilitiesResponse(BaseModel):
    ups_name: str
    commands: list[UpsCommandInfo] = Field(default_factory=list)
    writable_vars: list[UpsWritableVarInfo] = Field(default_factory=list)
    credentials_configured: bool = False
    credential_source: str = "none"


class RunUpsCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=128)
    confirm_dangerous: bool = False


class RunUpsCommandResponse(BaseModel):
    ok: bool
    ups_name: str
    command: str
    message: str
    report_id: int | None = None


class SetUpsVarRequest(BaseModel):
    var_name: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=256)


class SetUpsVarResponse(BaseModel):
    ok: bool
    ups_name: str
    var_name: str
    value: str
    message: str


SelfTestFrequency = Literal["daily", "weekly", "biweekly", "monthly"]


class SelfTestScheduleCreate(BaseModel):
    ups_name: str = Field(min_length=1, max_length=64)
    test_command: str = Field(default="test.battery.start.quick", min_length=1, max_length=128)
    frequency: SelfTestFrequency = "weekly"
    day_of_week: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=31)
    time_hhmm: str = Field(default="03:00", pattern=r"^\d{2}:\d{2}$")
    min_battery_pct: float = Field(default=80.0, ge=0.0, le=100.0)
    require_online: bool = True
    notify_on_complete: bool = True
    notify_on_fail: bool = True
    enabled: bool = True

    @field_validator("time_hhmm")
    @classmethod
    def _validate_time_hhmm(cls, value: str) -> str:
        hh_str, mm_str = value.split(":", 1)
        hh, mm = int(hh_str), int(mm_str)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("time_hhmm must be a valid 24-hour HH:MM time (00:00 - 23:59)")
        return value


class SelfTestScheduleRecord(BaseModel):
    id: int
    ups_name: str
    test_command: str
    frequency: SelfTestFrequency
    day_of_week: int
    day_of_month: int
    time_hhmm: str
    min_battery_pct: float
    require_online: bool
    notify_on_complete: bool
    notify_on_fail: bool
    enabled: bool
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str
    updated_at: str


class SelfTestReportRecord(BaseModel):
    id: int
    schedule_id: int | None = None
    ups_name: str
    test_command: str
    trigger_type: str
    status: str
    started_at: str
    completed_at: str | None = None
    duration_sec: int | None = None
    battery_before: float | None = None
    battery_after: float | None = None
    runtime_before: float | None = None
    runtime_after: float | None = None
    load_pct: float | None = None
    ups_test_result: str = ""
    message: str = ""


class SelfTestReportsSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    warning: int = 0
    skipped: int = 0
    running: int = 0
    pass_rate_pct: float | None = None
    last_passed_at: str | None = None
    avg_battery_drop: float | None = None


class SelfTestReportsResponse(BaseModel):
    summary: SelfTestReportsSummary
    reports: list[SelfTestReportRecord] = Field(default_factory=list)


# --- Home Assistant Integration ---

class HomeAssistantInfoResponse(BaseModel):
    instance_id: str
    name: str = "WebNUT"
    version: str
    server_url: str
    api_key_required: bool
    homeassistant_enabled: bool


class HomeAssistantUps(BaseModel):
    name: str
    description: str = ""
    manufacturer: str = ""
    model: str = ""
    status: str = "UNKNOWN"
    status_label: str = "Unknown"
    on_battery: bool = False
    low_battery: bool = False
    battery_charge: float | None = None
    battery_runtime: int | None = None
    battery_voltage: float | None = None
    ups_load: float | None = None
    input_voltage: float | None = None
    output_voltage: float | None = None
    realpower_watts: float | None = None


class HomeAssistantHost(BaseModel):
    id: int
    name: str
    ip_or_host: str
    enabled: bool
    is_online: bool
    wake_state: str
    last_seen_at: str | None = None
    virtual_ups_name: str = ""


class HomeAssistantOrchestrator(BaseModel):
    state: str
    state_label: str
    last_ups_status: str
    stability_remaining_sec: int = 0
    disconnect_elapsed_sec: int = 0
    auto_recovery_enabled: bool = True


class HomeAssistantStateResponse(BaseModel):
    instance_id: str
    version: str
    latest_version: str | None = None
    update_available: bool = False
    connected: bool
    error: str | None = None
    orchestrator: HomeAssistantOrchestrator
    repeater_running: bool
    clients_connected: int
    hosts_online: int
    hosts_total: int
    ups: list[HomeAssistantUps] = Field(default_factory=list)
    hosts: list[HomeAssistantHost] = Field(default_factory=list)
    updated_at: str


# --- Grafana & Prometheus Integration ---

class PrometheusConfigRequest(BaseModel):
    job_name: str = Field(default="webnut", min_length=1, max_length=64)
    target_host: str = Field(default="", max_length=255)
    scheme: Literal["http", "https"] = "http"
    scrape_interval_sec: int = Field(default=15, ge=5, le=300)
    metrics_path: Literal["/metrics", "/api/integrations/prometheus/metrics"] = "/metrics"
    reveal_api_key: bool = False


class PrometheusConfigResponse(BaseModel):
    scrape_yaml: str
    metrics_url: str
    requires_api_key: bool
    sample_metrics: str
    notes: list[str] = Field(default_factory=list)


# --- System Info & Distributed Repeater Node Models ---

class SystemInfoResponse(BaseModel):
    role: Literal["main", "repeater"]
    version: str
    web_port: int
    nut_port: int
    node_name: str = ""
    paired: bool = False


class RepeaterJoinTokenCreateRequest(BaseModel):
    label: str = Field(default="", max_length=80)
    expires_hours: int = Field(default=24, ge=1, le=720)
    max_uses: int = Field(default=1, ge=1, le=100)


class RepeaterJoinTokenView(BaseModel):
    id: int
    label: str = ""
    token_prefix: str = ""
    expires_at: str
    max_uses: int
    use_count: int
    revoked: bool = False
    expired: bool = False
    created_at: str


class RepeaterJoinTokenCreateResponse(BaseModel):
    token: str
    token_record: RepeaterJoinTokenView
    main_url: str
    docker_compose_yaml: str


class RepeaterJoinRequest(BaseModel):
    join_token: str = Field(min_length=8, max_length=256)
    node_name: str = Field(min_length=1, max_length=80)
    ui_url: str = Field(default="", max_length=255)
    app_version: str = Field(default="", max_length=32)


class RepeaterJoinResponse(BaseModel):
    node_id: int
    node_name: str
    node_slug: str
    auth_token: str
    ws_path: str = "/ws/repeater"


class RepeaterNodeView(BaseModel):
    id: int
    name: str
    slug: str
    ui_url: str = ""
    remote_ip: str = ""
    app_version: str = ""
    enabled: bool = True
    online: bool = False
    latency_ms: float | None = None
    last_seen_at: str | None = None
    ups_count: int = 0
    ups_names: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class RepeaterNodeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    ui_url: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None


class RepeaterLinkPairRequest(BaseModel):
    main_url: str = Field(min_length=1, max_length=255)
    join_token: str = Field(min_length=8, max_length=256)
    node_name: str = Field(min_length=1, max_length=80)
    public_url: str = Field(default="", max_length=255)


class RepeaterLocalUpsPreview(BaseModel):
    local_name: str
    main_name: str
    status: str
    battery_charge: float | None = None
    ups_load: float | None = None
    description: str = ""


class RepeaterLinkStatusResponse(BaseModel):
    paired: bool = False
    connected: bool = False
    main_url: str = ""
    node_id: int | None = None
    node_name: str = ""
    node_slug: str = ""
    public_url: str = ""
    push_interval_sec: int = 3
    last_push_at: str | None = None
    last_error: str | None = None
    latency_ms: float | None = None
    ups_preview: list[RepeaterLocalUpsPreview] = Field(default_factory=list)
    updated_at: str | None = None


# --- End-to-End Flow Drill Models ---

FlowDrillMode = Literal["dry_run", "live"]
FlowDrillTargetScope = Literal["selected", "all"]


class FlowDrillStepEntry(BaseModel):
    stage: str
    status: Literal["running", "passed", "warning", "failed", "skipped", "aborted"] = "running"
    started_at: str
    completed_at: str | None = None
    message: str = ""


class FlowDrillHostResult(BaseModel):
    host_id: int
    host_name: str
    ip_or_host: str
    shutdown_mode: str = "nut_stream"
    initial_online: bool = False
    nut_client_connected: bool = False
    ssh_configured: bool = False
    shutdown_triggered: bool = False
    shutdown_verified_offline: bool = False
    shutdown_duration_sec: int | None = None
    wake_triggered: bool = False
    wake_verified_online: bool = False
    wake_attempts: int = 0
    wake_duration_sec: int | None = None
    status: Literal["passed", "warning", "failed", "skipped", "aborted"] = "passed"
    message: str = ""


class FlowDrillRunRequest(BaseModel):
    mode: FlowDrillMode = "dry_run"
    target_scope: FlowDrillTargetScope = "all"
    host_ids: list[int] = Field(default_factory=list)
    min_battery_pct: float = Field(default=80.0, ge=0.0, le=100.0)
    require_online: bool = True
    shutdown_timeout_sec: int = Field(default=180, ge=10, le=1800)
    restore_delay_sec: int = Field(default=15, ge=0, le=300)
    notify_on_complete: bool = True
    notify_on_fail: bool = True
    confirm_live: bool = False


class FlowDrillScheduleCreate(BaseModel):
    name: str = Field(default="Weekly Flow Drill", min_length=1, max_length=100)
    mode: FlowDrillMode = "dry_run"
    target_scope: FlowDrillTargetScope = "all"
    host_ids: list[int] = Field(default_factory=list)
    frequency: SelfTestFrequency = "weekly"
    day_of_week: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=31)
    time_hhmm: str = Field(default="04:00", pattern=r"^\d{2}:\d{2}$")
    min_battery_pct: float = Field(default=80.0, ge=0.0, le=100.0)
    require_online: bool = True
    shutdown_timeout_sec: int = Field(default=180, ge=10, le=1800)
    restore_delay_sec: int = Field(default=15, ge=0, le=300)
    notify_on_complete: bool = True
    notify_on_fail: bool = True
    enabled: bool = True

    @field_validator("time_hhmm")
    @classmethod
    def _validate_drill_time_hhmm(cls, value: str) -> str:
        hh_str, mm_str = value.split(":", 1)
        hh, mm = int(hh_str), int(mm_str)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("time_hhmm must be a valid 24-hour HH:MM time (00:00 - 23:59)")
        return value


class FlowDrillScheduleRecord(FlowDrillScheduleCreate):
    id: int
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str
    updated_at: str


class FlowDrillReportRecord(BaseModel):
    id: int
    schedule_id: int | None = None
    schedule_name: str = ""
    mode: FlowDrillMode
    trigger_type: str
    status: str
    current_stage: str = "precheck"
    ups_name: str = ""
    ups_status_start: str = ""
    battery_start: float | None = None
    battery_end: float | None = None
    hosts_total: int = 0
    hosts_passed: int = 0
    hosts_failed: int = 0
    started_at: str
    completed_at: str | None = None
    duration_sec: int | None = None
    summary_message: str = ""
    steps: list[FlowDrillStepEntry] = Field(default_factory=list)
    host_results: list[FlowDrillHostResult] = Field(default_factory=list)


class FlowDrillReportsSummary(BaseModel):
    total: int = 0
    passed: int = 0
    warning: int = 0
    failed: int = 0
    skipped: int = 0
    aborted: int = 0
    running: int = 0
    pass_rate_pct: float | None = None
    avg_shutdown_sec: float | None = None
    avg_wake_sec: float | None = None
    last_passed_at: str | None = None


class FlowDrillReportsResponse(BaseModel):
    summary: FlowDrillReportsSummary
    reports: list[FlowDrillReportRecord] = Field(default_factory=list)
