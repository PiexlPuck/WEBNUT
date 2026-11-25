"""Encrypted persistent store for generated and SSH-deployed NUT client configs per device."""

from __future__ import annotations

import json
from typing import Literal

from app.database import Database, utc_now_iso
from app.models.schemas import (
    NutClientConfigResponse,
    NutClientPlatform,
    RecordedClientConfigDetail,
    RecordedClientConfigSummary,
)
from app.services.crypto import SecretBox


async def upsert_client_config_record(
    db: Database,
    secret_box: SecretBox,
    *,
    host_id: int | None,
    device_name: str,
    platform: str,
    server_host: str,
    nut_port: int,
    ups_name: str,
    username: str,
    final_delay: int,
    poll_freq: int,
    deadtime: int,
    shutdown_cmd: str,
    delivery_method: Literal["manual", "ssh_deploy"],
    deploy_status: Literal["generated", "deployed", "failed"],
    config_response: NutClientConfigResponse,
) -> int:
    """Encrypt and store (or update) a recorded NUT client configuration for a device."""
    clean_device = device_name.strip() or ups_name.strip()
    payload_enc = secret_box.encrypt(config_response.model_dump_json())
    now = utc_now_iso()

    existing = None
    if host_id is not None:
        existing = await db.fetch_one(
            "SELECT id FROM nut_client_configs WHERE host_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (host_id,),
        )
    if existing is None:
        existing = await db.fetch_one(
            """
            SELECT id FROM nut_client_configs
            WHERE host_id IS ? AND device_name = ? AND ups_name = ? AND platform = ?
            LIMIT 1
            """,
            (host_id, clean_device, ups_name.strip(), platform),
        )

    if existing is not None:
        rec_id = int(existing["id"])
        await db.execute(
            """
            UPDATE nut_client_configs
            SET host_id = ?,
                device_name = ?,
                platform = ?,
                server_host = ?,
                nut_port = ?,
                ups_name = ?,
                username = ?,
                final_delay = ?,
                poll_freq = ?,
                deadtime = ?,
                shutdown_cmd = ?,
                delivery_method = ?,
                deploy_status = ?,
                config_payload_enc = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                host_id,
                clean_device,
                platform,
                server_host.strip(),
                int(nut_port),
                ups_name.strip(),
                username.strip(),
                int(final_delay),
                int(poll_freq),
                int(deadtime),
                shutdown_cmd.strip() or "/sbin/shutdown -h +0",
                delivery_method,
                deploy_status,
                payload_enc,
                now,
                rec_id,
            ),
        )
        return rec_id

    rec_id = await db.execute(
        """
        INSERT INTO nut_client_configs (
            host_id, device_name, platform, server_host, nut_port,
            ups_name, username, final_delay, poll_freq, deadtime,
            shutdown_cmd, delivery_method, deploy_status, config_payload_enc,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            host_id,
            clean_device,
            platform,
            server_host.strip(),
            int(nut_port),
            ups_name.strip(),
            username.strip(),
            int(final_delay),
            int(poll_freq),
            int(deadtime),
            shutdown_cmd.strip() or "/sbin/shutdown -h +0",
            delivery_method,
            deploy_status,
            payload_enc,
            now,
            now,
        ),
    )
    return rec_id


async def list_client_config_records(db: Database) -> list[RecordedClientConfigSummary]:
    rows = await db.fetch_all(
        """
        SELECT id, host_id, device_name, platform, server_host, nut_port,
               ups_name, username, final_delay, poll_freq, deadtime,
               shutdown_cmd, delivery_method, deploy_status, created_at, updated_at
        FROM nut_client_configs
        ORDER BY updated_at DESC, id DESC
        """
    )
    results: list[RecordedClientConfigSummary] = []
    for r in rows:
        plat: NutClientPlatform = (
            r["platform"]
            if r["platform"] in ("linux_debian", "linux_rhel", "truenas", "synology", "windows")
            else "linux_debian"
        )
        results.append(
            RecordedClientConfigSummary(
                id=int(r["id"]),
                host_id=r.get("host_id"),
                device_name=str(r["device_name"]),
                platform=plat,
                server_host=str(r["server_host"]),
                nut_port=int(r["nut_port"]),
                ups_name=str(r["ups_name"]),
                username=str(r["username"]),
                final_delay=int(r["final_delay"]),
                poll_freq=int(r["poll_freq"]),
                deadtime=int(r["deadtime"]),
                shutdown_cmd=str(r["shutdown_cmd"]),
                delivery_method="ssh_deploy" if r.get("delivery_method") == "ssh_deploy" else "manual",
                deploy_status=r["deploy_status"]
                if r.get("deploy_status") in ("generated", "deployed", "failed")
                else "generated",
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
            )
        )
    return results


async def get_client_config_record(
    db: Database, secret_box: SecretBox, record_id: int
) -> RecordedClientConfigDetail | None:
    row = await db.fetch_one("SELECT * FROM nut_client_configs WHERE id = ?", (record_id,))
    if row is None:
        return None
    raw_json = secret_box.decrypt(str(row.get("config_payload_enc") or ""))
    parsed_cfg = NutClientConfigResponse.model_validate(json.loads(raw_json))
    plat: NutClientPlatform = (
        row["platform"]
        if row["platform"] in ("linux_debian", "linux_rhel", "truenas", "synology", "windows")
        else "linux_debian"
    )
    return RecordedClientConfigDetail(
        id=int(row["id"]),
        host_id=row.get("host_id"),
        device_name=str(row["device_name"]),
        platform=plat,
        server_host=str(row["server_host"]),
        nut_port=int(row["nut_port"]),
        ups_name=str(row["ups_name"]),
        username=str(row["username"]),
        final_delay=int(row["final_delay"]),
        poll_freq=int(row["poll_freq"]),
        deadtime=int(row["deadtime"]),
        shutdown_cmd=str(row["shutdown_cmd"]),
        delivery_method="ssh_deploy" if row.get("delivery_method") == "ssh_deploy" else "manual",
        deploy_status=row["deploy_status"]
        if row.get("deploy_status") in ("generated", "deployed", "failed")
        else "generated",
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        config=parsed_cfg,
    )


async def delete_client_config_record(db: Database, record_id: int) -> bool:
    existing = await db.fetch_one("SELECT id FROM nut_client_configs WHERE id = ?", (record_id,))
    if existing is None:
        return False
    await db.execute("DELETE FROM nut_client_configs WHERE id = ?", (record_id,))
    return True
