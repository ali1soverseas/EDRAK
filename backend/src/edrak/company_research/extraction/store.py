"""Store an extraction result: new suggestions, replaced suggestions, and conflict groups, in one transaction."""

from __future__ import annotations

import aiosqlite

from edrak.company_research.extraction.models import ExtractionResult
from edrak.company_research.models import CompanyFact
from edrak.db.repositories import company_facts


async def apply_extraction(db: aiosqlite.Connection, workspace_id: str, result: ExtractionResult) -> list[CompanyFact]:
    """Save `result` and return the facts created.

    A stored fact is changed in two ways only: a suggestion nobody edited can be deleted because a
    better source replaces it, and any fact can join a conflict group, which changes its group and
    nothing else. Approved and edited facts keep their value, status and source.
    """
    try:
        await company_facts.delete_suggested(db, workspace_id, result.supersede, commit=False)
        created = [await company_facts.create_fact(db, fact, commit=False) for fact in result.facts]
        members: dict[str, list[str]] = {}
        for fact_id, group in result.conflict_groups.items():
            members.setdefault(group, []).append(fact_id)
        for group, ids in members.items():
            await company_facts.set_conflict_group(db, workspace_id, ids, group, commit=False)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return created
