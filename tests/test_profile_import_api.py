import uuid

import pytest
from fastapi.testclient import TestClient

from edrak.api.routers import profile_import
from edrak.company_profile import ProfileDraft, ProfileExtractionError, ProfileFields, ProfileLLMError
from edrak.company_profile.url_import import SiteUnreachable, UrlBlocked
from edrak.core.config import settings
from edrak.db import connection

URL = "/api/company/profile/import/url"


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


def draft():
    return ProfileDraft(
        profile=ProfileFields(name="Acme Pay", products=["Acme Terminal"]),
        field_sources={"name": ["S1"], "products": ["S2"]},
        gaps=["aliases", "notes", "industry", "markets", "goals"],
        warnings=["1 page(s) could not be read."],
    )


def test_the_endpoint_needs_a_session(client):
    assert client.post(URL, json={"url": "acme.example"}).status_code == 401


def test_it_returns_the_draft_and_saves_nothing(client, monkeypatch):
    sign_in(client)
    seen = []

    async def fake(url):
        seen.append(url)
        return draft()

    monkeypatch.setattr(profile_import, "import_profile_from_url", fake)
    before = client.get("/api/company").json()
    resp = client.post(URL, json={"url": "acme.example"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seen == ["acme.example"]
    assert body["profile"]["name"] == "Acme Pay" and body["profile"]["products"] == ["Acme Terminal"]
    assert body["field_sources"]["name"] == ["S1"] and "markets" in body["gaps"] and body["warnings"]
    assert set(body) == {"profile", "links", "field_sources", "gaps", "warnings", "sources"}
    assert client.get("/api/company").json() == before  # nothing was written


@pytest.mark.parametrize("url", ["ftp://example.com", "http://127.0.0.1/", "http://169.254.169.254/", "http://localhost:8000/api/company", "   "])
def test_invalid_and_blocked_addresses_are_422_before_any_request(client, url):
    sign_in(client)
    resp = client.post(URL, json={"url": url})  # the real importer: it refuses these without touching the network
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_url"
    assert resp.json()["detail"]["message"]


def test_a_missing_url_field_is_422(client):
    sign_in(client)
    assert client.post(URL, json={}).status_code == 422


@pytest.mark.parametrize(
    "error, code",
    [
        (SiteUnreachable("The website took too long to answer."), "site_unreachable"),
        (ProfileLLMError("down"), "model_unavailable"),
        (ProfileExtractionError("bad", attempts=2, last_error="x", last_reply="y"), "extraction_failed"),
    ],
)
def test_upstream_failures_are_502_with_a_clear_message(client, monkeypatch, error, code):
    sign_in(client)

    async def fail(url):
        raise error

    monkeypatch.setattr(profile_import, "import_profile_from_url", fail)
    resp = client.post(URL, json={"url": "acme.example"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == code
    assert resp.json()["detail"]["message"]
    if code == "site_unreachable":
        assert resp.json()["detail"]["message"] == "The website took too long to answer."


def test_a_blocked_url_error_from_the_importer_is_422(client, monkeypatch):
    sign_in(client)

    async def blocked(url):
        raise UrlBlocked("That address cannot be imported: it is a local or internal address.")

    monkeypatch.setattr(profile_import, "import_profile_from_url", blocked)
    resp = client.post(URL, json={"url": "x.example"})
    assert resp.status_code == 422 and "internal address" in resp.json()["detail"]["message"]
