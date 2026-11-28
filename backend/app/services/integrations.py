"""External view tool integration helpers (Homepage widget summary, YAML generator, API keys)."""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import quote

from app.database import Database, utc_now_iso
from app.models.schemas import (
    HomeAssistantHost,
    HomeAssistantOrchestrator,
    HomeAssistantStateResponse,
    HomeAssistantUps,
    HomepageConfigRequest,
    HomepageDisplayMode,
    HomepageFieldOption,
    HomepageSummaryResponse,
    OrchestratorConfigModel,
    OrchestratorStateModel,
    PrometheusConfigRequest,
)

HOMEPAGE_FIELD_CATALOG: dict[str, HomepageFieldOption] = {
    "ups_status_label": HomepageFieldOption(
        field="ups_status_label",
        label="Status",
        format="text",
        description="Human-readable UPS power state (Online, On Battery, Low Battery, etc.)",
    ),
    "ups_status": HomepageFieldOption(
        field="ups_status",
        label="NUT Flags",
        format="text",
        description="Raw NUT ups.status flags (e.g. OL, OB LB, FSD)",
    ),
    "battery_charge": HomepageFieldOption(
        field="battery_charge",
        label="Battery",
        format="percent",
        description="Current battery charge percentage",
    ),
    "ups_load": HomepageFieldOption(
        field="ups_load",
        label="Load",
        format="percent",
        description="Current UPS load percentage",
    ),
    "battery_runtime_sec": HomepageFieldOption(
        field="battery_runtime_sec",
        label="Runtime",
        format="duration",
        description="Estimated remaining runtime (rendered by Homepage as duration)",
    ),
    "battery_runtime_min": HomepageFieldOption(
        field="battery_runtime_min",
        label="Runtime",
        format="number",
        suffix=" min",
        description="Estimated remaining runtime in minutes",
    ),
    "realpower_watts": HomepageFieldOption(
        field="realpower_watts",
        label="Power",
        format="number",
        suffix=" W",
        description="Active load in Watts (direct ups.realpower or derived from nominal)",
    ),
    "input_voltage": HomepageFieldOption(
        field="input_voltage",
        label="Input",
        format="float",
        suffix=" V",
        description="Utility input voltage",
    ),
    "output_voltage": HomepageFieldOption(
        field="output_voltage",
        label="Output",
        format="float",
        suffix=" V",
        description="UPS output voltage",
    ),
    "orchestrator_state_label": HomepageFieldOption(
        field="orchestrator_state_label",
        label="Recovery",
        format="text",
        description="WebNUT recovery orchestrator state (Normal, On Battery, Stabilizing, Recovering)",
    ),
    "hosts_summary": HomepageFieldOption(
        field="hosts_summary",
        label="Hosts Online",
        format="text",
        description="Online managed hosts ratio (e.g. 4/5)",
    ),
    "hosts_online": HomepageFieldOption(
        field="hosts_online",
        label="Hosts Online",
        format="number",
        description="Count of online managed hosts",
    ),
    "clients_connected": HomepageFieldOption(
        field="clients_connected",
        label="NUT Clients",
        format="number",
        description="Active NUT repeater and upsd client connections",
    ),
    "ups_model": HomepageFieldOption(
        field="ups_model",
        label="UPS Model",
        format="text",
        description="Detected UPS manufacturer/model string",
    ),
    "instance_name": HomepageFieldOption(
        field="instance_name",
        label="Instance",
        format="text",
        description="Custom WebNUT instance display name",
    ),
}

HOMEPAGE_PRESETS: dict[str, list[str]] = {
    "power": ["ups_status_label", "battery_charge", "ups_load", "battery_runtime_sec"],
    "homelab": ["ups_status_label", "battery_charge", "ups_load", "hosts_summary"],
    "recovery": ["ups_status_label", "orchestrator_state_label", "hosts_summary", "clients_connected"],
    "detailed": [
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
}

_ORCH_LABELS: dict[str, str] = {
    "NORMAL": "Normal",
    "ON_BATTERY": "On Battery",
    "STABILIZING": "Stabilizing",
    "RECOVERING": "Recovering",
}


def mask_api_key_preview(api_key: str) -> str:
    """Return a masked preview of an integration API key."""
    cleaned = api_key.strip()
    if not cleaned:
        return ""
    if len(cleaned) >= 8:
        return f"{cleaned[:3]}••••{cleaned[-4:]}"
    return "••••••••"


def generate_integration_api_key() -> str:
    """Generate a high-entropy read-only API key for external view integrations."""
    return f"wn_{secrets.token_urlsafe(24)}"


def verify_api_key(provided: str | None, stored_plaintext: str) -> bool:
    """Constant-time comparison for API key headers."""
    if not provided or not stored_plaintext:
        return False
    return hmac.compare_digest(provided.strip(), stored_plaintext.strip())


async def get_instance_name(db: Database) -> str:
    """Return the configured WebNUT instance display name, defaulting to 'WebNUT'."""
    try:
        row = await db.fetch_one("SELECT instance_name FROM onboarding_state WHERE id = 1")
        if row:
            cleaned = str(row.get("instance_name") or "").strip()
            if cleaned:
                return cleaned
    except Exception:
        pass
    return "WebNUT"


async def get_integration_settings_row(db: Database) -> dict:
    """Fetch singleton integration settings row, falling back to safe defaults."""
    row = await db.fetch_one("SELECT * FROM integration_settings WHERE id = 1")
    if row is None:
        return {
            "id": 1,
            "api_key_enabled": 0,
            "api_key_enc": "",
            "homepage_enabled": 1,
            "homepage_default_ups": "",
            "homepage_refresh_ms": 10000,
            "homeassistant_enabled": 1,
            "prometheus_enabled": 1,
            "instance_id": "",
            "updated_at": utc_now_iso(),
        }
    return row


def _to_float(val: object) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _compute_realpower_watts(raw_vars: dict, ups_load: float | None) -> float | None:
    direct = _to_float(raw_vars.get("ups.realpower"))
    if direct is not None:
        return round(direct, 1)
    nominal = _to_float(raw_vars.get("ups.realpower.nominal"))
    if nominal is None:
        nominal = _to_float(raw_vars.get("ups.power.nominal"))
    if nominal is not None and ups_load is not None:
        return round(nominal * (ups_load / 100.0), 1)
    return None


def _format_ups_status_label(connected: bool, status: str, tokens: set[str]) -> str:
    if not connected:
        return "Disconnected"
    if "COMMBAD" in tokens or "NOCOMM" in tokens:
        return "Comms Lost"
    if "FSD" in tokens:
        return "Forced Shutdown"
    if "OB" in tokens and "LB" in tokens:
        return "Low Battery"
    if "OB" in tokens:
        return "On Battery"
    if "OL" in tokens and "CHRG" in tokens:
        return "Online (Charging)"
    if "OL" in tokens:
        return "Online"
    return status or "Unknown"


def _count_connected_clients(ups_list: list[dict], repeater_status: dict | None) -> int:
    seen: set[tuple[str, str]] = set()
    if repeater_status:
        for rc in repeater_status.get("connected_clients") or []:
            uname = str(rc.get("ups_name") or "")
            cip = str(rc.get("client_ip") or "")
            if cip:
                seen.add((uname, cip))
    for u in ups_list:
        uname = str(u.get("name") or "")
        for cip in u.get("connected_clients") or []:
            if cip:
                seen.add((uname, str(cip)))
    return len(seen)


def build_homepage_summary(
    telemetry: dict,
    orch_state: OrchestratorStateModel,
    orch_cfg: OrchestratorConfigModel,
    hosts: list[dict],
    repeater_status: dict | None = None,
    requested_ups: str = "",
    default_ups: str = "",
    instance_name: str = "WebNUT",
) -> HomepageSummaryResponse:
    """Build a flat, Homepage customapi-friendly summary payload."""
    clean_instance_name = (instance_name or "").strip() or "WebNUT"
    ups_list = list(telemetry.get("ups_list") or [])
    telemetry_connected = bool(telemetry.get("connected"))
    updated_at = str(telemetry.get("updated_at") or utc_now_iso())

    hosts_total = len(hosts)
    hosts_online = sum(1 for h in hosts if h.get("is_online"))
    hosts_waking = sum(
        1
        for h in hosts
        if h.get("wake_state") in ("WAITING_STAGGER", "WAITING_BATTERY", "WAKING", "RETRYING")
    )
    hosts_summary = f"{hosts_online}/{hosts_total}"
    orch_label = _ORCH_LABELS.get(orch_state.state, orch_state.state.title())
    repeater_running = bool(repeater_status and repeater_status.get("running"))
    clients_connected = _count_connected_clients(ups_list, repeater_status)

    req_name = requested_ups.strip()
    def_name = default_ups.strip()
    mon_name = orch_cfg.monitored_ups.strip()

    ups_by_name = {str(u.get("name") or ""): u for u in ups_list if u.get("name")}
    target_ups: dict | None = None

    if req_name:
        target_ups = ups_by_name.get(req_name)
        if target_ups is None:
            return HomepageSummaryResponse(
                instance_name=clean_instance_name,
                connected=False,
                ups_name=req_name,
                ups_model="",
                ups_status="NOT_FOUND",
                ups_status_label="UPS Not Found",
                on_battery=False,
                low_battery=False,
                orchestrator_state=orch_state.state,
                orchestrator_state_label=orch_label,
                hosts_online=hosts_online,
                hosts_total=hosts_total,
                hosts_summary=hosts_summary,
                hosts_waking=hosts_waking,
                repeater_running=repeater_running,
                clients_connected=clients_connected,
                ups_count=len(ups_list),
                updated_at=updated_at,
            )
    else:
        for candidate in (def_name, mon_name):
            if candidate and candidate in ups_by_name:
                target_ups = ups_by_name[candidate]
                break
        if target_ups is None and ups_list:
            target_ups = ups_list[0]

    if not telemetry_connected or target_ups is None:
        return HomepageSummaryResponse(
            instance_name=clean_instance_name,
            connected=False,
            ups_name=req_name or def_name or mon_name,
            ups_model="",
            ups_status="UNKNOWN",
            ups_status_label="Disconnected",
            on_battery=False,
            low_battery=False,
            orchestrator_state=orch_state.state,
            orchestrator_state_label=orch_label,
            hosts_online=hosts_online,
            hosts_total=hosts_total,
            hosts_summary=hosts_summary,
            hosts_waking=hosts_waking,
            repeater_running=repeater_running,
            clients_connected=clients_connected,
            ups_count=len(ups_list),
            updated_at=updated_at,
        )

    raw_vars = dict(target_ups.get("raw_vars") or {})
    status_str = str(target_ups.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    tokens = set(target_ups.get("status_tokens") or status_str.split())
    ups_load = _to_float(target_ups.get("ups_load"))
    battery_charge = _to_float(target_ups.get("battery_charge"))
    runtime_val = _to_float(target_ups.get("battery_runtime"))
    runtime_sec = int(round(runtime_val)) if runtime_val is not None else None
    runtime_min = round(runtime_val / 60.0, 1) if runtime_val is not None else None
    input_v = _to_float(target_ups.get("input_voltage"))
    output_v = _to_float(target_ups.get("output_voltage"))
    realpower = _compute_realpower_watts(raw_vars, ups_load)

    mfr = str(target_ups.get("ups_mfr") or "").strip()
    model = str(target_ups.get("ups_model") or "").strip()
    if mfr and model and not model.lower().startswith(mfr.lower()):
        ups_model = f"{mfr} {model}"
    else:
        ups_model = model or mfr or str(target_ups.get("description") or "")

    return HomepageSummaryResponse(
        instance_name=clean_instance_name,
        connected=True,
        ups_name=str(target_ups.get("name") or ""),
        ups_model=ups_model,
        ups_status=status_str,
        ups_status_label=_format_ups_status_label(True, status_str, tokens),
        on_battery="OB" in tokens,
        low_battery="LB" in tokens,
        battery_charge=battery_charge,
        ups_load=ups_load,
        battery_runtime_sec=runtime_sec,
        battery_runtime_min=runtime_min,
        input_voltage=input_v,
        output_voltage=output_v,
        realpower_watts=realpower,
        orchestrator_state=orch_state.state,
        orchestrator_state_label=orch_label,
        hosts_online=hosts_online,
        hosts_total=hosts_total,
        hosts_summary=hosts_summary,
        hosts_waking=hosts_waking,
        repeater_running=repeater_running,
        clients_connected=clients_connected,
        ups_count=len(ups_list),
        updated_at=updated_at,
    )


def _validate_single_line(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise ValueError(f"{field_name} contains invalid control characters or newlines")
    return cleaned


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_homepage_yaml(
    req: HomepageConfigRequest,
    api_key_enabled: bool,
    api_key_value: str = "",
) -> tuple[str, str, list[HomepageFieldOption], HomepageDisplayMode, list[str]]:
    """Render a Homepage `services.yaml` entry using the customapi widget."""
    service_name = _validate_single_line(req.service_name, "service_name")
    if not service_name:
        raise ValueError("service_name cannot be empty")
    description = _validate_single_line(req.description, "description")
    icon = _validate_single_line(req.icon, "icon")
    base_url = _validate_single_line(req.base_url, "base_url").rstrip("/")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ValueError("base_url must start with http:// or https://")
    href_url = _validate_single_line(req.href_url, "href_url") if req.href_url.strip() else base_url
    ups_name = _validate_single_line(req.ups_name, "ups_name")

    endpoint_url = f"{base_url}/api/integrations/homepage/summary"
    if ups_name:
        endpoint_url = f"{endpoint_url}?ups={quote(ups_name, safe='')}"

    if req.preset == "custom":
        seen: set[str] = set()
        field_keys: list[str] = []
        for f in req.selected_fields:
            if f in HOMEPAGE_FIELD_CATALOG and f not in seen:
                seen.add(f)
                field_keys.append(f)
        if not field_keys:
            field_keys = list(HOMEPAGE_PRESETS["power"])
    else:
        field_keys = list(HOMEPAGE_PRESETS.get(req.preset, HOMEPAGE_PRESETS["power"]))

    mappings = [HOMEPAGE_FIELD_CATALOG[k] for k in field_keys]
    display: HomepageDisplayMode = req.display

    lines: list[str] = [
        f"- {_yaml_quote(service_name)}:",
    ]
    if icon:
        lines.append(f"    icon: {_yaml_quote(icon)}")
    lines.append(f"    href: {_yaml_quote(href_url)}")
    if description:
        lines.append(f"    description: {_yaml_quote(description)}")

    lines.extend(
        [
            "    widget:",
            "      type: customapi",
            f"      url: {_yaml_quote(endpoint_url)}",
            f"      refreshInterval: {req.refresh_interval_ms}",
            "      method: GET",
        ]
    )
    if display == "list":
        lines.append("      display: list")

    if api_key_enabled:
        header_secret = (
            api_key_value if (req.reveal_api_key and api_key_value) else "YOUR_WEBNUT_API_KEY"
        )
        lines.append("      headers:")
        lines.append(f"        X-API-Key: {_yaml_quote(header_secret)}")

    lines.append("      mappings:")
    for item in mappings:
        lines.append(f"        - field: {item.field}")
        lines.append(f"          label: {_yaml_quote(item.label)}")
        lines.append(f"          format: {item.format}")
        if item.suffix:
            lines.append(f"          suffix: {_yaml_quote(item.suffix)}")

    notes: list[str] = [
        "Paste this block under a group in your Homepage config/services.yaml file.",
        "If Homepage runs in Docker, ensure Base URL is reachable from the Homepage container (e.g. host LAN IP or Docker service URL).",
    ]
    if api_key_enabled:
        if req.reveal_api_key and api_key_value:
            notes.append(
                "Your WebNUT API key is embedded in headers.X-API-Key. Consider using Homepage's {{HOMEPAGE_VAR_...}} env substitution in production."
            )
        else:
            notes.append(
                "API key auth is enabled. Replace YOUR_WEBNUT_API_KEY (or check 'Embed actual API key in YAML') before saving services.yaml."
            )
    if len(mappings) > 4 and display == "block":
        notes.append(
            "More than 4 fields selected — switch Display Mode to 'Vertical List' (display: list) for the cleanest Homepage layout."
        )

    return "\n".join(lines) + "\n", endpoint_url, mappings, display, notes


def _ups_manufacturer_model(ups: dict) -> tuple[str, str]:
    mfr = str(ups.get("ups_mfr") or "").strip()
    model = str(ups.get("ups_model") or "").strip()
    return mfr, model


def build_homeassistant_state(
    instance_id: str,
    version: str,
    latest_version: str | None,
    update_available: bool,
    telemetry: dict,
    orch_state: OrchestratorStateModel,
    orch_cfg: OrchestratorConfigModel,
    hosts: list[dict],
    repeater_status: dict | None = None,
    instance_name: str = "WebNUT",
) -> HomeAssistantStateResponse:
    """Build the full Home Assistant coordinator payload (all UPS units + managed hosts)."""
    clean_instance_name = (instance_name or "").strip() or "WebNUT"
    ups_list = list(telemetry.get("ups_list") or [])
    connected = bool(telemetry.get("connected"))

    ups_models: list[HomeAssistantUps] = []
    for u in ups_list:
        name = str(u.get("name") or "").strip()
        if not name:
            continue
        raw_vars = dict(u.get("raw_vars") or {})
        status_str = str(u.get("status") or "UNKNOWN").strip() or "UNKNOWN"
        tokens = set(u.get("status_tokens") or status_str.split())
        load = _to_float(u.get("ups_load"))
        runtime = _to_float(u.get("battery_runtime"))
        mfr, model = _ups_manufacturer_model(u)
        ups_models.append(
            HomeAssistantUps(
                name=name,
                description=str(u.get("description") or ""),
                manufacturer=mfr,
                model=model,
                status=status_str,
                status_label=_format_ups_status_label(connected, status_str, tokens),
                on_battery="OB" in tokens,
                low_battery="LB" in tokens,
                battery_charge=_to_float(u.get("battery_charge")),
                battery_runtime=int(round(runtime)) if runtime is not None else None,
                battery_voltage=_to_float(raw_vars.get("battery.voltage")),
                ups_load=load,
                input_voltage=_to_float(u.get("input_voltage")),
                output_voltage=_to_float(u.get("output_voltage")),
                realpower_watts=_compute_realpower_watts(raw_vars, load),
            )
        )

    host_models = [
        HomeAssistantHost(
            id=int(h["id"]),
            name=str(h.get("name") or f"host_{h['id']}"),
            ip_or_host=str(h.get("ip_or_host") or ""),
            enabled=bool(h.get("enabled", 1)),
            is_online=bool(h.get("is_online")),
            wake_state=str(h.get("wake_state") or "IDLE"),
            last_seen_at=h.get("last_seen_at"),
            virtual_ups_name=str(h.get("virtual_ups_name") or ""),
        )
        for h in hosts
    ]

    return HomeAssistantStateResponse(
        instance_id=instance_id,
        instance_name=clean_instance_name,
        version=version,
        latest_version=latest_version,
        update_available=update_available,
        connected=connected,
        error=telemetry.get("error"),
        orchestrator=HomeAssistantOrchestrator(
            state=orch_state.state,
            state_label=_ORCH_LABELS.get(orch_state.state, orch_state.state.title()),
            last_ups_status=orch_state.last_ups_status,
            stability_remaining_sec=orch_state.stability_remaining_sec,
            disconnect_elapsed_sec=orch_state.disconnect_elapsed_sec,
            auto_recovery_enabled=orch_cfg.auto_recovery_enabled,
        ),
        repeater_running=bool(repeater_status and repeater_status.get("running")),
        clients_connected=_count_connected_clients(ups_list, repeater_status),
        hosts_online=sum(1 for h in host_models if h.is_online),
        hosts_total=len(host_models),
        ups=ups_models,
        hosts=host_models,
        updated_at=str(telemetry.get("updated_at") or utc_now_iso()),
    )


def _prom_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_labels(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    inner = ",".join(f'{k}="{_prom_escape(v)}"' for k, v in pairs)
    return "{" + inner + "}"


def _prom_gauge(
    lines: list[str],
    name: str,
    help_text: str,
    samples: list[tuple[list[tuple[str, str]], float | int]],
) -> None:
    if not samples:
        return
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    for labels, val in samples:
        lines.append(f"{name}{_prom_labels(labels)} {val}")


def build_prometheus_metrics(state: HomeAssistantStateResponse) -> str:
    """Render Prometheus/OpenMetrics text exposition for WebNUT UPS, orchestrator, and hosts."""
    lines: list[str] = []

    _prom_gauge(
        lines,
        "webnut_info",
        "Static metadata about the WebNUT instance",
        [
            (
                [
                    ("version", state.version),
                    ("instance_id", state.instance_id),
                    ("instance_name", state.instance_name),
                ],
                1,
            )
        ],
    )
    _prom_gauge(
        lines,
        "webnut_ups_connected",
        "1 if WebNUT is receiving live telemetry from upsd",
        [([], 1 if state.connected else 0)],
    )
    _prom_gauge(
        lines,
        "webnut_repeater_running",
        "1 if the built-in Smart NUT Repeater is listening",
        [([], 1 if state.repeater_running else 0)],
    )
    _prom_gauge(
        lines,
        "webnut_clients_connected",
        "Number of connected NUT clients across repeater and upstream upsd",
        [([], state.clients_connected)],
    )
    _prom_gauge(
        lines,
        "webnut_hosts_online",
        "Number of managed hosts currently online",
        [([], state.hosts_online)],
    )
    _prom_gauge(
        lines,
        "webnut_hosts_total",
        "Total number of managed hosts configured in WebNUT",
        [([], state.hosts_total)],
    )

    active_orch = state.orchestrator.state.lower()
    _prom_gauge(
        lines,
        "webnut_orchestrator_state",
        "Active power recovery orchestrator state (1 for current state)",
        [
            ([("state", s)], 1 if s == active_orch else 0)
            for s in ("normal", "on_battery", "stabilizing", "recovering")
        ],
    )
    _prom_gauge(
        lines,
        "webnut_orchestrator_stability_remaining_seconds",
        "Remaining stability hold countdown in seconds before WoL recovery",
        [([], state.orchestrator.stability_remaining_sec)],
    )
    _prom_gauge(
        lines,
        "webnut_orchestrator_disconnect_elapsed_seconds",
        "Elapsed UPS communications disconnect time in seconds",
        [([], state.orchestrator.disconnect_elapsed_sec)],
    )

    if state.ups:
        _prom_gauge(
            lines,
            "webnut_ups_up",
            "1 if UPS telemetry is connected and available",
            [
                (
                    [
                        ("ups", u.name),
                        ("model", f"{u.manufacturer} {u.model}".strip() or u.description),
                    ],
                    1 if state.connected else 0,
                )
                for u in state.ups
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_on_battery",
            "1 when the UPS is running on battery (OB)",
            [([("ups", u.name)], 1 if u.on_battery else 0) for u in state.ups],
        )
        _prom_gauge(
            lines,
            "webnut_ups_low_battery",
            "1 when the UPS reports low battery (LB)",
            [([("ups", u.name)], 1 if u.low_battery else 0) for u in state.ups],
        )
        _prom_gauge(
            lines,
            "webnut_ups_battery_charge_percent",
            "UPS battery charge percentage",
            [
                ([("ups", u.name)], u.battery_charge)
                for u in state.ups
                if u.battery_charge is not None
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_battery_runtime_seconds",
            "Estimated UPS battery runtime remaining in seconds",
            [
                ([("ups", u.name)], u.battery_runtime)
                for u in state.ups
                if u.battery_runtime is not None
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_battery_voltage_volts",
            "UPS battery DC voltage in volts",
            [
                ([("ups", u.name)], u.battery_voltage)
                for u in state.ups
                if u.battery_voltage is not None
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_load_percent",
            "UPS output load percentage",
            [
                ([("ups", u.name)], u.ups_load)
                for u in state.ups
                if u.ups_load is not None
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_realpower_watts",
            "UPS active power output in watts",
            [
                ([("ups", u.name)], u.realpower_watts)
                for u in state.ups
                if u.realpower_watts is not None
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_input_voltage_volts",
            "UPS input utility voltage in volts",
            [
                ([("ups", u.name)], u.input_voltage)
                for u in state.ups
                if u.input_voltage is not None
            ],
        )
        _prom_gauge(
            lines,
            "webnut_ups_output_voltage_volts",
            "UPS output voltage in volts",
            [
                ([("ups", u.name)], u.output_voltage)
                for u in state.ups
                if u.output_voltage is not None
            ],
        )

    if state.hosts:
        _prom_gauge(
            lines,
            "webnut_host_online",
            "1 if managed host is currently reachable",
            [
                ([("host", h.name), ("ip", h.ip_or_host)], 1 if h.is_online else 0)
                for h in state.hosts
            ],
        )
        _prom_gauge(
            lines,
            "webnut_host_wake_state",
            "Current Wake-on-LAN recovery state for each managed host",
            [
                (
                    [
                        ("host", h.name),
                        ("ip", h.ip_or_host),
                        ("state", h.wake_state.lower()),
                    ],
                    1,
                )
                for h in state.hosts
            ],
        )

    return "\n".join(lines) + "\n"


def _normalize_scrape_target(raw: str, fallback: str) -> str:
    cleaned = _validate_single_line(raw, "target_host") if raw.strip() else fallback
    for prefix in ("http://", "https://"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.rstrip("/").split("/", 1)[0]
    if not cleaned:
        raise ValueError("target_host cannot be empty")
    return cleaned


def render_prometheus_scrape_yaml(
    req: PrometheusConfigRequest,
    default_target: str,
    api_key_enabled: bool,
    api_key_value: str = "",
) -> tuple[str, str, list[str]]:
    """Render a `prometheus.yml` scrape_configs snippet for WebNUT."""
    job_name = _validate_single_line(req.job_name, "job_name")
    if not job_name:
        raise ValueError("job_name cannot be empty")
    target = _normalize_scrape_target(req.target_host, default_target)
    metrics_url = f"{req.scheme}://{target}{req.metrics_path}"

    lines = [
        "scrape_configs:",
        f"  - job_name: {_yaml_quote(job_name)}",
        f"    scrape_interval: {req.scrape_interval_sec}s",
        f"    metrics_path: {req.metrics_path}",
        f"    scheme: {req.scheme}",
    ]
    if api_key_enabled:
        secret = api_key_value if (req.reveal_api_key and api_key_value) else "YOUR_WEBNUT_API_KEY"
        lines.extend(
            [
                "    authorization:",
                "      type: Bearer",
                f"      credentials: {_yaml_quote(secret)}",
            ]
        )
    lines.extend(
        [
            "    static_configs:",
            f"      - targets: [{_yaml_quote(target)}]",
        ]
    )

    notes = [
        "Add this job under `scrape_configs:` in your `prometheus.yml` (or configure Grafana Alloy / Prometheus scrape).",
        "Import the downloadable Grafana Dashboard JSON in Grafana under Dashboards → New → Import.",
    ]
    if api_key_enabled and not (req.reveal_api_key and api_key_value):
        notes.append(
            "API key auth is enabled — replace YOUR_WEBNUT_API_KEY (or check 'Embed actual API key in YAML') before reloading Prometheus."
        )
    return "\n".join(lines) + "\n", metrics_url, notes


def build_grafana_dashboard_json(instance_name: str = "WebNUT") -> dict:
    """Return a ready-to-import Grafana dashboard JSON for WebNUT Prometheus metrics."""
    clean_name = (instance_name or "").strip() or "WebNUT"
    ds_ref = {"type": "prometheus", "uid": "${datasource}"}

    def _stat_panel(
        pid: int,
        title: str,
        expr: str,
        unit: str,
        x: int,
        w: int = 4,
        mappings: list | None = None,
        thresholds: list | None = None,
    ) -> dict:
        defaults: dict = {
            "unit": unit,
            "thresholds": {
                "mode": "absolute",
                "steps": thresholds or [{"color": "green", "value": None}],
            },
        }
        if mappings:
            defaults["mappings"] = mappings
        return {
            "id": pid,
            "type": "stat",
            "title": title,
            "datasource": ds_ref,
            "gridPos": {"h": 4, "w": w, "x": x, "y": 0},
            "targets": [{"refId": "A", "expr": expr, "instant": True}],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            },
        }

    def _timeseries_panel(
        pid: int,
        title: str,
        targets: list[tuple[str, str, str]],
        unit: str,
        x: int,
        y: int,
        w: int = 12,
        h: int = 8,
    ) -> dict:
        return {
            "id": pid,
            "type": "timeseries",
            "title": title,
            "datasource": ds_ref,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "targets": [
                {"refId": ref, "expr": expr, "legendFormat": legend}
                for ref, expr, legend in targets
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": unit,
                    "custom": {"lineWidth": 2, "fillOpacity": 15},
                },
                "overrides": [],
            },
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
        }

    return {
        "__inputs": [
            {
                "name": "DS_PROMETHEUS",
                "label": "Prometheus",
                "description": "Prometheus datasource scraping WebNUT /metrics",
                "type": "datasource",
                "pluginId": "prometheus",
                "pluginName": "Prometheus",
            }
        ],
        "__requires": [
            {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.0.0"},
            {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"},
            {"type": "panel", "id": "stat", "name": "Stat", "version": ""},
            {"type": "panel", "id": "timeseries", "name": "Time series", "version": ""},
        ],
        "id": None,
        "uid": "webnut-power-overview",
        "title": f"{clean_name} — UPS & Power Orchestrator",
        "tags": ["webnut", "ups", "nut", "power"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "15s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "datasource",
                    "type": "datasource",
                    "query": "prometheus",
                    "current": {},
                    "hide": 0,
                    "label": "Datasource",
                },
                {
                    "name": "ups",
                    "type": "query",
                    "datasource": ds_ref,
                    "query": "label_values(webnut_ups_up, ups)",
                    "refresh": 1,
                    "includeAll": False,
                    "multi": False,
                    "label": "UPS",
                },
            ]
        },
        "panels": [
            _stat_panel(
                1,
                "Power State",
                'webnut_ups_on_battery{ups="$ups"}',
                "none",
                0,
                mappings=[
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "Online", "color": "green"},
                            "1": {"text": "On Battery", "color": "orange"},
                        },
                    }
                ],
                thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}],
            ),
            _stat_panel(
                2,
                "Battery Charge",
                'webnut_ups_battery_charge_percent{ups="$ups"}',
                "percent",
                4,
                thresholds=[
                    {"color": "red", "value": None},
                    {"color": "orange", "value": 30},
                    {"color": "green", "value": 60},
                ],
            ),
            _stat_panel(
                3,
                "UPS Load",
                'webnut_ups_load_percent{ups="$ups"}',
                "percent",
                8,
                thresholds=[
                    {"color": "green", "value": None},
                    {"color": "orange", "value": 70},
                    {"color": "red", "value": 90},
                ],
            ),
            _stat_panel(4, "Active Power", 'webnut_ups_realpower_watts{ups="$ups"}', "watt", 12),
            _stat_panel(5, "Est. Runtime", 'webnut_ups_battery_runtime_seconds{ups="$ups"}', "s", 16),
            _stat_panel(6, "Hosts Online", "webnut_hosts_online", "short", 20),
            _timeseries_panel(
                7,
                "Battery Charge & Runtime",
                [
                    ("A", 'webnut_ups_battery_charge_percent{ups="$ups"}', "Battery %"),
                    ("B", 'webnut_ups_battery_runtime_seconds{ups="$ups"} / 60', "Runtime (min)"),
                ],
                "short",
                0,
                4,
            ),
            _timeseries_panel(
                8,
                "UPS Load & Active Power",
                [
                    ("A", 'webnut_ups_load_percent{ups="$ups"}', "Load %"),
                    ("B", 'webnut_ups_realpower_watts{ups="$ups"}', "Power (W)"),
                ],
                "short",
                12,
                4,
            ),
            _timeseries_panel(
                9,
                "Input, Output & Battery Voltage",
                [
                    ("A", 'webnut_ups_input_voltage_volts{ups="$ups"}', "Input (V)"),
                    ("B", 'webnut_ups_output_voltage_volts{ups="$ups"}', "Output (V)"),
                    ("C", 'webnut_ups_battery_voltage_volts{ups="$ups"}', "Battery (V)"),
                ],
                "volt",
                0,
                12,
            ),
            _timeseries_panel(
                10,
                "Orchestrator State & Connected NUT Clients",
                [
                    ("A", "webnut_orchestrator_state", "State: {{state}}"),
                    ("B", "webnut_clients_connected", "Connected NUT Clients"),
                ],
                "short",
                12,
                12,
            ),
            _timeseries_panel(
                11,
                "Managed Hosts Online Status",
                [("A", "webnut_host_online", "{{host}} ({{ip}})")],
                "short",
                0,
                20,
                w=24,
                h=7,
            ),
        ],
    }
