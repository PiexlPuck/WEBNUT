"""UPS instant commands, writable variables, self-test schedules, and reports API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.models.schemas import (
    RunUpsCommandRequest,
    RunUpsCommandResponse,
    SelfTestReportRecord,
    SelfTestReportsResponse,
    SelfTestScheduleCreate,
    SelfTestScheduleRecord,
    SetUpsVarRequest,
    SetUpsVarResponse,
    UpsCapabilitiesResponse,
    UpsCommandInfo,
    UpsWritableVarInfo,
)
from app.nut.client import _classify_command, _validate_nut_scalar, _validate_nut_token
from app.services.selftest_scheduler import (
    resolve_control_credentials,
    resolve_control_credentials_with_source,
)

router = APIRouter(prefix="/api/control", tags=["control"])


def _is_selftest_or_calibration(command: str) -> bool:
    lower = command.lower()
    return "test.battery" in lower or "calibrate" in lower


# --- Self-Test Schedules ---


@router.get("/schedules", response_model=list[SelfTestScheduleRecord])
async def list_selftest_schedules(request: Request) -> list[SelfTestScheduleRecord]:
    scheduler = request.app.state.selftest_scheduler
    return await scheduler.list_schedules()


@router.post("/schedules", response_model=SelfTestScheduleRecord)
async def create_selftest_schedule(
    payload: SelfTestScheduleCreate,
    request: Request,
) -> SelfTestScheduleRecord:
    try:
        _validate_nut_token(payload.ups_name, "ups_name")
        _validate_nut_token(payload.test_command, "test_command")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scheduler = request.app.state.selftest_scheduler
    return await scheduler.create_schedule(payload)


@router.put("/schedules/{schedule_id}", response_model=SelfTestScheduleRecord)
async def update_selftest_schedule(
    schedule_id: int,
    payload: SelfTestScheduleCreate,
    request: Request,
) -> SelfTestScheduleRecord:
    try:
        _validate_nut_token(payload.ups_name, "ups_name")
        _validate_nut_token(payload.test_command, "test_command")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scheduler = request.app.state.selftest_scheduler
    updated = await scheduler.update_schedule(schedule_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Self-test schedule {schedule_id} not found")
    return updated


@router.delete("/schedules/{schedule_id}")
async def delete_selftest_schedule(schedule_id: int, request: Request) -> dict:
    scheduler = request.app.state.selftest_scheduler
    deleted = await scheduler.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Self-test schedule {schedule_id} not found")
    return {"deleted": True, "id": schedule_id}


@router.post("/schedules/{schedule_id}/run-now", response_model=SelfTestReportRecord)
async def run_selftest_schedule_now(
    schedule_id: int,
    request: Request,
) -> SelfTestReportRecord:
    scheduler = request.app.state.selftest_scheduler
    report = await scheduler.run_schedule_now(schedule_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Self-test schedule {schedule_id} not found")
    return report


# --- Self-Test Reports ---


@router.get("/reports", response_model=SelfTestReportsResponse)
async def list_selftest_reports(
    request: Request,
    ups_name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> SelfTestReportsResponse:
    scheduler = request.app.state.selftest_scheduler
    cleaned_ups = ups_name.strip() if ups_name and ups_name.strip() else None
    return await scheduler.list_reports(ups_name=cleaned_ups, limit=limit)


@router.delete("/reports/{report_id}")
async def delete_selftest_report(report_id: int, request: Request) -> dict:
    scheduler = request.app.state.selftest_scheduler
    deleted = await scheduler.delete_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Self-test report {report_id} not found")
    return {"deleted": True, "id": report_id}


@router.delete("/reports")
async def clear_selftest_reports(
    request: Request,
    ups_name: str | None = Query(default=None),
) -> dict:
    scheduler = request.app.state.selftest_scheduler
    cleaned_ups = ups_name.strip() if ups_name and ups_name.strip() else None
    cleared = await scheduler.clear_reports(ups_name=cleaned_ups)
    return {"cleared": cleared}


# --- UPS Capabilities, Commands & Writable Vars ---


@router.get("/{ups_name}/capabilities", response_model=UpsCapabilitiesResponse)
async def get_ups_capabilities(ups_name: str, request: Request) -> UpsCapabilitiesResponse:
    try:
        safe_ups = _validate_nut_token(ups_name, "ups_name")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    nut_client = request.app.state.nut_client
    db = request.app.state.db
    nut_manager = request.app.state.nut_manager

    try:
        raw_cmds = await nut_client.list_commands(safe_ups)
        raw_vars = await nut_client.list_writable_vars(safe_ups)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to query NUT capabilities: {exc}") from exc

    username, password, source = await resolve_control_credentials_with_source(db, nut_manager)
    return UpsCapabilitiesResponse(
        ups_name=safe_ups,
        commands=[UpsCommandInfo(**c) for c in raw_cmds],
        writable_vars=[UpsWritableVarInfo(**v) for v in raw_vars],
        credentials_configured=bool(username and password),
        credential_source=source,
    )


@router.post("/{ups_name}/command", response_model=RunUpsCommandResponse)
async def run_ups_command(
    ups_name: str,
    payload: RunUpsCommandRequest,
    request: Request,
) -> RunUpsCommandResponse:
    try:
        safe_ups = _validate_nut_token(ups_name, "ups_name")
        safe_cmd = _validate_nut_token(payload.command, "command")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    risk, _ = _classify_command(safe_cmd)
    if risk == "dangerous" and not payload.confirm_dangerous:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Command '{safe_cmd}' is classified as dangerous and requires "
                "confirm_dangerous=true"
            ),
        )

    if _is_selftest_or_calibration(safe_cmd):
        scheduler = request.app.state.selftest_scheduler
        report = await scheduler.execute_selftest(
            ups_name=safe_ups,
            test_command=safe_cmd,
            trigger_type="manual",
            min_battery_pct=0.0,
            require_online=False,
            notify_on_complete=True,
            notify_on_fail=True,
        )
        return RunUpsCommandResponse(
            ok=(report.status != "failed"),
            ups_name=safe_ups,
            command=safe_cmd,
            message=report.message,
            report_id=report.id,
        )

    db = request.app.state.db
    nut_manager = request.app.state.nut_manager
    nut_client = request.app.state.nut_client
    event_log = request.app.state.event_log

    username, password = await resolve_control_credentials(db, nut_manager)
    if not username or not password:
        return RunUpsCommandResponse(
            ok=False,
            ups_name=safe_ups,
            command=safe_cmd,
            message=(
                "No NUT control credentials configured "
                "(add a user with instcmds=ALL in upsd.users or configure External NUT credentials)"
            ),
        )

    ok, msg = await nut_client.run_instant_command(safe_ups, safe_cmd, username, password)
    await event_log.record(
        "ups_control",
        "success" if ok else "error",
        "ups_instant_command",
        f"INSTCMD '{safe_cmd}' on '{safe_ups}': {msg}",
        {"ups_name": safe_ups, "command": safe_cmd, "ok": ok},
    )
    return RunUpsCommandResponse(
        ok=ok,
        ups_name=safe_ups,
        command=safe_cmd,
        message=msg,
    )


@router.post("/{ups_name}/var", response_model=SetUpsVarResponse)
async def set_ups_var(
    ups_name: str,
    payload: SetUpsVarRequest,
    request: Request,
) -> SetUpsVarResponse:
    try:
        safe_ups = _validate_nut_token(ups_name, "ups_name")
        safe_var = _validate_nut_token(payload.var_name, "var_name")
        safe_val = _validate_nut_scalar(payload.value, "value")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db = request.app.state.db
    nut_manager = request.app.state.nut_manager
    nut_client = request.app.state.nut_client
    event_log = request.app.state.event_log

    username, password = await resolve_control_credentials(db, nut_manager)
    if not username or not password:
        return SetUpsVarResponse(
            ok=False,
            ups_name=safe_ups,
            var_name=safe_var,
            value=safe_val,
            message=(
                "No NUT control credentials configured "
                "(add a user with actions=SET in upsd.users or configure External NUT credentials)"
            ),
        )

    ok, msg = await nut_client.set_var(safe_ups, safe_var, safe_val, username, password)
    await event_log.record(
        "ups_control",
        "success" if ok else "error",
        "ups_set_var",
        f"SET VAR '{safe_var}' on '{safe_ups}' = '{safe_val}': {msg}",
        {"ups_name": safe_ups, "var_name": safe_var, "value": safe_val, "ok": ok},
    )
    return SetUpsVarResponse(
        ok=ok,
        ups_name=safe_ups,
        var_name=safe_var,
        value=safe_val,
        message=msg,
    )
