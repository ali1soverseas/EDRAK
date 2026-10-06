from datetime import date

import pytest

from edrak.agents.customer_trends.providers.apify.provider import (
    ActorSpec,
    date_window,
    render_input,
    template_values,
)
from edrak.agents.customer_trends.providers.base import CallParams
from edrak.agents.customer_trends.providers.config import load_providers_config
from tests.customer_trends.factories import call_params

TODAY = date(2026, 10, 1)


def values(**overrides: object) -> dict[str, object]:
    call = CallParams.model_validate(call_params(**overrides))
    return template_values(call, TODAY, call.wanted)


def test_exact_placeholders_keep_their_type() -> None:
    template = {"max": "{max_results}", "terms": "{keywords}", "flag": True, "fixed": "x"}
    rendered = render_input(template, {"max_results": 7, "keywords": ["a", "b"]}, {})
    assert rendered == {"max": 7, "terms": ["a", "b"], "flag": True, "fixed": "x"}


def test_embedded_placeholders_are_formatted() -> None:
    template = {"url": "https://www.instagram.com/explore/tags/{hashtag}/"}
    assert render_input(template, {"hashtag": "gitlab"}, {}) == {
        "url": "https://www.instagram.com/explore/tags/gitlab/"
    }


def test_keys_and_list_items_without_a_value_are_left_out() -> None:
    template = {
        "a": "{x}",
        "b": "{y}",
        "items": ["{x}", "{y}"],
        "empty": ["{y}"],
        "nested": {"k": "{y}"},
    }
    rendered = render_input(template, {"x": 1, "y": None}, {})
    assert rendered == {"a": 1, "items": [1]}


def test_an_emptied_object_inside_a_list_disappears() -> None:
    assert render_input({"startUrls": [{"url": "{url}"}], "n": 3}, {"url": None}, {}) == {"n": 3}


def test_value_mappings_translate_and_drop_unmapped_values() -> None:
    mappings = {"sort": {"recent": "Latest", "top": "Top"}}
    assert render_input({"sort": "{sort}"}, {"sort": "top"}, mappings) == {"sort": "Top"}
    assert render_input({"sort": "{sort}"}, {"sort": "relevance"}, mappings) == {}


def test_an_unknown_placeholder_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown input placeholder"):
        render_input({"a": "{nope}"}, {"x": 1}, {})


@pytest.mark.parametrize(
    ("since", "window"),
    [
        (None, None),
        (date(2026, 10, 1), "day"),
        (date(2026, 9, 28), "week"),
        (date(2026, 9, 10), "month"),
        (date(2026, 7, 25), "quarter"),
        (date(2026, 5, 1), "half_year"),
        (date(2025, 12, 1), "year"),
        (date(2020, 1, 1), "all"),
    ],
)
def test_date_window(since: date | None, window: str | None) -> None:
    assert date_window(since, TODAY) == window


def test_template_values_cover_the_call() -> None:
    v = values(
        query="gitlab duo",
        hashtags=["devops"],
        languages=["en"],
        geo="EG",
        since="2026-09-01",
        until="2026-09-30",
        max_results=40,
        post_url="https://x.com/a/status/2100000000000000001",
        sort="top",
        target="B0BTYCRJSS",
        country="eg",
    )
    assert v["query"] == "gitlab duo devops"
    assert v["hashtag"] == "gitlabduodevops"
    assert v["language"] == "en"
    assert (v["geo"], v["since"], v["until"]) == ("EG", "2026-09-01", "2026-09-30")
    assert (v["max_results"], v["max_results_plus_one"]) == (40, 41)
    assert v["status_id"] == "2100000000000000001"
    assert v["target_url"] == "https://www.amazon.com/dp/B0BTYCRJSS"
    assert (v["country_lower"], v["country_upper"]) == ("eg", "EG")
    assert v["window"] == "month"


def test_language_is_only_set_when_exactly_one_is_requested() -> None:
    assert values(query="q", languages=["ar", "en"])["language"] is None
    assert values(query="q", languages=["ar"])["language"] == "ar"


def test_amazon_urls_are_kept_and_empty_values_are_none() -> None:
    v = values(target="https://www.amazon.co.uk/dp/B0BTYCRJSS")
    assert v["target_url"] == "https://www.amazon.co.uk/dp/B0BTYCRJSS"
    empty = values()
    assert empty["query"] is None and empty["hashtag"] is None and empty["keywords"] is None
    assert empty["status_id"] is None and empty["target_url"] is None


def test_every_configured_actor_is_a_valid_spec_with_only_known_placeholders() -> None:
    config = load_providers_config().providers["apify"].options["actors"]
    known = set(values(query="q"))
    for capability, raw in config.items():
        spec = ActorSpec.model_validate(raw)
        render_input(spec.input, {name: "x" for name in known}, {})
        assert set(spec.requires) <= known, capability
        assert spec.actor or not spec.enabled, capability
