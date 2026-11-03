"""Researched facts about a company, filed by category and key. Every query is scoped to one workspace."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Sequence, Union

import aiosqlite

from edrak.company_research.models import CompanyFact, FactStatus, NewFact, ValueType, check_value
from edrak.db.util import fetch_all, fetch_one, new_id, now_iso

Filter = Union[str, Enum, Sequence[Union[str, Enum]]]


def _shape(row: dict[str, Any]) -> CompanyFact:
    row["edited_by_user"] = bool(row["edited_by_user"])
    return CompanyFact.model_validate(row)


def _values(value: Filter) -> list[str]:
    items = [value] if isinstance(value, (str, Enum)) else list(value)
    return [i.value if isinstance(i, Enum) else i for i in items]


async def create_fact(db: aiosqlite.Connection, fact: NewFact, *, fact_id: Optional[str] = None, commit: bool = True) -> CompanyFact:
    fact_id = fact_id or new_id()
    await db.execute(
        """
        INSERT INTO company_facts (id, workspace_id, category, proposed_category, key, value, value_type, source_tier,
                                   source_url, pdf_page, quote, observed_at, confidence, status, conflict_group,
                                   run_id, edited_by_user)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (fact_id, fact.workspace_id, fact.category, fact.proposed_category, fact.key, fact.value,
         fact.value_type.value, fact.source_tier.value, fact.source_url, fact.pdf_page, fact.quote,
         fact.observed_at or now_iso(), fact.confidence.value, fact.status.value, fact.conflict_group,
         fact.run_id, 1 if fact.edited_by_user else 0),
    )
    if commit:
        await db.commit()
    created = await get_fact(db, fact.workspace_id, fact_id)
    assert created is not None
    return created


async def get_fact(db: aiosqlite.Connection, workspace_id: str, fact_id: str) -> Optional[CompanyFact]:
    row = await fetch_one(db, "SELECT * FROM company_facts WHERE id = ? AND workspace_id = ?", (fact_id, workspace_id))
    return _shape(row) if row else None


async def list_facts(
    db: aiosqlite.Connection,
    workspace_id: str,
    *,
    category: Optional[Filter] = None,
    status: Optional[Filter] = None,
    source_tier: Optional[Filter] = None,
    run_id: Optional[str] = None,
    conflict_group: Optional[str] = None,
) -> list[CompanyFact]:
    """Facts of one workspace, by category then key. Each filter takes one value or several."""
    where, params = ["workspace_id = ?"], [workspace_id]
    for column, wanted in (("category", category), ("status", status), ("source_tier", source_tier)):
        if wanted is None:
            continue
        values = _values(wanted)
        where.append(f"{column} IN ({', '.join('?' * len(values))})" if values else "0")
        params.extend(values)
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if conflict_group is not None:
        where.append("conflict_group = ?")
        params.append(conflict_group)
    rows = await fetch_all(
        db, f"SELECT * FROM company_facts WHERE {' AND '.join(where)} ORDER BY category, key, created_at, id", params
    )
    return [_shape(r) for r in rows]


async def update_status(
    db: aiosqlite.Connection, workspace_id: str, fact_id: str, status: FactStatus, *, commit: bool = True
) -> Optional[CompanyFact]:
    """Approve, reject or return a fact to suggested. None when the workspace has no such fact."""
    cursor = await db.execute(
        "UPDATE company_facts SET status = ? WHERE id = ? AND workspace_id = ?",
        (FactStatus(status).value, fact_id, workspace_id),
    )
    if commit:
        await db.commit()
    return await get_fact(db, workspace_id, fact_id) if cursor.rowcount else None


async def edit_value(
    db: aiosqlite.Connection,
    workspace_id: str,
    fact_id: str,
    value: str,
    *,
    value_type: Optional[ValueType] = None,
    commit: bool = True,
) -> Optional[CompanyFact]:
    """Replace a fact's value by hand and mark it as edited. The supporting quote stays as it was found.

    Raises ValueError for a blank value or a list that is not a JSON array of strings.
    """
    current = await get_fact(db, workspace_id, fact_id)
    if current is None:
        return None
    kind = ValueType(value_type) if value_type is not None else current.value_type
    check_value(value, kind)
    await db.execute(
        "UPDATE company_facts SET value = ?, value_type = ?, edited_by_user = 1 WHERE id = ? AND workspace_id = ?",
        (value, kind.value, fact_id, workspace_id),
    )
    if commit:
        await db.commit()
    return await get_fact(db, workspace_id, fact_id)


async def set_conflict_group(
    db: aiosqlite.Connection,
    workspace_id: str,
    fact_ids: Sequence[str],
    conflict_group: Optional[str],
    *,
    commit: bool = True,
) -> int:
    """Put facts in one conflict group, or take them out with None. Returns how many facts changed."""
    if not fact_ids:
        return 0
    marks = ", ".join("?" * len(fact_ids))
    cursor = await db.execute(
        f"UPDATE company_facts SET conflict_group = ? WHERE workspace_id = ? AND id IN ({marks})",
        [conflict_group, workspace_id, *fact_ids],
    )
    if commit:
        await db.commit()
    return cursor.rowcount


async def delete_suggested(db: aiosqlite.Connection, workspace_id: str, fact_ids: Sequence[str], *, commit: bool = True) -> int:
    """Delete facts that are still suggestions nobody edited. Approved, rejected and edited facts are never deleted.

    Returns how many were deleted.
    """
    if not fact_ids:
        return 0
    marks = ", ".join("?" * len(fact_ids))
    cursor = await db.execute(
        f"DELETE FROM company_facts WHERE workspace_id = ? AND status = 'suggested' AND edited_by_user = 0 AND id IN ({marks})",
        [workspace_id, *fact_ids],
    )
    if commit:
        await db.commit()
    return cursor.rowcount
