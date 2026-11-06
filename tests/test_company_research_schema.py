import asyncio
import json
import sqlite3

import pytest
from company_research_fixtures import new_workspace

from edrak.company_research.categories import CATEGORY_KEYS
from edrak.company_research.seed_keys import SEED_KEYS, seed_fact_keys
from edrak.db import connection
from edrak.db.connection import get_db, init_db

TABLES = ("company_facts", "fact_keys", "research_runs")


def prepared(path) -> sqlite3.Connection:
    asyncio.run(init_db())
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def columns(conn, table) -> dict[str, sqlite3.Row]:
    return {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}


def test_a_new_database_has_the_three_tables_with_their_columns(research_db_path):
    conn = prepared(research_db_path)
    assert set(TABLES) <= {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert set(columns(conn, "company_facts")) == {
        "id", "workspace_id", "category", "proposed_category", "key", "value", "value_type", "source_tier",
        "source_url", "pdf_page", "quote", "observed_at", "confidence", "status", "conflict_group", "run_id",
        "created_at", "updated_at", "edited_by_user",
    }
    assert set(columns(conn, "fact_keys")) == {
        "id", "category", "key", "label_en", "label_ar", "synonyms", "value_type", "created_at", "origin",
    }
    assert set(columns(conn, "research_runs")) == {
        "id", "workspace_id", "started_at", "finished_at", "status", "input", "pages_crawled", "pdfs_read",
        "facts_found", "warnings", "error", "created_at", "stage", "stats",
    }
    nullable = {n for n, c in columns(conn, "company_facts").items() if not c[3] and not c[5]}
    assert nullable == {"proposed_category", "pdf_page", "conflict_group", "run_id"}


def test_facts_are_indexed_by_company_and_category_and_by_status(research_db_path):
    conn = prepared(research_db_path)
    indexes = {r[1]: [c[2] for c in conn.execute(f"PRAGMA index_info({r[1]})")] for r in conn.execute("PRAGMA index_list(company_facts)")}
    assert indexes["company_facts_ws_category_idx"][:2] == ["workspace_id", "category"]
    assert indexes["company_facts_ws_status_idx"] == ["workspace_id", "status"]
    plan = " ".join(
        r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM company_facts WHERE workspace_id = 'w' AND category = 'leadership'"
        )
    )
    assert "company_facts_ws_category_idx" in plan
    plan = " ".join(
        r[3] for r in conn.execute("EXPLAIN QUERY PLAN SELECT * FROM company_facts WHERE workspace_id = 'w' AND status = 'approved'")
    )
    assert "company_facts_ws_status_idx" in plan


def test_fact_keys_are_seeded_for_every_category_but_other(research_db_path):
    conn = prepared(research_db_path)
    rows = conn.execute("SELECT category, key, synonyms, origin FROM fact_keys").fetchall()
    assert len(rows) == len(SEED_KEYS)
    assert {r[0] for r in rows} == set(CATEGORY_KEYS) - {"other"}
    assert {r[3] for r in rows} == {"seed"}
    ceo = json.loads(next(r[2] for r in rows if (r[0], r[1]) == ("leadership", "ceo")))
    assert "chief executive officer" in ceo["en"] and "المدير التنفيذي" in ceo["ar"]


def test_preparing_the_database_again_keeps_rows_and_adds_no_duplicates(research_db_path):
    conn = prepared(research_db_path)
    conn.execute("UPDATE fact_keys SET label_en = 'Edited' WHERE category = 'leadership' AND key = 'ceo'")
    conn.commit()
    before = conn.execute("SELECT count(*) FROM fact_keys").fetchone()[0]

    connection._apply_additions(research_db_path)
    assert seed_fact_keys(conn) == 0
    assert conn.execute("SELECT count(*) FROM fact_keys").fetchone()[0] == before
    assert conn.execute("SELECT label_en FROM fact_keys WHERE key = 'ceo'").fetchone()[0] == "Edited"


def test_an_existing_database_without_the_tables_gets_them(research_db_path):
    prepared(research_db_path).close()
    conn = sqlite3.connect(research_db_path)
    conn.executescript("DROP TABLE company_facts; DROP TABLE fact_keys; DROP TABLE research_runs;")
    conn.close()

    connection._apply_additions(research_db_path)
    conn = sqlite3.connect(research_db_path)
    assert conn.execute("SELECT count(*) FROM fact_keys").fetchone()[0] == len(SEED_KEYS)
    assert conn.execute("SELECT count(*) FROM company_facts").fetchone()[0] == 0


FACT = dict(
    category="leadership", key="ceo", value="Amal Nasr", value_type="text", source_tier="own_site",
    source_url="https://acme.example/about", quote="Amal Nasr, CEO", confidence="high",
)


def insert_fact(conn, workspace_id, **overrides):
    row = {**FACT, **overrides, "workspace_id": workspace_id}
    names = ", ".join(row)
    conn.execute(f"INSERT INTO company_facts ({names}) VALUES ({', '.join('?' * len(row))})", list(row.values()))


@pytest.fixture
def workspace(research_db_path):
    async def go():
        async with get_db() as db:
            return await new_workspace(db)

    asyncio.run(go())  # prepares the file and creates the workspace
    return asyncio.run(_first_workspace(research_db_path))


async def _first_workspace(path):
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM workspaces LIMIT 1")
        return (await cursor.fetchone())[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"value_type": "boolean"},
        {"source_tier": "blog"},
        {"confidence": "certain"},
        {"status": "pending"},
        {"pdf_page": 0},
        {"key": "Chief Executive"},
        {"value_type": "list", "value": "not json"},
        {"value_type": "list", "value": '{"a": 1}'},
        {"category": "other"},  # a proposed category is required
        {"source_url": "ftp://acme.example/a"},
        {"edited_by_user": 2},
        {"quote": None},
    ],
)
def test_the_database_refuses_a_fact_that_breaks_a_rule(research_db_path, workspace, overrides):
    conn = sqlite3.connect(research_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(conn, workspace, **overrides)


def test_the_database_accepts_the_fact_shapes_the_rules_allow(research_db_path, workspace):
    conn = sqlite3.connect(research_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    insert_fact(conn, workspace)
    insert_fact(conn, workspace, key="countries", value_type="list", value='["Egypt","KSA"]', pdf_page=4, source_tier="own_pdf")
    insert_fact(conn, workspace, category="other", proposed_category="Sustainability", key="carbon_target")
    row = conn.execute("SELECT status, edited_by_user, observed_at FROM company_facts LIMIT 1").fetchone()
    assert row[0] == "suggested" and row[1] == 0 and row[2].endswith("Z")


def test_facts_and_runs_need_a_real_workspace_and_go_with_it(research_db_path, workspace):
    conn = sqlite3.connect(research_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(conn, "no-such-workspace")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO research_runs (workspace_id) VALUES ('no-such-workspace')")

    insert_fact(conn, workspace)
    conn.execute("INSERT INTO research_runs (workspace_id) VALUES (?)", (workspace,))
    conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace,))
    assert conn.execute("SELECT count(*) FROM company_facts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM research_runs").fetchone()[0] == 0


def test_run_rules_and_the_updated_at_trigger(research_db_path, workspace):
    conn = sqlite3.connect(research_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO research_runs (workspace_id, status) VALUES (?, 'paused')", (workspace,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO research_runs (workspace_id, input) VALUES (?, '[]')", (workspace,))

    insert_fact(conn, workspace)
    conn.execute("UPDATE company_facts SET updated_at = '2000-01-01T00:00:00.000Z'")
    conn.execute("UPDATE company_facts SET status = 'approved'")
    assert conn.execute("SELECT updated_at FROM company_facts").fetchone()[0] > "2000-01-01T00:00:00.000Z"


def test_a_key_is_unique_within_its_category(research_db_path):
    conn = prepared(research_db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO fact_keys (category, key, label_en, label_ar) VALUES ('leadership', 'ceo', 'x', 'y')")
    conn.execute("INSERT INTO fact_keys (category, key, label_en, label_ar, origin) VALUES ('other', 'ceo', 'x', 'y', 'promoted')")
