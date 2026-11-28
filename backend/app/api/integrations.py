"""External view tool integration endpoints (Homepage widget API, config generator, API keys)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.config import settings
from app.database import utc_now_iso
from app.models.schemas import (
    HomeAssistantInfoResponse,
    HomeAssistantStateResponse,
    HomepageConfigRequest,
    HomepageConfigResponse,
    HomepageFieldOption,
    HomepageSummaryResponse,
    IntegrationApiKeyResponse,
    IntegrationSettingsUpdateRequest,
    IntegrationSettingsView,
    PrometheusConfigRequest,
    PrometheusConfigResponse,
)
from app.nut.client_config import detect_local_lan_ip
from app.services.integrations import (
    HOMEPAGE_FIELD_CATALOG,
    build_grafana_dashboard_json,
    build_homeassistant_state,
    build_homepage_summary,
    build_prometheus_metrics,
    generate_integration_api_key,
    get_instance_name,
    get_integration_settings_row,
    mask_api_key_preview,
    render_homepage_yaml,
    render_prometheus_scrape_yaml,
    verify_api_key,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _collect_ups_names(request: Request) -> list[str]:
    names: list[str] = []
    manager = getattr(request.app.state, "nut_manager", None)
    if manager is not None:
        try:
            ups_parsed, _ = manager.read_config("ups.conf", mask_secrets=True)
            for u in ups_parsed.get("ups_list", []):
                uname = str(u.get("name") or "").strip()
                if uname and uname not in names:
                    names.append(uname)
        except Exception:
            pass

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        telemetry = getattr(orchestrator, "latest_telemetry", {}) or {}
        for u in telemetry.get("ups_list", []):
            uname = str(u.get("name") or "").strip()
            if uname and uname not in names:
                names.append(uname)
    return names


async def _build_settings_view(request: Request) -> IntegrationSettingsView:
    db = request.app.state.db
    secret_box = request.app.state.secret_box
    row = await get_integration_settings_row(db)
    instance_name = await get_instance_name(db)
    enc = str(row.get("api_key_enc") or "")
    plaintext = secret_box.decrypt(enc) if enc else ""
    server_url = f"http://{detect_local_lan_ip()}:{settings.web_port}"
    return IntegrationSettingsView(
        api_key_enabled=bool(row.get("api_key_enabled")) and bool(plaintext),
        api_key_configured=bool(plaintext),
        api_key_preview=mask_api_key_preview(plaintext) if plaintext else "",
        homepage_enabled=bool(row.get("homepage_enabled", 1)),
        homepage_default_ups=str(row.get("homepage_default_ups") or ""),
        homepage_refresh_ms=int(row.get("homepage_refresh_ms") or 10000),
        homeassistant_enabled=bool(row.get("homeassistant_enabled", 1)),
        prometheus_enabled=bool(row.get("prometheus_enabled", 1)),
        instance_id=str(row.get("instance_id") or ""),
        instance_name=instance_name,
        server_url=server_url,
        web_port=settings.web_port,
        ups_names=_collect_ups_names(request),
    )


async def _compute_summary(request: Request, ups: str, default_ups: str) -> HomepageSummaryResponse:
    orchestrator = request.app.state.orchestrator
    db = request.app.state.db
    repeater = getattr(request.app.state, "repeater", None)

    orch_state = await orchestrator.get_state()
    orch_cfg = await orchestrator.get_config()
    hosts = await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
    instance_name = await get_instance_name(db)
    repeater_status = repeater.get_status() if repeater is not None else None
    return build_homepage_summary(
        telemetry=orchestrator.latest_telemetry,
        orch_state=orch_state,
        orch_cfg=orch_cfg,
        hosts=hosts,
        repeater_status=repeater_status,
        requested_ups=ups,
        default_ups=default_ups,
        instance_name=instance_name,
    )


def _extract_bearer_or_api_key(x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    return None


def _enforce_api_key(
    request: Request,
    row: dict,
    x_api_key: str | None,
    authorization: str | None,
) -> None:
    """Raise 401 when integration API key auth is enabled and the request key is missing/invalid."""
    if not (bool(row.get("api_key_enabled")) and row.get("api_key_enc")):
        return
    stored_key = request.app.state.secret_box.decrypt(str(row["api_key_enc"]))
    if not stored_key:
        return
    provided = _extract_bearer_or_api_key(x_api_key, authorization)
    if not verify_api_key(provided, stored_key):
        raise HTTPException(status_code=401, detail="Invalid or missing WebNUT API key")


@router.get("/settings", response_model=IntegrationSettingsView)
async def get_integration_settings(request: Request) -> IntegrationSettingsView:
    return await _build_settings_view(request)


@router.put("/settings", response_model=IntegrationSettingsView)
async def update_integration_settings(
    payload: IntegrationSettingsUpdateRequest,
    request: Request,
) -> IntegrationSettingsView:
    db = request.app.state.db
    secret_box = request.app.state.secret_box
    existing = await get_integration_settings_row(db)
    api_key_enc = str(existing.get("api_key_enc") or "")

    # Ensure enabling API key auth never leaves an empty key
    if payload.api_key_enabled and not (api_key_enc and secret_box.decrypt(api_key_enc)):
        api_key_enc = secret_box.encrypt(generate_integration_api_key())

    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO integration_settings (
            id, api_key_enabled, api_key_enc, homepage_enabled,
            homepage_default_ups, homepage_refresh_ms, homeassistant_enabled,
            prometheus_enabled, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            api_key_enabled = excluded.api_key_enabled,
            api_key_enc = excluded.api_key_enc,
            homepage_enabled = excluded.homepage_enabled,
            homepage_default_ups = excluded.homepage_default_ups,
            homepage_refresh_ms = excluded.homepage_refresh_ms,
            homeassistant_enabled = excluded.homeassistant_enabled,
            prometheus_enabled = excluded.prometheus_enabled,
            updated_at = excluded.updated_at
        """,
        (
            1 if payload.api_key_enabled else 0,
            api_key_enc,
            1 if payload.homepage_enabled else 0,
            payload.homepage_default_ups.strip(),
            payload.homepage_refresh_ms,
            1 if payload.homeassistant_enabled else 0,
            1 if payload.prometheus_enabled else 0,
            now,
        ),
    )

    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "integrations",
            "info",
            "integration_settings_updated",
            "Updated Homepage, Home Assistant & Grafana/Prometheus integration settings",
            {
                "homepage_enabled": payload.homepage_enabled,
                "homeassistant_enabled": payload.homeassistant_enabled,
                "prometheus_enabled": payload.prometheus_enabled,
                "api_key_enabled": payload.api_key_enabled,
                "homepage_default_ups": payload.homepage_default_ups.strip(),
                "homepage_refresh_ms": payload.homepage_refresh_ms,
            },
        )
    return await _build_settings_view(request)


@router.post("/api-key/rotate", response_model=IntegrationApiKeyResponse)
async def rotate_integration_api_key(request: Request) -> IntegrationApiKeyResponse:
    db = request.app.state.db
    secret_box = request.app.state.secret_box
    existing = await get_integration_settings_row(db)

    new_key = generate_integration_api_key()
    enc = secret_box.encrypt(new_key)
    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO integration_settings (
            id, api_key_enabled, api_key_enc, homepage_enabled,
            homepage_default_ups, homepage_refresh_ms, updated_at
        ) VALUES (1, 1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            api_key_enabled = 1,
            api_key_enc = excluded.api_key_enc,
            updated_at = excluded.updated_at
        """,
        (
            enc,
            int(existing.get("homepage_enabled", 1)),
            str(existing.get("homepage_default_ups") or ""),
            int(existing.get("homepage_refresh_ms") or 10000),
            now,
        ),
    )

    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "integrations",
            "info",
            "integration_api_key_rotated",
            "Generated/rotated external integration API key",
            {"preview": mask_api_key_preview(new_key)},
        )

    return IntegrationApiKeyResponse(
        api_key=new_key,
        api_key_preview=mask_api_key_preview(new_key),
        api_key_enabled=True,
    )


@router.get("/api-key/reveal", response_model=IntegrationApiKeyResponse)
async def reveal_integration_api_key(request: Request) -> IntegrationApiKeyResponse:
    db = request.app.state.db
    secret_box = request.app.state.secret_box
    row = await get_integration_settings_row(db)
    enc = str(row.get("api_key_enc") or "")
    key = secret_box.decrypt(enc) if enc else ""
    if not key:
        raise HTTPException(status_code=404, detail="No integration API key is currently configured")
    return IntegrationApiKeyResponse(
        api_key=key,
        api_key_preview=mask_api_key_preview(key),
        api_key_enabled=bool(row.get("api_key_enabled")),
    )


@router.delete("/api-key")
async def revoke_integration_api_key(request: Request) -> dict:
    db = request.app.state.db
    now = utc_now_iso()
    await db.execute(
        """
        UPDATE integration_settings
        SET api_key_enabled = 0, api_key_enc = '', updated_at = ?
        WHERE id = 1
        """,
        (now,),
    )
    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "integrations",
            "warning",
            "integration_api_key_revoked",
            "Revoked external integration API key and disabled key auth",
        )
    return {"revoked": True}


@router.get("/homepage/fields", response_model=list[HomepageFieldOption])
async def list_homepage_fields() -> list[HomepageFieldOption]:
    return list(HOMEPAGE_FIELD_CATALOG.values())


@router.get("/homepage/summary", response_model=HomepageSummaryResponse)
async def get_homepage_summary(
    request: Request,
    ups: str = Query(default=""),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> HomepageSummaryResponse:
    db = request.app.state.db
    row = await get_integration_settings_row(db)

    if not bool(row.get("homepage_enabled", 1)):
        raise HTTPException(
            status_code=403,
            detail="Homepage widget endpoint is disabled in WebNUT Integrations settings",
        )

    _enforce_api_key(request, row, x_api_key, authorization)

    return await _compute_summary(
        request,
        ups=ups,
        default_ups=str(row.get("homepage_default_ups") or ""),
    )


@router.get("/homepage/preview", response_model=HomepageSummaryResponse)
async def preview_homepage_summary(
    request: Request,
    ups: str = Query(default=""),
) -> HomepageSummaryResponse:
    row = await get_integration_settings_row(request.app.state.db)
    return await _compute_summary(
        request,
        ups=ups,
        default_ups=str(row.get("homepage_default_ups") or ""),
    )


@router.post("/homepage/generate", response_model=HomepageConfigResponse)
async def generate_homepage_config(
    payload: HomepageConfigRequest,
    request: Request,
) -> HomepageConfigResponse:
    db = request.app.state.db
    secret_box = request.app.state.secret_box
    row = await get_integration_settings_row(db)

    enc = str(row.get("api_key_enc") or "")
    stored_key = secret_box.decrypt(enc) if enc else ""
    api_key_enabled = bool(row.get("api_key_enabled")) and bool(stored_key)

    try:
        yaml_text, endpoint_url, mappings, display, notes = render_homepage_yaml(
            payload,
            api_key_enabled=api_key_enabled,
            api_key_value=stored_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    sample_payload = await _compute_summary(
        request,
        ups=payload.ups_name,
        default_ups=str(row.get("homepage_default_ups") or ""),
    )

    return HomepageConfigResponse(
        yaml=yaml_text,
        endpoint_url=endpoint_url,
        requires_api_key=api_key_enabled,
        display=display,
        mappings=mappings,
        sample_payload=sample_payload,
        notes=notes,
    )


# --- Home Assistant ---

_HA_DISABLED_DETAIL = "Home Assistant integration endpoints are disabled in WebNUT Integrations settings"


async def _require_homeassistant(
    request: Request,
    x_api_key: str | None,
    authorization: str | None,
) -> dict:
    row = await get_integration_settings_row(request.app.state.db)
    if not bool(row.get("homeassistant_enabled", 1)):
        raise HTTPException(status_code=403, detail=_HA_DISABLED_DETAIL)
    _enforce_api_key(request, row, x_api_key, authorization)
    return row


async def _version_info(request: Request) -> tuple[str, str | None, bool]:
    checker = getattr(request.app.state, "version_checker", None)
    if checker is not None:
        try:
            status = await checker.get_status()
            return status.current_version, status.latest_version, bool(status.update_available)
        except Exception:
            pass
    from app.services.version_checker import read_local_version

    return read_local_version(settings.webnut_version_file), None, False


async def _compute_homeassistant_state(request: Request, row: dict) -> HomeAssistantStateResponse:
    orchestrator = request.app.state.orchestrator
    db = request.app.state.db
    repeater = getattr(request.app.state, "repeater", None)
    version, latest, update_available = await _version_info(request)
    instance_name = await get_instance_name(db)
    return build_homeassistant_state(
        instance_id=str(row.get("instance_id") or ""),
        version=version,
        latest_version=latest,
        update_available=update_available,
        telemetry=orchestrator.latest_telemetry,
        orch_state=await orchestrator.get_state(),
        orch_cfg=await orchestrator.get_config(),
        hosts=await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC"),
        repeater_status=repeater.get_status() if repeater is not None else None,
        instance_name=instance_name,
    )


@router.get("/homeassistant/info", response_model=HomeAssistantInfoResponse)
async def get_homeassistant_info(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> HomeAssistantInfoResponse:
    """Used by the Home Assistant config flow to validate connectivity, API key, and unique ID."""
    row = await _require_homeassistant(request, x_api_key, authorization)
    version, _, _ = await _version_info(request)
    instance_name = await get_instance_name(request.app.state.db)
    return HomeAssistantInfoResponse(
        instance_id=str(row.get("instance_id") or ""),
        name=instance_name,
        version=version,
        server_url=f"http://{detect_local_lan_ip()}:{settings.web_port}",
        api_key_required=bool(row.get("api_key_enabled")) and bool(row.get("api_key_enc")),
        homeassistant_enabled=True,
    )


@router.get("/homeassistant/state", response_model=HomeAssistantStateResponse)
async def get_homeassistant_state(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> HomeAssistantStateResponse:
    """Full coordinator payload polled by the Home Assistant WebNUT integration."""
    row = await _require_homeassistant(request, x_api_key, authorization)
    return await _compute_homeassistant_state(request, row)


@router.get("/homeassistant/preview", response_model=HomeAssistantStateResponse)
async def preview_homeassistant_state(request: Request) -> HomeAssistantStateResponse:
    """Same payload as /state for the WebNUT UI preview (no API key required)."""
    row = await get_integration_settings_row(request.app.state.db)
    return await _compute_homeassistant_state(request, row)


@router.post("/homeassistant/hosts/{host_id}/wake")
async def homeassistant_wake_host(
    host_id: int,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """Trigger a tracked Wake-on-LAN sequence for a managed host from Home Assistant."""
    await _require_homeassistant(request, x_api_key, authorization)
    orchestrator = request.app.state.orchestrator
    try:
        await orchestrator.manual_wake_host(host_id, track_retries=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "integrations",
            "info",
            "homeassistant_wake_host",
            f"Home Assistant requested Wake-on-LAN for host #{host_id}",
            {"host_id": host_id},
        )
    return {"started": True, "host_id": host_id}


def _resolve_ha_component_dir() -> Path | None:
    candidates = [
        settings.webnut_ha_component_dir,
        Path(__file__).resolve().parents[3] / "custom_components" / "webnut",
    ]
    for c in candidates:
        if (c / "manifest.json").is_file():
            return c
    return None


@router.get("/homeassistant/package.zip")
async def download_homeassistant_package() -> Response:
    """Download the WebNUT Home Assistant custom integration (extract into /config)."""
    comp_dir = _resolve_ha_component_dir()
    if comp_dir is None:
        raise HTTPException(status_code=404, detail="Home Assistant integration package not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for hacs_candidate in (
            comp_dir.parent / "hacs.json",
            Path(__file__).resolve().parents[3] / "hacs.json",
        ):
            if hacs_candidate.is_file():
                zf.write(hacs_candidate, "hacs.json")
                break
        for path in sorted(comp_dir.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            arcname = Path("custom_components") / "webnut" / path.relative_to(comp_dir)
            zf.write(path, arcname.as_posix())
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="webnut-homeassistant.zip"'},
    )


# --- Grafana & Prometheus ---

_PROM_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_PROM_DISABLED_DETAIL = "Prometheus /metrics endpoint is disabled in WebNUT Integrations settings"


@router.get("/prometheus/metrics", response_class=PlainTextResponse)
async def get_prometheus_metrics(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> PlainTextResponse:
    """Expose WebNUT UPS, orchestrator, and host metrics in Prometheus text format."""
    row = await get_integration_settings_row(request.app.state.db)
    if not bool(row.get("prometheus_enabled", 1)):
        raise HTTPException(status_code=403, detail=_PROM_DISABLED_DETAIL)
    _enforce_api_key(request, row, x_api_key, authorization)
    state = await _compute_homeassistant_state(request, row)
    return PlainTextResponse(content=build_prometheus_metrics(state), media_type=_PROM_MEDIA_TYPE)


@router.get("/prometheus/preview", response_class=PlainTextResponse)
async def preview_prometheus_metrics(request: Request) -> PlainTextResponse:
    """Internal UI preview of /metrics output without requiring external API key headers."""
    row = await get_integration_settings_row(request.app.state.db)
    state = await _compute_homeassistant_state(request, row)
    return PlainTextResponse(content=build_prometheus_metrics(state), media_type=_PROM_MEDIA_TYPE)


@router.post("/prometheus/generate", response_model=PrometheusConfigResponse)
async def generate_prometheus_config(
    payload: PrometheusConfigRequest,
    request: Request,
) -> PrometheusConfigResponse:
    """Generate a prometheus.yml scrape_configs block and live sample metrics."""
    db = request.app.state.db
    secret_box = request.app.state.secret_box
    row = await get_integration_settings_row(db)
    enc = str(row.get("api_key_enc") or "")
    stored_key = secret_box.decrypt(enc) if enc else ""
    api_key_enabled = bool(row.get("api_key_enabled")) and bool(stored_key)

    default_target = f"{detect_local_lan_ip()}:{settings.web_port}"
    try:
        scrape_yaml, metrics_url, notes = render_prometheus_scrape_yaml(
            payload,
            default_target=default_target,
            api_key_enabled=api_key_enabled,
            api_key_value=stored_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state = await _compute_homeassistant_state(request, row)
    return PrometheusConfigResponse(
        scrape_yaml=scrape_yaml,
        metrics_url=metrics_url,
        requires_api_key=api_key_enabled,
        sample_metrics=build_prometheus_metrics(state),
        notes=notes,
    )


@router.get("/grafana/dashboard.json")
async def download_grafana_dashboard(request: Request) -> Response:
    """Download a pre-built Grafana dashboard JSON for WebNUT Prometheus metrics."""
    instance_name = await get_instance_name(request.app.state.db)
    payload = json.dumps(build_grafana_dashboard_json(instance_name=instance_name), indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="webnut-grafana-dashboard.json"'},
    )
