"""WOLNUT-style power recovery orchestrator & automatic retry-on-fail state machine."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.database import Database, utc_now_iso
from app.models.schemas import OrchestratorConfigModel, OrchestratorStateModel
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.services.event_log import EventLogService
from app.services.ws_hub import WebSocketHub
from app.wol.magic_packet import send_wol_packet
from app.wol.network import check_host_reachable

logger = logging.getLogger(__name__)

ReachabilityFn = Callable[[str, str, int | None, float], Awaitable[bool]]
WolSenderFn = Callable[[str, str | None], list[str]]


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


class RecoveryOrchestrator:
    """Monitors UPS transitions (OB -> OL) and orchestrates staggered WoL recovery with retries."""

    def __init__(
        self,
        db: Database,
        nut_client: NutTcpClient,
        dispatcher: NotificationDispatcher,
        event_log: EventLogService,
        ws_hub: WebSocketHub | None = None,
        reachability_fn: ReachabilityFn = check_host_reachable,
        wol_sender: WolSenderFn = send_wol_packet,
        check_poll_step: float = 3.0,
    ) -> None:
        self.db = db
        self.nut_client = nut_client
        self.dispatcher = dispatcher
        self.event_log = event_log
        self.ws_hub = ws_hub
        self.reachability_fn = reachability_fn
        self.wol_sender = wol_sender
        self.check_poll_step = check_poll_step

        self._poll_task: asyncio.Task[None] | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._manual_tasks: dict[int, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()

        self._last_comms_ok: bool | None = None
        self._low_battery_notified: bool = False
        self._power_restored_notified: bool = False
        self._latest_telemetry: dict = {"connected": False, "error": "Initializing", "ups_list": [], "updated_at": utc_now_iso()}

    @property
    def latest_telemetry(self) -> dict:
        return self._latest_telemetry

    async def get_config(self) -> OrchestratorConfigModel:
        row = await self.db.fetch_one("SELECT * FROM orchestrator_config WHERE id = 1")
        if row is None:
            return OrchestratorConfigModel()
        return OrchestratorConfigModel(
            monitored_ups=row["monitored_ups"],
            min_battery_charge=float(row["min_battery_charge"]),
            stability_hold_sec=int(row["stability_hold_sec"]),
            host_poll_interval_sec=int(row["host_poll_interval_sec"]),
            ups_poll_interval_sec=int(row["ups_poll_interval_sec"]),
            auto_recovery_enabled=bool(row["auto_recovery_enabled"]),
        )

    async def update_config(self, cfg: OrchestratorConfigModel) -> OrchestratorConfigModel:
        now = utc_now_iso()
        await self.db.execute(
            """
            UPDATE orchestrator_config
            SET monitored_ups = ?,
                min_battery_charge = ?,
                stability_hold_sec = ?,
                host_poll_interval_sec = ?,
                ups_poll_interval_sec = ?,
                auto_recovery_enabled = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                cfg.monitored_ups.strip(),
                cfg.min_battery_charge,
                cfg.stability_hold_sec,
                cfg.host_poll_interval_sec,
                cfg.ups_poll_interval_sec,
                1 if cfg.auto_recovery_enabled else 0,
                now,
            ),
        )
        await self.event_log.record(
            "orchestrator",
            "info",
            "orchestrator_config_updated",
            "Updated WoL recovery policy settings",
            cfg.model_dump(),
        )
        return await self.get_config()

    async def get_state(self) -> OrchestratorStateModel:
        row = await self.db.fetch_one("SELECT * FROM orchestrator_state WHERE id = 1")
        cfg = await self.get_config()
        if row is None:
            return OrchestratorStateModel(
                state="NORMAL",
                last_ups_status="UNKNOWN",
                snapshot_frozen=False,
                is_dry_run=False,
                updated_at=utc_now_iso(),
            )
        remaining = 0
        if row["state"] == "STABILIZING" and row["stabilizing_started_at"]:
            started = _parse_iso(row["stabilizing_started_at"])
            if started is not None:
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                remaining = max(0, int(round(cfg.stability_hold_sec - elapsed)))

        return OrchestratorStateModel(
            state=row["state"],
            last_ups_status=row["last_ups_status"],
            outage_started_at=row["outage_started_at"],
            stabilizing_started_at=row["stabilizing_started_at"],
            recovery_started_at=row["recovery_started_at"],
            snapshot_frozen=bool(row["snapshot_frozen"]),
            is_dry_run=bool(row["is_dry_run"]),
            stability_remaining_sec=remaining,
            updated_at=row["updated_at"],
        )

    async def _broadcast_state(self) -> None:
        if self.ws_hub is not None:
            st = await self.get_state()
            await self.ws_hub.broadcast("orchestrator", st.model_dump())

    async def _broadcast_hosts(self) -> None:
        if self.ws_hub is not None:
            rows = await self.db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
            await self.ws_hub.broadcast("hosts", rows)

    async def reconcile_on_startup(self) -> None:
        """Cleanly reconcile interrupted recovery states after container/host reboot."""
        row = await self.db.fetch_one("SELECT * FROM orchestrator_state WHERE id = 1")
        if row is None:
            return

        now = utc_now_iso()
        interrupted_hosts = await self.db.fetch_all(
            "SELECT id FROM hosts WHERE wake_state IN ('WAITING_STAGGER', 'WAKING', 'RETRYING')"
        )
        if interrupted_hosts:
            await self.db.execute(
                """
                UPDATE hosts
                SET wake_state = 'IDLE', wake_attempt = 0, updated_at = ?
                WHERE wake_state IN ('WAITING_STAGGER', 'WAKING', 'RETRYING')
                """,
                (now,),
            )

        if row["state"] in ("RECOVERING", "STABILIZING"):
            prev_state = row["state"]
            await self.db.execute(
                """
                UPDATE orchestrator_state
                SET state = 'NORMAL',
                    stabilizing_started_at = NULL,
                    recovery_started_at = NULL,
                    snapshot_frozen = 0,
                    is_dry_run = 0,
                    updated_at = ?
                WHERE id = 1
                """,
                (now,),
            )
            await self.event_log.record(
                "orchestrator",
                "warning",
                "system_reboot_reconciled",
                f"Reconciled interrupted '{prev_state}' state on startup to NORMAL",
                {"previous_state": prev_state, "reset_hosts": len(interrupted_hosts)},
            )

    async def _cancel_recovery_task(self) -> None:
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        self._recovery_task = None

    async def evaluate_telemetry(self, snapshot: dict) -> None:
        """Process a TelemetrySnapshot dict and advance the recovery state machine."""
        self._latest_telemetry = snapshot
        if self.ws_hub is not None:
            await self.ws_hub.broadcast("telemetry", snapshot)

        cfg = await self.get_config()
        state_row = await self.db.fetch_one("SELECT * FROM orchestrator_state WHERE id = 1")
        if state_row is None:
            return

        current_state = state_row["state"]
        ups_list = snapshot.get("ups_list") or []
        connected = bool(snapshot.get("connected"))

        # Handle UPS communications status
        if not connected or not ups_list:
            if self._last_comms_ok is True:
                self._last_comms_ok = False
                err = snapshot.get("error") or "No UPS telemetry available"
                await self.event_log.record(
                    "ups",
                    "warning",
                    "ups_comms_lost",
                    f"Lost communications with NUT upsd: {err}",
                    {"error": err},
                )
                await self.dispatcher.dispatch_event(
                    "ups_comms_lost",
                    "UPS Communications Lost",
                    f"WEBNUT lost connection to upsd: {err}",
                )
            elif self._last_comms_ok is None:
                self._last_comms_ok = False
            return

        # Select monitored UPS
        target_ups = None
        if cfg.monitored_ups:
            for u in ups_list:
                if u["name"] == cfg.monitored_ups:
                    target_ups = u
                    break
        if target_ups is None:
            target_ups = ups_list[0]

        status_str = str(target_ups.get("status", "UNKNOWN"))
        tokens = set(target_ups.get("status_tokens") or status_str.split())
        charge = target_ups.get("battery_charge")
        ups_name = target_ups.get("name", "ups")

        if "COMMBAD" in tokens or "NOCOMM" in tokens:
            if self._last_comms_ok is True:
                self._last_comms_ok = False
                await self.event_log.record(
                    "ups",
                    "warning",
                    "ups_comms_lost",
                    f"UPS '{ups_name}' reported {status_str}",
                    {"ups": ups_name, "status": status_str},
                )
                await self.dispatcher.dispatch_event(
                    "ups_comms_lost",
                    f"UPS '{ups_name}' Communications Lost",
                    f"UPS '{ups_name}' status is {status_str}.",
                )
            return

        self._last_comms_ok = True
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

        is_ob = "OB" in tokens
        is_ol = "OL" in tokens
        is_lb = "LB" in tokens

        # Low Battery alert
        if is_lb and not self._low_battery_notified:
            self._low_battery_notified = True
            await self.event_log.record(
                "ups",
                "error",
                "ups_low_battery",
                f"UPS '{ups_name}' battery is LOW (charge={charge}%)",
                {"ups": ups_name, "battery_charge": charge, "status": status_str},
            )
            await self.dispatcher.dispatch_event(
                "ups_low_battery",
                f"UPS '{ups_name}' Low Battery",
                f"Battery charge is {charge}% (status: {status_str}).",
            )
        elif not is_lb:
            self._low_battery_notified = False

        # State transitions
        if is_ob:
            self._power_restored_notified = False
            if current_state == "NORMAL":
                # Freeze pre-outage snapshot and transition to ON_BATTERY
                await self.db.execute(
                    """
                    UPDATE orchestrator_state
                    SET state = 'ON_BATTERY',
                        last_ups_status = ?,
                        outage_started_at = ?,
                        stabilizing_started_at = NULL,
                        recovery_started_at = NULL,
                        snapshot_frozen = 1,
                        is_dry_run = 0,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (status_str, now, now),
                )
                await self.event_log.record(
                    "ups",
                    "warning",
                    "ups_on_battery",
                    f"UPS '{ups_name}' switched to ON BATTERY (OB); pre-outage host snapshot frozen",
                    {"ups": ups_name, "battery_charge": charge, "status": status_str},
                )
                await self.dispatcher.dispatch_event(
                    "ups_on_battery",
                    f"Power Outage: UPS '{ups_name}' On Battery",
                    f"UPS switched to battery power (charge: {charge}%). Pre-outage host snapshot frozen.",
                )
                await self._broadcast_state()
                return

            if current_state == "STABILIZING":
                # Power flickered back to OB during stability hold
                await self.db.execute(
                    """
                    UPDATE orchestrator_state
                    SET state = 'ON_BATTERY',
                        last_ups_status = ?,
                        stabilizing_started_at = NULL,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (status_str, now),
                )
                await self.event_log.record(
                    "orchestrator",
                    "warning",
                    "stability_hold_reset",
                    f"UPS '{ups_name}' returned to OB during stability hold; timer reset",
                    {"ups": ups_name, "status": status_str},
                )
                await self._broadcast_state()
                return

            if current_state == "RECOVERING":
                # Power failed mid-recovery; abort active wake loop
                await self._cancel_recovery_task()
                await self.db.execute(
                    """
                    UPDATE hosts
                    SET wake_state = 'IDLE', wake_attempt = 0, updated_at = ?
                    WHERE wake_state IN ('WAITING_STAGGER', 'WAKING', 'RETRYING')
                    """,
                    (now,),
                )
                await self.db.execute(
                    """
                    UPDATE orchestrator_state
                    SET state = 'ON_BATTERY',
                        last_ups_status = ?,
                        stabilizing_started_at = NULL,
                        recovery_started_at = NULL,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (status_str, now),
                )
                await self.event_log.record(
                    "orchestrator",
                    "warning",
                    "recovery_aborted_on_battery",
                    f"UPS '{ups_name}' returned to OB; active wake sequence aborted",
                    {"ups": ups_name},
                )
                await self._broadcast_hosts()
                await self._broadcast_state()
                return

            await self.db.execute(
                "UPDATE orchestrator_state SET last_ups_status = ?, updated_at = ? WHERE id = 1",
                (status_str, now),
            )
            return

        if is_ol:
            charge_ok = charge is None or float(charge) >= cfg.min_battery_charge

            if current_state == "ON_BATTERY":
                if not self._power_restored_notified:
                    self._power_restored_notified = True
                    await self.event_log.record(
                        "ups",
                        "success",
                        "ups_power_restored",
                        f"Utility power restored on '{ups_name}' (OL, battery={charge}%)",
                        {"ups": ups_name, "battery_charge": charge, "status": status_str},
                    )
                    await self.dispatcher.dispatch_event(
                        "ups_power_restored",
                        f"Power Restored: UPS '{ups_name}' Online",
                        f"UPS is back online (battery charge: {charge}%).",
                    )

                if not cfg.auto_recovery_enabled:
                    await self.db.execute(
                        """
                        UPDATE orchestrator_state
                        SET state = 'NORMAL',
                            last_ups_status = ?,
                            stabilizing_started_at = NULL,
                            snapshot_frozen = 0,
                            updated_at = ?
                        WHERE id = 1
                        """,
                        (status_str, now),
                    )
                    await self._broadcast_state()
                    return

                if charge_ok:
                    if cfg.stability_hold_sec <= 0:
                        await self._trigger_recovery(now, status_str, ups_name, charge)
                    else:
                        await self.db.execute(
                            """
                            UPDATE orchestrator_state
                            SET state = 'STABILIZING',
                                last_ups_status = ?,
                                stabilizing_started_at = ?,
                                updated_at = ?
                            WHERE id = 1
                            """,
                            (status_str, now, now),
                        )
                        await self.event_log.record(
                            "orchestrator",
                            "info",
                            "stability_hold_started",
                            f"Battery {charge}% >= {cfg.min_battery_charge}%; holding {cfg.stability_hold_sec}s before WoL",
                            {"battery_charge": charge, "hold_sec": cfg.stability_hold_sec},
                        )
                        await self._broadcast_state()
                else:
                    await self.db.execute(
                        "UPDATE orchestrator_state SET last_ups_status = ?, updated_at = ? WHERE id = 1",
                        (status_str, now),
                    )
                    await self._broadcast_state()
                return

            if current_state == "STABILIZING":
                if not charge_ok:
                    await self.db.execute(
                        """
                        UPDATE orchestrator_state
                        SET state = 'ON_BATTERY',
                            last_ups_status = ?,
                            stabilizing_started_at = NULL,
                            updated_at = ?
                        WHERE id = 1
                        """,
                        (status_str, now),
                    )
                    await self.event_log.record(
                        "orchestrator",
                        "warning",
                        "stability_hold_charge_dropped",
                        f"Battery charge dropped to {charge}% (< {cfg.min_battery_charge}%); waiting",
                    )
                    await self._broadcast_state()
                    return

                started = _parse_iso(state_row["stabilizing_started_at"])
                elapsed = (now_dt - started).total_seconds() if started else 0.0
                if elapsed >= cfg.stability_hold_sec:
                    await self._trigger_recovery(now, status_str, ups_name, charge)
                else:
                    await self.db.execute(
                        "UPDATE orchestrator_state SET last_ups_status = ?, updated_at = ? WHERE id = 1",
                        (status_str, now),
                    )
                    await self._broadcast_state()
                return

        await self.db.execute(
            "UPDATE orchestrator_state SET last_ups_status = ?, updated_at = ? WHERE id = 1",
            (status_str, now),
        )

    async def _trigger_recovery(
        self,
        now: str,
        status_str: str,
        ups_name: str,
        charge: float | None,
    ) -> None:
        await self.db.execute(
            """
            UPDATE orchestrator_state
            SET state = 'RECOVERING',
                last_ups_status = ?,
                recovery_started_at = ?,
                is_dry_run = 0,
                updated_at = ?
            WHERE id = 1
            """,
            (status_str, now, now),
        )
        await self.event_log.record(
            "orchestrator",
            "info",
            "wake_sequence_started",
            f"Battery threshold met on '{ups_name}' ({charge}%); starting WoL recovery sequence",
            {"ups": ups_name, "battery_charge": charge},
        )
        await self.dispatcher.dispatch_event(
            "wake_sequence_started",
            "WoL Recovery Sequence Initiated",
            f"UPS '{ups_name}' stabilized at {charge}% battery. Starting host recovery.",
        )
        await self._broadcast_state()
        self._recovery_task = asyncio.create_task(
            self.run_recovery_sequence(dry_run=False),
            name="wol-recovery-sequence",
        )

    async def _wake_single_host(
        self,
        host: dict,
        track_retries: bool = True,
        dry_run: bool = False,
    ) -> bool:
        host_id = int(host["id"])
        name = str(host["name"])
        mac = str(host["mac_address"])
        ip_or_host = str(host["ip_or_host"])
        method = str(host["check_method"])
        port = host["check_port"]
        boot_timeout = float(host["boot_timeout_sec"])
        retry_interval = float(host["retry_interval_sec"])
        max_retries = int(host["max_retries"]) if track_retries else 1

        for attempt in range(1, max_retries + 1):
            now = utc_now_iso()
            state_label = "WAKING" if attempt == 1 else "RETRYING"
            await self.db.execute(
                """
                UPDATE hosts
                SET wake_state = ?, wake_attempt = ?, updated_at = ?
                WHERE id = ?
                """,
                (state_label, attempt, now, host_id),
            )
            await self._broadcast_hosts()

            if dry_run:
                await self.event_log.record(
                    "wol",
                    "info",
                    "wol_dry_run_packet",
                    f"[DRY RUN] Would send WoL magic packet to '{name}' ({mac}) [attempt {attempt}/{max_retries}]",
                    {"host_id": host_id, "mac": mac, "attempt": attempt},
                )
                alive = await self.reachability_fn(ip_or_host, method, port, 1.0)
                if alive:
                    await self.db.execute(
                        """
                        UPDATE hosts
                        SET is_online = 1, last_seen_at = ?, wake_state = 'WAKE_SUCCEEDED',
                            last_wake_error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (utc_now_iso(), utc_now_iso(), host_id),
                    )
                    await self._broadcast_hosts()
                    return True
                await self.db.execute(
                    """
                    UPDATE hosts
                    SET wake_state = 'IDLE', wake_attempt = 0,
                        last_wake_error = 'Dry run simulated (no magic packet sent)', updated_at = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), host_id),
                )
                await self._broadcast_hosts()
                return True

            # Send real magic packet
            try:
                targets = self.wol_sender(mac, host["subnet_broadcast"])
                await self.event_log.record(
                    "wol",
                    "info",
                    "wol_packet_sent",
                    f"Sent WoL magic packet to '{name}' ({mac}) via {', '.join(targets)} (attempt {attempt}/{max_retries})",
                    {"host_id": host_id, "mac": mac, "targets": targets, "attempt": attempt},
                )
            except Exception as exc:
                err_msg = f"Failed to send WoL packet: {exc}"
                await self.db.execute(
                    """
                    UPDATE hosts
                    SET wake_state = 'WAKE_FAILED', last_wake_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (err_msg, utc_now_iso(), host_id),
                )
                await self.event_log.record(
                    "wol",
                    "error",
                    "host_wake_failed",
                    f"WoL send error for '{name}': {exc}",
                    {"host_id": host_id, "error": str(exc)},
                )
                await self.dispatcher.dispatch_event(
                    "host_wake_failed",
                    f"Wake Failed: {name}",
                    err_msg,
                )
                await self._broadcast_hosts()
                return False

            # Poll host reachability up to boot_timeout
            loop = asyncio.get_running_loop()
            deadline = loop.time() + boot_timeout
            while True:
                alive = await self.reachability_fn(ip_or_host, method, port, 2.0)
                if alive:
                    done_now = utc_now_iso()
                    await self.db.execute(
                        """
                        UPDATE hosts
                        SET is_online = 1,
                            last_seen_at = ?,
                            wake_state = 'WAKE_SUCCEEDED',
                            wake_attempt = ?,
                            last_wake_error = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (done_now, attempt, done_now, host_id),
                    )
                    await self.event_log.record(
                        "wol",
                        "success",
                        "host_wake_succeeded",
                        f"Host '{name}' ({ip_or_host}) came online on attempt {attempt}/{max_retries}",
                        {"host_id": host_id, "attempt": attempt},
                    )
                    await self.dispatcher.dispatch_event(
                        "host_wake_succeeded",
                        f"Host Online: {name}",
                        f"'{name}' ({ip_or_host}) woke and verified online (attempt {attempt}/{max_retries}).",
                    )
                    await self._broadcast_hosts()
                    return True

                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self.check_poll_step, remaining))

            # Attempt timed out
            if attempt < max_retries:
                msg = f"Host '{name}' did not respond within {int(boot_timeout)}s; retrying ({attempt}/{max_retries})"
                await self.db.execute(
                    """
                    UPDATE hosts
                    SET wake_state = 'RETRYING', last_wake_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (msg, utc_now_iso(), host_id),
                )
                await self.event_log.record(
                    "wol",
                    "warning",
                    "host_wake_retrying",
                    msg,
                    {"host_id": host_id, "attempt": attempt, "max_retries": max_retries},
                )
                await self.dispatcher.dispatch_event(
                    "host_wake_retrying",
                    f"Retrying Wake: {name} ({attempt}/{max_retries})",
                    f"'{name}' did not come online after {int(boot_timeout)}s. Waiting {int(retry_interval)}s before retry {attempt + 1}/{max_retries}.",
                )
                await self._broadcast_hosts()
                await asyncio.sleep(retry_interval)
            else:
                fail_msg = f"Host '{name}' failed to wake after {max_retries} attempt(s)"
                await self.db.execute(
                    """
                    UPDATE hosts
                    SET wake_state = 'WAKE_FAILED', last_wake_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (fail_msg, utc_now_iso(), host_id),
                )
                await self.event_log.record(
                    "wol",
                    "error",
                    "host_wake_failed",
                    fail_msg,
                    {"host_id": host_id, "attempts": max_retries},
                )
                await self.dispatcher.dispatch_event(
                    "host_wake_failed",
                    f"WAKE FAILED: {name}",
                    f"'{name}' ({ip_or_host}) remained unreachable after {max_retries} wake attempt(s).",
                )
                await self._broadcast_hosts()
                return False

        return False

    async def run_recovery_sequence(self, dry_run: bool = False) -> None:
        """Execute staggered wake sequence for all eligible hosts."""
        cancelled = False
        try:
            eligible = await self.db.fetch_all(
                """
                SELECT * FROM hosts
                WHERE enabled = 1 AND (was_online_before_outage = 1 OR always_wake = 1)
                ORDER BY boot_priority ASC, id ASC
                """
            )
            if not eligible:
                await self.event_log.record(
                    "orchestrator",
                    "info",
                    "wake_sequence_empty",
                    "No eligible hosts required wake-on-LAN recovery",
                )
                return

            # Filter out hosts already online (unless dry_run where we still log simulation)
            to_wake: list[dict] = []
            for host in eligible:
                alive = await self.reachability_fn(
                    host["ip_or_host"],
                    host["check_method"],
                    host["check_port"],
                    2.0,
                )
                if alive and not dry_run:
                    now = utc_now_iso()
                    await self.db.execute(
                        """
                        UPDATE hosts
                        SET is_online = 1, last_seen_at = ?, wake_state = 'WAKE_SUCCEEDED',
                            wake_attempt = 0, last_wake_error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, host["id"]),
                    )
                else:
                    await self.db.execute(
                        """
                        UPDATE hosts
                        SET wake_state = 'WAITING_STAGGER', wake_attempt = 0,
                            last_wake_error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (utc_now_iso(), host["id"]),
                    )
                    to_wake.append(host)

            await self._broadcast_hosts()

            if to_wake:
                worker_tasks: list[asyncio.Task[bool]] = []
                for idx, host in enumerate(to_wake):
                    if idx > 0:
                        delay = max(0, int(host["stagger_delay_sec"]))
                        if dry_run:
                            delay = min(delay, 1)
                        if delay > 0:
                            await asyncio.sleep(float(delay))
                    task = asyncio.create_task(
                        self._wake_single_host(host, track_retries=True, dry_run=dry_run),
                        name=f"wake-host-{host['id']}",
                    )
                    worker_tasks.append(task)
                await asyncio.gather(*worker_tasks)

            await self.event_log.record(
                "orchestrator",
                "success",
                "wake_sequence_completed",
                f"{'[DRY RUN] ' if dry_run else ''}Recovery sequence completed for {len(eligible)} eligible host(s)",
                {"dry_run": dry_run, "eligible_count": len(eligible)},
            )
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not cancelled:
                now = utc_now_iso()
                await self.db.execute(
                    """
                    UPDATE orchestrator_state
                    SET state = 'NORMAL',
                        stabilizing_started_at = NULL,
                        recovery_started_at = NULL,
                        snapshot_frozen = 0,
                        is_dry_run = 0,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (now,),
                )
                await self._broadcast_state()
                await self._broadcast_hosts()

    async def manual_wake_host(self, host_id: int, track_retries: bool = True) -> None:
        """Trigger an immediate wake (with optional auto-retry) for a single host."""
        host = await self.db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
        if host is None:
            raise ValueError(f"Host {host_id} not found")

        existing = self._manual_tasks.get(host_id)
        if existing is not None and not existing.done():
            existing.cancel()
            try:
                await existing
            except asyncio.CancelledError:
                pass

        async def _runner() -> None:
            await self._wake_single_host(host, track_retries=track_retries, dry_run=False)

        self._manual_tasks[host_id] = asyncio.create_task(_runner(), name=f"manual-wake-{host_id}")

    async def simulate_recovery(self, dry_run: bool = True) -> OrchestratorStateModel:
        """Manually trigger a recovery run (dry-run or live) for testing."""
        if self._recovery_task is not None and not self._recovery_task.done():
            raise RuntimeError("A recovery sequence is already in progress")

        now = utc_now_iso()
        await self.db.execute(
            """
            UPDATE orchestrator_state
            SET state = 'RECOVERING',
                recovery_started_at = ?,
                snapshot_frozen = 1,
                is_dry_run = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (now, 1 if dry_run else 0, now),
        )
        await self.event_log.record(
            "orchestrator",
            "info",
            "recovery_simulation_started",
            f"Triggered manual recovery sequence ({'dry-run' if dry_run else 'live'})",
            {"dry_run": dry_run},
        )
        await self._broadcast_state()
        self._recovery_task = asyncio.create_task(
            self.run_recovery_sequence(dry_run=dry_run),
            name="simulated-recovery",
        )
        return await self.get_state()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                snapshot = await self.nut_client.fetch_telemetry()
                await self.evaluate_telemetry(snapshot.model_dump())
            except Exception as exc:
                logger.exception("RecoveryOrchestrator poll failed: %s", exc)

            cfg = await self.get_config()
            interval = max(1, cfg.ups_poll_interval_sec)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=float(interval))
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._poll_task is None or self._poll_task.done():
            self._stop_event.clear()
            self._poll_task = asyncio.create_task(self._run_loop(), name="nut-orchestrator")

    async def stop(self) -> None:
        self._stop_event.set()
        await self._cancel_recovery_task()
        for task in list(self._manual_tasks.values()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._manual_tasks.clear()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
