"""WebSocket broadcast hub for live UI updates."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketHub:
    """Thread/async-safe registry of connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, message_type: str, payload: Any) -> None:
        """Broadcast a typed JSON envelope to all active clients."""
        async with self._lock:
            clients = list(self._clients)

        if not clients:
            return

        envelope = {"type": message_type, "data": payload}
        stale: list[WebSocket] = []

        for ws in clients:
            try:
                await ws.send_json(envelope)
            except Exception:
                stale.append(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    self._clients.discard(ws)
