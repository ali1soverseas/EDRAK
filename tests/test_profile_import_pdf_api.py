import uuid

import pytest
from fastapi.testclient import TestClient

import pdf_fixtures as fx
from edrak.api.routers import profile_import
from edrak.company_profile import ProfileDraft, ProfileExtractionError, ProfileFields, ProfileLLMError
from edrak.company_profile.pdf_import import importer as importer_module
from edrak.core.config import settings
from edrak.db import connection

URL = "/api/company/profile/import/pdf"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EDRAK_DB_PATH", tmp_path / "edrak.db")
    monkeypatch.setattr(settings, "UPLOADS_PATH", tmp_path / "uploads")
    monkeypatch.setattr(settings, "VECTOR_STORE_PATH", tmp_path / "vectors")
    connection.reset_ready_cache()
    from edrak.main import app

    with TestClient(app) as c:
        yield c
    connection.reset_ready_cache()


def sign_in(client):
    resp = client.post(
        "/api/auth/create-account",
        json={"email": f"u_{uuid.uuid4().hex[:8]}@example.com", "password": "correct horse", "name": "Layla", "workspace_name": "Layla Co"},
    )
    assert resp.status_code == 200, resp.text


def upload(client, content: bytes, name="profile.pdf", mime="application/pdf"):
    return client.post(URL, files={"file": (name, content, mime)})


def draft():
    return ProfileDraft(
        profile=ProfileFields(name="Acme Pay"),
        field_sources={"name": ["S1"]},
        gaps=["aliases", "notes", "products", "industry", "markets", "goals"],
        warnings=["Page(s) 3 are mostly scanned images and OCR is not available on this server, so the text in those images was not read."],
    )


def test_the_endpoint_needs_a_session(client, tmp_path):
    assert upload(client, fx.english_single(tmp_path / "a.pdf").read_bytes()).status_code == 401


def test_it_returns_a_draft_and_saves_nothing(client, tmp_path, monkeypatch):
    sign_in(client)
    seen = []

    async def fake(content):
        seen.append(content[:5])
        return draft()

    monkeypatch.setattr(profile_import, "import_profile_from_pdf", fake)
    before = client.get("/api/company").json()
    resp = upload(client, fx.english_single(tmp_path / "a.pdf").read_bytes())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seen == [b"%PDF-"]
    assert body["profile"]["name"] == "Acme Pay" and body["field_sources"] == {"name": ["S1"]} and body["warnings"]
    assert set(body) == {"profile", "links", "field_sources", "gaps", "warnings", "sources"}  # same keys as the URL importer
    assert client.get("/api/company").json() == before
    assert client.get("/api/documents").json() == []  # and the PDF is not kept as a document


def test_the_real_pipeline_runs_behind_the_endpoint_with_the_core_mocked(client, tmp_path, monkeypatch):
    sign_in(client)
    captured = {}

    def fake_core(documents, links, llm=None):
        captured["refs"] = [d.ref for d in documents]
        captured["links"] = links
        return draft()

    monkeypatch.setattr(importer_module, "build_profile_draft", fake_core)
    resp = upload(client, fx.english_single(tmp_path / "a.pdf").read_bytes())
    assert resp.status_code == 200, resp.text
    assert captured["refs"] == ["p.1", "p.2"]
    assert captured["links"][0] == {"platform": "website", "url": "https://www.acmepay.example"}


@pytest.mark.parametrize(
    "make, name, code",
    [
        (lambda p: b"PK\x03\x04 a zip renamed to pdf", "brochure.pdf", "not_a_pdf"),
        (lambda p: b"", "empty.pdf", "empty_file"),
        (lambda p: b"%PDF-1.7\n" + b"garbage\n" * 40, "broken.pdf", "corrupt"),
        (lambda p: fx.encrypted(p / "x.pdf").read_bytes(), "locked.pdf", "encrypted"),
    ],
)
def test_bad_files_are_422_with_a_code_and_a_message(client, tmp_path, make, name, code):
    sign_in(client)
    resp = upload(client, make(tmp_path), name=name)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == code and detail["message"]


def test_an_oversized_upload_and_too_many_pages_are_422(client, tmp_path, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(settings, "PROFILE_IMPORT_PDF_MAX_MB", 1)
    big = upload(client, b"%PDF-1.7\n" + b"0" * (2 * 1024 * 1024))
    assert big.status_code == 422 and big.json()["detail"]["code"] == "too_large"

    monkeypatch.setattr(settings, "PROFILE_IMPORT_PDF_MAX_MB", 20)
    monkeypatch.setattr(settings, "PROFILE_IMPORT_PDF_MAX_PAGES", 3)
    many = upload(client, fx.deck(tmp_path / "many.pdf", slides=6).read_bytes())
    assert many.status_code == 422 and many.json()["detail"]["code"] == "too_many_pages"


def test_a_missing_file_is_422(client):
    sign_in(client)
    assert client.post(URL).status_code == 422


@pytest.mark.parametrize(
    "error, code",
    [(ProfileLLMError("down"), "model_unavailable"), (ProfileExtractionError("bad", attempts=2, last_error="x", last_reply="y"), "extraction_failed")],
)
def test_model_failures_are_502(client, tmp_path, monkeypatch, error, code):
    sign_in(client)

    async def fail(content):
        raise error

    monkeypatch.setattr(profile_import, "import_profile_from_pdf", fail)
    resp = upload(client, fx.english_single(tmp_path / "a.pdf").read_bytes())
    assert resp.status_code == 502 and resp.json()["detail"]["code"] == code
