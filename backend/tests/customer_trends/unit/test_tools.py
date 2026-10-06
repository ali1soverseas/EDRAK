import json
from typing import Any

import pytest

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.schemas.common import Budget, ToolResponse, ToolStatus
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.base import ToolContext, invoke_tool
from edrak.agents.customer_trends.tools.registry import COLLECTION_SPECS
from tests.customer_trends.factories import RUN_ID
from tests.customer_trends.tool_helpers import (
    evidence_result,
    fails,
    make_context,
    serves,
    trend_series,
)

SPECS = {spec.name: spec for spec in COLLECTION_SPECS}

# tool -> (valid arguments, capability it calls, arguments that must be refused)
CASES: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] = {
    "web_search": ({"query": "gitlab duo review"}, "web_search", {"query": "x"}),
    "fetch_page": (
        {"url": "https://blog.example.test/post"},
        "fetch_page",
        {"url": "https://x.com/a/status/1"},
    ),
    "social_search": (
        {"platform": "reddit", "query": "gitlab duo"},
        "social_search:reddit",
        {"platform": "myspace", "query": "gitlab duo"},
    ),
    "social_comments": (
        {"platform": "youtube", "post_url": "https://www.youtube.com/watch?v=abc123xyz00"},
        "social_comments:youtube",
        {"platform": "youtube", "post_url": "https://www.reddit.com/r/x/comments/1/"},
    ),
    "search_interest": ({"keywords": ["gitlab duo"]}, "search_interest", {"keywords": []}),
    "reviews_fetch": (
        {"store": "google_play", "target_id_or_url": "com.spotify.music"},
        "reviews:google_play",
        {"store": "windows_store", "target_id_or_url": "x"},
    ),
    "news_coverage": ({"query": "gitlab"}, "news:gdelt", {"query": "gitlab", "source": "bbc"}),
}


def success_result(capability: str, **kwargs: Any) -> ProviderResult:
    if capability == "search_interest":
        return ProviderResult(items=[trend_series()], raw_count=1, cost_estimate=0.003)
    return evidence_result(capability, **kwargs)


async def run(ctx: ToolContext, tool: str, arguments: dict[str, Any]) -> ToolResponse:
    return await invoke_tool(ctx, SPECS[tool], arguments)


def set_up(
    store: EvidenceStore, tool: str, behavior: Any = None, **kwargs: Any
) -> tuple[ToolContext, Any, dict[str, Any], str]:
    arguments, capability, _ = CASES[tool]
    ctx, provider = make_context(
        store, capability, behavior or serves(success_result(capability)), **kwargs
    )
    return ctx, provider, arguments, capability


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_success_stores_a_batch_and_returns_pointers(store: EvidenceStore, tool: str) -> None:
    ctx, provider, arguments, capability = set_up(store, tool)
    response = await run(ctx, tool, arguments)
    assert response.status is ToolStatus.OK
    assert response.error_code is None
    assert response.batch_id and response.batch_id.startswith("b_")
    assert response.count >= 1
    assert response.provider_used == ctx.providers.routing_for(capability)[0]
    assert response.cost_estimate > 0
    assert 1 <= len(response.preview) <= 5
    assert {"id", "platform", "snippet", "url"} <= set(response.preview[0])
    assert all(len(item["snippet"]) <= 200 for item in response.preview)
    assert response.coverage
    summary = store.run_summary(RUN_ID)
    assert summary.evidence_count == response.count
    assert summary.batch_ids == [response.batch_id]
    assert provider.calls[0]["run_id"] == RUN_ID


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_a_partial_provider_result_is_a_partial_response(
    store: EvidenceStore, tool: str
) -> None:
    _, capability, _ = CASES[tool]
    result = success_result(capability).model_copy(
        update={"partial": True, "warnings": ["page 2 failed"]}
    )
    ctx, _, arguments, _ = set_up(store, tool, serves(result))
    response = await run(ctx, tool, arguments)
    assert response.status is ToolStatus.PARTIAL
    assert response.count >= 1
    assert "page 2 failed" in response.warnings


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_provider_exhausted_is_an_error_response_with_the_failures(
    store: EvidenceStore, tool: str
) -> None:
    ctx, _, arguments, capability = set_up(store, tool, fails(ProviderRateLimited("slow down")))
    response = await run(ctx, tool, arguments)
    assert response.status is ToolStatus.ERROR
    assert response.error_code == "provider_exhausted"
    assert response.batch_id is None and response.count == 0 and response.preview == []
    assert capability in response.gaps[0]
    assert "ProviderRateLimited" in response.gaps[0]


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_an_exhausted_budget_is_an_error_response(store: EvidenceStore, tool: str) -> None:
    ctx, provider, arguments, _ = set_up(store, tool, budget=Budget(max_tool_calls=1))
    assert (await run(ctx, tool, arguments)).status is ToolStatus.OK
    second = await run(ctx, tool, arguments)
    assert second.status is ToolStatus.ERROR
    assert second.error_code == "budget_exceeded"
    assert "tool call limit" in second.gaps[0]
    assert len(provider.calls) == 1


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_invalid_input_is_an_error_response_and_calls_no_provider(
    store: EvidenceStore, tool: str
) -> None:
    ctx, provider, _, _ = set_up(store, tool)
    response = await run(ctx, tool, CASES[tool][2])
    assert response.status is ToolStatus.ERROR
    assert response.error_code == "invalid_input"
    assert response.gaps[0].startswith("invalid input:")
    assert provider.calls == []
    assert store.run_summary(RUN_ID).evidence_count == 0


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_unknown_arguments_are_invalid_input(store: EvidenceStore, tool: str) -> None:
    ctx, provider, arguments, _ = set_up(store, tool)
    response = await run(ctx, tool, {**arguments, "surprise": 1})
    assert response.error_code == "invalid_input"
    assert "surprise" in response.gaps[0]
    assert provider.calls == []


FAILURES = [
    ProviderRateLimited("429"),
    ProviderUnavailable("down"),
    ProviderBadResponse("bad"),
    ProviderQuotaExceeded("quota"),
    ProviderNotConfigured("key"),
    KeyError("field"),
    ValueError("value"),
    TypeError("type"),
    RuntimeError("bug in a provider"),
    ZeroDivisionError(),
]


@pytest.mark.parametrize("tool", sorted(CASES))
@pytest.mark.parametrize("failure", FAILURES, ids=lambda f: type(f).__name__)
async def test_no_collection_tool_ever_raises_for_provider_failures(
    store: EvidenceStore, tool: str, failure: Exception
) -> None:
    ctx, _, arguments, _ = set_up(store, tool, fails(failure))
    response = await run(ctx, tool, arguments)
    assert response.status is ToolStatus.ERROR
    assert response.error_code in {"provider_exhausted", "tool_error"}
    assert response.gaps


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_a_failing_store_and_a_failing_event_emitter_do_not_raise(
    store: EvidenceStore, tool: str
) -> None:
    def broken_emit(event: dict[str, Any]) -> None:
        raise RuntimeError("ui went away")

    ctx, _, arguments, _ = set_up(store, tool, emit=broken_emit)
    assert (await run(ctx, tool, arguments)).status is ToolStatus.OK
    store.close()
    response = await run(ctx, tool, arguments)
    assert response.status is ToolStatus.ERROR
    assert response.error_code == "tool_error"


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_every_call_emits_one_tool_called_event(store: EvidenceStore, tool: str) -> None:
    events: list[dict[str, Any]] = []
    ctx, _, arguments, _ = set_up(store, tool, emit=events.append)
    ctx = ctx.for_branch("social")
    ok = await run(ctx, tool, arguments)
    await run(ctx, tool, CASES[tool][2])
    assert [e["type"] for e in events] == ["tool_called", "tool_called"]
    first, second = events
    assert (first["tool"], first["branch"], first["status"], first["count"]) == (
        tool,
        "social",
        "ok",
        ok.count,
    )
    assert first["provider"] == ok.provider_used and first["cost"] == ok.cost_estimate
    assert first["latency_ms"] >= 0 and first["run_id"] == RUN_ID
    assert (second["status"], second["error_code"]) == ("error", "invalid_input")


async def test_the_event_args_are_short_and_leave_out_ids_and_defaults(
    store: EvidenceStore,
) -> None:
    events: list[dict[str, Any]] = []
    ctx, _, _, _ = set_up(store, "web_search", emit=events.append)
    await run(ctx, "web_search", {"query": "q" * 300, "num": 5})
    args = events[0]["args"]
    assert args["num"] == 5 and len(args["query"]) <= 80
    assert "run_id" not in args and "task_id" not in args and "depth" not in args


async def test_repeating_an_identical_call_stores_nothing_new(store: EvidenceStore) -> None:
    ctx, _, arguments, _ = set_up(store, "social_search")
    first = await run(ctx, "social_search", arguments)
    again = await run(ctx, "social_search", arguments)
    assert first.count == 3
    assert (again.count, again.batch_id, again.preview, again.coverage) == (0, None, [], {})
    assert again.status is ToolStatus.OK
    assert any("already collected" in gap for gap in again.gaps)
    assert store.run_summary(RUN_ID).evidence_count == 3


async def test_a_partly_new_batch_counts_only_the_new_items(store: EvidenceStore) -> None:
    ctx, provider, arguments, capability = set_up(store, "social_search")
    await run(ctx, "social_search", arguments)
    provider.behavior = serves(evidence_result(capability, count=5))
    response = await run(ctx, "social_search", arguments)
    assert response.count == 2
    assert store.run_summary(RUN_ID).evidence_count == 5


async def test_preview_is_capped_at_five_short_items(store: EvidenceStore) -> None:
    result = evidence_result("social_search:reddit", count=12)
    long = [i.model_copy(update={"text": i.text + " words" * 100}) for i in result.items]  # type: ignore[union-attr]  # evidence items
    ctx, _, arguments, _ = set_up(
        store, "social_search", serves(result.model_copy(update={"items": long}))
    )
    response = await run(ctx, "social_search", arguments)
    assert response.count == 12 and len(response.preview) == 5
    assert all(len(item["snippet"]) <= 200 for item in response.preview)
    top = store.query(RUN_ID, limit=5, sample="top").items
    assert [p["id"] for p in response.preview] == [i.id for i in top]


async def test_coverage_counts_the_new_batch_by_language_platform_and_type(
    store: EvidenceStore,
) -> None:
    ctx, _, arguments, _ = set_up(store, "social_search")
    response = await run(ctx, "social_search", arguments)
    assert response.coverage == {
        "languages": {"en": 3},
        "platforms": {"reddit": 3},
        "source_types": {"social_post": 3},
    }


async def test_items_without_a_platform_are_counted_by_source_type_only(
    store: EvidenceStore,
) -> None:
    ctx, _, arguments, _ = set_up(store, "web_search")
    response = await run(ctx, "web_search", arguments)
    assert response.coverage["platforms"] == {}
    assert response.coverage["source_types"] == {"web": 3}


async def test_gaps_name_a_missing_language_zero_results_and_snippet_only_data(
    store: EvidenceStore,
) -> None:
    ctx, _, arguments, _ = set_up(
        store, "web_search", serves(evidence_result("web_search", snippet_only=True))
    )
    response = await run(ctx, "web_search", {**arguments, "languages": ["ar", "en"]})
    assert "no ar items in this batch" in response.gaps
    assert not any("no en items" in gap for gap in response.gaps)
    assert any(gap.startswith("snippet-only data") for gap in response.gaps)

    empty_ctx, _, _, _ = set_up(store, "news_coverage", serves(ProviderResult()))
    empty = await run(empty_ctx, "news_coverage", {"query": "nothing"})
    assert empty.status is ToolStatus.OK and empty.count == 0
    assert "no results were returned for this request" in empty.gaps


async def test_provider_warnings_and_the_fallback_flag_reach_the_response(
    store: EvidenceStore,
) -> None:
    result = evidence_result("social_search:reddit").model_copy(
        update={"warnings": ["snippet level"], "next_cursor": "more"}
    )
    ctx, _, arguments, _ = set_up(store, "social_search", serves(result))
    first_choice = await run(ctx, "social_search", arguments)
    assert (first_choice.fallback_used, first_choice.warnings, first_choice.next_cursor) == (
        False,
        ["snippet level"],
        "more",
    )
    fallback_ctx, _ = make_context(store, "social_search:reddit", serves(result), position=2)
    fallback = await run(fallback_ctx, "social_search", {**arguments, "query": "another query"})
    assert fallback.fallback_used is True
    assert fallback.provider_used == "serper"


async def test_the_brief_defaults_fill_what_the_model_leaves_out(store: EvidenceStore) -> None:
    defaults = {
        "languages": ["en"],
        "geo": "EG",
        "since": "2026-01-01",
        "depth": "light",
        "entity": "GitLab",
    }
    ctx, provider, arguments, _ = set_up(store, "social_search", defaults=defaults)
    await run(ctx, "social_search", arguments)
    sent = provider.calls[0]
    assert (
        sent["languages"] == ["en"]
        and sent["geo"] == "EG"
        and sent["since"].isoformat() == "2026-01-01"
    )
    assert sent["max_results"] == 30

    await run(
        ctx, "social_search", {**arguments, "query": "other", "languages": ["ar"], "geo": "SA"}
    )
    assert provider.calls[1]["languages"] == ["ar"] and provider.calls[1]["geo"] == "SA"


async def test_defaults_that_a_tool_does_not_take_are_ignored(store: EvidenceStore) -> None:
    ctx, provider, arguments, _ = set_up(
        store, "fetch_page", defaults={"languages": ["en"], "geo": "EG"}
    )
    assert (await run(ctx, "fetch_page", arguments)).status is ToolStatus.OK
    assert "languages" not in provider.calls[0]


async def test_the_model_cannot_choose_the_run_or_task_id(store: EvidenceStore) -> None:
    ctx, provider, arguments, _ = set_up(store, "social_search")
    await run(ctx, "social_search", {**arguments, "run_id": "someone-elses-run", "task_id": "x"})
    assert provider.calls[0]["run_id"] == RUN_ID
    assert store.run_summary(RUN_ID).evidence_count == 3


@pytest.mark.parametrize(
    ("depth", "requested", "expected"),
    [
        ("light", None, 30),
        ("light", 500, 50),
        ("standard", 120, 120),
        ("deep", 5000, 1000),
        ("light", 5, 5),
    ],
)
async def test_max_results_follow_the_depth_cap(
    store: EvidenceStore, depth: str, requested: int | None, expected: int
) -> None:
    ctx, provider, arguments, _ = set_up(store, "social_search")
    extra = {"max_results": requested} if requested is not None else {}
    await run(ctx, "social_search", {**arguments, "depth": depth, **extra})
    assert provider.calls[0]["max_results"] == expected


async def test_each_tool_has_its_own_default_number_of_results(store: EvidenceStore) -> None:
    expected = {
        "web_search": 10,
        "social_search": 30,
        "social_comments": 30,
        "reviews_fetch": 30,
        "news_coverage": 25,
    }
    for tool, count in expected.items():
        ctx, provider, arguments, _ = set_up(store, tool)
        await run(ctx, tool, arguments)
        assert provider.calls[0]["max_results"] == count, tool


async def test_web_search_num_and_news_type(store: EvidenceStore) -> None:
    ctx, provider, _, _ = set_up(store, "web_search")
    await run(ctx, "web_search", {"query": "gitlab", "num": 7})
    assert provider.calls[0]["max_results"] == 7

    news_ctx, news_provider = make_context(
        store, "news:google_news", serves(evidence_result("news:google_news"))
    )
    response = await run(news_ctx, "web_search", {"query": "gitlab", "search_type": "news"})
    assert response.status is ToolStatus.OK and news_provider.calls[0]["query"] == "gitlab"


async def test_comments_are_sorted_by_top_unless_asked(store: EvidenceStore) -> None:
    ctx, provider, arguments, _ = set_up(store, "social_comments")
    await run(ctx, "social_comments", arguments)
    await run(ctx, "social_comments", {**arguments, "sort": "recent"})
    assert [c["sort"] for c in provider.calls] == ["top", "recent"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"platform": "youtube", "post_url": "not a url"},
        {"platform": "youtube", "post_url": "ftp://youtube.com/watch?v=abc"},
        {"platform": "x", "post_url": "https://www.youtube.com/watch?v=abc"},
        {"platform": "reddit", "post_url": "https://notreddit.com/r/x"},
    ],
)
async def test_social_comments_need_a_post_url_on_the_platform(
    store: EvidenceStore, arguments: dict[str, Any]
) -> None:
    ctx, provider, _, _ = set_up(store, "social_comments")
    response = await run(ctx, "social_comments", arguments)
    assert response.error_code == "invalid_input" and "post_url" in response.gaps[0]
    assert provider.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/abc",
        "https://m.youtube.com/watch?v=abc",
        "https://www.youtube.com/shorts/abc",
    ],
)
async def test_social_comments_accept_the_platforms_own_domains(
    store: EvidenceStore, url: str
) -> None:
    ctx, _, _, _ = set_up(store, "social_comments")
    assert (
        await run(ctx, "social_comments", {"platform": "youtube", "post_url": url})
    ).status is ToolStatus.OK


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "gitlab", "since": "2026-09-30", "until": "2026-09-01"},
        {"query": "gitlab", "languages": ["arabic"]},
        {"query": "gitlab", "geo": "EGY"},
        {"query": "gitlab", "depth": "enormous"},
        {"query": "gitlab", "max_results": 0},
    ],
)
async def test_scope_fields_are_validated(store: EvidenceStore, arguments: dict[str, Any]) -> None:
    ctx, provider, _, _ = set_up(store, "web_search")
    assert (await run(ctx, "web_search", arguments)).error_code == "invalid_input"
    assert provider.calls == []


async def test_reviews_use_the_store_country_or_fall_back_to_geo(store: EvidenceStore) -> None:
    ctx, provider, arguments, _ = set_up(store, "reviews_fetch")
    await run(ctx, "reviews_fetch", {**arguments, "country": "eg"})
    await run(ctx, "reviews_fetch", {**arguments, "target_id_or_url": "com.other.app", "geo": "SA"})
    await run(ctx, "reviews_fetch", {**arguments, "target_id_or_url": "com.third.app"})
    assert [c["country"] for c in provider.calls] == ["EG", "SA", None]
    assert provider.calls[0]["target"] == "com.spotify.music"


async def test_reviews_keep_their_ratings_in_the_store(store: EvidenceStore) -> None:
    ctx, _, arguments, _ = set_up(store, "reviews_fetch")
    response = await run(ctx, "reviews_fetch", arguments)
    assert response.coverage["source_types"] == {"review": 3}
    assert store.query(RUN_ID, limit=1).items[0].source_type.value == "review"


# news_coverage


async def test_news_volume_is_summarized_and_stored_with_the_batch(store: EvidenceStore) -> None:
    result = evidence_result("news:gdelt", count=4).model_copy(
        update={
            "meta": {
                "volume_by_day": {"2026-09-18": 2, "2026-09-20": 5},
                "volume_basis": "returned_articles",
            }
        }
    )
    ctx, _, arguments, _ = set_up(store, "news_coverage", serves(result))
    response = await run(ctx, "news_coverage", arguments)
    assert response.coverage["volume"] == {
        "days": 2,
        "total": 7,
        "peak_day": "2026-09-20",
        "peak_count": 5,
    }
    assert response.batch_id is not None
    meta = store.batch_meta(RUN_ID, response.batch_id)
    assert meta is not None and meta["volume_by_day"] == {"2026-09-18": 2, "2026-09-20": 5}


async def test_news_volume_is_counted_from_the_items_when_the_provider_gives_none(
    store: EvidenceStore,
) -> None:
    ctx, _ = make_context(
        store, "news:google_news", serves(evidence_result("news:google_news", count=3))
    )
    response = await run(ctx, "news_coverage", {"query": "gitlab", "source": "google_news"})
    assert response.coverage["volume"]["days"] == 3 and response.coverage["volume"]["total"] == 3
    assert response.batch_id and store.batch_meta(RUN_ID, response.batch_id)["volume_by_day"]  # type: ignore[index]  # meta exists


# search_interest


async def test_search_interest_preview_summarizes_each_keyword(store: EvidenceStore) -> None:
    ctx, provider, arguments, _ = set_up(store, "search_interest")
    response = await run(
        ctx, "search_interest", {**arguments, "geo": "US", "timeframe": "today 3-m"}
    )
    [entry] = response.preview
    assert (entry["keyword"], entry["first"], entry["last"]) == ("gitlab duo", 10.0, 90.0)
    assert (entry["peak_value"], entry["peak_date"], entry["direction"]) == (
        90.0,
        "2026-02-08",
        "up",
    )
    assert entry["related"] == ["gitlab duo pricing", "gitlab duo agent", "duo vs copilot"]
    assert entry["platform"] == "trend" and "rising" not in entry["snippet"]
    assert "up" in entry["snippet"]
    assert response.coverage == {
        "keywords": ["gitlab duo"],
        "timeframe": "today 12-m",
        "points": {"gitlab duo": 6},
    }
    assert (
        provider.calls[0]["keywords"] == ["gitlab duo"]
        and provider.calls[0]["timeframe"] == "today 3-m"
    )
    assert provider.calls[0]["max_results"] == 1


async def test_search_interest_stores_the_series_and_a_citable_trend_point(
    store: EvidenceStore,
) -> None:
    ctx, _, arguments, _ = set_up(store, "search_interest")
    response = await run(ctx, "search_interest", arguments)
    assert response.count == 1 and response.batch_id
    [series] = store.get_trend_series(RUN_ID)
    assert series.batch_id == response.batch_id and len(series.points) == 6
    [point] = store.query(RUN_ID, limit=5).items
    assert point.source_type.value == "trend_point" and point.id == response.preview[0]["id"]
    assert point.metadata["direction"] == "up"


async def test_search_interest_does_not_store_the_same_keyword_twice(store: EvidenceStore) -> None:
    ctx, _, arguments, _ = set_up(store, "search_interest")
    await run(ctx, "search_interest", arguments)
    again = await run(ctx, "search_interest", arguments)
    assert (again.count, again.batch_id) == (0, None)
    assert len(store.get_trend_series(RUN_ID)) == 1


async def test_search_interest_can_drop_related_queries(store: EvidenceStore) -> None:
    ctx, _, arguments, _ = set_up(store, "search_interest")
    response = await run(ctx, "search_interest", {**arguments, "include_related": False})
    assert response.preview[0]["related"] == []
    assert store.get_trend_series(RUN_ID)[0].related_queries == []


async def test_search_interest_reports_a_flat_zero_series_as_a_gap(store: EvidenceStore) -> None:
    result = ProviderResult(items=[trend_series("obscure term", [0, 0, 0, 0])])
    ctx, _, _, _ = set_up(store, "search_interest", serves(result))
    response = await run(ctx, "search_interest", {"keywords": ["obscure term"]})
    assert response.preview[0]["direction"] == "flat"
    assert response.gaps == ["no search interest data for 'obscure term' (all values are zero)"]


async def test_search_interest_handles_several_keywords(store: EvidenceStore) -> None:
    items = [trend_series("a", [50, 40, 30, 20]), trend_series("b", [10, 10, 11, 10])]
    ctx, _, _, _ = set_up(store, "search_interest", serves(ProviderResult(items=items)))
    response = await run(ctx, "search_interest", {"keywords": ["a", "b"]})
    assert [(p["keyword"], p["direction"]) for p in response.preview] == [
        ("a", "down"),
        ("b", "flat"),
    ]
    assert response.count == 2


@pytest.mark.parametrize("keywords", [[], ["a", "b", "c", "d", "e", "f"], [""]])
async def test_search_interest_needs_one_to_five_keywords(
    store: EvidenceStore, keywords: list[str]
) -> None:
    ctx, _, _, _ = set_up(store, "search_interest")
    assert (await run(ctx, "search_interest", {"keywords": keywords})).error_code == "invalid_input"


async def test_search_interest_rejects_an_unknown_timeframe(store: EvidenceStore) -> None:
    ctx, _, _, _ = set_up(store, "search_interest")
    response = await run(
        ctx, "search_interest", {"keywords": ["a"], "timeframe": "last week or so"}
    )
    assert response.error_code == "invalid_input" and "timeframe" in response.gaps[0]


@pytest.mark.parametrize(
    "timeframe", ["today 12-m", "today 5-y", "now 7-d", "all", "2026-01-01 2026-03-01"]
)
async def test_search_interest_accepts_google_timeframes(
    store: EvidenceStore, timeframe: str
) -> None:
    ctx, _, _, _ = set_up(store, "search_interest")
    assert (
        await run(ctx, "search_interest", {"keywords": ["a"], "timeframe": timeframe})
    ).status is ToolStatus.OK


# the compact form the model reads


async def test_the_model_sees_a_compact_json_string(store: EvidenceStore) -> None:
    from edrak.agents.customer_trends.tools.registry import compact_json

    ctx, _, arguments, _ = set_up(store, "social_search")
    response = await run(ctx, "social_search", arguments)
    data = json.loads(compact_json(response))
    assert data["status"] == "ok" and data["count"] == 3 and data["batch_id"] == response.batch_id
    assert (
        "next_cursor" not in data
        and "error_code" not in data
        and "fallback_used" not in data
        or data["fallback_used"]
    )
    assert len(data["preview"]) == 3
    assert len(compact_json(response)) < 2500


async def test_an_error_response_is_compact_too(store: EvidenceStore) -> None:
    from edrak.agents.customer_trends.tools.registry import compact_json

    ctx, _, _, _ = set_up(store, "social_search")
    response = await run(ctx, "social_search", {"platform": "x", "query": "q"})
    data = json.loads(compact_json(response))
    assert data == {
        "status": "error",
        "count": 0,
        "gaps": data["gaps"],
        "error_code": "invalid_input",
    }
