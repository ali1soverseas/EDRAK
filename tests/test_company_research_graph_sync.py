"""Sharing approved company facts with the knowledge graph. A real temporary database, a fake graph."""

import asyncio
import json
import sqlite3

import pytest
from company_research_fixtures import new_workspace

from edrak.company_research import graph_sync
from edrak.company_research.models import Confidence, FactStatus, NewFact, SourceTier
from edrak.core.config import settings
from edrak.db.connection import get_db
from edrak.db.repositories import company_fact_graph_sync as sync_repo
from edrak.db.repositories import company_facts as facts_repo
from edrak.graph.ingestion.company_facts import fact_node_id


class FakeGraph:
    """Stands in for GraphConnection: records writes, and can be switched off or made to fail."""

    def __init__(self, available=True, fail_on=None):
        self.is_available = available
        self.fail_on = fail_on  # a substring of the query that should raise
        self.calls = []
        self.schema_applied = 0
        self.available_checks = 0

    def available(self):
        self.available_checks += 1
        return self.is_available

    def apply_schema(self):
        self.schema_applied += 1

    def write(self, query, **params):
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("neo4j said no")
        self.calls.append((query, params))

    def write_batches(self, query, rows, **params):
        self.write(query, rows=list(rows), **params)

    # what the writes amount to
    def published(self):
        return [r["props"]["fact_id"] for q, p in self.calls if "MERGE (f:CompanyFact" in q for r in p["rows"]]

    def retracted(self):
        return [i for q, p in self.calls if "DETACH DELETE f" in q for i in p["node_ids"]]


@pytest.fixture
def sync_on(research_db_path, monkeypatch):
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_GRAPH_SYNC", True)
    monkeypatch.setattr("edrak.graph.connection.effective_graph_workspace_id", lambda ws: ws)
    graph_sync._schema_ready.clear()


def run(coro_fn):
    async def go():
        async with get_db() as db:
            return await coro_fn(db)

    return asyncio.run(go())


def rows(sql, *params):
    from edrak.core.config import settings as s

    conn = sqlite3.connect(s.EDRAK_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


async def company(db, name="Acme Industries"):
    ws = await new_workspace(db, name)
    from edrak.db.repositories import company as company_repo

    await company_repo.get_company(db, ws)  # creates the profile row
    await db.execute("UPDATE company_profiles SET name = ? WHERE workspace_id = ?", (name, ws))
    await db.commit()
    return ws


async def change(db, sql, *params):
    """One statement, committed on the same connection."""
    await db.execute(sql, params)
    await db.commit()


async def add(db, ws, key="ceo", value="Amal Nasr", category="leadership", status=FactStatus.APPROVED, **extra):
    fact = NewFact(
        workspace_id=ws, category=category, key=key, value=value, source_tier=SourceTier.OWN_SITE,
        source_url="https://acme.example/about", quote=f"{value} is named on the site", confidence=Confidence.HIGH,
        status=status, **extra,
    )
    return (await facts_repo.create_fact(db, fact)).id


def sync(ws, graph):
    return run(lambda db: graph_sync.sync_workspace(db, ws, conn=graph))


# ---------------------------------------------------------------------------- off by default


def test_the_sync_is_off_unless_switched_on(research_db_path, monkeypatch):
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_GRAPH_SYNC", False)
    graph = FakeGraph()

    async def go(db):
        ws = await company(db)
        await add(db, ws)
        return ws

    ws = run(go)
    result = sync(ws, graph)
    assert "off" in result.skipped_reason and result.published == 0
    assert graph.calls == [] and graph.available_checks == 0  # not even contacted
    assert settings.__class__.model_fields["COMPANY_RESEARCH_GRAPH_SYNC"].default is False


# ---------------------------------------------------------------------------- publishing


def test_only_approved_facts_are_published_and_remembered(sync_on):
    graph = FakeGraph()

    async def go(db):
        ws = await company(db)
        approved = await add(db, ws, "ceo", "Amal Nasr")
        await add(db, ws, "cfo", "Mona Said", status=FactStatus.SUGGESTED)
        await add(db, ws, "cto", "Omar Adel", status=FactStatus.REJECTED)
        return ws, approved

    ws, approved = run(go)
    result = sync(ws, graph)

    assert (result.published, result.retracted, result.failed, result.skipped_reason) == (1, 0, 0, None)
    assert graph.published() == [approved]
    assert graph.schema_applied == 1
    company_call = next(p for q, p in graph.calls if "MERGE (c:Company" in q and "CompanyFact" not in q)
    assert company_call["company_name"] == "Acme Industries" and company_call["company_id"] == "acme-industries"
    assert set(run(lambda db: sync_repo.list_synced(db, ws))) == {approved}


def test_a_second_sync_with_nothing_changed_does_not_touch_the_graph(sync_on):
    graph = FakeGraph()
    ws = run(lambda db: _seed_one(db))
    sync(ws, graph)
    graph.calls.clear()
    graph.available_checks = 0

    result = sync(ws, graph)
    assert result.as_dict()["published"] == 0 and result.skipped_reason is None
    assert graph.calls == [] and graph.available_checks == 0


async def _seed_one(db):
    ws = await company(db)
    await add(db, ws)
    return ws


def test_an_edit_to_an_approved_fact_is_published_again(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws, "ceo", "Amal Nasr")

    ws, fact_id = run(seed)
    sync(ws, graph)
    graph.calls.clear()

    run(lambda db: facts_repo.edit_value(db, ws, fact_id, "Amal Nasr-Hassan"))
    result = sync(ws, graph)

    assert result.published == 1 and graph.published() == [fact_id]
    row = next(p["rows"][0] for q, p in graph.calls if "MERGE (f:CompanyFact" in q)
    assert row["props"]["value"] == "Amal Nasr-Hassan" and row["props"]["edited_by_user"] is True


def test_a_renamed_company_republishes_its_facts(sync_on):
    graph = FakeGraph()
    ws = run(_seed_one)
    sync(ws, graph)
    graph.calls.clear()

    run(lambda db: change(db, "UPDATE company_profiles SET name = 'Acme Group' WHERE workspace_id = ?", ws))
    result = sync(ws, graph)

    assert result.published == 1
    assert next(p for q, p in graph.calls if "MERGE (c:Company" in q and "CompanyFact" not in q)["company_id"] == "acme-group"


def test_the_graph_workspace_override_is_what_the_graph_sees(sync_on, monkeypatch):
    monkeypatch.setattr("edrak.graph.connection.effective_graph_workspace_id", lambda ws: "sandbox")
    graph = FakeGraph()
    ws = run(_seed_one)
    sync(ws, graph)
    fact_ids = graph.published()
    assert all(p["ws"] == "sandbox" for q, p in graph.calls if "MERGE (c:Company" in q and "CompanyFact" not in q)
    assert fact_node_id("sandbox", fact_ids[0]) in [r["id"] for q, p in graph.calls if "MERGE (f:CompanyFact" in q for r in p["rows"]]


def test_a_company_without_a_name_is_skipped(sync_on):
    graph = FakeGraph()

    async def go(db):
        ws = await company(db, name="")
        await add(db, ws)
        return ws

    result = sync(run(go), graph)
    assert "no name" in result.skipped_reason and graph.calls == []


# ---------------------------------------------------------------------------- retracting


def test_rejecting_a_published_fact_removes_it_from_the_graph(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws)

    ws, fact_id = run(seed)
    sync(ws, graph)
    graph.calls.clear()

    run(lambda db: facts_repo.update_status(db, ws, fact_id, FactStatus.REJECTED))
    result = sync(ws, graph)

    assert (result.retracted, result.published) == (1, 0)
    assert graph.retracted() == [fact_node_id(ws, fact_id)]
    assert run(lambda db: sync_repo.list_synced(db, ws)) == {}


def test_a_fact_that_no_longer_exists_is_removed_from_the_graph_too(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws)

    ws, fact_id = run(seed)
    sync(ws, graph)
    graph.calls.clear()

    run(lambda db: change(db, "DELETE FROM company_facts WHERE id = ?", fact_id))
    assert sync(ws, graph).retracted == 1 and graph.retracted() == [fact_node_id(ws, fact_id)]


def test_a_fact_returned_to_suggested_is_retracted(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws)

    ws, fact_id = run(seed)
    sync(ws, graph)
    run(lambda db: facts_repo.update_status(db, ws, fact_id, FactStatus.SUGGESTED))
    assert sync(ws, graph).retracted == 1


# ---------------------------------------------------------------------------- the graph is not there

def test_a_graph_that_is_down_leaves_the_facts_pending_and_the_next_sync_catches_up(sync_on):
    graph = FakeGraph(available=False)
    ws = run(_seed_one)

    down = sync(ws, graph)
    assert down.pending == 1 and down.published == 0 and "unreachable" in down.skipped_reason
    assert graph.calls == [] and run(lambda db: sync_repo.list_synced(db, ws)) == {}

    graph.is_available = True
    assert sync(ws, graph).published == 1


def test_a_failed_publish_is_counted_not_remembered_and_retried(sync_on):
    graph = FakeGraph(fail_on="MERGE (f:CompanyFact")
    ws = run(_seed_one)

    failed = sync(ws, graph)
    assert failed.failed == 1 and failed.published == 0 and "neo4j said no" in failed.error
    assert run(lambda db: sync_repo.list_synced(db, ws)) == {}
    assert rows("SELECT action FROM audit_log WHERE action LIKE 'company_fact.graph_%'") == []

    graph.fail_on = None
    assert sync(ws, graph).published == 1


def test_a_failed_retraction_does_not_stop_the_publishing_and_is_retried(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws, "ceo", "Amal Nasr")

    ws, old = run(seed)
    sync(ws, graph)
    run(lambda db: facts_repo.update_status(db, ws, old, FactStatus.REJECTED))
    run(lambda db: add(db, ws, "cfo", "Mona Said"))

    graph.fail_on = "DETACH DELETE f"
    result = sync(ws, graph)
    assert result.published == 1 and result.failed == 1 and result.retracted == 0
    assert old in run(lambda db: sync_repo.list_synced(db, ws))  # still remembered, so it is retried

    graph.fail_on = None
    assert sync(ws, graph).retracted == 1


def test_the_sync_never_raises_into_the_caller(sync_on):
    class Broken(FakeGraph):
        def available(self):
            raise RuntimeError("driver exploded")

    ws = run(_seed_one)
    result = sync(ws, Broken())
    assert "driver exploded" in result.error and result.published == 0


# ---------------------------------------------------------------------------- scoping and audit


def test_one_workspace_never_syncs_anothers_facts(sync_on):
    graph = FakeGraph()

    async def seed(db):
        a, b = await company(db, "Acme"), await company(db, "Beta")
        await add(db, a, "ceo", "Amal Nasr")
        other = await add(db, b, "ceo", "Karim Zaki")
        return a, b, other

    a, b, other = run(seed)
    sync(a, graph)
    assert other not in graph.published()
    assert run(lambda db: sync_repo.list_synced(db, b)) == {}


def test_a_publish_and_a_retraction_are_audited_as_the_system(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws)

    ws, fact_id = run(seed)
    sync(ws, graph)
    run(lambda db: facts_repo.update_status(db, ws, fact_id, FactStatus.REJECTED))
    sync(ws, graph)

    audit = rows("SELECT action, actor_kind, actor_id, target_type, metadata FROM audit_log WHERE action LIKE 'company_fact.graph_%' ORDER BY id")
    assert [a["action"] for a in audit] == ["company_fact.graph_published", "company_fact.graph_retracted"]
    assert all(a["actor_kind"] == "system" and a["actor_id"] is None for a in audit)
    assert json.loads(audit[0]["metadata"]) == {"count": 1, "fact_ids": [fact_id]}


def test_syncing_never_changes_a_facts_updated_at(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        return ws, await add(db, ws)

    ws, fact_id = run(seed)
    before = rows("SELECT updated_at FROM company_facts WHERE id = ?", fact_id)
    sync(ws, graph)
    assert rows("SELECT updated_at FROM company_facts WHERE id = ?", fact_id) == before


def test_two_syncs_at_once_publish_a_fact_once(sync_on):
    graph = FakeGraph()
    ws = run(_seed_one)

    async def both():
        async with get_db() as one, get_db() as two:
            return await asyncio.gather(
                graph_sync.sync_workspace(one, ws, conn=graph), graph_sync.sync_workspace(two, ws, conn=graph)
            )

    results = asyncio.run(both())
    assert sorted(r.published for r in results) == [0, 1]
    assert len(graph.published()) == 1


def test_the_schema_is_applied_once_per_connection(sync_on):
    graph = FakeGraph()

    async def seed(db):
        ws = await company(db)
        await add(db, ws, "ceo", "Amal Nasr")
        return ws

    ws = run(seed)
    sync(ws, graph)
    run(lambda db: add(db, ws, "cfo", "Mona Said"))
    sync(ws, graph)
    assert graph.schema_applied == 1


def test_the_hash_changes_with_the_content_the_graph_holds():
    from edrak.graph.ingestion.company_facts import CompanyFactRecord

    base = dict(fact_id="f", category="leadership", key="ceo", value="A", quote="A", source_url="https://a.example",
                source_tier="own_site", confidence="high")
    one = CompanyFactRecord(**base)
    assert graph_sync.content_hash(one, "acme", "ws") == graph_sync.content_hash(CompanyFactRecord(**base), "acme", "ws")
    for changed in (dict(value="B"), dict(quote="B"), dict(pdf_page=3), dict(confidence="low"), dict(conflict_group="g"), dict(edited_by_user=True)):
        assert graph_sync.content_hash(CompanyFactRecord(**{**base, **changed}), "acme", "ws") != graph_sync.content_hash(one, "acme", "ws")
    assert graph_sync.content_hash(one, "other", "ws") != graph_sync.content_hash(one, "acme", "ws")
    assert graph_sync.content_hash(one, "acme", "other") != graph_sync.content_hash(one, "acme", "ws")
