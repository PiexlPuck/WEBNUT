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
    return await request.app.state.orchestrator.update_config(payload)


@router.post("/simulate-recovery", response_model=OrchestratorStateModel)
async def simulate_recovery(
    payload: SimulateRecoveryRequest,
    request: Request,
) -> OrchestratorStateModel:
    try:
        return await request.app.state.orchestrator.simulate_recovery(dry_run=payload.dry_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
