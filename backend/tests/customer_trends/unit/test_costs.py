import pytest

from edrak.agents.customer_trends.providers.config import (
    ProviderConfig,
    ProvidersConfig,
    load_providers_config,
)
from edrak.agents.customer_trends.providers.costs import cost_per_100, cost_table

# How much dearer an earlier provider may be than a later one and still keep its place: a gap
# this small is within the noise of the prices, and the earlier one has other advantages.
TOLERANCE = 2.0
# Serper only ever serves social search as snippets, so it is a last resort whatever it costs.
LAST_RESORT = ("serper", "social_search:")


def synthetic_config() -> ProvidersConfig:
    return ProvidersConfig(
        providers={
            "apify": ProviderConfig(
                options={
                    "actors": {
                        "social_search:x": {
                            "actor": "a/b",
                            "mapper": "x_post",
                            "cost_per_item_usd": 0.002,
                            "cost_per_run_usd": 0.05,
                        },
                        "social_search:reddit": {"actor": None, "mapper": "reddit_post"},
                        "social_search:tiktok": {
                            "actor": "a/c",
                            "mapper": "tiktok_video",
                            "enabled": False,
                        },
                    }
                }
            ),
            "socialcrawl": ProviderConfig(
                options={
                    "usd_per_credit": 0.01,
                    "endpoints": {
                        "social_search:x": {
                            "path": "/p",
                            "credits_per_page": 2,
                            "page_size": 20,
                            "expected_yield": 0.5,
                        },
                        "social_comments:x": {
                            "path": "/c",
                            "input": "url",
                            "credits_per_page": 1,
                            "page_size": 100,
                        },
                    },
                }
            ),
            "serper": ProviderConfig(max_per_request=100, cost_per_call_usd=0.001),
            "youtube_api": ProviderConfig(),
            "gdelt": ProviderConfig(),
            "direct_http": ProviderConfig(),
            "google_trends_api": ProviderConfig(),
        },
        routing={},
    )


def test_apify_costs_the_run_fee_plus_the_item_price() -> None:
    assert cost_per_100("apify", "social_search:x", synthetic_config()) == pytest.approx(
        0.05 + 100 * 0.002
    )


def test_apify_actors_that_are_missing_or_switched_off_are_not_offered() -> None:
    config = synthetic_config()
    assert cost_per_100("apify", "social_search:reddit", config) is None
    assert cost_per_100("apify", "social_search:tiktok", config) is None
    assert cost_per_100("apify", "social_search:youtube", config) is None


def test_socialcrawl_counts_the_pages_needed_after_filtering() -> None:
    # 20 rows a page, half survive: 10 usable per page, so 10 pages of 2 credits at 0.01
    assert cost_per_100("socialcrawl", "social_search:x", synthetic_config()) == pytest.approx(
        10 * 2 * 0.01
    )
    assert cost_per_100("socialcrawl", "social_comments:x", synthetic_config()) == pytest.approx(
        0.01
    )
    assert cost_per_100("socialcrawl", "social_search:reddit", synthetic_config()) is None


def test_serper_youtube_gdelt_and_the_fetcher() -> None:
    config = synthetic_config()
    assert cost_per_100("serper", "web_search", config) == pytest.approx(0.001)
    assert cost_per_100("serper", "social_comments:x", config) is None
    assert cost_per_100("youtube_api", "social_search:youtube", config) == 0.0
    assert cost_per_100("youtube_api", "social_search:x", config) is None
    assert cost_per_100("gdelt", "news:gdelt", config) == 0.0
    assert cost_per_100("direct_http", "fetch_page", config) == 0.0
    assert cost_per_100("google_trends_api", "search_interest", config) is None
    assert cost_per_100("unknown", "web_search", config) is None


def test_the_shipped_routing_puts_the_cheapest_provider_first_within_the_tolerance() -> None:
    config = load_providers_config()
    problems = []
    for capability, row in cost_table(config).items():
        priced = [
            (name, cost)
            for name, cost in row
            if cost is not None
            and not (name == LAST_RESORT[0] and capability.startswith(LAST_RESORT[1]))
        ]
        for i, (earlier, early_cost) in enumerate(priced):
            for later, late_cost in priced[i + 1 :]:
                if early_cost > TOLERANCE * late_cost:
                    problems.append(
                        f"{capability}: {earlier} ({early_cost:.4f}) before {later} "
                        f"({late_cost:.4f})"
                    )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("capability", "first"),
    [
        ("social_search:reddit", "socialcrawl"),
        ("social_search:tiktok", "socialcrawl"),
        ("social_search:instagram", "socialcrawl"),
        ("social_search:facebook", "socialcrawl"),
        ("social_search:youtube", "youtube_api"),
        ("social_search:x", "apify"),
        ("social_comments:tiktok", "socialcrawl"),
        ("social_comments:instagram", "socialcrawl"),
        ("social_comments:facebook", "socialcrawl"),
        ("social_comments:reddit", "socialcrawl"),
        ("social_comments:x", "socialcrawl"),
        ("social_comments:youtube", "youtube_api"),
        ("search_interest", "apify"),
        ("reviews:app_store", "apify"),
        ("reviews:google_play", "apify"),
        ("reviews:amazon", "apify"),
    ],
)
def test_the_first_choice_for_each_capability(capability: str, first: str) -> None:
    assert load_providers_config().routing[capability][0] == first


def test_the_worst_case_gaps_are_the_ones_that_motivated_the_order() -> None:
    config = load_providers_config()

    def ratio(capability: str) -> float:
        apify = cost_per_100("apify", capability, config)
        socialcrawl = cost_per_100("socialcrawl", capability, config)
        assert apify is not None and socialcrawl is not None
        return apify / socialcrawl

    assert ratio("social_search:reddit") > 8
    assert ratio("social_search:tiktok") > 10
    assert ratio("social_comments:tiktok") > 5
    assert ratio("social_comments:facebook") > 10
    assert 1 < ratio("social_comments:instagram") < 3


def test_the_credit_price_is_the_growth_pack_and_can_be_changed_in_one_place() -> None:
    config = load_providers_config()
    assert config.providers["socialcrawl"].options["usd_per_credit"] == 0.0033
    dearer = config.model_copy(deep=True)
    dearer.providers["socialcrawl"].options["usd_per_credit"] = 0.008
    base = cost_per_100("socialcrawl", "social_comments:instagram", config)
    assert base is not None
    assert cost_per_100("socialcrawl", "social_comments:instagram", dearer) == pytest.approx(
        base * 0.008 / 0.0033
    )
