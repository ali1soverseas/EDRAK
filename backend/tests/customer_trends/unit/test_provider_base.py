from datetime import date

import pytest

from edrak.agents.customer_trends.providers.base import (
    CallParams,
    ProviderExhausted,
    ProviderFailure,
    ProviderResult,
    canonical_url,
    make_capability,
    new_evidence,
    parse_capability,
)
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from tests.customer_trends.factories import NOW, call_params, make_evidence


@pytest.mark.parametrize(
    "capability",
    [
        "web_search",
        "fetch_page",
        "search_interest",
        "social_search:x",
        "social_search:reddit",
        "social_comments:youtube",
        "reviews:google_play",
        "reviews:app_store",
        "reviews:amazon",
        "news:gdelt",
        "news:google_news",
    ],
)
def test_valid_capability_names_round_trip(capability: str) -> None:
    kind, variant = parse_capability(capability)
    assert make_capability(kind, variant) == capability


@pytest.mark.parametrize(
    "capability",
    [
        "",
        "social_search",
        "social_search:myspace",
        "web_search:x",
        "news:bbc",
        "unknown",
        "reviews:",
    ],
)
def test_invalid_capability_names_are_rejected(capability: str) -> None:
    with pytest.raises(ValueError, match="capability"):
        parse_capability(capability)


def test_call_params_defaults_and_extras() -> None:
    params = CallParams.model_validate(call_params(query="gitlab", custom="kept"))
    assert params.languages == ["ar", "en"]
    assert params.wanted == 10
    assert params.sort == "recent"
    assert params.model_extra == {"custom": "kept"}
    assert CallParams.model_validate(call_params(max_results=30)).wanted == 30
    with pytest.raises(ValueError, match="max_results"):
        CallParams.model_validate(call_params(max_results=0))


def test_canonical_url_drops_noise() -> None:
    assert (
        canonical_url("HTTPS://Example.COM/Path/?utm_source=x&id=4&fbclid=zz#frag")
        == "https://example.com/Path?id=4"
    )
    assert canonical_url("https://example.com/") == "https://example.com"
    assert canonical_url(" https://example.com/a ") == "https://example.com/a"


def test_new_evidence_ids_ignore_the_provider_and_tracking_parameters() -> None:
    params = CallParams.model_validate(call_params())
    kwargs = {
        "source_type": SourceType.SOCIAL_POST,
        "platform": Platform.X,
        "text": "same post",
        "collected_at": NOW,
    }
    first = new_evidence(params, provider="apify", url="https://x.com/a/status/1?utm_x=1", **kwargs)  # type: ignore[arg-type]  # kwargs dict
    second = new_evidence(params, provider="serper", url="https://x.com/a/status/1/", **kwargs)  # type: ignore[arg-type]  # kwargs dict
    other_platform = new_evidence(
        params,
        provider="apify",
        url="https://x.com/a/status/1",
        **{**kwargs, "platform": Platform.REDDIT},  # type: ignore[arg-type]  # kwargs dict
    )
    assert first.id == second.id
    assert first.id != other_platform.id
    assert first.content_hash == second.content_hash
    assert first.batch_id == "pending"
    assert (first.run_id, first.task_id) == ("run-test-001", "task-test-001")


def test_new_evidence_without_url_uses_the_content_hash_and_detects_language() -> None:
    params = CallParams.model_validate(call_params())
    arabic = "الدعم الفني بطيء جدا ومكلف ولا يرد على الرسائل"
    item = new_evidence(
        params, provider="p", source_type=SourceType.WEB, text=arabic, collected_at=NOW
    )
    again = new_evidence(
        params, provider="p", source_type=SourceType.WEB, text=arabic, collected_at=NOW
    )
    assert item.id == again.id
    assert item.language == "ar"
    assert item.url is None


def test_provider_result_round_trips_both_item_kinds() -> None:
    evidence = ProviderResult(items=[make_evidence("a post")], raw_count=1, meta={"k": [1]})
    assert ProviderResult.model_validate_json(evidence.model_dump_json()) == evidence
    series = TrendSeries(
        keyword="duo",
        timeframe="today 12-m",
        granularity="week",
        points=[(date(2026, 1, 4), 10.0)],
        source="apify",
        batch_id="pending",
    )
    trends = ProviderResult(items=[series])
    again = ProviderResult.model_validate_json(trends.model_dump_json())
    assert again.items == [series]
    assert ProviderResult.model_validate_json(ProviderResult().model_dump_json()).items == []


def test_restamped_changes_run_and_task_ids_only_on_evidence() -> None:
    result = ProviderResult(items=[make_evidence("one"), make_evidence("two")])
    moved = result.restamped("run-b", "task-b")
    assert {i.run_id for i in moved.items} == {"run-b"}  # type: ignore[union-attr]  # evidence items
    assert {i.task_id for i in moved.items} == {"task-b"}  # type: ignore[union-attr]  # evidence items
    assert {i.run_id for i in result.items} == {"run-test-001"}  # type: ignore[union-attr]  # evidence items
    assert ProviderResult().restamped("a", "b") == ProviderResult()


def test_exhausted_error_lists_every_failure() -> None:
    error = ProviderExhausted(
        "web_search",
        [
            ProviderFailure("serper", "ProviderRateLimited", "slow down"),
            ProviderFailure("apify", "unregistered", "-", True),
        ],
    )
    assert "serper: ProviderRateLimited" in str(error)
    assert "apify: unregistered" in str(error)
    assert error.failures[1].skipped is True
    assert "no provider routed" in str(ProviderExhausted("web_search", []))
