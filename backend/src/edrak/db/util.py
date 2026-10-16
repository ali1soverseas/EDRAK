"""Small helpers shared by the repository modules."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import aiosqlite


def now_iso(delta: Optional[timedelta] = None) -> str:
    """UTC timestamp in the schema's text format, e.g. 2026-10-11T09:30:00.123Z."""
    moment = datetime.now(timezone.utc) + (delta or timedelta())
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def to_iso(value: Any) -> Optional[str]:
    """A datetime or an ISO string from a contract, in the schema's text format."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return str(value)


def new_id() -> str:
    """32 lowercase hex characters, the schema's default id shape."""
    return secrets.token_hex(16)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: Optional[str], default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_dict(row: Optional[aiosqlite.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


async def fetch_one(db: aiosqlite.Connection, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    cursor = await db.execute(sql, tuple(params))
    return row_dict(await cursor.fetchone())


async def fetch_all(db: aiosqlite.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    cursor = await db.execute(sql, tuple(params))
    return [dict(r) for r in await cursor.fetchall()]


async def fetch_value(db: aiosqlite.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    cursor = await db.execute(sql, tuple(params))
    row = await cursor.fetchone()
    return row[0] if row is not None else None


def slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "workspace"
    return f"{base}-{secrets.token_hex(3)}"
