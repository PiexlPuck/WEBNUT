"""Background host reachability monitor and pre-outage snapshot tracker."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.database import Database, utc_now_iso
from app.services.ws_hub import WebSocketHub
from app.wol.network import check_host_reachable

logger = logging.getLogger(__name__)

ReachabilityFn = Callable[[str, str, int | None, float], Awaitable[bool]]


class HostMonitor:
    """Continuously polls managed hosts and persists pre-outage online states."""

    def __init__(
        self,
        db: Database,
        ws_hub: WebSocketHub | None = None,
        reachability_fn: ReachabilityFn = check_host_reachable,
    ) -> None:
        self.db = db
        self.ws_hub = ws_hub
        self.reachability_fn = reachability_fn
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def poll_once(self) -> None:
        """Poll all enabled hosts once and persist status + unfrozen pre-outage snapshot."""
        state_row = await self.db.fetch_one("SELECT snapshot_frozen FROM orchestrator_state WHERE id = 1")
        snapshot_frozen = bool(state_row["snapshot_frozen"]) if state_row else False

        hosts = await self.db.fetch_all(
            "SELECT id, ip_or_host, check_method, check_port FROM hosts WHERE enabled = 1"
        )
        if not hosts:
            return

        sem = asyncio.Semaphore(16)

        async def probe(host: dict) -> tuple[int, bool]:
            async with sem:
                alive = await self.reachability_fn(
                    host["ip_or_host"],
                    host["check_method"],
                    host["check_port"],
                    2.0,
                )
                return int(host["id"]), alive

        results = await asyncio.gather(*(probe(h) for h in hosts))
        now = utc_now_iso()

        for host_id, alive in results:
            online_int = 1 if alive else 0
            if snapshot_frozen:
                if alive:
                    await self.db.conn.execute(
                        "UPDATE hosts SET is_online = 1, last_seen_at = ?, updated_at = ? WHERE id = ?",
                        (now, now, host_id),
                    )
                else:
                    await self.db.conn.execute(
                        "UPDATE hosts SET is_online = 0, updated_at = ? WHERE id = ?",
                        (now, host_id),
                    )
            else:
                if alive:
                    await self.db.conn.execute(
                        """
                        UPDATE hosts
                        SET is_online = 1,
                            last_seen_at = ?,
                            was_online_before_outage = 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, host_id),
                    )
                else:
                    await self.db.conn.execute(
                        """
                        UPDATE hosts
                        SET is_online = 0,
                            was_online_before_outage = 0,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, host_id),
                    )

        await self.db.conn.commit()
        if self.ws_hub is not None:
            all_hosts = await self.db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
            await self.ws_hub.broadcast("hosts", all_hosts)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
            except Exception as exc:
                logger.exception("HostMonitor poll_once failed: %s", exc)

            cfg = await self.db.fetch_one(
                "SELECT host_poll_interval_sec FROM orchestrator_config WHERE id = 1"
            )
            interval = max(5, int(cfg["host_poll_interval_sec"])) if cfg else 30
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=float(interval))
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_loop(), name="host-monitor")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
