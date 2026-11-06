"""The canonical keys company facts are filed under. The seed set is inserted when the database is prepared."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.company_research.models import FactKey, FactKeySpec, KeyOrigin
from edrak.db.util import dumps, fetch_all, fetch_one, loads


def _shape(row: dict[str, Any]) -> FactKey:
    row["synonyms"] = loads(row.get("synonyms"), {})
    return FactKey.model_validate(row)


async def list_fact_keys(
    db: aiosqlite.Connection, *, category: Optional[str] = None, origin: Optional[KeyOrigin] = None
) -> list[FactKey]:
    where, params = [], []
    if category is not None:
        where.append("category = ?")
        params.append(category)
    if origin is not None:
        where.append("origin = ?")
        params.append(origin.value)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = await fetch_all(db, f"SELECT * FROM fact_keys {clause} ORDER BY category, key", params)
    return [_shape(r) for r in rows]


async def get_fact_key(db: aiosqlite.Connection, category: str, key: str) -> Optional[FactKey]:
    row = await fetch_one(db, "SELECT * FROM fact_keys WHERE category = ? AND key = ?", (category, key))
    return _shape(row) if row else None


async def create_fact_key(
    db: aiosqlite.Connection, spec: FactKeySpec, *, origin: KeyOrigin = KeyOrigin.PROMOTED, commit: bool = True
) -> FactKey:
    """Add a key. Raises sqlite3.IntegrityError when the category already has it."""
    await db.execute(
        """
        INSERT INTO fact_keys (category, key, label_en, label_ar, synonyms, value_type, origin)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (spec.category, spec.key, spec.label_en, spec.label_ar, dumps(spec.synonyms.model_dump()),
         spec.value_type.value, origin.value),
    )
    if commit:
        await db.commit()
    created = await get_fact_key(db, spec.category, spec.key)
    assert created is not None
    return created
