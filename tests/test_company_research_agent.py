"""The company research graph and its runs, with the network and the model replaced."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from agent_fixtures import (
    AWARD, OWNER_QUOTE, REVENUE_QUOTE, held, BRANCH, CEO_QUOTE, FOUNDED, HOME, OWNER, REVENUE, Model, acme_site, ceo, company, deps, options, page,
    report_pdf, research, row, run_it,
)
from crawl_fixtures import PRIVATE_HOST, Site, limits as crawl_limits

from edrak.company_profile.extractor import ProfileLLMError
from edrak.company_profile.url_import import UrlBlocked
from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_research import FactStatus, NewFact, ResearchRunStatus, SourceTier
from edrak.company_research.agent import NoWebsite, ResearchOptions, ResearchRunManager, SearchHit, SearchUnavailable
from edrak.company_research.agent import nodes as agent_nodes
from edrak.company_research.agent.gaps import empty_categories, plan_targets, relevance
from edrak.company_research.crawler import CrawledPage, FetchMode
from edrak.core.config import settings
from edrak.db.connection import get_db
from edrak.db.repositories import company_facts as facts_repo
from edrak.db.repositories import research_runs as runs_repo
from edrak.company_profile.url_import.parse import Link

ALL = (ceo(), FOUNDED, BRANCH, OWNER, REVENUE)


def by_key(facts):
    return {f.key: f for f in facts}


# ------------------------------------------------------------------ the whole graph


def test_the_graph_reads_pages_and_a_pdf_and_saves_suggested_facts(research_db_path, tmp_path):
    site = acme_site(report_pdf(tmp_path))
    _, run, facts = run_it(site, Model(*ALL))

    assert run.status == ResearchRunStatus.DONE and run.error is None
    assert run.started_at and run.finished_at and run.stage is None
    assert (run.pages_crawled, run.pdfs_read, run.facts_found) == (5, 1, 5)
    found = by_key(facts)
    assert set(found) == {"ceo", "founded_year", "branch", "shareholder", "annual_revenue"}
    assert all(f.status == FactStatus.SUGGESTED and f.run_id == run.id for f in facts)
    assert found["ceo"].source_tier == SourceTier.OWN_SITE and found["ceo"].source_url == HOME + "leadership-team"
    assert found["annual_revenue"].source_tier == SourceTier.OWN_PDF and found["annual_revenue"].pdf_page == 1
    assert found["ceo"].quote == CEO_QUOTE
    assert run.stats["crawl"]["pages_crawled"] == 5 and run.stats["pdfs"]["read"] == 1
    assert run.stats["extraction"]["created"] == 5 and run.stats["rounds"] == 0


def test_the_run_record_shows_progress_while_it_runs(research_db_path, tmp_path):
    seen = []
    path = settings.EDRAK_DB_PATH
    model = Model(*ALL, on_call=lambda: seen.append(row(path, "SELECT status, stage, pages_crawled, pdfs_read FROM research_runs")[0]))
    run_it(acme_site(report_pdf(tmp_path)), model)
    assert seen == [("running", "extract", 5, 1)]


def test_a_quote_that_is_not_on_any_page_yields_no_fact(research_db_path):
    """The model cites a page for a quote that page does not contain. Quotes are checked against the sources."""
    invented = json.dumps({"facts": [{
        "category": "team", "key": "headcount", "label": "Staff", "value": "900", "value_type": "number",
        "source": "S1", "quote": "Acme Pay has 900 employees across Egypt", "confidence": "high",
    }]})
    _, run, facts = run_it(acme_site(), lambda messages: invented)
    assert facts == [] and run.status == ResearchRunStatus.DONE
    assert run.stats["extraction"]["facts_returned"] >= 1 and run.stats["extraction"]["dropped_no_quote"] >= 1


def test_the_seed_is_the_saved_website_or_the_request(research_db_path):
    site = acme_site()

    async def go():
        async with get_db() as db:
            saved = await company(db, website="https://acme.example/")
            none = await company(db, website=None, name="No Site")
        ran = await research(site, Model(), ws=saved)
        manager = ResearchRunManager(deps(site, Model()))
        with pytest.raises(NoWebsite):
            await manager.start(none)
        override = await research(acme_site(), Model(), ws=none, seed_url="acme.example")
        return ran, override

    ran, override = asyncio.run(go())
    assert ran[1].input == {"seed_url": "https://acme.example/"} and ran[1].pages_crawled == 5
    assert override[1].input == {"seed_url": "https://acme.example/"} and override[1].status == ResearchRunStatus.DONE


@pytest.mark.parametrize("url", ["ftp://acme.example", "http://127.0.0.1/", "http://localhost:8000/"])
def test_a_seed_that_is_not_a_public_website_is_refused_before_a_run_exists(research_db_path, url):
    async def go():
        async with get_db() as db:
            ws = await company(db, website=None)
        with pytest.raises(UrlBlocked):
            await ResearchRunManager(deps(acme_site(), Model())).start(ws, seed_url=url)
        async with get_db() as db:
            return await runs_repo.list_research_runs(db, ws)

    assert asyncio.run(go()) == []


def test_a_homepage_that_cannot_be_read_fails_the_run_with_a_reason(research_db_path):
    site = Site({"/": (500, "", "text/plain")})
    _, run, facts = run_it(site, Model(*ALL))
    assert run.status == ResearchRunStatus.FAILED and "could not be read" in run.error
    assert facts == [] and run.warnings and run.finished_at


def test_a_private_address_behind_the_website_is_never_fetched(research_db_path):
    site = Site({"/": (302, "", f"https://{PRIVATE_HOST}/secret")})
    _, run, _ = run_it(site, Model(*ALL))
    assert run.status == ResearchRunStatus.FAILED
    assert all(host != PRIVATE_HOST for host, _ in site.requests)


def test_a_model_that_cannot_be_reached_fails_the_run(research_db_path):
    model = Model(fail=ProfileLLMError("down"))
    _, run, facts = run_it(acme_site(), model)
    assert run.status == ResearchRunStatus.FAILED and run.error == "The language model could not read any source."
    assert facts == [] and any("could not be reached" in w for w in run.warnings)


def test_one_batch_failing_does_not_fail_the_run(research_db_path):
    site = acme_site()
    good = Model(*ALL)
    calls = []

    def flaky(messages):
        calls.append(1)
        if len(calls) == 1:
            raise ProfileLLMError("blip")
        return good(messages)

    async def go():
        small = deps(site, flaky).extraction_limits
        small.batch_chars = 1000  # one document per call
        return await research(site, flaky, extraction_limits=small)

    _, run, facts = asyncio.run(go())
    assert run.status == ResearchRunStatus.DONE and facts
    assert any("could not be reached" in w for w in run.warnings)


def test_a_step_that_breaks_adds_a_warning_and_the_run_goes_on(research_db_path, tmp_path, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("the PDF reader crashed")

    monkeypatch.setattr(agent_nodes, "read_pdfs", broken)
    _, run, facts = run_it(acme_site(report_pdf(tmp_path)), Model(*ALL))
    assert run.status == ResearchRunStatus.DONE
    assert any(w.startswith("The pdfs step stopped with an unexpected error") for w in run.warnings)
    assert "RuntimeError" in " ".join(run.warnings) and "crashed" not in " ".join(run.warnings)
    assert "annual_revenue" not in by_key(facts) and "ceo" in by_key(facts)  # the pages were still extracted and saved


def test_a_crash_while_reading_extra_pages_keeps_what_the_first_round_found(research_db_path, monkeypatch):
    real, calls = agent_nodes.crawl_company_site, []

    async def crash_after_the_first(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("the browser died")
        return await real(*args, **kwargs)

    monkeypatch.setattr(agent_nodes, "crawl_company_site", crash_after_the_first)
    _, run, facts = run_it(acme_site(), Model(*ALL), **home_only())
    assert run.status == ResearchRunStatus.DONE and run.error is None and len(calls) == 2
    assert "founded_year" in by_key(facts) and "ceo" not in by_key(facts)  # the home page was kept; the extra page was not read
    assert any("crawl step stopped with an unexpected error (RuntimeError)" in w for w in run.warnings)
    assert "browser died" not in " ".join(run.warnings)


def test_a_crash_while_reading_the_first_pages_fails_the_run(research_db_path, monkeypatch):
    async def crash(*args, **kwargs):
        raise RuntimeError("the browser died")

    monkeypatch.setattr(agent_nodes, "crawl_company_site", crash)
    _, run, facts = run_it(acme_site(), Model(*ALL))
    assert run.status == ResearchRunStatus.FAILED and "crawl step stopped" in run.error and facts == []


def test_a_gap_check_that_fails_does_not_send_the_run_round_again_with_old_targets(research_db_path, monkeypatch):
    real, calls = agent_nodes.empty_categories, []

    def fails_the_second_time(have):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("bad state")
        return real(have)

    monkeypatch.setattr(agent_nodes, "empty_categories", fails_the_second_time)
    site = acme_site()
    _, run, facts = run_it(site, Model(*ALL), **home_only())
    assert run.status == ResearchRunStatus.DONE and len(calls) == 2
    assert run.stats["rounds"] == 1 and run.pages_crawled == 4  # one extra round; the failed check chose nothing more
    assert any("gap check step stopped with an unexpected error" in w for w in run.warnings)
    assert {"ceo", "branch", "shareholder"} <= set(by_key(facts))


def test_warnings_are_listed_once_and_capped():
    many = [f"Skipped file {i}." for i in range(150)]
    tidy = agent_nodes.tidy_warnings(["same", "same", *many])
    assert tidy[0] == "same" and len(tidy) == agent_nodes.MAX_WARNINGS + 1
    assert tidy[-1] == "51 more warning(s) were left out."
    assert agent_nodes.tidy_warnings(["a", "b", "a"]) == ["a", "b"]


def test_a_failure_while_saving_fails_the_run(research_db_path, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(agent_nodes, "apply_extraction", broken)
    _, run, facts = run_it(acme_site(), Model(*ALL))
    assert run.status == ResearchRunStatus.FAILED and "merge and save" in run.error and facts == []


# ------------------------------------------------------------------ the gap loop


def home_only() -> dict:
    return dict(crawl_limits=crawl_limits(max_pages=1))


def test_important_gaps_send_the_run_back_to_the_site_for_pages_it_did_not_read(research_db_path):
    site = acme_site()
    _, run, facts = run_it(site, Model(*ALL), **home_only())
    found = by_key(facts)
    assert {"ceo", "branch", "shareholder", "founded_year"} <= set(found)  # the last three were read in the extra round
    assert run.stats["rounds"] == 1 and run.pages_crawled == 4 and run.stats["crawl"]["pages_crawled"] == 4
    assert site.paths.count("/leadership-team") == 1 and "/locations" in site.paths and "/investors" in site.paths
    assert "/about" not in site.paths  # history was not a gap: the home page had a founding year


def test_the_gap_loop_stops_at_two_extra_rounds_even_when_gaps_remain(research_db_path, monkeypatch):
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_GAP_MAX_ROUNDS", 9)
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_GAP_MAX_TARGETS", 1)
    assert ResearchOptions.from_settings().gap_rounds == 2
    site = acme_site()
    _, run, facts = run_it(site, Model(), options=ResearchOptions.from_settings(), **home_only())  # the model finds nothing
    assert run.stats["rounds"] == 2 and run.pages_crawled == 3  # the home page and one page per extra round
    assert facts == [] and run.status == ResearchRunStatus.DONE
    extra = [p for p in site.paths if p not in ("/", "/robots.txt", "/sitemap.xml") and not p.endswith(".pdf")]
    assert extra == ["/leadership-team", "/investors"]  # two more matching pages (locations, about) were not read


def test_the_gap_loop_does_not_run_when_important_categories_are_filled(research_db_path):
    site = acme_site()
    _, run, _ = run_it(site, Model(*ALL))
    assert run.stats["rounds"] == 0 and run.pages_crawled == 5


def test_the_gap_loop_can_be_turned_off_and_respects_the_time_budget(research_db_path):
    _, off, _ = run_it(acme_site(), Model(), options=options(gap_rounds=0), **home_only())
    _, late, _ = run_it(acme_site(), Model(), options=options(time_budget_s=0), **home_only())
    assert off.stats["rounds"] == 0 and off.pages_crawled == 1
    assert late.stats["rounds"] == 0 and late.pages_crawled == 1


def test_an_extra_page_that_cannot_be_read_is_a_warning(research_db_path):
    site = acme_site(**{"/leadership-team": (500, "", "text/plain")})
    _, run, facts = run_it(site, Model(*ALL), **home_only())
    assert run.status == ResearchRunStatus.DONE and "ceo" not in by_key(facts) and "branch" in by_key(facts)
    assert any("could not be read" in w for w in run.warnings)


def test_a_different_value_found_in_a_later_round_joins_the_earlier_one_in_a_conflict_group(research_db_path):
    other = "Omar Hassan is the Chief Executive Officer of Acme Pay"
    site = acme_site(**{
        "/": page("Acme Pay", f"{other}.", "<a href='/investors'>Investor relations</a>"),
        "/investors": page("Investor relations", f"{OWNER_QUOTE}. {CEO_QUOTE}."),
    })
    rules = (ceo("Omar Hassan", other), ceo(), OWNER)
    _, run, facts = run_it(site, Model(*rules), **home_only())
    ceos = [f for f in facts if f.key == "ceo"]
    assert {f.value for f in ceos} == {"Omar Hassan", "Amal Nasr"}
    assert ceos[0].conflict_group and ceos[0].conflict_group == ceos[1].conflict_group
    assert run.stats["rounds"] == 1


def test_a_better_source_found_in_a_later_round_replaces_the_earlier_suggestion(research_db_path, tmp_path):
    """The PDF gave the revenue in round 0; the company's own investors page gives it again in round 1."""
    site = acme_site(report_pdf(tmp_path), **{"/investors": page("Investor relations", f"{OWNER_QUOTE}. {REVENUE_QUOTE}.")})
    _, run, facts = run_it(site, Model(OWNER, REVENUE), **home_only())
    revenue = [f for f in facts if f.key == "annual_revenue"]
    assert run.stats["rounds"] == 1 and run.pdfs_read == 1
    assert len(revenue) == 1 and revenue[0].source_tier == SourceTier.OWN_SITE and revenue[0].source_url == HOME + "investors"


def test_an_extra_page_that_redirects_off_the_company_site_is_not_read_as_its_own(research_db_path):
    stranger = "Dana Reda is the Chief Executive Officer of Acme Pay"
    site = acme_site(**{
        "/leadership-team": (302, "", "https://other.example/leadership"),
        "other.example/leadership": page("Directory", f"{stranger}."),
    })
    _, run, facts = run_it(site, Model(ceo("Dana Reda", stranger), BRANCH), **home_only())
    assert "ceo" not in by_key(facts) and "branch" in by_key(facts)
    assert any("redirected away from the company's website" in w for w in run.warnings)


def test_a_stored_approved_fact_is_never_overwritten_by_a_run(research_db_path):
    async def go():
        async with get_db() as db:
            ws = await company(db)
            approved = await facts_repo.create_fact(db, NewFact(
                workspace_id=ws, category="leadership", key="ceo", value="Old Name", source_tier=SourceTier.OWN_SITE,
                source_url=HOME + "old", quote="Old Name is the CEO", confidence="high", status=FactStatus.APPROVED,
            ))
        _, run, facts = await research(acme_site(), Model(*ALL), ws=ws)
        return approved, run, facts

    approved, run, facts = asyncio.run(go())
    stored = {f.id: f for f in facts}
    assert stored[approved.id].value == "Old Name" and stored[approved.id].status == FactStatus.APPROVED
    new = [f for f in facts if f.key == "ceo" and f.id != approved.id]
    assert len(new) == 1 and new[0].status == FactStatus.SUGGESTED and new[0].value == "Amal Nasr"
    assert new[0].conflict_group and new[0].conflict_group == stored[approved.id].conflict_group


# ------------------------------------------------------------------ own social profiles


def test_the_social_step_is_skipped_with_a_warning_when_no_tool_reads_profiles(research_db_path):
    _, run, facts = run_it(acme_site(), Model(*ALL))
    social = [w for w in run.warnings if "social profiles were not read" in w]
    assert len(social) == 1 and "without a paid service or a login" in social[0]
    assert all(f.source_tier != SourceTier.OWN_SOCIAL for f in facts)


def test_the_social_step_does_nothing_when_no_category_is_empty(research_db_path):
    nodes = agent_nodes.Nodes(deps(acme_site(), Model()))
    assert asyncio.run(nodes.own_social({"empty": []})) == {}  # no warning either: there is nothing to fill


def test_the_social_step_reads_a_company_profile_when_a_reader_exists(research_db_path):
    read = []

    async def reader(platform, url):
        read.append((platform, url))
        return "Acme Pay won the Fintech Award in 2022 and shares news here."

    async def go():
        async with get_db() as db:
            ws = await company(db)
            from edrak.db.repositories import company as company_repo

            await company_repo.save_company(db, ws, {"links": [
                {"platform": "website", "url": HOME}, {"platform": "x", "url": "https://x.com/acmepay"},
            ]})
        return await research(acme_site(), Model(*ALL, AWARD), ws=ws, social_reader=reader)

    _, run, facts = asyncio.run(go())
    assert read == [("x", "https://x.com/acmepay")]
    award = by_key(facts)["award"]
    assert award.source_tier == SourceTier.OWN_SOCIAL and award.source_url == "https://x.com/acmepay"
    assert not any("social profiles were not read" in w for w in run.warnings)


def test_a_social_profile_that_cannot_be_read_is_a_warning(research_db_path):
    async def reader(platform, url):
        raise RuntimeError("blocked")

    async def go():
        async with get_db() as db:
            ws = await company(db)
            from edrak.db.repositories import company as company_repo

            await company_repo.save_company(db, ws, {"links": [
                {"platform": "website", "url": HOME}, {"platform": "x", "url": "https://x.com/acmepay"},
            ]})
        return await research(acme_site(), Model(*ALL), ws=ws, social_reader=reader)

    _, run, facts = asyncio.run(go())
    assert run.status == ResearchRunStatus.DONE and any("x profile could not be read" in w for w in run.warnings)


# ------------------------------------------------------------------ third-party sources


OTHER_NEWS = "Acme Pay won the Fintech Award in 2022"


def news_site(**extra) -> Site:
    return acme_site(**{
        "other.example/story": page("Fintech awards", f"{OTHER_NEWS}, the judges said."), **extra,
    })


def test_the_third_party_tier_is_off_by_default():
    assert settings.COMPANY_RESEARCH_THIRD_PARTY is False
    assert ResearchOptions.from_settings().third_party is False


def test_no_search_is_made_when_the_flag_is_off(research_db_path):
    searches = []

    async def search(query, num):
        searches.append(query)
        return [SearchHit("https://other.example/story")]

    _, run, facts = run_it(news_site(), Model(*ALL, AWARD), search=search)
    assert searches == [] and run.stats["third_party"] == {}
    assert all(f.source_tier != SourceTier.THIRD_PARTY for f in facts)


def test_with_the_flag_on_empty_categories_are_searched_and_facts_are_marked_third_party(research_db_path):
    searches = []

    async def search(query, num):
        searches.append(query)
        return [SearchHit("https://other.example/story", "Fintech awards"), SearchHit("https://acme.example/about"),
                SearchHit("https://x.com/acmepay"), SearchHit("https://other.example/report.pdf")]

    site = news_site()
    _, run, facts = run_it(site, Model(*ALL, AWARD), options=options(third_party=True, third_party_searches=2), search=search)
    assert searches == ["Acme Pay Financials", "Acme Pay Customers"]  # the first empty categories, company name first
    award = by_key(facts)["award"]
    assert award.source_tier == SourceTier.THIRD_PARTY and award.source_url == "https://other.example/story"
    assert run.stats["third_party"] == {"searches": 2, "pages": 1}
    fetched = [(h, p) for h, p in site.requests if h == "other.example"]
    assert ("other.example", "/story") in fetched and ("other.example", "/report.pdf") not in fetched
    assert ("x.com", "/acmepay") not in site.requests


def test_third_party_facts_never_replace_the_companys_own(research_db_path):
    other = "Dana Reda is the Chief Executive Officer of Acme Pay"
    site = acme_site(**{"other.example/story": page("Profile", f"{other}, according to a directory.")})

    async def search(query, num):
        return [SearchHit("https://other.example/story")]

    # The company's own pages say Amal Nasr; the directory says someone else. Both are kept, in one group.
    rules = (ceo(), ceo("Dana Reda", other), FOUNDED, BRANCH, OWNER)
    _, run, facts = run_it(site, Model(*rules), options=options(third_party=True, third_party_searches=1), search=search)
    ceos = sorted((f for f in facts if f.key == "ceo"), key=lambda f: f.source_tier.value)
    assert [(f.value, f.source_tier) for f in ceos] == [("Amal Nasr", SourceTier.OWN_SITE), ("Dana Reda", SourceTier.THIRD_PARTY)]
    assert ceos[0].conflict_group == ceos[1].conflict_group and ceos[0].conflict_group


def test_a_missing_search_key_skips_the_third_party_step_with_a_warning(research_db_path):
    async def search(query, num):
        raise SearchUnavailable("SERPER_API_KEY is not set.")

    _, run, facts = run_it(news_site(), Model(*ALL), options=options(third_party=True), search=search)
    assert run.status == ResearchRunStatus.DONE and facts
    assert "Third-party search was skipped: SERPER_API_KEY is not set." in run.warnings


def test_a_search_that_fails_is_a_warning_and_the_next_search_still_runs(research_db_path):
    calls = []

    async def search(query, num):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("quota")
        return [SearchHit("https://other.example/story")]

    _, run, facts = run_it(news_site(), Model(*ALL, AWARD), options=options(third_party=True, third_party_searches=2), search=search)
    assert len(calls) == 2 and any("search for Financials failed" in w for w in run.warnings)
    assert "award" in by_key(facts)


def test_a_third_party_page_is_fetched_with_the_same_safeguards(research_db_path):
    async def search(query, num):
        return [SearchHit("http://127.0.0.1/story"), SearchHit("http://169.254.169.254/latest"), SearchHit("https://other.example/gone")]

    site = news_site()
    _, run, _ = run_it(site, Model(*ALL), options=options(third_party=True, third_party_searches=1), search=search)
    assert all(host not in ("127.0.0.1", "169.254.169.254") for host, _ in site.requests)
    assert sum("third-party page on" in w and "could not be read" in w for w in run.warnings) == 3


# ------------------------------------------------------------------ one run at a time, and cancelling


def test_a_run_that_hangs_is_stopped_at_its_time_limit_and_saves_nothing(research_db_path):
    async def go():
        site, gate = acme_site(), asyncio.Event()  # the homepage never answers
        manager = ResearchRunManager(deps(site, Model(*ALL), fetcher=held(site, gate), options=options(max_run_s=0.3)))
        async with get_db() as db:
            ws = await company(db)
        run = await manager.start(ws)
        await manager.wait(run.id)
        async with get_db() as db:
            return manager, run, await runs_repo.get_research_run(db, ws, run.id), await facts_repo.list_facts(db, ws)

    manager, run, stored, facts = asyncio.run(go())
    assert stored.status == ResearchRunStatus.FAILED and "time limit" in stored.error and facts == []
    assert not manager.is_active(run.id)


def test_the_run_time_limit_comes_from_config(monkeypatch):
    assert settings.COMPANY_RESEARCH_RUN_MAX_S == 3600.0
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_RUN_MAX_S", 60.0)
    assert ResearchOptions.from_settings().max_run_s == 60.0


def test_a_company_has_one_active_run_at_a_time(research_db_path):
    async def go():
        site, gate = acme_site(), asyncio.Event()
        manager = ResearchRunManager(deps(site, Model(*ALL), fetcher=held(site, gate)))
        async with get_db() as db:
            ws, other = await company(db), await company(db, name="Other Co")
        first = await manager.start(ws)
        with pytest.raises(runs_repo.ResearchRunActive):
            await manager.start(ws)
        elsewhere = await manager.start(other)  # another company is independent
        assert manager.is_active(first.id) and manager.is_active(elsewhere.id)
        gate.set()
        await manager.wait(first.id)
        await manager.wait(elsewhere.id)
        again = await manager.start(ws)  # allowed once the first has ended
        await manager.wait(again.id)
        async with get_db() as db:
            return await runs_repo.list_research_runs(db, ws)

    runs = asyncio.run(go())
    assert [r.status for r in runs] == [ResearchRunStatus.DONE, ResearchRunStatus.DONE]


def test_the_database_allows_only_one_active_run_per_workspace(research_db_path):
    async def go():
        async with get_db() as db:
            ws = await company(db)
            await runs_repo.create_research_run(db, workspace_id=ws)
            with pytest.raises(runs_repo.ResearchRunActive):
                await runs_repo.create_research_run(db, workspace_id=ws)
            active = await runs_repo.active_research_run(db, ws)
            await runs_repo.finish_research_run(db, active.id, status=ResearchRunStatus.FAILED)
            await runs_repo.create_research_run(db, workspace_id=ws)

    asyncio.run(go())


def test_cancelling_stops_the_run_and_saves_nothing(research_db_path):
    async def go():
        site, gate = acme_site(), asyncio.Event()
        manager = ResearchRunManager(deps(site, Model(*ALL), fetcher=held(site, gate)))
        async with get_db() as db:
            ws = await company(db)
        run = await manager.start(ws)
        await asyncio.sleep(0.05)
        assert manager.is_active(run.id)
        assert await manager.cancel(ws, run.id) is True
        gate.set()  # too late: the run is gone
        await asyncio.sleep(0.05)
        async with get_db() as db:
            stored = await runs_repo.get_research_run(db, ws, run.id)
            facts = await facts_repo.list_facts(db, ws)
        return manager, run, stored, facts, await manager.cancel(ws, run.id)

    manager, run, stored, facts, again = asyncio.run(go())
    assert stored.status == ResearchRunStatus.CANCELLED and stored.finished_at and stored.stage is None
    assert facts == [] and not manager.is_active(run.id) and again is False


def test_cancelling_a_run_that_belongs_to_another_company_or_has_ended_does_nothing(research_db_path):
    async def go():
        manager = ResearchRunManager(deps(acme_site(), Model(*ALL)))
        async with get_db() as db:
            ws, other = await company(db), await company(db, name="Other Co")
        run = await manager.start(ws)
        await manager.wait(run.id)
        return await manager.cancel(ws, run.id), await manager.cancel(other, run.id), await manager.cancel(ws, "nope")

    assert asyncio.run(go()) == (False, False, False)


def test_a_cancelled_run_is_not_overwritten_by_a_late_finish(research_db_path):
    async def go():
        async with get_db() as db:
            ws = await company(db)
            run = await runs_repo.create_research_run(db, workspace_id=ws)
            await runs_repo.finish_research_run(db, run.id, status=ResearchRunStatus.CANCELLED)
            await runs_repo.finish_research_run(db, run.id, status=ResearchRunStatus.DONE)
            await runs_repo.update_research_run(db, run.id, status=ResearchRunStatus.RUNNING, pages_crawled=9)
            return await runs_repo.get_research_run(db, ws, run.id)

    stored = asyncio.run(go())
    assert stored.status == ResearchRunStatus.CANCELLED and stored.pages_crawled == 0


def test_a_run_left_running_by_a_restart_is_failed(research_db_path):
    async def go():
        async with get_db() as db:
            ws = await company(db)
            run = await runs_repo.create_research_run(db, workspace_id=ws)
            ids = await runs_repo.fail_orphaned_research_runs(db)
            return run, ids, await runs_repo.get_research_run(db, ws, run.id)

    run, ids, stored = asyncio.run(go())
    assert ids == [run.id] and stored.status == ResearchRunStatus.FAILED and "restarted" in stored.error


def test_an_unexpected_error_in_the_graph_fails_the_run_without_leaking_details(research_db_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret internals")

    monkeypatch.setattr("edrak.company_research.agent.manager.build_research_graph", broken)
    _, run, _ = run_it(acme_site(), Model())
    assert run.status == ResearchRunStatus.FAILED and run.error == "The research run stopped with an unexpected error."


# ------------------------------------------------------------------ gap planning


def crawled(url, *links):
    return CrawledPage(url, "t", "en", "text", FetchMode.STATIC, links=[Link(u, t) for u, t in links])


def test_empty_categories_lists_the_gaps_in_order_and_never_other():
    gaps = empty_categories({"leadership", "other"})
    assert "leadership" not in gaps and "other" not in gaps and gaps[0] == "ownership_and_funding"


def test_a_link_is_matched_by_its_text_and_its_address_in_both_languages():
    assert relevance("leadership", Link("https://acme.example/x", "Our Leadership")) == 2
    assert relevance("leadership", Link("https://acme.example/management", "")) == 1
    assert relevance("leadership", Link("https://acme.example/leadership", "Leadership")) == 3
    assert relevance("leadership", Link("https://acme.example/m", "مجلس الإدارة")) >= 2
    assert relevance("leadership", Link("https://acme.example/products", "Products")) == 0
    assert relevance("locations", Link("https://acme.example/products", "Products")) == 0


def test_targets_are_unvisited_on_the_company_site_and_skip_logins_files_and_social():
    home = crawled(HOME, ("https://acme.example/leadership", "Leadership"), ("https://acme.example/about", "About us"),
                   ("https://acme.example/login", "Leadership login"), ("https://other.example/leadership", "Leadership"),
                   ("https://x.com/acmepay", "Leadership on X"), ("https://acme.example/board.zip", "Board"),
                   ("https://acme.example/products", "Products"))
    got = plan_targets(["leadership", "history"], [home], HOME, visited=[], already_chosen=[], limit=5)
    assert got == ["https://acme.example/leadership", "https://acme.example/about"]
    again = plan_targets(["leadership"], [home], HOME, visited=["acme.example/about"], already_chosen=got, limit=5)
    assert again == []  # chosen and read pages are not chosen again


def test_targets_are_shared_between_categories_and_limited():
    home = crawled(HOME, ("https://acme.example/leadership", "Leadership"), ("https://acme.example/management", "Management"),
                   ("https://acme.example/branches", "Branches"), ("https://acme.example/history", "Our history"))
    got = plan_targets(["leadership", "locations", "history"], [home], HOME, [], [], limit=3)
    assert got == ["https://acme.example/leadership", "https://acme.example/branches", "https://acme.example/history"]
