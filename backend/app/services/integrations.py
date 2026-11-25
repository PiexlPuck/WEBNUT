"""External view tool integration helpers (Homepage widget summary, YAML generator, API keys)."""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import quote

from app.database import Database, utc_now_iso
from app.models.schemas import (
    HomepageConfigRequest,
    HomepageDisplayMode,
    HomepageFieldOption,
    HomepageSummaryResponse,
    OrchestratorConfigModel,
    OrchestratorStateModel,
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
) -> HomepageSummaryResponse:
    """Build a flat, Homepage customapi-friendly summary payload."""
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
