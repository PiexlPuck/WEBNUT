"""End-to-end outage & recovery flow drill endpoints (one-off, scheduled, and reporting)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.models.schemas import (
    FlowDrillReportRecord,
    FlowDrillReportsResponse,
    FlowDrillRunRequest,
    FlowDrillScheduleCreate,
    FlowDrillScheduleRecord,
)

router = APIRouter(prefix="/api/drills", tags=["drills"])


def _require_scheduler(request: Request):
    sched = getattr(request.app.state, "drill_scheduler", None)
    if sched is None:
        raise HTTPException(
            status_code=400,
            detail="Flow Drills are only available on Main WebNUT instances",
        )
    return sched


@router.post("/run", response_model=FlowDrillReportRecord)
async def run_flow_drill(
    payload: FlowDrillRunRequest,
    request: Request,
) -> FlowDrillReportRecord:
    sched = _require_scheduler(request)
    try:
        return await sched.start_drill(
            mode=payload.mode,
            target_scope=payload.target_scope,
            host_ids=payload.host_ids,
            min_battery_pct=payload.min_battery_pct,
            require_online=payload.require_online,
            shutdown_timeout_sec=payload.shutdown_timeout_sec,
            restore_delay_sec=payload.restore_delay_sec,
            notify_on_complete=payload.notify_on_complete,
            notify_on_fail=payload.notify_on_fail,
            confirm_live=payload.confirm_live,
            trigger_type="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{report_id}/abort", response_model=FlowDrillReportRecord)
async def abort_flow_drill(report_id: int, request: Request) -> FlowDrillReportRecord:
    sched = _require_scheduler(request)
    rep = await sched.abort_drill(report_id)
    if rep is None:
        raise HTTPException(status_code=404, detail=f"Flow drill report {report_id} not found")
    return rep


@router.get("/schedules", response_model=list[FlowDrillScheduleRecord])
async def list_drill_schedules(request: Request) -> list[FlowDrillScheduleRecord]:
    sched = _require_scheduler(request)
    return await sched.list_schedules()


@router.post("/schedules", response_model=FlowDrillScheduleRecord)
async def create_drill_schedule(
    payload: FlowDrillScheduleCreate,
    request: Request,
) -> FlowDrillScheduleRecord:
    sched = _require_scheduler(request)
    return await sched.create_schedule(payload)


@router.put("/schedules/{schedule_id}", response_model=FlowDrillScheduleRecord)
async def update_drill_schedule(
    schedule_id: int,
    payload: FlowDrillScheduleCreate,
    request: Request,
) -> FlowDrillScheduleRecord:
    sched = _require_scheduler(request)
    updated = await sched.update_schedule(schedule_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Flow drill schedule {schedule_id} not found")
    return updated


@router.delete("/schedules/{schedule_id}")
async def delete_drill_schedule(schedule_id: int, request: Request) -> dict:
    sched = _require_scheduler(request)
    deleted = await sched.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Flow drill schedule {schedule_id} not found")
    return {"deleted": True, "id": schedule_id}


@router.post("/schedules/{schedule_id}/run-now", response_model=FlowDrillReportRecord)
async def run_drill_schedule_now(
    schedule_id: int, request: Request
) -> FlowDrillReportRecord:
    sched = _require_scheduler(request)
    try:
        rep = await sched.run_schedule_now(schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if rep is None:
        raise HTTPException(status_code=404, detail=f"Flow drill schedule {schedule_id} not found")
    return rep


@router.get("/reports", response_model=FlowDrillReportsResponse)
async def list_drill_reports(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> FlowDrillReportsResponse:
    sched = _require_scheduler(request)
    return await sched.list_reports(limit=limit)


@router.get("/reports/{report_id}", response_model=FlowDrillReportRecord)
async def get_drill_report(report_id: int, request: Request) -> FlowDrillReportRecord:
    sched = _require_scheduler(request)
    rep = await sched.get_report(report_id)
    if rep is None:
        raise HTTPException(status_code=404, detail=f"Flow drill report {report_id} not found")
    return rep


@router.delete("/reports/{report_id}")
async def delete_drill_report(report_id: int, request: Request) -> dict:
    sched = _require_scheduler(request)
    deleted = await sched.delete_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Flow drill report {report_id} not found")
    return {"deleted": True, "id": report_id}


@router.delete("/reports")
async def clear_drill_reports(request: Request) -> dict:
    sched = _require_scheduler(request)
    cleared = await sched.clear_reports()
    return {"cleared": cleared}
