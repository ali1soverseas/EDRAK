import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderNotConfigured,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import RateLimiter, make_client
from edrak.agents.customer_trends.providers.serper import SerperProvider, parse_serper_date
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from tests.customer_trends.factories import NOW, call_params

SEARCH = "https://google.serper.dev/search"
NEWS = "https://google.serper.dev/news"
CONFIG = ProviderConfig(min_interval_s=0, max_per_request=100, cost_per_call_usd=0.001)


def organic(n: int, prefix: str = "Result") -> list[dict[str, Any]]:
    return [
        {
            "title": f"{prefix} {i}",
            "link": f"https://site.example.test/{prefix.lower()}/{i}",
            "snippet": f"Snippet about GitLab Duo number {i}",
            "position": i + 1,
        }
        for i in range(n)
    ]


async def run(capability: str, **params: Any) -> Any:
    async with make_client(5) as client:
        provider = SerperProvider(
            client, "test-key", CONFIG, clock=lambda: NOW, limiter=RateLimiter(0)
        )
        return await provider.call(capability, call_params(**params))


def items(result: Any) -> list[EvidenceItem]:
    return list(result.items)


async def test_web_search_request_shape(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(SEARCH).mock(return_value=httpx.Response(200, json={"organic": []}))
    await run(
        "web_search",
        query="gitlab duo",
        max_results=3,
        geo="EG",
        languages=["ar", "en"],
        since=date(2026, 4, 1),
        until=date(2026, 9, 30),
    )
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.read()) == {
        "q": "gitlab duo",
        "num": 3,
        "gl": "eg",
        "hl": "ar",
        "tbs": "cdr:1,cd_min:4/1/2026,cd_max:9/30/2026",
        "page": 1,
    }


async def test_web_search_maps_results_to_snippet_only_evidence(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "GitLab Duo review",
                        "link": "https://blog.example.test/duo?utm_source=x",
                        "snippet": "A hands-on look at the assistant.",
                        "date": "Sep 3, 2026",
                        "position": 1,
                    },
                    {
                        "title": "مراجعة جيت لاب",
                        "link": "https://ar.example.test/duo",
                        "snippet": "الدعم الفني بطيء جدا ومكلف ولا يرد على الرسائل",
                        "date": "2 days ago",
                    },
                    {"link": "https://empty.example.test"},
                ]
            },
        )
    )
    result = await run("web_search", query="gitlab duo", max_results=5)
    first, second = items(result)
    assert (first.source_type, first.platform, first.provider) == (SourceType.WEB, None, "serper")
    assert first.text == "GitLab Duo review. A hands-on look at the assistant."
    assert first.snippet_only is True
    assert first.url == "https://blog.example.test/duo?utm_source=x"
    assert first.published_at == datetime(2026, 9, 3, tzinfo=UTC)
    assert first.metadata["position"] == 1
    assert second.language == "ar"
    assert second.text.startswith("مراجعة جيت لاب. الدعم")
    assert second.published_at == datetime(2026, 9, 29, 9, tzinfo=UTC)
    assert result.raw_count == 3
    assert len(result.items) == 2
    assert result.cost_estimate == pytest.approx(0.001)


async def test_news_uses_the_news_endpoint_and_key(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(NEWS).mock(
        return_value=httpx.Response(
            200,
            json={
                "news": [
                    {
                        "title": "GitLab ships Duo agents",
                        "link": "https://n.example.test/a",
                        "snippet": "Details.",
                        "date": "1 day ago",
                        "source": "Example News",
                    }
                ]
            },
        )
    )
    result = await run("news:google_news", query="gitlab")
    [item] = items(result)
    assert route.called
    assert item.source_type is SourceType.NEWS
    assert item.metadata["source"] == "Example News"
    assert item.snippet_only is True


@pytest.mark.parametrize(
    ("platform", "scope"),
    [
        ("x", "(site:x.com OR site:twitter.com)"),
        ("reddit", "site:reddit.com"),
        ("tiktok", "site:tiktok.com"),
        ("instagram", "site:instagram.com"),
        ("facebook", "site:facebook.com"),
    ],
)
async def test_social_fallback_searches_the_platform_site(
    respx_mock: respx.MockRouter, platform: str, scope: str
) -> None:
    route = respx_mock.post(SEARCH).mock(
        return_value=httpx.Response(200, json={"organic": organic(2)})
    )
    result = await run(
        f"social_search:{platform}", query="copilot", hashtags=["#devops"], max_results=2
    )
    assert json.loads(route.calls.last.request.read())["q"] == f"{scope} copilot #devops"
    assert {i.platform for i in items(result)} == {Platform(platform)}
    assert {i.source_type for i in items(result)} == {SourceType.SOCIAL_POST}
    assert all(i.snippet_only for i in items(result))
    [warning] = result.warnings
    assert warning.startswith(f"social_search:{platform} served from a Google site: search")
    assert "snippet-level" in warning
    assert all(i.engagement == {} for i in items(result))


async def test_pagination_requests_pages_until_enough_results(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(SEARCH).mock(
        side_effect=[
            httpx.Response(200, json={"organic": organic(100, "A")}),
            httpx.Response(200, json={"organic": organic(100, "B")}),
        ]
    )
    result = await run("web_search", query="q", max_results=150)
    bodies = [json.loads(call.request.read()) for call in route.calls]
    assert [(b["num"], b["page"]) for b in bodies] == [(100, 1), (100, 2)]
    assert len(items(result)) == 150
    assert result.meta["requests"] == 2
    assert result.cost_estimate == pytest.approx(0.002)


async def test_pagination_stops_at_a_short_page(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(SEARCH).mock(
        return_value=httpx.Response(200, json={"organic": organic(40)})
    )
    result = await run("web_search", query="q", max_results=250)
    assert route.call_count == 1
    assert len(items(result)) == 40


async def test_no_results_is_an_empty_result_not_an_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(SEARCH).mock(return_value=httpx.Response(200, json={"searchParameters": {}}))
    result = await run("web_search", query="nothing")
    assert result.items == [] and result.raw_count == 0


async def test_errors_are_mapped(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(SEARCH).mock(return_value=httpx.Response(403))
    with pytest.raises(ProviderNotConfigured):
        await run("web_search", query="q")
    respx_mock.post(SEARCH).mock(return_value=httpx.Response(200, json=["not", "a", "dict"]))
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        await run("web_search", query="q")


async def test_unsupported_capabilities_are_rejected() -> None:
    with pytest.raises(ProviderBadResponse, match="unsupported"):
        await run("social_comments:x", query="q")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sep 3, 2026", datetime(2026, 9, 3, tzinfo=UTC)),
        ("September 3, 2026", datetime(2026, 9, 3, tzinfo=UTC)),
        ("2026-09-03", datetime(2026, 9, 3, tzinfo=UTC)),
        ("3 Sep 2026", datetime(2026, 9, 3, tzinfo=UTC)),
        ("5 hours ago", datetime(2026, 10, 1, 4, tzinfo=UTC)),
        ("2 weeks ago", datetime(2026, 9, 17, 9, tzinfo=UTC)),
        ("1 month ago", datetime(2026, 9, 1, 9, tzinfo=UTC)),
        ("قبل 6 ساعات", datetime(2026, 10, 1, 3, tzinfo=UTC)),
        ("قبل ٣ أيام", datetime(2026, 9, 28, 9, tzinfo=UTC)),
        ("قبل يوم", datetime(2026, 9, 30, 9, tzinfo=UTC)),
        ("قبل يومين", datetime(2026, 9, 29, 9, tzinfo=UTC)),
        ("قبل أسبوع", datetime(2026, 9, 24, 9, tzinfo=UTC)),
        ("قبل 15 دقيقة", datetime(2026, 10, 1, 8, 45, tzinfo=UTC)),
        ("قبل سنة", datetime(2025, 10, 1, 9, tzinfo=UTC)),
        ("29 أغسطس 2025", datetime(2025, 8, 29, tzinfo=UTC)),
        ("٣ سبتمبر ٢٠٢٦", datetime(2026, 9, 3, tzinfo=UTC)),
        ("5 تشرين الأول 2025", datetime(2025, 10, 5, tzinfo=UTC)),
        ("31 فبراير 2026", None),
        ("29" + chr(0x200F) + "/08" + chr(0x200F) + "/2025", datetime(2025, 8, 29, tzinfo=UTC)),
        ("٠٣/١٢/٢٠٢٥", datetime(2025, 12, 3, tzinfo=UTC)),
        ("31/02/2025", None),
        ("yesterday-ish", None),
        ("il y a 3 jours", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_serper_date(text: str | None, expected: datetime | None) -> None:
    assert parse_serper_date(text, NOW) == expected
