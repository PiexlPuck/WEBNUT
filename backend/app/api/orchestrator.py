"""Recovery orchestrator status, policy configuration, and simulation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    OrchestratorConfigModel,
    OrchestratorStateModel,
    SimulateRecoveryRequest,
)

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/status", response_model=OrchestratorStateModel)
async def get_orchestrator_status(request: Request) -> OrchestratorStateModel:
    return await request.app.state.orchestrator.get_state()


@router.get("/config", response_model=OrchestratorConfigModel)
async def get_orchestrator_config(request: Request) -> OrchestratorConfigModel:
    return await request.app.state.orchestrator.get_config()


@router.put("/config", response_model=OrchestratorConfigModel)
async def update_orchestrator_config(
    payload: OrchestratorConfigModel,
    request: Request,
) -> OrchestratorConfigModel:
    prev_cfg = await request.app.state.orchestrator.get_config()
    updated = await request.app.state.orchestrator.update_config(payload)
    repeater = getattr(request.app.state, "repeater", None)
    if repeater is not None and (
        prev_cfg.repeater_enabled != updated.repeater_enabled
        or prev_cfg.repeater_port != updated.repeater_port
    ):
        if updated.repeater_enabled:
            await repeater.start(updated.repeater_port)
        else:
            await repeater.stop()
    return updated


@router.post("/simulate-recovery", response_model=OrchestratorStateModel)
async def simulate_recovery(
    payload: SimulateRecoveryRequest,
    request: Request,
) -> OrchestratorStateModel:
    try:
        return await request.app.state.orchestrator.simulate_recovery(
            dry_run=payload.dry_run,
            scope=payload.scope,
            host_ids=payload.host_ids,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
