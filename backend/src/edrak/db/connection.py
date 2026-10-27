"""Async SQLite connection and schema setup.

The schema lives in schema.sql (v0.2). On first use a database file is created from it. A file
in the old v1 layout (an `analyses` table) is migrated: copied to edrak.v1.bak.db, then rebuilt
in the v0.2 layout with its data carried over (see migrate_v1.py).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from edrak.core.config import settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_V3_PATH = Path(__file__).with_name("schema_v3.sql")
SCHEMA_VERSION = 2

_ready: set[str] = set()
# A thread lock, not an asyncio one: tests run several event loops in one process.
_init_lock = threading.Lock()


def get_db_path() -> Path:
    path = settings.get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _prepare_file(path: Path) -> None:
    """Create or migrate the database file so it holds the v0.2 schema. Blocking; runs once per path."""
    conn = sqlite3.connect(path)
    try:
        tables = _tables(conn)
    finally:
        conn.close()

    if "analyses" in tables and "business_requests" not in tables:
        from edrak.db.migrate_v1 import migrate_v1_file

        migrate_v1_file(path)
        _apply_additions(path)
        return

    if "business_requests" not in tables:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(schema_sql())
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.commit()
        finally:
            conn.close()
        logger.info("Created database %s (schema v%d)", path, SCHEMA_VERSION)
    _apply_additions(path)


def _apply_additions(path: Path) -> None:
    """Tables added after v0.2 (brief v2). Idempotent, so it runs on every start."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        dcols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
        if dcols and "action_plan_id" not in dcols:
            conn.execute("ALTER TABLE decisions ADD COLUMN action_plan_id text REFERENCES action_plans(id)")
        if "challenge_id" not in cols:
            conn.execute("ALTER TABLE metrics ADD COLUMN challenge_id text")
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(chat_threads)")}
        if tcols and "card_key" not in tcols:
            conn.execute("ALTER TABLE chat_threads ADD COLUMN card_key text")
        if "label" not in cols:  # databases created by an earlier build of this file
            conn.execute("ALTER TABLE metrics ADD COLUMN label text")
        ecols = {r[1] for r in conn.execute("PRAGMA table_info(evidence)")}
        if ecols and "page_text" not in ecols:
            conn.execute("ALTER TABLE evidence ADD COLUMN page_text text")
        conn.commit()
    finally:
        conn.close()


async def init_db() -> None:
    """Make sure the database file exists in the current layout."""
    path = get_db_path()
    key = str(path)
    if key in _ready:
        return
    def prepare() -> None:
        with _init_lock:
            if key not in _ready:
                _prepare_file(path)
                _ready.add(key)

    await asyncio.to_thread(prepare)


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """One connection, with the pragmas every connection needs."""
    await init_db()
    db = await aiosqlite.connect(str(get_db_path()))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA busy_timeout = 5000;")
    try:
        yield db
    finally:
        await db.close()


def reset_ready_cache() -> None:
    """Forget which files were prepared. Tests call this after pointing EDRAK_DB_PATH elsewhere."""
    _ready.clear()