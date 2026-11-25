"""WOLNUT-style power recovery orchestrator, per-host shutdown streaming, and disconnect grace engine."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.database import Database, fetch_public_hosts, utc_now_iso
from app.models.schemas import OrchestratorConfigModel, OrchestratorStateModel
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.nut.repeater import NutRepeaterServer, VirtualUpsOverride
from app.services.crypto import SecretBox
from app.services.event_log import EventLogService
from app.services.ssh_deployer import execute_ssh_shutdown
from app.services.ws_hub import WebSocketHub
from app.wol.magic_packet import send_wol_packet
from app.wol.network import check_host_reachable

logger = logging.getLogger(__name__)

ReachabilityFn = Callable[[str, str, int | None, float], Awaitable[bool]]
WolSenderFn = Callable[[str, str | None], list[str]]
SshShutdownFn = Callable[[str, int, str, str, str, str], Awaitable[tuple[bool, str]]]


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def make_virtual_ups_name(host_row: dict) -> str:
    explicit = str(host_row.get("virtual_ups_name") or "").strip()
    if explicit:
        return explicit
    raw = str(host_row.get("name") or f"host_{host_row.get('id', 1)}")
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw.strip()).strip("_").lower()
    return slug or f"host_{host_row.get('id', 1)}"


class RecoveryOrchestrator:
    """Monitors UPS transitions, streams per-host NUT shutdowns, enforces disconnect grace, and wakes hosts."""

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
        repeater: NutRepeaterServer | None = None,
        secret_box: SecretBox | None = None,
        ssh_shutdown_fn: SshShutdownFn = execute_ssh_shutdown,
        repeater_hub: Any = None,
    ) -> None:
        self.db = db
        self.nut_client = nut_client
        self.dispatcher = dispatcher
        self.event_log = event_log
        self.ws_hub = ws_hub
        self.reachability_fn = reachability_fn
        self.wol_sender = wol_sender
        self.check_poll_step = check_poll_step
        self.repeater = repeater
        self.secret_box = secret_box
        self.ssh_shutdown_fn = ssh_shutdown_fn
        self.repeater_hub = repeater_hub

        self._poll_task: asyncio.Task[None] | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._manual_tasks: dict[int, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._eval_lock = asyncio.Lock()
        self._last_local_snapshot: dict | None = None

        self._last_comms_ok: bool | None = None
        self._low_battery_notified: bool = False
        self._power_restored_notified: bool = False
        self._disconnect_started_at: datetime | None = None
        self._ups_disconnect_started_at: dict[str, datetime] = {}
        self._ups_ob_started_at: dict[str, datetime] = {}
        self._host_shutdown_forced: dict[int, tuple[str, str]] = {}
        self._host_ssh_shutdown_sent: set[int] = set()
        self._latest_telemetry: dict = {
            "connected": False,
            "error": "Initializing",
            "ups_list": [],
            "updated_at": utc_now_iso(),
        }

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
            disconnect_policy=row.get("disconnect_policy") or "keep_running",
            disconnect_grace_sec=int(row.get("disconnect_grace_sec", 600)),
            repeater_enabled=bool(row.get("repeater_enabled", 1)),
            repeater_port=int(row.get("repeater_port", 3493)),
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
                disconnect_policy = ?,
                disconnect_grace_sec = ?,
                repeater_enabled = ?,
                repeater_port = ?,
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
                cfg.disconnect_policy,
                cfg.disconnect_grace_sec,
                1 if cfg.repeater_enabled else 0,
                cfg.repeater_port,
                now,
            ),
        )
        await self.event_log.record(
            "orchestrator",
            "info",
            "orchestrator_config_updated",
            "Updated recovery, disconnect grace, and repeater settings",
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

        disc_elapsed = 0
        disc_iso: str | None = None
        if self._disconnect_started_at is not None:
            disc_iso = self._disconnect_started_at.isoformat()
            disc_elapsed = max(
                0, int((datetime.now(timezone.utc) - self._disconnect_started_at).total_seconds())
            )

        return OrchestratorStateModel(
            state=row["state"],
            last_ups_status=row["last_ups_status"],
            outage_started_at=row["outage_started_at"],
            stabilizing_started_at=row["stabilizing_started_at"],
            recovery_started_at=row["recovery_started_at"],
            disconnect_started_at=disc_iso,
            disconnect_elapsed_sec=disc_elapsed,
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
            rows = await fetch_public_hosts(self.db)
            await self.ws_hub.broadcast("hosts", rows)

    async def reconcile_on_startup(self) -> None:
        """Cleanly reconcile interrupted recovery states after container/host reboot."""
        row = await self.db.fetch_one("SELECT * FROM orchestrator_state WHERE id = 1")
        if row is None:
            return

        now = utc_now_iso()
        interrupted_hosts = await self.db.fetch_all(
            "SELECT id FROM hosts WHERE wake_state IN ('WAITING_STAGGER', 'WAITING_BATTERY', 'WAKING', 'RETRYING')"
        )
        if interrupted_hosts:
            await self.db.execute(
                """
                UPDATE hosts
                SET wake_state = 'IDLE', wake_attempt = 0, updated_at = ?
                WHERE wake_state IN ('WAITING_STAGGER', 'WAITING_BATTERY', 'WAKING', 'RETRYING')
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

    async def _sync_repeater(self, ups_list: list[dict], default_ups_name: str) -> None:
        if self.repeater is None:
            return
        hosts = await self.db.fetch_all("SELECT * FROM hosts WHERE enabled = 1")
        virtuals: list[VirtualUpsOverride] = []
        for h in hosts:
            hid = int(h["id"])
            vname = make_virtual_ups_name(h)
            upstream = str(h.get("nut_ups_name") or "").strip() or default_ups_name
            forced = self._host_shutdown_forced.get(hid)
            virtuals.append(
                VirtualUpsOverride(
                    virtual_name=vname,
                    host_id=hid,
                    host_name=str(h["name"]),
                    upstream_ups=upstream,
                    forced_status=forced[0] if forced else None,
                    reason=forced[1] if forced else "",
                )
            )
        self.repeater.update_state(ups_list, virtuals)

    async def _trigger_host_shutdown(self, host: dict, reason: str) -> None:
        hid = int(host["id"])
        name = str(host["name"])
        mode = str(host.get("shutdown_mode") or "nut_stream")
        if mode == "disabled":
            return

        current_forced = self._host_shutdown_forced.get(hid)
        if not current_forced or current_forced[0] != "OB LB FSD":
            if mode in ("nut_stream", "both"):
                self._host_shutdown_forced[hid] = ("OB LB FSD", reason)
                await self.event_log.record(
                    "orchestrator",
                    "warning",
                    "host_shutdown_streamed",
                    f"Streaming OB LB FSD shutdown signal to '{name}' ({reason})",
                    {"host_id": hid, "reason": reason, "virtual_ups": make_virtual_ups_name(host)},
                )

        if mode in ("ssh", "both") and hid not in self._host_ssh_shutdown_sent:
            self._host_ssh_shutdown_sent.add(hid)
            ssh_user = str(host.get("ssh_username") or "").strip()
            ssh_pw_enc = str(host.get("ssh_password_enc") or "")
            ssh_sudo_enc = str(host.get("ssh_sudo_password_enc") or "")
            if ssh_user and ssh_pw_enc and self.secret_box is not None:
                pw = self.secret_box.decrypt(ssh_pw_enc)
                sudo_pw = self.secret_box.decrypt(ssh_sudo_enc)
                ok, detail = await self.ssh_shutdown_fn(
                    str(host["ip_or_host"]),
                    int(host.get("ssh_port") or 22),
                    ssh_user,
                    pw,
                    sudo_pw,
                    str(host.get("ssh_shutdown_cmd") or "sudo -S shutdown -h now"),
                )
                await self.event_log.record(
                    "orchestrator",
                    "warning" if ok else "error",
                    "host_ssh_shutdown",
                    f"SSH fallback shutdown for '{name}': {detail}",
                    {"host_id": hid, "ok": ok, "reason": reason},
                )

    async def _evaluate_host_policies_matrix(
        self,
        cfg: OrchestratorConfigModel,
        now_dt: datetime,
        ups_list: list[dict],
        default_ups_name: str,
        monitored_ob_elapsed: float | None = None,
    ) -> None:
        """Evaluate per-host ON_BATTERY and Disconnect Grace policies against each host's bound UPS."""
        ups_by_name = {
            str(u.get("name")): u for u in ups_list if isinstance(u, dict) and u.get("name")
        }
        hosts = await self.db.fetch_all("SELECT * FROM hosts WHERE enabled = 1")
        for h in hosts:
            hid = int(h["id"])
            host_ups_name = str(h.get("nut_ups_name") or "").strip() or default_ups_name
            host_ups = ups_by_name.get(host_ups_name)
            tokens = (
                set(host_ups.get("status_tokens") or str(host_ups.get("status") or "").split())
                if host_ups is not None
                else set()
            )
            is_disconnected = (
                host_ups is None
                or host_ups.get("node_online") is False
                or "OFFLINE" in tokens
                or "NOCOMM" in tokens
                or "COMMBAD" in tokens
            )

            if is_disconnected:
                self._ups_ob_started_at.pop(host_ups_name, None)
                if host_ups_name not in self._ups_disconnect_started_at:
                    self._ups_disconnect_started_at[host_ups_name] = now_dt
                disc_start = self._ups_disconnect_started_at[host_ups_name]
                if host_ups_name == default_ups_name and self._disconnect_started_at is not None:
                    disc_start = min(disc_start, self._disconnect_started_at)
                elapsed = (now_dt - disc_start).total_seconds()

                h_policy = str(h.get("disconnect_policy") or "inherit")
                effective_policy = cfg.disconnect_policy if h_policy == "inherit" else h_policy
                h_grace = h.get("disconnect_grace_sec")
                effective_grace = (
                    int(h_grace) if h_grace is not None else int(cfg.disconnect_grace_sec)
                )
                if effective_policy == "shutdown_after_grace" and elapsed >= effective_grace:
                    await self._trigger_host_shutdown(
                        h,
                        f"UPS '{host_ups_name}' disconnected for {int(elapsed)}s (>= {effective_grace}s grace)",
                    )
                elif self._host_shutdown_forced.get(hid, ("", ""))[0] != "OB LB FSD":
                    # Hold virtual stream at OL so clients keep running during grace window
                    self._host_shutdown_forced[hid] = ("OL", "disconnect_grace_hold")
                continue

            # Host's bound UPS is reachable
            self._ups_disconnect_started_at.pop(host_ups_name, None)
            if "OB" in tokens:
                if host_ups_name not in self._ups_ob_started_at:
                    self._ups_ob_started_at[host_ups_name] = now_dt
                ob_elapsed = (
                    monitored_ob_elapsed
                    if (monitored_ob_elapsed is not None and host_ups_name == default_ups_name)
                    else max(0.0, (now_dt - self._ups_ob_started_at[host_ups_name]).total_seconds())
                )
                mode = str(h.get("shutdown_mode") or "nut_stream")
                if mode == "disabled":
                    continue
                charge = host_ups.get("battery_charge") if host_ups else None
                is_lb = "LB" in tokens
                bat_thresh = h.get("shutdown_battery_pct")
                ob_delay = h.get("shutdown_ob_delay_sec")

                reason: str | None = None
                if is_lb:
                    reason = "UPS Low Battery (LB)"
                elif (
                    bat_thresh is not None
                    and charge is not None
                    and float(charge) <= float(bat_thresh)
                ):
                    reason = (
                        f"Battery {float(charge):.0f}% <= {float(bat_thresh):.0f}% host shutdown threshold"
                    )
                elif ob_delay is not None and ob_elapsed >= int(ob_delay):
                    reason = f"On battery {int(ob_elapsed)}s >= {int(ob_delay)}s delay"

                if reason:
                    await self._trigger_host_shutdown(h, reason)
                elif self._host_shutdown_forced.get(hid, ("", ""))[0] != "OB LB FSD":
                    self._host_shutdown_forced[hid] = ("OB", "waiting_host_threshold")
            elif "OL" in tokens:
                self._ups_ob_started_at.pop(host_ups_name, None)
                self._host_shutdown_forced.pop(hid, None)
                self._host_ssh_shutdown_sent.discard(hid)

        await self._sync_repeater(ups_list, default_ups_name)

    async def _evaluate_disconnect_policies(
        self,
        cfg: OrchestratorConfigModel,
        now_dt: datetime,
        ups_list: list[dict],
        default_ups_name: str,
    ) -> None:
        if self._disconnect_started_at is None:
            self._disconnect_started_at = now_dt
        await self._evaluate_host_policies_matrix(cfg, now_dt, ups_list, default_ups_name)
        await self._broadcast_state()

    async def _evaluate_on_battery_policies(
        self,
        charge: float | None,
        is_lb: bool,
        ob_elapsed: float,
        ups_list: list[dict],
        default_ups_name: str,
    ) -> None:
        cfg = await self.get_config()
        await self._evaluate_host_policies_matrix(
            cfg,
            datetime.now(timezone.utc),
            ups_list,
            default_ups_name,
            monitored_ob_elapsed=ob_elapsed,
        )

    async def _min_eligible_wake_battery(self, cfg: OrchestratorConfigModel) -> float:
        eligible = await self.db.fetch_all(
            """
            SELECT wake_min_battery_charge FROM hosts
            WHERE enabled = 1 AND (was_online_before_outage = 1 OR always_wake = 1)
            """
        )
        if not eligible:
            return cfg.min_battery_charge
        thresholds = [
            float(r["wake_min_battery_charge"])
            if r["wake_min_battery_charge"] is not None
            else cfg.min_battery_charge
            for r in eligible
        ]
        return min(thresholds)

    def _build_merged_snapshot(self, local_snapshot: dict) -> dict:
        self._last_local_snapshot = dict(local_snapshot)
        local_ups = list(local_snapshot.get("ups_list") or [])
        remote_ups = (
            self.repeater_hub.get_merged_ups_list()
            if self.repeater_hub is not None
            else []
        )
        seen_names = {str(u.get("name")) for u in local_ups if isinstance(u, dict) and u.get("name")}
        merged_ups = list(local_ups)
        for ru in remote_ups:
            rname = str(ru.get("name") or "")
            if rname and rname not in seen_names:
                seen_names.add(rname)
                merged_ups.append(ru)

        local_connected = bool(local_snapshot.get("connected"))
        connected = bool(local_connected or len(merged_ups) > 0)
        return {
            "connected": connected,
            "error": None if connected else local_snapshot.get("error"),
            "ups_list": merged_ups,
            "updated_at": utc_now_iso(),
        }

    async def refresh_from_repeaters(self) -> None:
        base_local = self._last_local_snapshot or {
            "connected": False,
            "error": "No local UPS configured",
            "ups_list": [],
            "updated_at": utc_now_iso(),
        }
        merged = self._build_merged_snapshot(base_local)
        await self.evaluate_telemetry(merged)

    async def evaluate_telemetry(self, snapshot: dict) -> None:
        """Process a TelemetrySnapshot dict and advance state machine + repeater streams."""
        async with self._eval_lock:
            await self._evaluate_telemetry_locked(snapshot)

    async def _evaluate_telemetry_locked(self, snapshot: dict) -> None:
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
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

        # Handle UPS communications status / Disconnection grace policy
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
                    f"WebNUT lost connection to upsd: {err}. Grace policy: {cfg.disconnect_policy}.",
                )
            elif self._last_comms_ok is None:
                self._last_comms_ok = False
            await self._evaluate_disconnect_policies(cfg, now_dt, ups_list, cfg.monitored_ups or "ups1")
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
        ups_name = str(target_ups.get("name", "ups"))

        if "COMMBAD" in tokens or "NOCOMM" in tokens or "OFFLINE" in tokens or target_ups.get("node_online") is False:
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
                    f"UPS '{ups_name}' status is {status_str}. Grace policy: {cfg.disconnect_policy}.",
                )
            await self._evaluate_disconnect_policies(cfg, now_dt, ups_list, ups_name)
            return

        # Communications healthy
        self._last_comms_ok = True
        self._disconnect_started_at = None

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
            outage_dt = _parse_iso(state_row["outage_started_at"]) or now_dt
            ob_elapsed = max(0.0, (now_dt - outage_dt).total_seconds())

            if current_state == "NORMAL":
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
                await self._evaluate_on_battery_policies(charge, is_lb, 0.0, ups_list, ups_name)
                await self._broadcast_state()
                return

            if current_state == "STABILIZING":
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
                await self._evaluate_on_battery_policies(charge, is_lb, ob_elapsed, ups_list, ups_name)
                await self._broadcast_state()
                return

            if current_state == "RECOVERING":
                await self._cancel_recovery_task()
                await self.db.execute(
                    """
                    UPDATE hosts
                    SET wake_state = 'IDLE', wake_attempt = 0, updated_at = ?
                    WHERE wake_state IN ('WAITING_STAGGER', 'WAITING_BATTERY', 'WAKING', 'RETRYING')
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
                await self._evaluate_on_battery_policies(charge, is_lb, ob_elapsed, ups_list, ups_name)
                await self._broadcast_hosts()
                await self._broadcast_state()
                return

            await self.db.execute(
                "UPDATE orchestrator_state SET last_ups_status = ?, updated_at = ? WHERE id = 1",
                (status_str, now),
            )
            await self._evaluate_on_battery_policies(charge, is_lb, ob_elapsed, ups_list, ups_name)
            return

        if is_ol:
            await self._evaluate_host_policies_matrix(cfg, now_dt, ups_list, ups_name)

            min_needed_charge = await self._min_eligible_wake_battery(cfg)
            charge_ok = charge is None or float(charge) >= min_needed_charge

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
                            f"Battery {charge}% >= {min_needed_charge:.0f}%; holding {cfg.stability_hold_sec}s before WoL",
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
                        f"Battery charge dropped to {charge}% (< {min_needed_charge:.0f}%); waiting",
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

        await self._evaluate_host_policies_matrix(cfg, now_dt, ups_list, ups_name)
        await self.db.execute(
            "UPDATE orchestrator_state SET last_ups_status = ?, updated_at = ? WHERE id = 1",
            (status_str, now),
        )

    def _current_battery_charge(self, preferred_ups: str) -> float | None:
        ups_list = self._latest_telemetry.get("ups_list") or []
        if not ups_list:
            return None
        target = None
        if preferred_ups:
            for u in ups_list:
                if u.get("name") == preferred_ups:
                    target = u
                    break
        if target is None:
            target = ups_list[0]
        val = target.get("battery_charge")
        return float(val) if val is not None else None

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
            f"Power stabilized on '{ups_name}' ({charge}%); starting staged WoL recovery",
            {"ups": ups_name, "battery_charge": charge},
        )
        await self.dispatcher.dispatch_event(
            "wake_sequence_started",
            "WoL Recovery Sequence Initiated",
            f"UPS '{ups_name}' stabilized at {charge}% battery. Starting staged host recovery.",
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
                SET wake_state = ?, wake_attempt = ?, last_wake_error = NULL, updated_at = ?
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

    async def run_recovery_sequence(
        self, dry_run: bool = False, target_host_ids: list[int] | None = None
    ) -> None:
        """Execute staged battery-threshold + staggered WoL recovery sequence."""
        cancelled = False
        worker_tasks: list[asyncio.Task[bool]] = []
        try:
            cfg = await self.get_config()
            clean_ids = [int(hid) for hid in (target_host_ids or []) if int(hid) > 0]
            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                eligible = await self.db.fetch_all(
                    f"""
                    SELECT * FROM hosts
                    WHERE id IN ({placeholders})
                    ORDER BY COALESCE(wake_min_battery_charge, 0) ASC, boot_priority ASC, id ASC
                    """,
                    tuple(clean_ids),
                )
            else:
                eligible = await self.db.fetch_all(
                    """
                    SELECT * FROM hosts
                    WHERE enabled = 1 AND (was_online_before_outage = 1 OR always_wake = 1)
                    ORDER BY COALESCE(wake_min_battery_charge, 0) ASC, boot_priority ASC, id ASC
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

            pending_hosts: list[tuple[dict, float]] = []
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
                    req_pct = (
                        float(host["wake_min_battery_charge"])
                        if host.get("wake_min_battery_charge") is not None
                        else cfg.min_battery_charge
                    )
                    cur_charge = self._current_battery_charge(cfg.monitored_ups)
                    initial_state = (
                        "WAITING_BATTERY"
                        if (not dry_run and cur_charge is not None and cur_charge < req_pct)
                        else "WAITING_STAGGER"
                    )
                    err_note = (
                        f"Waiting for battery >= {req_pct:.0f}%"
                        if initial_state == "WAITING_BATTERY"
                        else None
                    )
                    await self.db.execute(
                        """
                        UPDATE hosts
                        SET wake_state = ?, wake_attempt = 0,
                            last_wake_error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (initial_state, err_note, utc_now_iso(), host["id"]),
                    )
                    pending_hosts.append((host, req_pct))

            await self._broadcast_hosts()

            dispatched_count = 0
            while pending_hosts:
                cur_charge = self._current_battery_charge(cfg.monitored_ups)
                ready_indices: list[int] = []
                for i, (_h, req_pct) in enumerate(pending_hosts):
                    if dry_run or cur_charge is None or cur_charge >= req_pct:
                        ready_indices.append(i)

                if not ready_indices:
                    await asyncio.sleep(self.check_poll_step)
                    continue

                # Dispatch ready hosts in order with stagger delay
                for idx_offset, ready_i in enumerate(ready_indices):
                    host, _ = pending_hosts[ready_i - idx_offset]
                    pending_hosts.pop(ready_i - idx_offset)
                    if dispatched_count > 0:
                        delay = max(0, int(host["stagger_delay_sec"]))
                        if dry_run:
                            delay = min(delay, 1)
                        if delay > 0:
                            await asyncio.sleep(float(delay))
                    dispatched_count += 1
                    task = asyncio.create_task(
                        self._wake_single_host(host, track_retries=True, dry_run=dry_run),
                        name=f"wake-host-{host['id']}",
                    )
                    worker_tasks.append(task)

            if worker_tasks:
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
            for t in worker_tasks:
                if not t.done():
                    t.cancel()
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

    async def test_run_host(self, host_id: int, dry_run: bool = True) -> dict:
        """Run a diagnostic dry-run or live single-host WoL test run."""
        host = await self.db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
        if host is None:
            raise ValueError(f"Host {host_id} not found")

        cfg = await self.get_config()
        reachable = await self.reachability_fn(
            str(host["ip_or_host"]),
            str(host["check_method"]),
            host["check_port"],
            2.0,
        )
        vname = make_virtual_ups_name(host)

        host_ip = str(host["ip_or_host"]).strip()
        connected_ips: set[str] = set()
        if self.repeater is not None:
            for c in self.repeater.get_connected_clients():
                connected_ips.add(str(c.get("client_ip") or "").strip())
        for u in self._latest_telemetry.get("ups_list") or []:
            for cip in u.get("connected_clients") or []:
                connected_ips.add(str(cip).strip())
        nut_connected = host_ip in connected_ips

        cur_charge = self._current_battery_charge(cfg.monitored_ups)
        eff_wake = (
            float(host["wake_min_battery_charge"])
            if host.get("wake_min_battery_charge") is not None
            else float(cfg.min_battery_charge)
        )
        would_wake = cur_charge is None or float(cur_charge) >= eff_wake

        s_mode = str(host.get("shutdown_mode") or "nut_stream")
        if s_mode == "disabled":
            shutdown_summary = "Shutdown disabled"
        else:
            parts: list[str] = []
            if host.get("shutdown_battery_pct") is not None:
                parts.append(f"<= {float(host['shutdown_battery_pct']):.0f}% battery")
            if host.get("shutdown_ob_delay_sec") is not None:
                parts.append(f"after {int(host['shutdown_ob_delay_sec'])}s OB")
            if not parts:
                parts.append("on UPS Low Battery (LB)")
            shutdown_summary = f"{', '.join(parts)} ({s_mode})"

        h_pol = str(host.get("disconnect_policy") or "inherit")
        eff_pol = cfg.disconnect_policy if h_pol == "inherit" else h_pol
        h_grace = host.get("disconnect_grace_sec")
        eff_grace = int(h_grace) if h_grace is not None else int(cfg.disconnect_grace_sec)
        disconnect_summary = (
            "Keep running & alert"
            if eff_pol == "keep_running"
            else f"Hold OL for {eff_grace}s then shutdown"
        )

        wol_targets = ["255.255.255.255"]
        if host.get("subnet_broadcast"):
            wol_targets.append(str(host["subnet_broadcast"]))

        notes: list[str] = []
        notes.append(
            f"Reachability ({host['check_method'].upper()}): {'Online' if reachable else 'Offline'}."
        )
        notes.append(
            f"NUT Repeater Stream '{vname}': {'Connected' if nut_connected else 'Not currently connected'}."
        )
        if not would_wake and cur_charge is not None:
            notes.append(
                f"Battery check: current {cur_charge:.0f}% < wake minimum {eff_wake:.0f}% (auto-recovery would wait in WAITING_BATTERY)."
            )
        else:
            notes.append(
                f"Battery check: wake threshold >= {eff_wake:.0f}% OL satisfied."
            )

        started_live_wake = False
        if not dry_run:
            await self.manual_wake_host(host_id, track_retries=True)
            started_live_wake = True
            notes.append("Dispatched live WoL magic packet test with retry verification loop.")
        else:
            notes.append("Dry-run only: no WoL magic packet transmitted.")

        await self.event_log.record(
            "orchestrator",
            "info",
            "host_test_run",
            f"{'[DRY RUN] ' if dry_run else ''}Individual host test run for '{host['name']}' ({host_ip})",
            {
                "host_id": host_id,
                "dry_run": dry_run,
                "reachable": reachable,
                "nut_client_connected": nut_connected,
                "would_wake_now": would_wake,
                "started_live_wake": started_live_wake,
            },
        )

        return {
            "host_id": host_id,
            "host_name": str(host["name"]),
            "ip_or_host": host_ip,
            "dry_run": dry_run,
            "reachable": reachable,
            "check_method": str(host["check_method"]),
            "virtual_ups_name": vname,
            "nut_client_connected": nut_connected,
            "current_battery_charge": cur_charge,
            "effective_wake_battery_pct": eff_wake,
            "would_wake_now": would_wake,
            "wol_targets": wol_targets,
            "shutdown_rule_summary": shutdown_summary,
            "disconnect_rule_summary": disconnect_summary,
            "started_live_wake": started_live_wake,
            "notes": notes,
        }

    async def simulate_recovery(
        self,
        dry_run: bool = True,
        scope: str = "bulk",
        host_ids: list[int] | None = None,
    ) -> OrchestratorStateModel:
        """Manually trigger a bulk or single-host recovery test run (dry-run or live)."""
        if self._recovery_task is not None and not self._recovery_task.done():
            raise RuntimeError("A recovery sequence is already in progress")

        clean_ids = [int(hid) for hid in (host_ids or []) if int(hid) > 0]
        target_ids = clean_ids if clean_ids else None
        scope_label = f"{scope} ({len(clean_ids)} selected host(s))" if clean_ids else "bulk (all eligible hosts)"

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
            f"Triggered {scope_label} recovery test run ({'dry-run' if dry_run else 'live'})",
            {"dry_run": dry_run, "scope": scope, "host_ids": clean_ids},
        )
        await self._broadcast_state()
        self._recovery_task = asyncio.create_task(
            self.run_recovery_sequence(dry_run=dry_run, target_host_ids=target_ids),
            name="simulated-recovery",
        )
        return await self.get_state()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                snapshot = await self.nut_client.fetch_telemetry()
                merged = self._build_merged_snapshot(snapshot.model_dump())
                await self.evaluate_telemetry(merged)
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
