import io
import json
from typing import Any

import httpx
import pytest
import respx

from edrak.agents.customer_trends.logging import configure_logging
from edrak.agents.customer_trends.providers.apify import ApifyProvider
from edrak.agents.customer_trends.providers.base import (
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
)
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.http import RateLimiter, make_client
from edrak.agents.customer_trends.providers.keys import KeyRing, with_failover
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.providers.socialcrawl import SocialCrawlProvider
from edrak.agents.customer_trends.schemas.common import Budget
from edrak.agents.customer_trends.settings import Settings
from tests.customer_trends.factories import NOW, call_params, fixture_json

APIFY_RUN = "https://api.apify.com/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items"
SC_SEARCH = "https://www.socialcrawl.dev/v1/twitter/search/tweets"
SC_BALANCE = "https://www.socialcrawl.dev/v1/credits/balance"
APIFY = load_providers_config().providers["apify"]
SOCIALCRAWL = load_providers_config().providers["socialcrawl"]
TOKENS = ["apify_api_one", "apify_api_two", "apify_api_three"]
KEYS = ["sc_one", "sc_two", "sc_three"]


def settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]  # plain strings for secrets


# the ring


def test_the_ring_starts_on_the_first_key_and_moves_forward_once_per_key() -> None:
    ring = KeyRing(["a", "b", "c"])
    assert (ring.current, ring.position, ring.size) == ("a", 1, 3)
    assert ring.rotate_from("a") is True and ring.current == "b"
    assert ring.rotate_from("b") is True and ring.current == "c"
    assert ring.rotate_from("c") is False and ring.current == "c"


def test_a_concurrent_rotation_is_not_repeated() -> None:
    ring = KeyRing(["a", "b", "c"])
    assert ring.rotate_from("a") is True
    assert ring.rotate_from("a") is True  # someone already moved on: use the new key
    assert ring.current == "b"


def test_the_ring_drops_blanks_and_repeats_and_needs_one_key() -> None:
    assert KeyRing(["a", " ", "", "a", " b "]).size == 2
    with pytest.raises(ValueError, match="at least one"):
        KeyRing(["", " "])


async def test_with_failover_rotates_on_quota_and_rejected_keys_only() -> None:
    ring = KeyRing(["a", "b", "c"])
    seen: list[str] = []

    async def attempt() -> str:
        seen.append(ring.current)
        if ring.current == "a":
            raise ProviderQuotaExceeded("out of credit")
        if ring.current == "b":
            raise ProviderNotConfigured("rejected")
        return "served"

    assert await with_failover(ring, "test", attempt) == "served"
    assert seen == ["a", "b", "c"]
    seen.clear()
    assert await with_failover(ring, "test", attempt) == "served"
    assert seen == ["c"]


async def test_with_failover_raises_the_last_error_when_every_key_failed() -> None:
    ring = KeyRing(["a", "b"])

    async def attempt() -> None:
        raise ProviderQuotaExceeded(f"{ring.current} is empty")

    with pytest.raises(ProviderQuotaExceeded, match="b is empty"):
        await with_failover(ring, "test", attempt)
    with pytest.raises(ProviderQuotaExceeded):
        await with_failover(ring, "test", attempt)
    assert ring.current == "b"


async def test_other_errors_do_not_rotate() -> None:
    ring = KeyRing(["a", "b"])

    async def attempt() -> None:
        raise ProviderRateLimited("slow down")

    with pytest.raises(ProviderRateLimited):
        await with_failover(ring, "test", attempt)
    assert ring.current == "a"


# settings


def test_key_list_joins_the_primary_and_the_fallbacks_in_order() -> None:
    s = settings(apify_token="one", apify_fallback_tokens=" two, three ,,one ,four")
    assert s.key_list("apify_token", "apify_fallback_tokens") == ["one", "two", "three", "four"]


def test_key_list_works_without_a_primary_or_without_anything() -> None:
    assert settings(apify_fallback_tokens="two,three").key_list(
        "apify_token", "apify_fallback_tokens"
    ) == ["two", "three"]
    assert settings().key_list("apify_token", "apify_fallback_tokens") == []


def test_fallback_keys_are_secrets_in_the_redacted_view() -> None:
    view = settings(apify_fallback_tokens="secret-one,secret-two").redacted()
    assert (
        view["apify_fallback_tokens"] == "set"
        and view["socialcrawl_fallback_api_keys"] == "missing"
    )
    assert "secret-one" not in str(view)


# Apify


def served(items: list[dict[str, Any]] | None = None) -> httpx.Response:
    return httpx.Response(201, json=items or fixture_json("providers", "apify", "x_post.json"))


def out_of_credit() -> httpx.Response:
    return httpx.Response(402, json={"error": {"type": "not-enough-usage-to-run-paid-actor"}})


def bearer(route: respx.Route, index: int) -> str:
    return route.calls[index].request.headers["authorization"].removeprefix("Bearer ")


async def apify_call() -> Any:
    async with make_client(5) as client:
        provider = ApifyProvider(
            client, TOKENS[0], APIFY, fallback_tokens=TOKENS[1:], clock=lambda: NOW
        )
        first = await provider.call("social_search:x", call_params(query="q", max_results=3))
        second = await provider.call("social_search:x", call_params(query="q again", max_results=3))
        return first, second


async def test_apify_moves_to_a_fallback_token_when_the_first_is_out_of_credit(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(APIFY_RUN).mock(side_effect=[out_of_credit(), served(), served()])
    first, second = await apify_call()
    assert len(first.items) == 3 and len(second.items) == 3
    assert [bearer(route, i) for i in range(3)] == [TOKENS[0], TOKENS[1], TOKENS[1]]


async def test_apify_keeps_going_down_the_list_and_remembers_where_it_got_to(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(APIFY_RUN).mock(
        side_effect=[out_of_credit(), out_of_credit(), served(), served()]
    )
    await apify_call()
    assert [bearer(route, i) for i in range(4)] == [TOKENS[0], TOKENS[1], TOKENS[2], TOKENS[2]]


async def test_apify_reports_the_quota_error_once_every_token_is_used_up(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(APIFY_RUN).mock(return_value=out_of_credit())
    async with make_client(5) as client:
        provider = ApifyProvider(client, TOKENS[0], APIFY, fallback_tokens=TOKENS[1:])
        with pytest.raises(ProviderQuotaExceeded):
            await provider.call("social_search:x", call_params(query="q"))
    assert route.call_count == 3


async def test_apify_rotates_away_from_a_revoked_token(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(APIFY_RUN).mock(side_effect=[httpx.Response(401), served()])
    async with make_client(5) as client:
        provider = ApifyProvider(client, TOKENS[0], APIFY, fallback_tokens=TOKENS[1:])
        result = await provider.call("social_search:x", call_params(query="q", max_results=3))
    assert len(result.items) == 3 and bearer(route, 1) == TOKENS[1]


async def test_apify_without_fallbacks_behaves_as_before(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(APIFY_RUN).mock(return_value=out_of_credit())
    async with make_client(5) as client:
        with pytest.raises(ProviderQuotaExceeded):
            await ApifyProvider(client, TOKENS[0], APIFY).call(
                "social_search:x", call_params(query="q")
            )
    assert route.call_count == 1


async def test_an_actor_failure_does_not_burn_through_the_tokens(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(APIFY_RUN).mock(
        return_value=httpx.Response(
            400, json={"error": {"type": "run-failed", "message": "status: FAILED"}}
        )
    )
    async with make_client(5) as client:
        provider = ApifyProvider(client, TOKENS[0], APIFY, fallback_tokens=TOKENS[1:])
        with pytest.raises(Exception, match="actor run failed"):
            await provider.call("social_search:x", call_params(query="q"))
    assert route.call_count == 1


# SocialCrawl


def sc_page(credits_left: int = 90) -> httpx.Response:
    page = fixture_json("providers", "socialcrawl", "social_search.x.json")
    page["credits_remaining"] = credits_left
    return httpx.Response(200, json=page)


def insufficient() -> httpx.Response:
    return httpx.Response(
        402, json={"success": False, "error": {"type": "INSUFFICIENT_CREDITS", "status": 402}}
    )


def sc_key(route: respx.Route, index: int) -> str:
    return route.calls[index].request.headers["x-api-key"]


def socialcrawl(client: httpx.AsyncClient) -> SocialCrawlProvider:
    return SocialCrawlProvider(
        client, KEYS[0], "https://www.socialcrawl.dev/v1", SOCIALCRAWL,
        fallback_keys=KEYS[1:], clock=lambda: NOW, limiter=RateLimiter(0),
    )  # fmt: skip


async def test_socialcrawl_moves_to_a_fallback_key_when_credits_run_out(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(SC_SEARCH).mock(side_effect=[insufficient(), sc_page(), sc_page()])
    async with make_client(5) as client:
        provider = socialcrawl(client)
        first = await provider.call("social_search:x", call_params(query="q", max_results=3))
        await provider.call("social_search:x", call_params(query="q", max_results=3))
    assert len(first.items) == 3
    assert [sc_key(route, i) for i in range(3)] == [KEYS[0], KEYS[1], KEYS[1]]


async def test_socialcrawl_rotates_when_the_balance_check_finds_too_few_credits(
    respx_mock: respx.MockRouter,
) -> None:
    balance = respx_mock.get(SC_BALANCE).mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "data": {"balance": 1}}),
            httpx.Response(200, json={"success": True, "data": {"balance": 100}}),
        ]
    )
    search = respx_mock.get("https://www.socialcrawl.dev/v1/instagram/post/comments").mock(
        return_value=httpx.Response(
            200, json=fixture_json("providers", "socialcrawl", "social_comments.instagram.json")
        )
    )
    async with make_client(5) as client:
        result = await socialcrawl(client).call(
            "social_comments:instagram",
            call_params(post_url="https://www.instagram.com/p/abc/", max_results=15),
        )
    assert len(result.items) == 3
    assert [balance.calls[i].request.headers["x-api-key"] for i in range(2)] == [KEYS[0], KEYS[1]]
    assert sc_key(search, 0) == KEYS[1]


async def test_socialcrawl_credits_remaining_reads_the_current_key(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(SC_SEARCH).mock(side_effect=[insufficient(), sc_page()])
    balance = respx_mock.get(SC_BALANCE).mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"balance": 100}})
    )
    async with make_client(5) as client:
        provider = socialcrawl(client)
        await provider.call("social_search:x", call_params(query="q", max_results=3))
        await provider.credits_remaining()
    assert route.call_count == 2 and balance.calls[0].request.headers["x-api-key"] == KEYS[1]


async def test_socialcrawl_reports_the_quota_error_when_every_key_is_empty(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(SC_SEARCH).mock(return_value=insufficient())
    async with make_client(5) as client:
        with pytest.raises(ProviderQuotaExceeded):
            await socialcrawl(client).call("social_search:x", call_params(query="q"))
    assert route.call_count == 3


# the registry and the logs


async def test_the_registry_builds_both_providers_with_their_fallbacks(
    respx_mock: respx.MockRouter, tmp_path: Any
) -> None:
    config = settings(
        edrak_data_dir=tmp_path / "data",
        apify_token=TOKENS[0],
        apify_fallback_tokens=",".join(TOKENS[1:]),
        socialcrawl_api_key=KEYS[0],
        socialcrawl_fallback_api_keys=",".join(KEYS[1:]),
    )
    apify = respx_mock.post(APIFY_RUN).mock(side_effect=[out_of_credit(), served()])
    registry = ProviderRegistry.from_config(
        config, BudgetTracker(Budget()), CircuitBreaker(), clock=lambda: NOW
    )
    try:
        result = await registry.call("social_search:x", call_params(query="q", max_results=3))
    finally:
        await registry.aclose()
    assert (result.provider, result.fallback_used) == ("apify", False)
    assert [bearer(apify, i) for i in range(2)] == [TOKENS[0], TOKENS[1]]


async def test_a_provider_is_registered_from_fallbacks_alone(tmp_path: Any) -> None:
    registry = ProviderRegistry.from_config(
        settings(edrak_data_dir=tmp_path / "data", socialcrawl_fallback_api_keys="only_fallback"),
        BudgetTracker(Budget()),
        CircuitBreaker(),
    )
    try:
        assert "socialcrawl" in registry.providers and "apify" not in registry.providers
    finally:
        await registry.aclose()


async def test_rotation_is_logged_without_any_key_value(respx_mock: respx.MockRouter) -> None:
    stream = io.StringIO()
    configure_logging(settings(edrak_env="production"), stream=stream)
    respx_mock.post(APIFY_RUN).mock(side_effect=[out_of_credit(), served()])
    async with make_client(5) as client:
        provider = ApifyProvider(client, TOKENS[0], APIFY, fallback_tokens=TOKENS[1:])
        await provider.call("social_search:x", call_params(query="q", max_results=3))
    entry = next(
        json.loads(line) for line in stream.getvalue().splitlines() if "api_key_rotated" in line
    )
    assert (entry["provider"], entry["reason"], entry["position"], entry["of"]) == (
        "apify",
        "ProviderQuotaExceeded",
        2,
        3,
    )
    assert not any(token in stream.getvalue() for token in TOKENS)


async def test_an_account_over_an_actors_monthly_limit_moves_to_the_next_token(
    respx_mock: respx.MockRouter,
) -> None:
    refused = httpx.Response(201, json=[{"noResults": True}])
    route = respx_mock.post(APIFY_RUN).mock(side_effect=[refused, served()])
    respx_mock.get("https://api.apify.com/v2/acts/apidojo~tweet-scraper/runs").mock(
        return_value=httpx.Response(200, json={"data": {"items": [{"id": "run1"}]}})
    )
    respx_mock.get("https://api.apify.com/v2/actor-runs/run1/log").mock(
        return_value=httpx.Response(200, text="Monthly run limit exceeded per user.")
    )
    async with make_client(5) as client:
        provider = ApifyProvider(client, TOKENS[0], APIFY, fallback_tokens=TOKENS[1:])
        result = await provider.call("social_search:x", call_params(query="q", max_results=3))
    assert len(result.items) == 3
    assert [bearer(route, i) for i in range(2)] == [TOKENS[0], TOKENS[1]]


async def test_x_search_goes_to_socialcrawl_when_every_apify_account_is_over_the_limit(
    respx_mock: respx.MockRouter, tmp_path: Any
) -> None:
    config = settings(
        edrak_data_dir=tmp_path / "data",
        apify_token=TOKENS[0],
        apify_fallback_tokens=",".join(TOKENS[1:]),
        socialcrawl_api_key=KEYS[0],
    )
    apify = respx_mock.post(APIFY_RUN).mock(
        return_value=httpx.Response(201, json=[{"noResults": 1}])
    )
    respx_mock.get("https://api.apify.com/v2/acts/apidojo~tweet-scraper/runs").mock(
        return_value=httpx.Response(200, json={"data": {"items": [{"id": "run1"}]}})
    )
    respx_mock.get("https://api.apify.com/v2/actor-runs/run1/log").mock(
        return_value=httpx.Response(200, text="Monthly run limit exceeded per user.")
    )
    respx_mock.get(SC_BALANCE).mock(
        return_value=httpx.Response(200, json={"data": {"credits_remaining": 90}})
    )
    sc = respx_mock.get(SC_SEARCH).mock(return_value=sc_page())
    breaker = CircuitBreaker()
    registry = ProviderRegistry.from_config(
        config, BudgetTracker(Budget()), breaker, clock=lambda: NOW
    )
    try:
        first = await registry.call("social_search:x", call_params(query="q", max_results=3))
        second = await registry.call("social_search:x", call_params(query="q2", max_results=3))
    finally:
        await registry.aclose()
    assert (first.provider, first.fallback_used) == ("socialcrawl", True)
    assert (second.provider, second.fallback_used) == ("socialcrawl", True)
    assert apify.call_count == 3  # one refused run per token, then Apify is not asked again
    assert sc.call_count == 2
    assert breaker.is_open("apify") is False
