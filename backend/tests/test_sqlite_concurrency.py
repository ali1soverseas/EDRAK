import asyncio
import sqlite3
from contextlib import asynccontextmanager

from edrak.api import run_manager as run_manager_module
from edrak.api.run_manager import RunManager
from edrak.core.config import settings
from edrak.db import connection


def test_existing_database_is_reopened_in_wal_mode_with_busy_timeout(tmp_path, monkeypatch):
    db_path = tmp_path / "edrak.db"
    monkeypatch.setattr(settings, "EDRAK_DB_PATH", db_path)
    connection.reset_ready_cache()

    asyncio.run(connection.init_db())
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA journal_mode = DELETE")

    connection.reset_ready_cache()
    asyncio.run(connection.init_db())

    async def get_pragmas():
        async with connection.get_db() as db:
            journal_mode = (await (await db.execute("PRAGMA journal_mode")).fetchone())[0]
            busy_timeout = (await (await db.execute("PRAGMA busy_timeout")).fetchone())[0]
            return journal_mode, busy_timeout

    journal_mode, busy_timeout = asyncio.run(get_pragmas())
    assert journal_mode.lower() == "wal"
    assert busy_timeout == connection.SQLITE_BUSY_TIMEOUT_MS


def test_run_manager_serializes_its_database_write_transactions(monkeypatch):
    @asynccontextmanager
    async def fake_get_db():
        yield object()

    monkeypatch.setattr(run_manager_module, "get_db", fake_get_db)
    manager = RunManager()
    order = []

    async def write():
        async with manager._write_db():
            order.append("start")
            await asyncio.sleep(0)
            order.append("finish")

    async def run_writes():
        await asyncio.gather(write(), write())

    asyncio.run(run_writes())

    assert order == ["start", "finish", "start", "finish"]
