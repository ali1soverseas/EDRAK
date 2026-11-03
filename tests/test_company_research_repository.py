import asyncio
import json
import sqlite3

import pytest
from company_research_fixtures import new_workspace
from pydantic import ValidationError

from edrak.company_research.models import (
    CompanyFact,
    Confidence,
    FactKeySpec,
    FactStatus,
    KeyOrigin,
    NewFact,
    ResearchRunStatus,
    SourceTier,
    Synonyms,
    ValueType,
)
from edrak.company_research.seed_keys import SEED_KEYS
from edrak.db.connection import get_db
from edrak.db.repositories import company_facts as facts_repo
from edrak.db.repositories import fact_keys as keys_repo
from edrak.db.repositories import research_runs as runs_repo


def run(coro_fn):
    """Run `coro_fn(db)` against the isolated database."""

    async def go():
        async with get_db() as db:
            return await coro_fn(db)

    return asyncio.run(go())


def fact(workspace_id, **overrides) -> NewFact:
    values = dict(
        workspace_id=workspace_id, category="leadership", key="ceo", value="Amal Nasr", source_tier=SourceTier.OWN_SITE,
        source_url="https://acme.example/about", quote="Amal Nasr, Chief Executive Officer", confidence=Confidence.HIGH,
    )
    values.update(overrides)
    return NewFact(**values)


# ------------------------------------------------------------------ the fact model


def test_a_fact_needs_a_quote_a_value_and_a_web_source():
    base = dict(workspace_id="w", category="leadership", key="ceo", value="x", source_tier="own_site",
                source_url="https://acme.example", quote="x", confidence="high")
    NewFact(**base)
    for bad in ({"quote": "  "}, {"value": ""}, {"source_url": "acme.example"}, {"pdf_page": 0}, {"key": "CEO"},
                {"category": "nonsense"}, {"confidence": "sure"}, {"source_tier": "rumour"}):
        with pytest.raises(ValidationError):
            NewFact(**{**base, **bad})


def test_other_needs_the_proposed_category_and_the_others_refuse_one():
    with pytest.raises(ValidationError):
        fact("w", category="other", key="carbon_target")
    with pytest.raises(ValidationError):
        fact("w", proposed_category="Sustainability")
    kept = fact("w", category="other", key="carbon_target", proposed_category="  Sustainability   goals ")
    assert kept.proposed_category == "Sustainability goals"


def test_a_list_value_is_a_json_array_of_strings():
    fact("w", key="country_presence", category="locations", value_type=ValueType.LIST, value='["Egypt", "KSA"]')
    for bad in ("Egypt, KSA", '{"a": 1}', "[1, 2]"):
        with pytest.raises(ValidationError):
            fact("w", key="country_presence", category="locations", value_type=ValueType.LIST, value=bad)


# ------------------------------------------------------------------ facts


def test_create_and_read_back_a_fact(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        created = await facts_repo.create_fact(
            db, fact(ws, pdf_page=7, source_tier=SourceTier.OWN_PDF, source_url="https://cdn.acme.example/report.pdf")
        )
        return ws, created, await facts_repo.get_fact(db, ws, created.id)

    ws, created, loaded = run(go)
    assert isinstance(created, CompanyFact) and loaded == created
    assert len(created.id) == 32 and created.workspace_id == ws
    assert (created.category, created.key, created.value) == ("leadership", "ceo", "Amal Nasr")
    assert created.source_tier == SourceTier.OWN_PDF and created.pdf_page == 7
    assert created.quote == "Amal Nasr, Chief Executive Officer"
    assert created.status == FactStatus.SUGGESTED and created.edited_by_user is False
    assert created.conflict_group is None and created.run_id is None and created.proposed_category is None
    assert created.observed_at.endswith("Z") and created.created_at.endswith("Z") and created.updated_at


def test_an_other_fact_keeps_the_category_the_model_proposed(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        return await facts_repo.create_fact(
            db, fact(ws, category="other", proposed_category="Sustainability", key="carbon_target", value="Net zero by 2040")
        )

    created = run(go)
    assert (created.category, created.proposed_category) == ("other", "Sustainability")


def test_a_list_fact_round_trips(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        return await facts_repo.create_fact(
            db, fact(ws, category="locations", key="country_presence", value_type=ValueType.LIST, value=json.dumps(["Egypt", "مصر"], ensure_ascii=False))
        )

    created = run(go)
    assert created.value_type == ValueType.LIST and json.loads(created.value) == ["Egypt", "مصر"]


def test_listing_filters_by_category_status_and_tier(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        other_ws = await new_workspace(db, "Other Co")
        ceo = await facts_repo.create_fact(db, fact(ws))
        founder = await facts_repo.create_fact(db, fact(ws, key="founder", value="Omar Nasr", source_tier=SourceTier.OWN_PDF, source_url="https://acme.example/r.pdf", pdf_page=2))
        hq = await facts_repo.create_fact(db, fact(ws, category="locations", key="headquarters", value="Cairo", source_tier=SourceTier.OWN_SOCIAL, source_url="https://facebook.com/acme"))
        await facts_repo.create_fact(db, fact(other_ws, value="Someone Else"))
        await facts_repo.update_status(db, ws, founder.id, FactStatus.APPROVED)
        await facts_repo.update_status(db, ws, hq.id, FactStatus.REJECTED)

        everything = await facts_repo.list_facts(db, ws)
        return {
            "all": [f.key for f in everything],
            "leadership": [f.key for f in await facts_repo.list_facts(db, ws, category="leadership")],
            "two_categories": [f.key for f in await facts_repo.list_facts(db, ws, category=["locations", "leadership"])],
            "approved": [f.key for f in await facts_repo.list_facts(db, ws, status=FactStatus.APPROVED)],
            "not_rejected": [f.key for f in await facts_repo.list_facts(db, ws, status=["suggested", "approved"])],
            "pdf": [f.key for f in await facts_repo.list_facts(db, ws, source_tier=SourceTier.OWN_PDF)],
            "combined": [f.key for f in await facts_repo.list_facts(db, ws, category="leadership", status="suggested")],
            "none": await facts_repo.list_facts(db, ws, category=[]),
            "no_match": await facts_repo.list_facts(db, ws, source_tier=SourceTier.THIRD_PARTY),
            "other_workspace": [f.value for f in await facts_repo.list_facts(db, other_ws)],
            "ceo": ceo,
        }

    out = run(go)
    assert out["all"] == ["ceo", "founder", "headquarters"]  # by category (leadership, locations), then key
    assert out["leadership"] == ["ceo", "founder"]
    assert out["two_categories"] == ["ceo", "founder", "headquarters"]
    assert out["approved"] == ["founder"]
    assert out["not_rejected"] == ["ceo", "founder"]
    assert out["pdf"] == ["founder"]
    assert out["combined"] == ["ceo"]
    assert out["none"] == [] and out["no_match"] == []
    assert out["other_workspace"] == ["Someone Else"]


def test_update_status_moves_a_fact_and_stays_inside_its_workspace(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        other_ws = await new_workspace(db, "Other Co")
        created = await facts_repo.create_fact(db, fact(ws))
        approved = await facts_repo.update_status(db, ws, created.id, FactStatus.APPROVED)
        wrong_workspace = await facts_repo.update_status(db, other_ws, created.id, FactStatus.REJECTED)
        missing = await facts_repo.update_status(db, ws, "nope", FactStatus.REJECTED)
        still = await facts_repo.get_fact(db, ws, created.id)
        return approved, wrong_workspace, missing, still, await facts_repo.get_fact(db, other_ws, created.id)

    approved, wrong_workspace, missing, still, seen_by_other = run(go)
    assert approved.status == FactStatus.APPROVED
    assert wrong_workspace is None and missing is None and seen_by_other is None
    assert still.status == FactStatus.APPROVED


def test_edit_value_marks_the_fact_edited_and_keeps_its_quote(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        created = await facts_repo.create_fact(db, fact(ws, category="team", key="employee_count", value="200", value_type=ValueType.NUMBER))
        edited = await facts_repo.edit_value(db, ws, created.id, "250")
        retyped = await facts_repo.edit_value(db, ws, created.id, '["a", "b"]', value_type=ValueType.LIST)
        errors = []
        for value, kind in (("  ", None), ("a, b", ValueType.LIST)):
            with pytest.raises(ValueError) as err:
                await facts_repo.edit_value(db, ws, created.id, value, value_type=kind)
            errors.append(str(err.value))
        missing = await facts_repo.edit_value(db, ws, "nope", "1")
        return created, edited, retyped, errors, missing, await facts_repo.get_fact(db, ws, created.id)

    created, edited, retyped, errors, missing, after = run(go)
    assert created.edited_by_user is False
    assert edited.value == "250" and edited.edited_by_user is True and edited.quote == created.quote
    assert retyped.value_type == ValueType.LIST
    assert len(errors) == 2 and missing is None
    assert after.value == '["a", "b"]'  # the refused edits changed nothing


def test_set_conflict_group_groups_and_ungroups(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        other_ws = await new_workspace(db, "Other Co")
        a = await facts_repo.create_fact(db, fact(ws, category="history", key="founded_year", value="2004", value_type=ValueType.NUMBER))
        b = await facts_repo.create_fact(db, fact(ws, category="history", key="founded_year", value="2006", value_type=ValueType.NUMBER))
        c = await facts_repo.create_fact(db, fact(ws))
        foreign = await facts_repo.create_fact(db, fact(other_ws))
        changed = await facts_repo.set_conflict_group(db, ws, [a.id, b.id, foreign.id], "grp-1")
        grouped = [f.id for f in await facts_repo.list_facts(db, ws, conflict_group="grp-1")]
        cleared = await facts_repo.set_conflict_group(db, ws, [b.id], None)
        return (changed, sorted(grouped) == sorted([a.id, b.id]), cleared, await facts_repo.set_conflict_group(db, ws, [], "x"),
                (await facts_repo.get_fact(db, ws, b.id)).conflict_group, (await facts_repo.get_fact(db, ws, c.id)).conflict_group,
                (await facts_repo.get_fact(db, other_ws, foreign.id)).conflict_group)

    changed, grouped, cleared, empty, b_group, c_group, foreign_group = run(go)
    assert changed == 2 and grouped and cleared == 1 and empty == 0
    assert b_group is None and c_group is None and foreign_group is None


def test_a_fact_can_be_attached_to_a_run_and_survives_its_deletion(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        r = await runs_repo.create_research_run(db, workspace_id=ws, input={"url": "https://acme.example"})
        created = await facts_repo.create_fact(db, fact(ws, run_id=r.id))
        by_run = await facts_repo.list_facts(db, ws, run_id=r.id)
        await db.execute("DELETE FROM research_runs WHERE id = ?", (r.id,))
        await db.commit()
        return created, by_run, await facts_repo.get_fact(db, ws, created.id)

    created, by_run, after = run(go)
    assert [f.id for f in by_run] == [created.id]
    assert after.run_id is None


# ------------------------------------------------------------------ runs


def test_a_run_goes_from_queued_to_running_to_done(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        queued = await runs_repo.create_research_run(db, workspace_id=ws, input={"url": "https://acme.example", "third_party": False})
        await runs_repo.update_research_run(db, queued.id, status="running")
        running = await runs_repo.get_research_run(db, ws, queued.id)
        await runs_repo.update_research_run(db, queued.id, pages_crawled=6, pdfs_read=1, facts_found=23, warnings=["robots.txt blocked /admin"])
        await runs_repo.update_research_run(db, queued.id, status=ResearchRunStatus.RUNNING)  # started_at is kept
        progressed = await runs_repo.get_research_run(db, ws, queued.id)
        await runs_repo.finish_research_run(db, queued.id, status=ResearchRunStatus.DONE)
        done = await runs_repo.get_research_run(db, ws, queued.id)
        return queued, running, progressed, done

    queued, running, progressed, done = run(go)
    assert (queued.status, queued.started_at, queued.finished_at) == (ResearchRunStatus.QUEUED, None, None)
    assert queued.input == {"url": "https://acme.example", "third_party": False}
    assert (queued.pages_crawled, queued.pdfs_read, queued.facts_found, queued.warnings, queued.error) == (0, 0, 0, [], None)
    assert running.status == ResearchRunStatus.RUNNING and running.started_at
    assert (progressed.pages_crawled, progressed.pdfs_read, progressed.facts_found) == (6, 1, 23)
    assert progressed.warnings == ["robots.txt blocked /admin"] and progressed.started_at == running.started_at
    assert done.status == ResearchRunStatus.DONE and done.finished_at and done.error is None


def test_a_run_can_fail_or_be_cancelled(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        failing = await runs_repo.create_research_run(db, workspace_id=ws)
        await runs_repo.update_research_run(db, failing.id, status="running")
        await runs_repo.finish_research_run(db, failing.id, status=ResearchRunStatus.FAILED, error="site unreachable", warnings=["one", "two"])
        cancelled = await runs_repo.create_research_run(db, workspace_id=ws)
        await runs_repo.finish_research_run(db, cancelled.id, status="cancelled")
        return await runs_repo.get_research_run(db, ws, failing.id), await runs_repo.get_research_run(db, ws, cancelled.id)

    failed, cancelled = run(go)
    assert failed.status == ResearchRunStatus.FAILED and failed.error == "site unreachable" and failed.warnings == ["one", "two"]
    assert cancelled.status == ResearchRunStatus.CANCELLED and cancelled.started_at is None and cancelled.finished_at


def test_run_updates_refuse_what_they_should(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        r = await runs_repo.create_research_run(db, workspace_id=ws)
        for call in (
            runs_repo.update_research_run(db, r.id, finished_at="x"),
            runs_repo.update_research_run(db, r.id, status="done"),
            runs_repo.update_research_run(db, r.id, status="paused"),
            runs_repo.finish_research_run(db, r.id, status=ResearchRunStatus.RUNNING),
        ):
            with pytest.raises(ValueError):
                await call
        return await runs_repo.get_research_run(db, ws, r.id)

    assert run(go).status == ResearchRunStatus.QUEUED


def test_listing_runs_is_newest_first_and_scoped(research_db_path):
    async def go(db):
        ws = await new_workspace(db)
        other_ws = await new_workspace(db, "Other Co")
        first = await runs_repo.create_research_run(db, workspace_id=ws)
        await db.execute("UPDATE research_runs SET created_at = '2020-01-01T00:00:00.000Z' WHERE id = ?", (first.id,))
        second = await runs_repo.create_research_run(db, workspace_id=ws)
        await runs_repo.create_research_run(db, workspace_id=other_ws)
        await runs_repo.finish_research_run(db, first.id, status=ResearchRunStatus.DONE)
        return (first, second,
                await runs_repo.list_research_runs(db, ws),
                await runs_repo.list_research_runs(db, ws, status=ResearchRunStatus.DONE),
                await runs_repo.list_research_runs(db, ws, limit=1),
                await runs_repo.get_research_run(db, other_ws, first.id))

    first, second, listed, done, limited, wrong_workspace = run(go)
    assert [r.id for r in listed] == [second.id, first.id]
    assert [r.id for r in done] == [first.id]
    assert [r.id for r in limited] == [second.id]
    assert wrong_workspace is None


# ------------------------------------------------------------------ keys


def test_the_seed_keys_can_be_listed_and_read(research_db_path):
    async def go(db):
        return (await keys_repo.list_fact_keys(db),
                await keys_repo.list_fact_keys(db, category="leadership"),
                await keys_repo.list_fact_keys(db, origin=KeyOrigin.PROMOTED),
                await keys_repo.get_fact_key(db, "leadership", "ceo"),
                await keys_repo.get_fact_key(db, "leadership", "nope"))

    every, leadership, promoted, ceo, missing = run(go)
    assert len(every) == len(SEED_KEYS)
    assert {k.key for k in leadership} >= {"ceo", "founder", "chairman", "board_member"}
    assert promoted == [] and missing is None
    assert ceo.origin == KeyOrigin.SEED and ceo.label_ar == "الرئيس التنفيذي" and ceo.value_type == ValueType.TEXT
    assert "ceo" in ceo.synonyms.en and "المدير التنفيذي" in ceo.synonyms.ar
    assert {k.key: k.value_type for k in every}["employee_count"] == ValueType.NUMBER


def test_a_key_can_be_promoted_and_then_matched(research_db_path):
    from edrak.company_research.keys import normalize_key

    spec = FactKeySpec(category="other", key="carbon_target", label_en="Carbon target", label_ar="هدف الكربون",
                       synonyms=Synonyms(en=["net zero target"], ar=["هدف الحياد الكربوني"]))

    async def go(db):
        promoted = await keys_repo.create_fact_key(db, spec)
        with pytest.raises(sqlite3.IntegrityError):
            await keys_repo.create_fact_key(db, spec)
        await db.rollback()
        return promoted, await keys_repo.list_fact_keys(db)

    promoted, every = run(go)
    assert promoted.origin == KeyOrigin.PROMOTED and promoted.created_at.endswith("Z") and len(promoted.id) == 32
    assert normalize_key("other", "Net Zero Target", every) == "carbon_target"
    assert normalize_key("other", "هدف الحياد الكربوني", every) == "carbon_target"
    assert normalize_key("leadership", "ceo", every) == "ceo"
