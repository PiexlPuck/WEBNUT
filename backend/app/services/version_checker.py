"""In-app version checker against GitHub Releases/VERSION with send-once Pushover alerting."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import httpx

from app.database import Database, utc_now_iso
from app.models.schemas import AppVersionStatus
from app.notifications.dispatcher import NotificationDispatcher
from app.services.event_log import EventLogService
from app.services.ws_hub import WebSocketHub

logger = logging.getLogger(__name__)

_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def read_local_version(explicit_path: Path | None = None) -> str:
    """Read local WEBNUT version from /app/VERSION or repository root VERSION."""
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(explicit_path)
    candidates.extend(
        [
            Path("/app/VERSION"),
            Path(__file__).resolve().parents[3] / "VERSION",
            Path.cwd() / "VERSION",
        ]
    )
    for path in candidates:
        try:
            if path.is_file():
                val = path.read_text(encoding="utf-8", errors="replace").strip()
                if val:
                    return val.lstrip("v")
        except OSError:
            continue
    return "1.0.0"


def parse_semver(version_str: str | None) -> tuple[int, int, int] | None:
    if not version_str:
        return None
    m = _SEMVER_RE.search(version_str.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer_version(current: str, candidate: str) -> bool:
    cur_t = parse_semver(current)
    cand_t = parse_semver(candidate)
    if cur_t is None or cand_t is None:
        return False
    return cand_t > cur_t


class VersionChecker:
    """Checks GitHub for newer WEBNUT versions and sends at most ONE Pushover notification."""

    def __init__(
        self,
        db: Database,
        dispatcher: NotificationDispatcher,
        event_log: EventLogService,
        ws_hub: WebSocketHub | None = None,
        github_repo: str = "PiexlPuck/WEBNUT",
        check_interval_sec: int = 21600,
        http_client: httpx.AsyncClient | None = None,
        current_version: str | None = None,
        version_file: Path | None = None,
    ) -> None:
        self.db = db
        self.dispatcher = dispatcher
        self.event_log = event_log
        self.ws_hub = ws_hub
        self.github_repo = github_repo
        self.check_interval_sec = max(300, int(check_interval_sec))
        self.http_client = http_client
        self.current_version = (current_version or read_local_version(version_file)).lstrip("v")
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def get_status(self) -> AppVersionStatus:
        row = await self.db.fetch_one("SELECT * FROM app_update_state WHERE id = 1")
        if row is None:
            return AppVersionStatus(
                current_version=self.current_version,
                github_repo=self.github_repo,
            )
        latest = row["latest_version"]
        update_available = bool(latest and is_newer_version(self.current_version, str(latest)))
        return AppVersionStatus(
            current_version=self.current_version,
            latest_version=latest,
            update_available=update_available,
            release_url=row["release_url"],
            release_notes=row["release_notes"],
            last_checked_at=row["last_checked_at"],
            last_error=row["last_error"],
            notified_version=row["notified_version"],
            notified_at=row["notified_at"],
            github_repo=self.github_repo,
        )

    async def _fetch_with_client(
        self, client: httpx.AsyncClient
    ) -> tuple[str, str, str | None]:
        """Query GitHub Releases and raw VERSION with cache-busting, picking the newest semver."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"WEBNUT/{self.current_version}",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        release_api = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        raw_version_url = (
            f"https://raw.githubusercontent.com/{self.github_repo}/main/VERSION?t={int(time.time())}"
        )
        repo_url = f"https://github.com/{self.github_repo}"

        errors: list[str] = []
        release_candidate: tuple[str, str, str | None] | None = None
        raw_candidate: tuple[str, str, str | None] | None = None

        try:
            resp = await client.get(release_api, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                tag = str(data.get("tag_name") or data.get("name") or "").strip().lstrip("v")
                if parse_semver(tag):
                    release_candidate = (tag, str(data.get("html_url") or repo_url), data.get("body"))
            elif resp.status_code != 404:
                errors.append(f"GitHub API HTTP {resp.status_code}")
        except Exception as exc:
            errors.append(f"GitHub API error: {exc}")

        try:
            raw_resp = await client.get(
                raw_version_url,
                headers={"User-Agent": f"WEBNUT/{self.current_version}", "Cache-Control": "no-cache", "Pragma": "no-cache"},
                timeout=10.0,
            )
            if raw_resp.status_code == 200:
                ver = raw_resp.text.strip().lstrip("v")
                if parse_semver(ver):
                    raw_candidate = (ver, repo_url, None)
                else:
                    errors.append("Remote VERSION file is not valid semver")
            else:
                errors.append(f"Raw VERSION HTTP {raw_resp.status_code}")
        except Exception as exc:
            errors.append(f"Raw VERSION error: {exc}")

        if release_candidate and raw_candidate:
            rel_t = parse_semver(release_candidate[0])
            raw_t = parse_semver(raw_candidate[0])
            if raw_t is not None and rel_t is not None and raw_t > rel_t:
                return raw_candidate
            return release_candidate

        if release_candidate:
            return release_candidate
        if raw_candidate:
            return raw_candidate

        detail = "; ".join(errors) if errors else "No release or VERSION found"
        raise RuntimeError(f"Failed to check GitHub updates ({detail})")

    async def _fetch_remote_release(self) -> tuple[str, str, str | None]:
        """Return (latest_version, release_url, release_notes) from GitHub Releases or VERSION fallback."""
        if self.http_client is not None:
            return await self._fetch_with_client(self.http_client)

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            return await self._fetch_with_client(client)

    async def check_now(self) -> AppVersionStatus:
        """Check GitHub for updates and send at most one notification ever if an update exists."""
        now = utc_now_iso()
        existing = await self.db.fetch_one("SELECT * FROM app_update_state WHERE id = 1")
        notified_version = existing["notified_version"] if existing else None
        notified_at = existing["notified_at"] if existing else None

        try:
            latest, release_url, release_notes = await self._fetch_remote_release()
            update_avail = is_newer_version(self.current_version, latest)
            last_error = None
        except Exception as exc:
            logger.warning("Version check failed: %s", exc)
            latest = existing["latest_version"] if existing else None
            release_url = existing["release_url"] if existing else None
            release_notes = existing["release_notes"] if existing else None
            update_avail = bool(latest and is_newer_version(self.current_version, str(latest)))
            last_error = str(exc)

        await self.db.execute(
            """
            INSERT INTO app_update_state (
                id, current_version, latest_version, release_url, release_notes,
                update_available, last_checked_at, last_error,
                notified_version, notified_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                current_version = excluded.current_version,
                latest_version = excluded.latest_version,
                release_url = excluded.release_url,
                release_notes = excluded.release_notes,
                update_available = excluded.update_available,
                last_checked_at = excluded.last_checked_at,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                self.current_version,
                latest,
                release_url,
                release_notes,
                1 if update_avail else 0,
                now,
                last_error,
                notified_version,
                notified_at,
                now,
            ),
        )

        # Send-once guarantee: only ever dispatch if update is available AND notified_at is unset
        if update_avail and latest and notified_at is None:
            results = await self.dispatcher.dispatch_event(
                "app_update_available",
                f"WEBNUT Update Available: v{latest}",
                f"WEBNUT v{latest} is available (currently running v{self.current_version}). "
                f"Update via 'git pull && docker compose up -d --build'. {release_url or ''}".strip(),
                metadata={"current_version": self.current_version, "latest_version": latest},
            )
            if any(r.success for r in results):
                sent_at = utc_now_iso()
                await self.db.execute(
                    """
                    UPDATE app_update_state
                    SET notified_version = ?, notified_at = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (latest, sent_at, sent_at),
                )
                await self.event_log.record(
                    "system",
                    "info",
                    "app_update_notified",
                    f"Sent one-time update alert for WEBNUT v{latest}",
                    {"current_version": self.current_version, "latest_version": latest},
                )

        status = await self.get_status()
        if self.ws_hub is not None:
            await self.ws_hub.broadcast("update_status", status.model_dump())
        return status

    async def _run_loop(self) -> None:
        # Short startup delay so network/Pushover are ready
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            try:
                await self.check_now()
            except Exception as exc:
                logger.exception("VersionChecker loop failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=float(self.check_interval_sec),
                )
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_loop(), name="version-checker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
