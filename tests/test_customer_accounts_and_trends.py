"""The customer worker reads official accounts and always collects search interest."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from edrak.agents.customer_trends import nodes
from edrak.agents.customer_trends.providers.base import make_capability
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.schemas.common import Platform, UseCase
from edrak.agents.customer_trends.schemas.task import QueryPlan, TaskBrief, brief_from_task
from edrak.agents.customer_trends.tools.account_posts import account_target
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    CompetitorRef,
    ResearchTask,
    SocialAccount,
    WorkerType,
)


def _task(**context) -> ResearchTask:
    return ResearchTask(
        parent_request_id="r1",
        worker=WorkerType.CUSTOMER_TRENDS,
        goal="What do merchants say about online payments?",
        focus="Pain points and demand",
        company_profile=CompanyProfile(
            name="Paymob",
            social_accounts=[
                SocialAccount(platform="x", url="https://x.com/paymob"),
                SocialAccount(platform="linkedin", url="https://www.linkedin.com/company/paymob"),
            ],
        ),
        business_context=BusinessContext(**{"use_case": "competitive_intelligence", **context}),
    )


def test_the_brief_lists_the_company_and_competitor_accounts():
    brief = brief_from_task(_task(
        targets=["Fawry"],
        competitors=[CompetitorRef(name="Fawry", social_accounts=[
            SocialAccount(platform="instagram", url="https://www.instagram.com/fawrypayments"),
            SocialAccount(platform="facebook", url="https://www.facebook.com/Fawrypayments"),
        ])],
        markets=["Egypt"],
        offering="Online checkout",
    ))
    assert brief.geo == "EG" and brief.market == "Egypt" and brief.offering == "Online checkout"
    assert [(a.owner, a.platform.value, a.is_entity) for a in brief.accounts] == [
        ("Paymob", "x", True), ("Fawry", "instagram", False), ("Fawry", "facebook", False),
    ]  # LinkedIn is not read (no actor), the company comes first


def test_market_entry_keeps_competitors_as_competitors():
    task = _task(use_case="market_entry_expansion", targets=["Fawry"], markets=["Saudi Arabia"])
    brief = brief_from_task(task)
    assert brief.use_case is UseCase.MARKET_ENTRY_EXPANSION
    assert brief.competitors == ["Fawry"] and brief.market == "Saudi Arabia" and brief.geo == "SA"


def test_account_targets():
    assert account_target(Platform.X, "https://twitter.com/@Paymob_eg") == "Paymob_eg"
    assert account_target(Platform.INSTAGRAM, "instagram.com/fawry") == "https://instagram.com/fawry"
    with pytest.raises(ValueError):
        account_target(Platform.X, "https://www.facebook.com/paymob")


def test_account_posts_is_a_known_capability_routed_to_apify():
    assert make_capability("account_posts", "instagram") == "account_posts:instagram"
    config = load_providers_config()
    assert config.routing["account_posts:x"] == ["apify"]
    actors = config.providers["apify"].options["actors"]
    assert actors["account_posts:facebook"]["actor"] == "apify/facebook-posts-scraper"
    # Search interest goes to Apify first, with enough time for the browser actor.
    assert config.routing["search_interest"][0] == "apify"
    assert actors["search_interest"]["timeout_s"] >= 120


def test_trend_period_and_keywords_follow_the_task():
    brief = TaskBrief(task_id="t", run_id="r.a1", use_case=UseCase.PRODUCT_LAUNCH, entity="Paymob",
                      question="q", competitors=["Fawry", "Kashier"], offering="Online checkout",
                      since=date.today() - timedelta(days=60))
    assert nodes.trend_timeframe(brief) == "today 3-m"
    assert nodes.trend_keywords(brief, None) == ["Paymob", "Online checkout", "Fawry", "Kashier"]
    assert nodes.trend_keywords(brief, QueryPlan(trend_keywords=["bnpl egypt"])) == ["bnpl egypt"]
    assert nodes.trend_timeframe(brief.model_copy(update={"since": None})) == "today 12-m"
