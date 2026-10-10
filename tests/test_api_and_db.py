"""Comprehensive end-to-end tests for EDRAK Database and API layer."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from edrak.main import app
from edrak.contracts import (
    BusinessRequest,
    CompanyProfile,
    OrchestrationResult,
    ResearchPlan,
    ResearchTask,
    RunStatus,
    UseCase,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.api.brief_builder import build_brief
from edrak.core.config import settings


@pytest.fixture
def client():
    return TestClient(app)


def test_auth_session(client):
    """Test session endpoint returns default demo session."""
    resp = client.post("/api/auth/session")
    assert resp.status_code == 200
    data = resp.json()
    assert "user" in data
    assert "workspace" in data
    assert data["user"]["email"] == "demo@edrak.ai"


def test_auth_sign_in_and_out(client):
    """Test sign in with demo credentials, and sign out."""
    resp = client.post("/api/auth/sign-in", json={"email": "demo@edrak.ai", "password": "edrak-demo"})
    assert resp.status_code == 200
    assert resp.json()["user"]["name"] == "Strategy Lead"

    # Invalid password
    bad_resp = client.post("/api/auth/sign-in", json={"email": "demo@edrak.ai", "password": "wrong"})
    assert bad_resp.status_code == 401

    # Sign out
    out_resp = client.post("/api/auth/sign-out")
    assert out_resp.status_code == 200


def test_auth_create_account(client):
    """Test new account creation."""
    import uuid

    new_email = f"test_user_{uuid.uuid4().hex[:6]}@example.com"
    resp = client.post(
        "/api/auth/create-account",
        json={
            "email": new_email,
            "password": "secretpassword",
            "name": "Test Engineer",
            "title": "Staff Architect",
            "workspace_name": "Test Workspace",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == new_email
    assert data["workspace"]["name"] == "Test Workspace"


def test_company_profile_get_and_save(client):
    """Test company profile GET and PUT, ensuring active_profile.json is written."""
    get_resp = client.get("/api/company")
    assert get_resp.status_code == 200
    assert "name" in get_resp.json()

    put_payload = {
        "name": "GitLab Pilot",
        "aliases": ["GitLab Inc."],
        "industry": "saas",
        "description": "GitLab DevSecOps platform and AI Duo assistant.",
        "offerings": ["GitLab Duo", "GitLab CI/CD", "GitLab Ultimate"],
        "markets": ["Global Enterprise"],
        "strategic_goals": "Expand enterprise AI market share.",
        "website": "https://about.gitlab.com",
        "socials": [],
    }

    put_resp = client.put("/api/company", json=put_payload)
    assert put_resp.status_code == 200
    data = put_resp.json()
    assert data["name"] == "GitLab Pilot"
    assert data["completed"] is True

    # Check active_profile.json was saved on disk
    active_profile_path = settings.BASE_DIR / "data" / "profiles" / "active_profile.json"
    assert active_profile_path.exists()
    content = json.loads(active_profile_path.read_text(encoding="utf-8"))
    assert content["name"] == "GitLab Pilot"


def test_company_documents_lifecycle(client, tmp_path):
    """Test document upload, listing, and deletion."""
    test_file_content = b"GitLab competitive analysis overview and strategy."
    files = [("files", ("strategy_doc.txt", test_file_content, "text/plain"))]

    upload_resp = client.post("/api/company/documents", files=files)
    assert upload_resp.status_code == 200
    docs = upload_resp.json()
    assert len(docs) == 1
    doc_id = docs[0]["document_id"]
    assert docs[0]["filename"] == "strategy_doc.txt"
    assert docs[0]["status"] == "indexed"

    # List documents
    list_resp = client.get("/api/company/documents")
    assert list_resp.status_code == 200
    assert any(d["document_id"] == doc_id for d in list_resp.json())

    # Delete document
    del_resp = client.delete(f"/api/company/documents/{doc_id}")
    assert del_resp.status_code == 200

    # Verify deleted
    list_resp_after = client.get("/api/company/documents")
    assert not any(d["document_id"] == doc_id for d in list_resp_after.json())


def test_analyses_draft_and_retrieval(client):
    """Test drafting a plan, checking research tasks contract, and retrieving it."""
    form_payload = {
        "use_case": "competitive_intelligence",
        "goal": "Evaluate GitLab Duo vs GitHub Copilot enterprise adoption",
        "competitors": ["GitHub Copilot", "Microsoft Azure DevOps"],
        "market": "Enterprise DevSecOps",
        "offering": "",
        "target_customers": "enterprise",
        "focus": ["features", "pricing"],
        "time_window_days": 90,
        "sources": {
            "internal_files": True,
            "competitor_web": True,
            "news_open_data": True,
            "reviews_social": True,
        },
        "repeat_weekly": False,
    }

    draft_resp = client.post("/api/analyses/draft", json={"form": form_payload})
    assert draft_resp.status_code == 200
    draft_data = draft_resp.json()
    analysis_id = draft_data["analysis_id"]
    assert draft_data["status"] == "awaiting_approval"
    assert draft_data["plan"] is not None
    assert len(draft_data["plan"]["tasks"]) > 0

    # Get single analysis
    detail_resp = client.get(f"/api/analyses/{analysis_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["analysis_id"] == analysis_id

    # List analyses includes it
    list_resp = client.get("/api/analyses")
    assert list_resp.status_code == 200
    assert any(a["analysis_id"] == analysis_id for a in list_resp.json())

    # Test reject plan
    reject_resp = client.post(
        f"/api/analyses/{analysis_id}/reject",
        json={"reason": "Need more focus on pricing models"},
    )
    assert reject_resp.status_code == 200

    detail_after = client.get(f"/api/analyses/{analysis_id}").json()
    assert detail_after["status"] == "draft"
    assert detail_after["rejection_reason"] == "Need more focus on pricing models"


def test_brief_builder():
    """Test the Brief builder logic directly."""
    from edrak.contracts import BusinessContext

    req = BusinessRequest(
        request_id="req-test-1",
        goal="Test strategic goal",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    task = ResearchTask(
        parent_request_id=req.request_id,
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Study competitor",
        focus="Pricing",
        company_profile=req.company_profile,
        business_context=req.business_context,
    )
    plan = ResearchPlan(request_id=req.request_id, tasks=[task])
    wresult = WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=WorkerStatus.COMPLETED,
        findings=[],
        evidence=[],
    )
    oresult = OrchestrationResult(
        request_id=req.request_id,
        status=RunStatus.COMPLETED,
        plan=plan,
        results=[wresult],
    )

    brief = build_brief(
        brief_id="brief-test-1",
        brief_no=1,
        analysis_id=req.request_id,
        analysis_title="Test Analysis",
        title="Test Brief Title",
        use_case="competitive_intelligence",
        created_at="2026-10-10T12:00:00Z",
        requested_by="Strategy Lead",
        request=req,
        orchestration=oresult,
        verification=None,
    )

    assert brief["brief_id"] == "brief-test-1"
    assert brief["brief_no"] == 1
    assert len(brief["lenses"]) == 1
    assert "summary" in brief
    assert "options" in brief


def test_upload_company_profile_json(client):
    """Uploading a company profile JSON should auto-ingest it and update active profile."""
    profile_data = {
        "name": "Nile Fintech Solutions",
        "aliases": ["NilePay", "Nile Ledger"],
        "industry": "fintech",
        "description": "Enterprise cloud-native banking and payments platform in MENA.",
        "products": ["NileCore Banking", "NileSwitch", "NileRisk"],
        "strategic_priorities": ["Expand Egyptian banking partnerships", "Acquire CBE digital license"],
        "tam": {"core_market": "North Africa Banking & Microfinance"},
        "website": "https://nilefintech.example",
    }
    json_bytes = json.dumps(profile_data).encode("utf-8")
    files = [("files", ("nile_profile.json", json_bytes, "application/json"))]

    resp = client.post("/api/company/documents", files=files)
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "nile_profile.json"

    # Verify that company endpoint now reflects this ingested profile
    company_resp = client.get("/api/company")
    assert company_resp.status_code == 200
    comp = company_resp.json()
    assert comp["name"] == "Nile Fintech Solutions"
    assert "NileCore Banking" in comp["offerings"]
    assert "North Africa Banking & Microfinance" in comp["markets"]

    # Verify active_profile.json on disk preserved full data
    active_path = settings.BASE_DIR / "data" / "profiles" / "active_profile.json"
    disk_data = json.loads(active_path.read_text(encoding="utf-8"))
    assert disk_data["name"] == "Nile Fintech Solutions"
    assert "strategic_priorities" in disk_data


def test_upload_multiple_documents_pdf_and_md(client):
    """Uploading multiple files (PDF + MD) together should extract text and index both."""
    import io
    import pypdf

    # Create in-memory PDF
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    pdf_buf = io.BytesIO()
    writer.write(pdf_buf)
    pdf_bytes = pdf_buf.getvalue()

    md_content = b"# Strategic Assessment\n\nNile Ledger delivers 99.999% uptime for core ledger transactions.\n"

    files = [
        ("files", ("quarterly_report.pdf", pdf_bytes, "application/pdf")),
        ("files", ("architecture.md", md_content, "text/markdown")),
    ]

    resp = client.post("/api/company/documents", files=files)
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 2

    # Check PDF size unit is pages
    pdf_doc = next(d for d in docs if d["filename"] == "quarterly_report.pdf")
    assert pdf_doc["size"]["unit"] == "pages"
    assert pdf_doc["size"]["value"] == 2
    assert pdf_doc["status"] == "indexed"

    # Check MD size unit is rows
    md_doc = next(d for d in docs if d["filename"] == "architecture.md")
    assert md_doc["size"]["unit"] == "rows"
    assert md_doc["size"]["value"] >= 3
    assert md_doc["status"] == "indexed"

