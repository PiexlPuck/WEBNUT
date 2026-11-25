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


class NutClientsOverview(BaseModel):
    server_ip: str
    nut_port: int
    ups_names: list[str] = Field(default_factory=list)
    available_users: list[str] = Field(default_factory=list)
    connected_clients: list[ConnectedNutClient] = Field(default_factory=list)


class CreateSecondaryUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)


class CreateSecondaryUserResponse(BaseModel):
    username: str
    password: str
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
