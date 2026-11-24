"""Pytest configuration and shared async fixtures."""

from __future__ import annotations

import sys
from pathlib import Path
import pytest_asyncio

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import Database  # noqa: E402
from app.services.event_log import EventLogService  # noqa: E402


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_app.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def event_log(db: Database):
    return EventLogService(db)
