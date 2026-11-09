from typing import Any

import pytest

from edrak.agents.customer_trends.schemas.analysis import ThemeAggregate
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult, Finding
from edrak.agents.customer_trends.ui.components import format as fmt
from tests.customer_trends.factories import make_evidence
from tests.customer_trends.tool_helpers import trend_series

ARABIC = "الدعم الفني بطيء جدا والأسعار مرتفعة"


def test_badges_are_colored_by_status_and_level() -> None:
    assert fmt.badge("complete") == ":green-badge[complete]"
    assert fmt.badge("insufficient") == ":red-badge[insufficient]"
    assert fmt.badge("partial") == ":orange-badge[partial]"
    assert fmt.badge("high") == ":green-badge[high]" and fmt.badge("low") == ":red-badge[low]"
    assert fmt.badge("something else") == ":gray-badge[something else]"


@pytest.mark.parametrize(
    ("text", "rtl"),
    [
        (ARABIC, True),
        ("The support team is slow", False),
        ("GitLab Duo " + ARABIC, True),
        ("a long English sentence about GitLab with one كلمة inside it", False),
        ("12345 !!!", False),
        ("", False),
    ],
)
def test_text_is_right_to_left_when_most_of_its_letters_are_arabic(text: str, rtl: bool) -> None:
    assert fmt.is_rtl(text) is rtl


def test_the_html_block_is_right_to_left_for_arabic_and_always_escaped() -> None:
    block = fmt.rtl_html(ARABIC)
    assert block.startswith('<div dir="rtl" style="text-align: right">') and ARABIC in block
    plain = fmt.rtl_html("a <script>alert(1)</script> & b\nnext")
    assert plain == "<div>a &lt;script&gt;alert(1)&lt;/script&gt; &amp; b<br>next</div>"
    assert "dir=" not in plain


def test_arguments_are_summarized_and_long_values_cut() -> None:
    summary = fmt.args_summary({"platform": "reddit", "keywords": ["a", "b"], "query": "x" * 200})
    assert summary.startswith("platform=reddit keywords=a, b query=" + "x" * 60)
    assert len(summary) < 120 and fmt.args_summary({}) == ""


def tool_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "tool_called",
        "ts": "2026-10-07T01:00:00.000+00:00",
        "tool": "social_search",
        "branch": "social",
        "args": {"platform": "x"},
        "provider": "apify",
        "fallback_used": False,
        "status": "ok",
        "count": 25,
        "latency_ms": 310,
        "cost": 0.0123456,
        "error_code": None,
    }
    return {**event, **overrides}


def test_tool_calls_become_table_rows_and_other_events_are_left_out() -> None:
    rows = fmt.tool_rows(
        [
            {"type": "node_started", "node": "social"},
            tool_event(),
            tool_event(
                provider=None,
                branch=None,
                fallback_used=True,
                status="error",
                error_code="budget_exceeded",
            ),
        ]
    )
    assert rows[0] == {
        "time": "01:00:00.000",
        "branch": "social",
        "tool": "social_search",
        "args": "platform=x",
        "provider": "apify",
        "fallback": "",
        "status": "ok",
        "count": 25,
        "latency_ms": 310,
        "cost": 0.0123,
        "error": "",
    }
    assert rows[1]["fallback"] == "yes" and rows[1]["error"] == "budget_exceeded"
    assert rows[1]["provider"] == "" and len(rows) == 2


def started(node: str) -> dict[str, Any]:
    return {"type": "node_started", "node": node}


def finished(node: str, status: str = "ok") -> dict[str, Any]:
    return {"type": "node_finished", "node": node, "status": status}


def test_the_timeline_shows_pending_running_done_and_error_nodes() -> None:
    events = [
        started("intake"),
        finished("intake"),
        started("plan_queries"),
        finished("plan_queries"),
        started("social"),
        started("demand"),
        finished("demand", "error"),
    ]
    states = {row["node"]: row["state"] for row in fmt.node_states(events, finished=False)}
    assert states["intake"] == "done" and states["social"] == "running"
    assert states["demand"] == "error" and states["reviews"] == "pending"
    assert list(states) == list(fmt.NODE_ORDER)


def test_a_finished_run_marks_the_nodes_it_never_reached_as_skipped() -> None:
    states = {
        r["node"]: r["state"]
        for r in fmt.node_states([started("intake"), finished("intake")], finished=True)
    }
    assert states["intake"] == "done" and states["social"] == "skipped"


def test_a_node_that_ran_twice_shows_its_latest_run() -> None:
    events = [started("plan_queries"), finished("plan_queries"), started("plan_queries")]
    states = {r["node"]: r["state"] for r in fmt.node_states(events, finished=False)}
    assert states["plan_queries"] == "running"


def test_the_budget_meter_counts_provider_calls_cost_and_elapsed_time() -> None:
    events = [
        tool_event(ts="2026-10-07T01:00:00.000+00:00", cost=0.01),
        tool_event(ts="2026-10-07T01:00:05.500+00:00", cost=0.02),
        tool_event(
            tool="compute_metrics", provider=None, cost=0.0, ts="2026-10-07T01:00:09.000+00:00"
        ),
        tool_event(status="error", error_code="budget_exceeded", cost=0.5),
    ]
    meter = {
        m["name"]: m
        for m in fmt.budget_meter(
            events, {"max_tool_calls": 10, "max_cost_usd": 0.1, "max_seconds": 20}
        )
    }
    assert meter["tool calls"]["used"] == 2.0 and meter["tool calls"]["share"] == pytest.approx(0.2)
    assert meter["cost USD"]["used"] == 0.03 and meter["cost USD"]["share"] == pytest.approx(0.3)
    assert meter["seconds"]["used"] == 9.0 and meter["seconds"]["share"] == pytest.approx(0.45)


def test_the_budget_meter_never_goes_past_full_and_copes_with_no_events() -> None:
    over = [tool_event() for _ in range(5)]
    assert (
        fmt.budget_meter(over, {"max_tool_calls": 2, "max_cost_usd": 1, "max_seconds": 1})[0][
            "share"
        ]
        == 1.0
    )
    empty = fmt.budget_meter([], {"max_tool_calls": 2, "max_cost_usd": 1, "max_seconds": 1})
    assert [m["used"] for m in empty] == [0.0, 0.0, 0.0]


def test_gap_and_replan_events_become_notices() -> None:
    notices = fmt.notices(
        [
            {"type": "gap_found", "severity": "critical", "description": "too few platforms"},
            {"type": "replan", "replan_count": 1, "gap_ids": ["platforms", "branch_social"]},
            tool_event(),
        ]
    )
    assert notices == [
        "gap (critical): too few platforms",
        "replan 1: closing platforms, branch_social",
    ]


def aggregate(label: str = "slow support", **overrides: Any) -> ThemeAggregate:
    fields: dict[str, Any] = {
        "theme_label": label,
        "count": 12,
        "share": 0.1556,
        "sentiment_mix": {"negative": 0.75, "positive": 0.25},
        "by_platform": {"reddit": 8, "x": 4},
        "by_language": {"ar": 5, "en": 7},
        "recent_growth": 0.5,
    }
    return ThemeAggregate(**{**fields, **overrides})


def test_themes_become_rows_charts_data_and_splits() -> None:
    [row] = fmt.theme_rows([aggregate()])
    assert row == {
        "theme": "slow support",
        "items": 12,
        "share %": 15.6,
        "negative %": 75.0,
        "positive %": 25.0,
        "growth": 0.5,
        "platforms": "reddit 8, x 4",
        "languages": "ar 5, en 7",
    }
    assert fmt.sentiment_rows([aggregate()]) == [
        {"theme": "slow support", "sentiment": "negative", "share": 75.0},
        {"theme": "slow support", "sentiment": "positive", "share": 25.0},
    ]
    assert fmt.split_rows([aggregate()], "by_platform") == [
        {"theme": "slow support", "name": "reddit", "items": 8},
        {"theme": "slow support", "name": "x", "items": 4},
    ]
    assert fmt.split_rows([aggregate()], "by_language")[0]["name"] == "ar"


def test_coverage_is_flattened_into_labelled_rows() -> None:
    rows = fmt.coverage_rows(
        {"by_platform": {"reddit": 30}, "by_source_type": {"news": 12}, "by_language": {"ar": 5}}
    )
    assert rows == [
        {"by": "platform", "name": "reddit", "items": 30},
        {"by": "source type", "name": "news", "items": 12},
        {"by": "language", "name": "ar", "items": 5},
    ]


def test_trend_series_give_a_summary_and_points() -> None:
    series = trend_series("gitlab duo", [10, 20, 35, 50, 70, 90])
    [summary] = fmt.trend_summaries([series])
    assert summary["keyword"] == "gitlab duo" and summary["direction"] == "up"
    assert summary["first"] == 10 and summary["last"] == 90
    assert fmt.trend_points(series)[0] == {"date": "2026-01-04", "interest": 10.0}
    assert fmt.trend_summaries([series.model_copy(update={"points": []})]) == []


def test_a_finding_card_shows_its_evidence_and_marks_what_is_missing() -> None:
    item = make_evidence("Support is slow", platform=Platform.REDDIT, url="https://r.example/1")
    snippet = make_evidence(
        "A news snippet", platform=None, source_type=SourceType.NEWS, snippet_only=True
    )
    finding = Finding(
        id="f1",
        type="pain_point",
        claim="Support is slow.",
        confidence="medium",
        evidence_ids=[item.id, snippet.id, "f" * 16],
        metrics={"count": 3},
        caveats=["small sample"],
        related_gaps=["language_ar"],
    )
    view = fmt.finding_view(finding, {item.id: item, snippet.id: snippet})
    assert (view["id"], view["type"], view["confidence"]) == ("f1", "pain_point", "medium")
    assert view["metrics"] == {"count": 3} and view["caveats"] == ["small sample"]
    first, second, third = view["evidence"]
    assert (
        first["platform"] == "reddit" and first["url"] == "https://r.example/1" and first["found"]
    )
    assert second["platform"] == "news" and second["snippet_only"] is True
    assert third == {"id": "f" * 16, "found": False, "platform": "", "url": None, "text": ""}


def test_evidence_rows_have_cut_text_and_total_engagement() -> None:
    item = make_evidence(
        "long " * 100, platform=Platform.X, engagement={"likes": 4, "replies": 2, "views": 900}
    )
    [row] = fmt.evidence_rows([item])
    assert row["platform"] == "x" and row["engagement"] == 6 and len(row["text"]) == 140
    assert row["published"] == "2026-07-01" and row["snippet_only"] is False


def test_provider_health_rows_put_open_breakers_first_and_show_what_a_provider_reported() -> None:
    report = {
        "apify": {"capabilities": 7, "breaker_open": False, "failures": 0, "key_in_use": "1 of 2"},
        "gdelt": {"capabilities": 1, "breaker_open": False, "failures": 1},
        "serper": {"capabilities": 3, "breaker_open": True, "failures": 3},
    }
    rows = fmt.health_rows(report)
    assert [row["provider"] for row in rows] == ["serper", "apify", "gdelt"]
    assert rows[0] == {
        "provider": "serper",
        "status": "breaker open",
        "failures in a row": 3,
        "details": "",
    }
    assert rows[1]["details"] == "key_in_use: 1 of 2" and rows[2]["status"] == "ok"
    assert fmt.health_rows({}) == []


def test_the_provenance_lists_every_call_with_what_was_asked() -> None:
    call = {
        "branch": "social",
        "tool": "social_search",
        "args": {"platform": "x", "query": "جيت لاب"},
        "provider": "apify",
        "status": "ok",
        "count": 0,
        "latency_ms": 3372,
    }
    result = CustomerTrendsResult.model_construct(provenance={"calls": [call]})
    [row] = fmt.provenance_rows(result)["calls"]
    assert row == {
        "branch": "social",
        "tool": "social_search",
        "args": '{"platform": "x", "query": "جيت لاب"}',
        "provider": "apify",
        "status": "ok",
        "count": 0,
        "ms": 3372,
        "error": None,
        "message": None,
    }
    assert fmt.provenance_rows(CustomerTrendsResult.model_construct(provenance={}))["calls"] == []
