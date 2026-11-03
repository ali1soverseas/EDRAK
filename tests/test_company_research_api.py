"""The company research endpoints: runs, grouped facts, and review actions. The network and the model are replaced."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from agent_fixtures import (
    ALL_RULES, HOME, Model, acme_site, deps, held, report_pdf, row,
)
from fastapi.testclient import TestClient

from edrak.company_research import CATEGORIES, FactStatus, NewFact, SourceTier
from edrak.company_research.agent import research_manager
from edrak.core.config import settings
from edrak.db import connection
from edrak.db.connection import get_db
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import company_facts as facts_repo

RUNS = "/api/company/research/runs"
FACTS = "/api/company/facts"


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


def sign_in(client, name="Acme Pay"):
    resp = client.post(
        "/api/auth/create-account",
        json={"email": f"u_{uuid.uuid4().hex[:8]}@example.com", "password": "correct horse", "name": "Layla", "workspace_name": name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["workspace"]["workspace_id"]


def other_user(client):
    second = TestClient(client.app)
    sign_in(second, "Other Co")
    return second


def save_website(ws, url=HOME):
    async def go():
        async with get_db() as db:
            await company_repo.save_company(db, ws, {"name": "Acme Pay", "links": [{"platform": "website", "url": url}]})

    asyncio.run(go())


def add_fact(ws, category="leadership", key="ceo", value="Amal Nasr", **fields):
    async def go():
        async with get_db() as db:
            values = dict(
                workspace_id=ws, category=category, key=key, value=value, source_tier=SourceTier.OWN_SITE,
                source_url="https://acme.example/about", quote=f"{value} is named on the page", confidence="high",
            )
            values.update(fields)
            return await facts_repo.create_fact(db, NewFact(**values))

    return asyncio.run(go())


def wait_for(client, run_id, until=("done", "failed", "cancelled"), stage=None, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = client.get(f"{RUNS}/{run_id}").json()
        if run["status"] in until and (stage is None or run["stage"] == stage):
            return run
        time.sleep(0.05)
    raise AssertionError(f"the run did not reach {until}: {run}")


ENDPOINTS = [
    ("post", RUNS, {}), ("get", f"{RUNS}/x", None), ("post", f"{RUNS}/x/cancel", None), ("get", FACTS, None),
    ("patch", f"{FACTS}/x", {"status": "approved"}), ("post", f"{FACTS}/bulk", {"ids": ["x"], "action": "approve"}),
    ("get", "/api/company/fact-categories", None),
]


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_every_endpoint_needs_a_session(client, method, path, body):
    assert getattr(client, method)(path, **({"json": body} if body is not None else {})).status_code == 401


# ------------------------------------------------------------------ runs


def test_starting_a_run_returns_it_and_it_can_be_followed_to_the_end(client, monkeypatch, tmp_path):
    ws = sign_in(client)
    save_website(ws)
    monkeypatch.setattr(research_manager, "deps", deps(acme_site(report_pdf(tmp_path)), Model(*ALL_RULES)))

    started = client.post(RUNS)
    assert started.status_code == 202, started.text
    body = started.json()
    assert body["status"] in ("queued", "running") and body["input"] == {"seed_url": HOME}
    assert "workspace_id" not in body
    done = wait_for(client, body["id"])
    assert done["status"] == "done" and done["facts_found"] == 5 and done["pages_crawled"] == 5 and done["pdfs_read"] == 1 and done["stage"] is None
    assert done["stats"]["extraction"]["created"] == 5 and done["finished_at"]
    grouped = client.get(FACTS).json()
    assert grouped["total"] == 5
    assert [c["category"] for c in grouped["categories"]] == ["leadership", "ownership_and_funding", "financials", "locations", "history"]
    assert row(settings.EDRAK_DB_PATH, "SELECT action FROM audit_log WHERE action LIKE 'company_research.%'") == [("company_research.started",)]


def test_a_seed_url_in_the_request_overrides_the_saved_website(client, monkeypatch):
    ws = sign_in(client)
    save_website(ws, "https://elsewhere.example/")
    monkeypatch.setattr(research_manager, "deps", deps(acme_site(), Model(*ALL_RULES)))
    started = client.post(RUNS, json={"seed_url": "acme.example"})
    assert started.status_code == 202 and started.json()["input"] == {"seed_url": HOME}
    assert wait_for(client, started.json()["id"])["status"] == "done"


def test_a_run_needs_a_website_and_a_public_address(client):
    ws = sign_in(client)
    none = client.post(RUNS)
    assert none.status_code == 422 and none.json()["detail"]["code"] == "no_website"
    for url in ("ftp://acme.example", "http://127.0.0.1/", "http://169.254.169.254/"):
        bad = client.post(RUNS, json={"seed_url": url})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_url", url
    assert client.get(FACTS).json()["total"] == 0
    save_website(ws)  # a run row is created only for a run that can start
    assert row(settings.EDRAK_DB_PATH, "SELECT count(*) FROM research_runs") == [(0,)]


def test_a_second_run_is_refused_while_one_is_active_and_the_first_can_be_cancelled(client, monkeypatch):
    ws = sign_in(client)
    save_website(ws)
    site, gate = acme_site(), asyncio.Event()
    monkeypatch.setattr(research_manager, "deps", deps(site, Model(*ALL_RULES), fetcher=held(site, gate)))

    first = client.post(RUNS).json()
    second = client.post(RUNS)
    assert second.status_code == 409
    assert second.json()["detail"] == {"code": "run_active", "message": "A research run is already in progress for this company.", "run_id": first["id"]}

    running = wait_for(client, first["id"], until=("running",), stage="crawl")
    assert running["pages_crawled"] == 0
    cancelled = client.post(f"{RUNS}/{first['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled" and cancelled.json()["finished_at"]
    assert client.get(f"{RUNS}/{first['id']}").json()["status"] == "cancelled"
    again = client.post(f"{RUNS}/{first['id']}/cancel")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "wrong_state"
    assert client.get(FACTS).json()["total"] == 0
    assert client.post(RUNS).status_code == 202  # a new run can start once the first has ended


def test_runs_belong_to_one_company(client):
    ws = sign_in(client)
    save_website(ws)
    run = asyncio.run(_queued_run(ws))
    outsider = other_user(client)
    assert client.get(f"{RUNS}/{run}").status_code == 200
    assert outsider.get(f"{RUNS}/{run}").status_code == 404
    assert outsider.post(f"{RUNS}/{run}/cancel").status_code == 404
    assert client.get(f"{RUNS}/nope").json()["detail"]["code"] == "research_run_not_found"
    assert outsider.get(f"{RUNS}/{run}").status_code == 404


async def _queued_run(ws):
    from edrak.db.repositories import research_runs as runs_repo

    async with get_db() as db:
        return (await runs_repo.create_research_run(db, workspace_id=ws)).id


# ------------------------------------------------------------------ categories and grouped facts


def test_the_category_list_has_both_labels(client):
    sign_in(client)
    body = client.get("/api/company/fact-categories").json()
    assert [c["key"] for c in body] == [c.key for c in CATEGORIES]
    assert body[0] == {"key": "leadership", "label_en": "Leadership", "label_ar": "القيادة"}
    assert all(c["label_en"] and c["label_ar"] for c in body)


def seeded(client):
    ws = sign_in(client)
    a = add_fact(ws, value="Amal Nasr", conflict_group="g1")
    b = add_fact(ws, value="Omar Hassan", source_tier=SourceTier.OWN_PDF, conflict_group="g1", pdf_page=3)
    cfo = add_fact(ws, key="cfo", value="Dina Fathy", status=FactStatus.APPROVED)
    revenue = add_fact(ws, category="financials", key="annual_revenue", value="EGP 1.2 billion", source_tier=SourceTier.THIRD_PARTY)
    novel = add_fact(ws, category="other", key="mascot", value="A camel", proposed_category="Brand")
    rejected = add_fact(ws, category="awards", key="award", value="Fintech Award", status=FactStatus.REJECTED)
    return ws, dict(a=a, b=b, cfo=cfo, revenue=revenue, novel=novel, rejected=rejected)


def test_facts_come_grouped_by_category_with_labels_and_conflicts_together(client):
    _, f = seeded(client)
    body = client.get(FACTS).json()
    assert body["total"] == 6
    assert [c["category"] for c in body["categories"]] == ["leadership", "financials", "awards", "other"]  # the category list's order
    leadership = body["categories"][0]
    assert (leadership["label_en"], leadership["label_ar"]) == ("Leadership", "القيادة")
    assert [x["id"] for x in leadership["facts"]] == [f["cfo"].id]
    [conflict] = leadership["conflicts"]
    assert conflict["conflict_group"] == "g1" and conflict["key"] == "ceo"
    assert {x["value"] for x in conflict["facts"]} == {"Amal Nasr", "Omar Hassan"}
    pdf = next(x for x in conflict["facts"] if x["source_tier"] == "own_pdf")
    assert pdf["pdf_page"] == 3 and pdf["quote"] and pdf["source_url"] and pdf["status"] == "suggested"
    assert (pdf["key_label_en"], pdf["key_label_ar"]) == ("Chief executive officer", "الرئيس التنفيذي")
    other = body["categories"][3]
    assert other["facts"][0]["proposed_category"] == "Brand" and other["facts"][0]["key_label_en"] is None


def test_facts_can_be_filtered_by_status_category_and_source_tier(client):
    _, f = seeded(client)

    def ids(query):
        body = client.get(f"{FACTS}?{query}").json()
        found = [x["id"] for c in body["categories"] for x in c["facts"]]
        found += [x["id"] for c in body["categories"] for g in c["conflicts"] for x in g["facts"]]
        return set(found), body["total"]

    assert ids("status=approved") == ({f["cfo"].id}, 1)
    assert ids("status=approved&status=rejected") == ({f["cfo"].id, f["rejected"].id}, 2)
    assert ids("category=financials") == ({f["revenue"].id}, 1)
    assert ids("category=financials&category=other") == ({f["revenue"].id, f["novel"].id}, 2)
    assert ids("source_tier=third_party") == ({f["revenue"].id}, 1)
    assert ids("source_tier=own_pdf&status=suggested") == ({f["b"].id}, 1)
    assert ids("category=leadership&status=suggested&source_tier=own_site")[0] == {f["a"].id}
    # A conflict needs two facts that pass the filters; a lone one is listed as a fact and keeps its group.
    lone = client.get(f"{FACTS}?source_tier=own_pdf").json()["categories"][0]
    assert lone["conflicts"] == [] and lone["facts"][0]["conflict_group"] == "g1"


def test_a_bad_filter_is_refused(client):
    sign_in(client)
    assert client.get(f"{FACTS}?category=nonsense").json()["detail"]["code"] == "unknown_category"
    assert client.get(f"{FACTS}?status=maybe").status_code == 422
    assert client.get(f"{FACTS}?source_tier=rumour").status_code == 422


def test_facts_of_another_company_are_never_shown(client):
    seeded(client)
    outsider = other_user(client)
    assert outsider.get(FACTS).json() == {"total": 0, "categories": []}


# ------------------------------------------------------------------ approve, reject, edit


def test_a_fact_can_be_approved_and_rejected(client):
    ws = sign_in(client)
    fact = add_fact(ws)
    approved = client.patch(f"{FACTS}/{fact.id}", json={"status": "approved"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved" and approved.json()["edited_by_user"] is False
    rejected = client.patch(f"{FACTS}/{fact.id}", json={"status": "rejected"})
    assert rejected.json()["status"] == "rejected" and rejected.json()["value"] == "Amal Nasr"
    actions = row(settings.EDRAK_DB_PATH, "SELECT action FROM audit_log WHERE action LIKE 'company_fact.%' ORDER BY id")
    assert actions == [("company_fact.approved",), ("company_fact.rejected",)]


def test_an_edit_changes_the_value_marks_it_edited_and_approves_it(client):
    ws = sign_in(client)
    fact = add_fact(ws, category="financials", key="annual_revenue", value="EGP 1.2 billion")
    edited = client.patch(f"{FACTS}/{fact.id}", json={"value": "EGP 1.3 billion"})
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["value"], body["status"], body["edited_by_user"]) == ("EGP 1.3 billion", "approved", True)
    assert body["quote"] == fact.quote and body["source_url"] == fact.source_url  # where it was found stays as it was
    assert row(settings.EDRAK_DB_PATH, "SELECT action FROM audit_log WHERE action = 'company_fact.edited'") == [("company_fact.edited",)]


def test_an_edit_can_change_the_value_type_and_a_list_must_be_valid(client):
    ws = sign_in(client)
    fact = add_fact(ws, category="partners", key="partner", value="Nile Bank")
    bad = client.patch(f"{FACTS}/{fact.id}", json={"value": "not a list", "value_type": "list"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_value"
    assert client.get(FACTS).json()["categories"][0]["facts"][0]["value"] == "Nile Bank"  # nothing changed
    assert client.get(FACTS).json()["categories"][0]["facts"][0]["status"] == "suggested"
    good = client.patch(f"{FACTS}/{fact.id}", json={"value": '["Nile Bank", "Delta Bank"]', "value_type": "list"})
    assert good.status_code == 200 and good.json()["value_type"] == "list" and good.json()["edited_by_user"] is True


@pytest.mark.parametrize("body,code", [
    ({}, "nothing_to_change"),
    ({"value": "  "}, "invalid_value"),
    ({"value": "x", "status": "rejected"}, "edit_and_reject"),
    ({"value_type": "number"}, "type_without_value"),
])
def test_a_bad_change_is_refused_and_changes_nothing(client, body, code):
    ws = sign_in(client)
    fact = add_fact(ws)
    resp = client.patch(f"{FACTS}/{fact.id}", json=body)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == code
    stored = client.get(FACTS).json()["categories"][0]["facts"][0]
    assert (stored["value"], stored["status"], stored["edited_by_user"]) == ("Amal Nasr", "suggested", False)


def test_only_approved_or_rejected_can_be_set_and_unknown_or_foreign_facts_are_404(client):
    ws = sign_in(client)
    fact = add_fact(ws)
    assert client.patch(f"{FACTS}/{fact.id}", json={"status": "suggested"}).status_code == 422
    assert client.patch(f"{FACTS}/nope", json={"status": "approved"}).json()["detail"]["code"] == "fact_not_found"
    outsider = other_user(client)
    assert outsider.patch(f"{FACTS}/{fact.id}", json={"status": "approved"}).status_code == 404
    assert outsider.patch(f"{FACTS}/{fact.id}", json={"value": "Hijacked"}).status_code == 404
    assert client.get(FACTS).json()["categories"][0]["facts"][0]["status"] == "suggested"


# ------------------------------------------------------------------ bulk


def test_many_facts_can_be_approved_at_once(client):
    ws = sign_in(client)
    facts = [add_fact(ws, key="ceo", value=f"Person {i}") for i in range(3)]
    resp = client.post(f"{FACTS}/bulk", json={"ids": [f.id for f in facts], "action": "approve"})
    assert resp.status_code == 200
    assert resp.json() == {"action": "approve", "updated": [f.id for f in facts], "not_found": []}
    assert client.get(f"{FACTS}?status=approved").json()["total"] == 3
    assert row(settings.EDRAK_DB_PATH, "SELECT action, metadata FROM audit_log WHERE action = 'company_fact.bulk_approve'")[0][0] == "company_fact.bulk_approve"


def test_many_facts_can_be_rejected_at_once_and_missing_ids_are_reported(client):
    ws = sign_in(client)
    one, two = add_fact(ws, value="A"), add_fact(ws, value="B")
    resp = client.post(f"{FACTS}/bulk", json={"ids": [one.id, "ghost", two.id, one.id], "action": "reject"})
    assert resp.json() == {"action": "reject", "updated": [one.id, two.id], "not_found": ["ghost"]}
    assert client.get(f"{FACTS}?status=rejected").json()["total"] == 2


def test_bulk_never_touches_another_companys_facts(client):
    ws = sign_in(client)
    mine = add_fact(ws)
    outsider = other_user(client)
    resp = outsider.post(f"{FACTS}/bulk", json={"ids": [mine.id], "action": "approve"})
    assert resp.json() == {"action": "approve", "updated": [], "not_found": [mine.id]}
    assert client.get(FACTS).json()["categories"][0]["facts"][0]["status"] == "suggested"


@pytest.mark.parametrize("body", [{"ids": [], "action": "approve"}, {"ids": ["a"], "action": "delete"}, {"ids": [str(i) for i in range(501)], "action": "approve"}, {"action": "approve"}])
def test_a_bad_bulk_request_is_refused(client, body):
    sign_in(client)
    assert client.post(f"{FACTS}/bulk", json=body).status_code == 422


def test_the_endpoints_are_in_the_openapi_document(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path, methods in {
        "/api/company/research/runs": {"post"}, "/api/company/research/runs/{run_id}": {"get"},
        "/api/company/research/runs/{run_id}/cancel": {"post"}, "/api/company/facts": {"get"},
        "/api/company/facts/{fact_id}": {"patch"}, "/api/company/facts/bulk": {"post"}, "/api/company/fact-categories": {"get"},
    }.items():
        assert methods <= set(paths[path]), path
