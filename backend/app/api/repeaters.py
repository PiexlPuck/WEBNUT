"""Distributed Repeater Node management (Main) and Main Link pairing (Repeater) API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    RepeaterJoinRequest,
    RepeaterJoinResponse,
    RepeaterJoinTokenCreateRequest,
    RepeaterJoinTokenCreateResponse,
    RepeaterJoinTokenView,
    RepeaterLinkPairRequest,
    RepeaterLinkStatusResponse,
    RepeaterNodeUpdateRequest,
    RepeaterNodeView,
)

router = APIRouter(prefix="/api/repeaters", tags=["repeaters"])


def _require_hub(request: Request):
    hub = getattr(request.app.state, "repeater_hub", None)
    if hub is None:
        raise HTTPException(
            status_code=400,
            detail="Repeater Hub endpoints are only available on Main WebNUT instances",
        )
    return hub


def _require_agent(request: Request):
    agent = getattr(request.app.state, "repeater_agent", None)
    if agent is None:
        raise HTTPException(
            status_code=400,
            detail="Repeater Link endpoints are only available when WEBNUT_ROLE=repeater",
        )
    return agent


# --- Main WebNUT Endpoints ---


@router.get("/nodes", response_model=list[RepeaterNodeView])
async def list_repeater_nodes(request: Request) -> list[RepeaterNodeView]:
    hub = _require_hub(request)
    return await hub.list_nodes()


@router.patch("/nodes/{node_id}", response_model=RepeaterNodeView)
async def update_repeater_node(
    node_id: int,
    payload: RepeaterNodeUpdateRequest,
    request: Request,
) -> RepeaterNodeView:
    hub = _require_hub(request)
    updated = await hub.update_node(node_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Repeater node {node_id} not found")
    return updated


@router.delete("/nodes/{node_id}")
async def delete_repeater_node(node_id: int, request: Request) -> dict:
    hub = _require_hub(request)
    deleted = await hub.delete_node(node_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Repeater node {node_id} not found")
    return {"deleted": True, "id": node_id}


@router.get("/tokens", response_model=list[RepeaterJoinTokenView])
async def list_join_tokens(request: Request) -> list[RepeaterJoinTokenView]:
    hub = _require_hub(request)
    return await hub.list_join_tokens()


@router.post("/tokens", response_model=RepeaterJoinTokenCreateResponse)
async def create_join_token(
    payload: RepeaterJoinTokenCreateRequest,
    request: Request,
) -> RepeaterJoinTokenCreateResponse:
    hub = _require_hub(request)
    return await hub.create_join_token(
        label=payload.label,
        expires_hours=payload.expires_hours,
        max_uses=payload.max_uses,
    )


@router.delete("/tokens/{token_id}")
async def revoke_join_token(token_id: int, request: Request) -> dict:
    hub = _require_hub(request)
    revoked = await hub.revoke_join_token(token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=f"Join token {token_id} not found")
    return {"revoked": True, "id": token_id}


@router.post("/join", response_model=RepeaterJoinResponse)
async def redeem_join_token(
    payload: RepeaterJoinRequest,
    request: Request,
) -> RepeaterJoinResponse:
    hub = _require_hub(request)
    remote_ip = request.client.host if request.client else ""
    try:
        return await hub.redeem_join_token(payload, remote_ip=remote_ip)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --- Repeater Node Endpoints (WEBNUT_ROLE=repeater) ---


@router.get("/link", response_model=RepeaterLinkStatusResponse)
async def get_repeater_link_status(request: Request) -> RepeaterLinkStatusResponse:
    agent = _require_agent(request)
    return await agent.get_link_status()


@router.post("/link/pair", response_model=RepeaterLinkStatusResponse)
async def pair_with_main(
    payload: RepeaterLinkPairRequest,
    request: Request,
) -> RepeaterLinkStatusResponse:
    agent = _require_agent(request)
    try:
        return await agent.pair(
            main_url=payload.main_url,
            join_token=payload.join_token,
            node_name=payload.node_name,
            public_url=payload.public_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/link/unpair", response_model=RepeaterLinkStatusResponse)
async def unpair_from_main(request: Request) -> RepeaterLinkStatusResponse:
    agent = _require_agent(request)
    return await agent.unpair()


@router.post("/link/reconnect", response_model=RepeaterLinkStatusResponse)
async def reconnect_to_main(request: Request) -> RepeaterLinkStatusResponse:
    agent = _require_agent(request)
    agent.trigger_reconnect()
    return await agent.get_link_status()
