"""API and database tests on an isolated database, uploads folder and vector store."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edrak.api.brief_builder import build_brief
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Evidence,
    EvidenceRef,
    Finding,
    OrchestrationResult,
    ResearchPlan,
    ResearchTask,
    RunStatus,
    SourceType,
    UseCase,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.core.config import settings
from edrak.db import connection


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EDRAK_DB_PATH", tmp_path / "edrak.db")
    monkeypatch.setattr(settings, "UPLOADS_PATH", tmp_path / "uploads")
    monkeypatch.setattr(settings, "VECTOR_STORE_PATH", tmp_path / "vectors")
    connection.reset_ready_cache()

    # Planning must not reach a real model in tests: the deterministic plan is used instead.
    from edrak.orchestration import planner

    def no_llm(self, request):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(planner.LlmPlanner, "plan", no_llm)
    yield tmp_path
    connection.reset_ready_cache()


@pytest.fixture
def client(isolated):
    from edrak.main import app

    with TestClient(app) as c:
        yield c


def _account(client: TestClient, name: str = "Layla Hassan") -> dict:
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/api/auth/create-account",
        json={"email": email, "password": "correct horse", "name": name, "workspace_name": f"{name} Co"},
    )
    assert resp.status_code == 200, resp.text
    return {"email": email, "password": "correct horse", **resp.json()}


def _poll_documents(client: TestClient, until=lambda docs: True, tries: int = 200) -> list[dict]:
    import time

    docs: list[dict] = []
    for _ in range(tries):
        docs = client.get("/api/documents").json()
        if until(docs):
            return docs
        time.sleep(0.1)
    return docs


# ------------------------------------------------------------------ auth


def test_create_account_session_and_sign_out(client):
    acct = _account(client)
    assert acct["user"]["email"] == acct["email"]
    assert acct["workspace"]["name"] == "Layla Hassan Co"
    assert acct["onboarding_complete"] is False

    assert client.post("/api/auth/session").status_code == 200
    assert client.post("/api/auth/sign-out").status_code == 200
    assert client.post("/api/auth/session").status_code == 401


def test_sign_in_rejects_a_wrong_password_and_requires_a_session(client):
    acct = _account(client)
    client.post("/api/auth/sign-out")
    assert client.get("/api/company").status_code == 401
    assert client.post("/api/auth/sign-in", json={"email": acct["email"], "password": "nope"}).status_code == 401
    ok = client.post("/api/auth/sign-in", json={"email": acct["email"].upper(), "password": acct["password"]})
    assert ok.status_code == 200
    assert client.get("/api/company").status_code == 200


def test_weak_password_and_duplicate_email(client):
    weak = client.post("/api/auth/create-account", json={"email": "a@example.com", "password": "short"})
    assert weak.status_code == 400 and weak.json()["detail"] == "weak_password"
    acct = _account(client)
    client.post("/api/auth/sign-out")
    dup = client.post("/api/auth/create-account", json={"email": acct["email"], "password": "long enough"})
    assert dup.status_code == 409


def test_passwords_are_argon2_and_sessions_are_stored_hashed(client, isolated):
    acct = _account(client)
    token = client.cookies.get("session_token")
    db = sqlite3.connect(isolated / "edrak.db")
    stored = db.execute("SELECT password_hash FROM users WHERE email = ?", (acct["email"],)).fetchone()[0]
    assert stored.startswith("$argon2id$")
    assert db.execute("SELECT count(*) FROM sessions WHERE token_hash = ?", (token,)).fetchone()[0] == 0


# ------------------------------------------------------------------ onboarding and company


def test_onboarding_saves_step_by_step_and_completes_only_when_ready(client):
    _account(client)
    got = client.get("/api/onboarding").json()
    assert got["step"] == 1 and got["completed"] is False

    client.put("/api/onboarding", json={"step": 2, "name": "Nile Ledger", "industry": "fintech"})
    early = client.post("/api/onboarding/complete")
    assert early.status_code == 422
    assert set(early.json()["detail"]["missing"]) == {"description", "offerings", "markets"}

    resp = client.put(
        "/api/onboarding",
        json={
            "step": 6,
            "description": "Payments for SMEs",
            "offerings": ["Invoicing", "Payroll"],
            "markets": [{"name": "Egypt", "relation": "serves"}, {"name": "Saudi Arabia", "relation": "considers"}],
            "competitors": [{"name": "Kanz Pay", "website": "kanzpay.example"}],
            "website": "nileledger.example",
            "links": [{"platform": "x", "url": "https://x.com/nileledger"}],
        },
    ).json()
    profile = resp["profile"]
    assert profile["website"] == "https://nileledger.example"
    assert [m["relation"] for m in profile["markets"]] == ["serves", "considers"]
    assert profile["competitors"][0]["name"] == "Kanz Pay"
    assert profile["competitors"][0]["website_domain"] == "kanzpay.example"

    done = client.post("/api/onboarding/complete")
    assert done.status_code == 200 and done.json()["completed"] is True
    assert client.post("/api/auth/session").json()["onboarding_complete"] is True


def test_company_get_and_put_keep_the_mvp_shape(client):
    _account(client)
    payload = {
        "name": "Nile Ledger",
        "aliases": ["NL"],
        "industry": "fintech",
        "description": "Payments.",
        "offerings": ["Invoicing"],
        "markets": ["Egypt"],
        "strategic_goals": "Grow in Egypt\nEnter KSA",
        "website": "https://nileledger.example",
        "socials": [{"link_id": "x", "platform": "linkedin", "url": "https://linkedin.com/company/nl"}],
    }
    data = client.put("/api/company", json=payload).json()
    assert data["completed"] is True
    assert data["markets"] == ["Egypt"]
    assert data["strategic_goals"] == "Grow in Egypt\nEnter KSA"
    assert data["socials"][0]["platform"] == "linkedin"


# ------------------------------------------------------------------ documents


def test_document_upload_indexes_in_the_background_and_can_be_viewed(client):
    _account(client)
    body = b"# Strategy\n\nWe plan to launch Arabic support in Q2.\n\n## Pricing\n\nEntry plan is 99 EGP."
    up = client.post("/api/documents", files=[("files", ("strategy.md", body, "text/markdown"))]).json()
    assert up["errors"] == [] and len(up["documents"]) == 1
    doc_id = up["documents"][0]["document_id"]

    docs = _poll_documents(client, until=lambda d: d and d[0]["status"] != "indexing")
    assert docs[0]["status"] == "indexed", docs[0]
    assert docs[0]["guardrail_status"] in ("clean", "flagged")

    preview = client.get(f"/api/documents/{doc_id}/preview").json()
    assert preview["view"] == "text" and "Arabic support" in preview["text"]
    assert len(preview["chunks"]) >= 1

    raw = client.get(f"/api/documents/{doc_id}/file")
    assert raw.status_code == 200 and raw.content == body

    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert client.get("/api/documents").json() == []


def test_uploads_are_checked_before_they_are_stored(client):
    _account(client)
    fake_pdf = client.post("/api/documents", files=[("files", ("report.pdf", b"MZ\x90\x00 not a pdf", "application/pdf"))]).json()
    assert fake_pdf["errors"][0]["code"] == "type_mismatch"
    exe = client.post("/api/documents", files=[("files", ("tool.exe", b"MZ", "application/octet-stream"))]).json()
    assert exe["errors"][0]["code"] == "unsupported_type"
    client.post("/api/documents", files=[("files", ("a.txt", b"hello world", "text/plain"))])
    dup = client.post("/api/documents", files=[("files", ("b.txt", b"hello world", "text/plain"))]).json()
    assert dup["errors"][0]["code"] == "duplicate"


def test_csv_upload_is_a_table_with_rows(client):
    _account(client)
    csv_bytes = b"ticket,topic\n1,Arabic replies\n2,Slow handoff\n3,Arabic replies\n"
    up = client.post("/api/documents", files=[("files", ("tickets.csv", csv_bytes, "text/csv"))]).json()
    doc_id = up["documents"][0]["document_id"]
    docs = _poll_documents(client, until=lambda d: d and d[0]["status"] != "indexing")
    assert docs[0]["kind"] == "table" and docs[0]["size"] == {"unit": "rows", "value": 3}
    preview = client.get(f"/api/documents/{doc_id}/preview").json()
    assert preview["view"] == "table" and preview["sheets"][0]["columns"] == ["ticket", "topic"]


def test_a_company_profile_json_fills_an_empty_profile(client):
    _account(client)
    profile = {"name": "Nile Fintech", "products": ["NileCore"], "tam": {"core_market": "North Africa"}}
    client.post("/api/documents", files=[("files", ("profile.json", json.dumps(profile).encode(), "application/json"))])
    _poll_documents(client, until=lambda d: d and d[0]["status"] != "indexing")
    company = client.get("/api/company").json()
    assert company["name"] == "Nile Fintech"
    assert company["offerings"] == ["NileCore"]
    assert company["markets"] == ["North Africa"]


def test_pdf_pages_are_counted(client):
    import pypdf

    _account(client)
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    client.post("/api/documents", files=[("files", ("blank.pdf", buf.getvalue(), "application/pdf"))])
    docs = _poll_documents(client, until=lambda d: d and d[0]["status"] != "indexing")
    # A PDF with no text cannot be indexed, and says why.
    assert docs[0]["status"] == "failed"
    assert "No text" in docs[0]["error_message"]
    assert docs[0]["size"] == {"unit": "pages", "value": 2}


# ------------------------------------------------------------------ analyses


FORM = {
    "use_case": "competitive_intelligence",
    "goal": "How do we compare with Kanz Pay on SME invoicing?",
    "competitors": ["Kanz Pay", "Sada"],
    "market": "Egypt",
    "offering": "",
    "target_customers": "SMEs",
    "focus": ["features", "pricing"],
    "time_window_days": 90,
    "sources": {"internal_files": True, "competitor_web": True, "news_open_data": False, "reviews_social": True},
    "repeat_weekly": False,
}


def test_draft_reject_redraft_and_approve_once(client, monkeypatch):
    _account(client)
    from edrak.api.run_manager import run_manager

    started: list[str] = []

    async def fake_start(analysis_id, *, user_id, approver_name, auto=False):
        started.append(analysis_id)

    monkeypatch.setattr(run_manager, "start_run", fake_start)

    draft = client.post("/api/analyses/draft", json={"form": FORM}).json()
    aid = draft["analysis_id"]
    assert draft["status"] == "awaiting_approval"
    workers = {t["worker"] for t in draft["plan"]["tasks"]}
    assert workers == {"internal_intelligence", "competitor_intelligence", "customer_trends"}
    # The market is not a competitor.
    assert draft["request"]["business_context"]["targets"] == ["Kanz Pay", "Sada"]

    assert any(a["analysis_id"] == aid for a in client.get("/api/analyses").json())

    assert client.post(f"/api/analyses/{aid}/reject", json={"reason": "Add pricing for Sada"}).status_code == 200
    detail = client.get(f"/api/analyses/{aid}").json()
    assert detail["status"] == "draft" and detail["rejection_reason"] == "Add pricing for Sada"

    redraft = client.post("/api/analyses/draft", json={"form": FORM, "analysis_id": aid}).json()
    assert redraft["status"] == "awaiting_approval"
    assert redraft["request"]["extras"]["rejected_plan_feedback"] == "Add pricing for Sada"

    assert client.post(f"/api/analyses/{aid}/approve").status_code == 200
    assert client.post(f"/api/analyses/{aid}/approve").status_code == 409
    assert started == [aid]


def test_another_workspace_cannot_see_an_analysis(client):
    _account(client, "Owner One")
    aid = client.post("/api/analyses/draft", json={"form": FORM}).json()["analysis_id"]
    client.post("/api/auth/sign-out")
    _account(client, "Owner Two")
    assert client.get(f"/api/analyses/{aid}").status_code == 404
    assert client.get(f"/api/analyses/{aid}/run").status_code == 404
    assert client.post(f"/api/analyses/{aid}/approve").status_code == 404
    assert all(a["analysis_id"] != aid for a in client.get("/api/analyses").json())


# ------------------------------------------------------------------ schema rules


def _seed_run(db_path: Path) -> tuple[str, str, str]:
    """A workspace, an approved plan with one task, and a run. Returns (workspace, request, task)."""
    from edrak.db.repositories import analyses as analyses_repo
    from edrak.db.repositories import auth as auth_repo
    from edrak.db.repositories import runs as runs_repo

    async def go():
        async with connection.get_db() as db:
            _, ws = await auth_repo.create_account(
                db, email=f"{uuid.uuid4().hex[:6]}@x.com", password_hash="x", full_name="A", job_title=None,
                workspace_name="W",
            )
            req = BusinessRequest(goal="g", company_profile=CompanyProfile(name="Acme"),
                                  business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE))
            await analyses_repo.create_request(
                db, request=req, workspace_id=ws["id"], created_by=ws["owner_id"], title="T", form={}, competitors=[],
                markets=[], offering=None, target_customers=None, allowed_source_types=["web_search"],
            )
            task = ResearchTask(parent_request_id=req.request_id, worker=WorkerType.COMPETITOR_INTELLIGENCE,
                                goal="g", focus="f", company_profile=req.company_profile,
                                business_context=req.business_context)
            plan = ResearchPlan(request_id=req.request_id, tasks=[task])
            await analyses_repo.save_plan(db, plan)
            assert await analyses_repo.approve_plan(db, plan.plan_id, user_id=ws["owner_id"])
            await runs_repo.create_run(db, request_id=req.request_id, plan_id=plan.plan_id, triggered_by=ws["owner_id"])
            return ws["id"], req.request_id, task.task_id

    return asyncio.run(go())


def test_results_are_normalized_and_numbered(isolated):
    from edrak.db.repositories import results as results_repo

    ws, request_id, task_id = _seed_run(isolated / "edrak.db")
    ev = [Evidence(source_type=SourceType.PRICING_PAGE, extracted_fact=f"fact {i}", source_url=f"https://x/{i}") for i in range(3)]
    result = WorkerResult(
        task_id=task_id, worker=WorkerType.COMPETITOR_INTELLIGENCE, status=WorkerStatus.COMPLETED,
        evidence=ev,
        findings=[Finding(statement="Kanz Pay has a free tier", evidence_refs=[EvidenceRef(evidence_id=ev[0].evidence_id)])],
    )

    async def go():
        async with connection.get_db() as db:
            assert await results_repo.write_worker_result(db, request_id, result)
            assert not await results_repo.write_worker_result(db, request_id, result)  # same attempt twice
            numbers = await results_repo.evidence_numbers(db, request_id)
            assert sorted(numbers.values()) == [1, 2, 3]
            loaded = await results_repo.load_worker_results(db, request_id)
            assert loaded[0].findings[0].evidence_ids == [ev[0].evidence_id]
            # Evidence is a record of what was retrieved.
            with pytest.raises(Exception):
                await db.execute("UPDATE evidence SET extracted_fact = 'changed' WHERE request_id = ?", (request_id,))

    asyncio.run(go())


def test_a_run_needs_an_approved_plan_and_no_evidence_cannot_carry_findings(isolated):
    ws, request_id, task_id = _seed_run(isolated / "edrak.db")
    db = sqlite3.connect(isolated / "edrak.db")
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(
        "INSERT INTO worker_results (task_id, attempt, worker, request_id, status) VALUES (?, 2, 'competitor_intelligence', ?, 'no_evidence')",
        (task_id, request_id),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO findings (task_id, attempt, request_id, statement) VALUES (?, 2, ?, 'x')", (task_id, request_id)
        )
    # A second plan version that was never approved cannot be run.
    plan_id = db.execute("SELECT plan_id FROM runs WHERE request_id = ?", (request_id,)).fetchone()[0]
    db.execute("UPDATE research_plans SET status = 'superseded' WHERE plan_id = ?", (plan_id,))
    db.execute("DELETE FROM worker_results")
    db.execute("DELETE FROM runs")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO runs (request_id, plan_id) VALUES (?, ?)", (request_id, plan_id))


def test_brief_numbers_are_per_workspace(isolated):
    from edrak.db.repositories import briefs as briefs_repo

    ws, request_id, _ = _seed_run(isolated / "edrak.db")

    async def go():
        async with connection.get_db() as db:
            saved = await briefs_repo.save_brief(
                db, workspace_id=ws, request_id=request_id, requested_by=None,
                build=lambda bid, no: {"brief_id": bid, "brief_no": no, "title": "T", "use_case": "competitive_intelligence",
                                       "summary": {"found": [{"text": "Hello"}]}, "evidence": [], "lenses": []},
            )
            assert saved["number"] == 1 and saved["document"]["brief_no"] == 1
            row = await briefs_repo.get_brief(db, saved["id"], ws)
            assert row["summary"] == "Hello"
            with pytest.raises(Exception):
                await db.execute("UPDATE briefs SET title = 'changed' WHERE id = ?", (saved["id"],))

    asyncio.run(go())


# ------------------------------------------------------------------ migration from v1


V1_SCHEMA = """
CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE users (user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, name TEXT NOT NULL, title TEXT, workspace_id TEXT NOT NULL);
CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE company_profiles (workspace_id TEXT PRIMARY KEY, fields_json TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE company_documents (document_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, filename TEXT NOT NULL, size_json TEXT, status TEXT NOT NULL DEFAULT 'indexing', created_at TEXT NOT NULL);
CREATE TABLE analyses (analysis_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', form_json TEXT NOT NULL, request_json TEXT NOT NULL, plan_json TEXT, rejection_reason TEXT, brief_no INTEGER, updated_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE runs (analysis_id TEXT PRIMARY KEY, approved_at TEXT NOT NULL, approved_by TEXT NOT NULL, started_at TEXT, finished_at TEXT, state TEXT NOT NULL DEFAULT 'running', stage TEXT NOT NULL DEFAULT 'plan', result_json TEXT, cancelled_at TEXT);
CREATE TABLE briefs (brief_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, brief_no INTEGER NOT NULL, brief_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def test_a_v1_database_is_migrated_and_old_passwords_still_work(isolated, monkeypatch):
    import hashlib

    path = isolated / "edrak.db"
    old = sqlite3.connect(path)
    old.executescript(V1_SCHEMA)
    old.execute("INSERT INTO workspaces VALUES ('ws-1', 'Acme')")
    sha = hashlib.sha256(b"edrak-demo").hexdigest()
    old.execute("INSERT INTO users VALUES ('u1', 'a@x.com', ?, 'Ann', 'CEO', 'ws-1')", (sha,))
    old.execute("INSERT INTO users VALUES ('u2', 'b@x.com', ?, 'Bob', NULL, 'ws-1')", (sha,))
    old.execute("INSERT INTO company_profiles VALUES ('ws-1', ?, 1)", (json.dumps({"name": "Acme", "offerings": ["Widget"], "markets": ["Egypt"]}),))

    request = BusinessRequest(request_id="a1", goal="Compare", company_profile=CompanyProfile(name="Acme"),
                              business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE, targets=["Beta"]))
    task = ResearchTask(parent_request_id="a1", worker=WorkerType.COMPETITOR_INTELLIGENCE, goal="g", focus="f",
                        company_profile=request.company_profile, business_context=request.business_context)
    plan = ResearchPlan(request_id="a1", tasks=[task])
    ev = Evidence(source_type=SourceType.NEWS_ARTICLE, extracted_fact="Beta raised prices")
    result = OrchestrationResult(request_id="a1", status=RunStatus.COMPLETED, plan=plan, results=[
        WorkerResult(task_id=task.task_id, worker=task.worker, status=WorkerStatus.COMPLETED, evidence=[ev],
                     findings=[Finding(statement="Beta is pricier", evidence_refs=[EvidenceRef(evidence_id=ev.evidence_id)])]),
    ])
    old.execute(
        "INSERT INTO analyses VALUES ('a1', 'ws-1', 'Compare', 'completed', ?, ?, ?, NULL, 4, '2026-01-01', '2026-01-01')",
        (json.dumps({"use_case": "competitive_intelligence", "competitors": ["Beta"], "sources": {"competitor_web": True}}),
         request.model_dump_json(), plan.model_dump_json()),
    )
    old.execute("INSERT INTO runs VALUES ('a1', '2026-01-01', 'Ann', '2026-01-01', '2026-01-02', 'completed', 'brief', ?, NULL)",
                (result.model_dump_json(),))
    old.execute("INSERT INTO briefs VALUES ('b1', 'a1', 4, ?, '2026-01-02')", (json.dumps({"title": "Compare", "lenses": []}),))
    old.commit()
    old.close()

    from edrak.main import app

    with TestClient(app) as client:
        assert (isolated / "edrak.v1.bak.db").exists()
        signed = client.post("/api/auth/sign-in", json={"email": "a@x.com", "password": "edrak-demo"})
        assert signed.status_code == 200
        assert signed.json()["workspace"]["name"] == "Acme"
        assert signed.json()["onboarding_complete"] is True
        brief = client.get("/api/briefs/b1").json()
        assert brief["brief_no"] == 4
        analyses = client.get("/api/analyses").json()
        assert analyses[0]["status"] == "completed" and analyses[0]["brief_id"] == "b1"
        client.post("/api/auth/sign-out")
        bob = client.post("/api/auth/sign-in", json={"email": "b@x.com", "password": "edrak-demo"}).json()
        assert bob["workspace"]["name"] == "Bob's workspace" and bob["onboarding_complete"] is False

    db = sqlite3.connect(path)
    assert db.execute("SELECT password_hash FROM users WHERE id = 'u1'").fetchone()[0].startswith("$argon2id$")
    assert db.execute("SELECT count(*) FROM findings").fetchone()[0] == 1
    assert db.execute("SELECT display_no FROM evidence").fetchone()[0] == 1


# ------------------------------------------------------------------ brief builder


def test_brief_builder():
    req = BusinessRequest(
        request_id="req-test-1",
        goal="Test strategic goal",
        company_profile=CompanyProfile(name="Acme"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    task = ResearchTask(
        parent_request_id=req.request_id, worker=WorkerType.COMPETITOR_INTELLIGENCE, goal="Study competitor",
        focus="Pricing", company_profile=req.company_profile, business_context=req.business_context,
    )
    plan = ResearchPlan(request_id=req.request_id, tasks=[task])
    ev = Evidence(source_type=SourceType.PRICING_PAGE, extracted_fact="Entry plan is $10")
    wresult = WorkerResult(task_id=task.task_id, worker=task.worker, status=WorkerStatus.COMPLETED, evidence=[ev])
    oresult = OrchestrationResult(request_id=req.request_id, status=RunStatus.COMPLETED, plan=plan, results=[wresult])

    brief = build_brief(
        brief_id="brief-test-1", brief_no=1, analysis_id=req.request_id, analysis_title="Test Analysis",
        title="Test Brief Title", use_case="competitive_intelligence", created_at="2026-10-10T12:00:00Z",
        requested_by="Strategy Lead", request=req, orchestration=oresult, verification=None,
        evidence_numbers={(task.task_id, 1, ev.evidence_id): 7},
    )
    assert brief["brief_id"] == "brief-test-1"
    assert len(brief["lenses"]) == 1
    assert brief["evidence"][0]["display_no"] == 7
    assert "summary" in brief and "options" in brief
