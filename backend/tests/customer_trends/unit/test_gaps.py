import pytest

from edrak.agents.customer_trends.gaps import (
    Coverage,
    Gap,
    coverage_of,
    critical_gaps,
    find_gaps,
    run_status,
)
from edrak.agents.customer_trends.schemas.common import SourceType, UseCase
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.usecases import UseCaseConfig, load_use_cases
from tests.customer_trends.factories import (
    RUN_ID,
    TASK_ID,
    load_brief,
    make_evidence,
)
from tests.customer_trends.tool_helpers import trend_series


def config_for(use_case: UseCase) -> UseCaseConfig:
    return load_use_cases().for_use_case(use_case)


def adequate(**overrides: object) -> Coverage:
    base: dict[str, object] = {
        "total": 60,
        "by_platform": {"reddit": 30, "x": 25},
        "by_language": {"ar": 20, "en": 40},
        "reviews": 5,
        "trend_series": 1,
    }
    return Coverage(**{**base, **overrides})  # type: ignore[arg-type]  # keyword overrides in tests


def brief_of(use_case: str, **changes: object) -> TaskBrief:
    return load_brief(use_case).model_copy(update=changes)


def ids(gaps: list[Gap]) -> list[str]:
    return [gap.id for gap in gaps]


@pytest.mark.parametrize("use_case", list(UseCase))
def test_adequate_coverage_has_no_gaps_for_any_use_case(use_case: UseCase) -> None:
    brief = brief_of(use_case.value)
    gaps = find_gaps(brief, config_for(use_case), adequate(), review_targets_planned=True)
    assert gaps == []
    assert run_status(gaps) == "complete"


def test_too_little_evidence_is_critical_and_makes_the_run_insufficient() -> None:
    brief = brief_of("competitive_intelligence")
    config = config_for(UseCase.COMPETITIVE_INTELLIGENCE)
    assert ids(find_gaps(brief, config, adequate(total=30))) == []
    [gap] = find_gaps(brief, config, adequate(total=29))
    assert gap.id == "evidence_total" and gap.severity == "critical"
    assert gap.description == "only 29 evidence items were collected; at least 30 are needed"
    assert run_status([gap]) == "insufficient"


def test_platforms_need_enough_items_each_not_just_to_appear() -> None:
    brief = brief_of("competitive_intelligence")
    config = config_for(UseCase.COMPETITIVE_INTELLIGENCE)
    thin = adequate(by_platform={"reddit": 40, "x": 19, "youtube": 3})
    [gap] = find_gaps(brief, config, thin)
    assert (gap.id, gap.severity) == ("platforms", "critical")
    assert gap.description == "1 platform(s) have at least 20 items; at least 2 are needed"
    assert gap.suggested_action == (
        "collect at least 20 items from more of: x, tiktok, instagram, facebook, youtube"
    )
    assert find_gaps(brief, config, adequate(by_platform={"reddit": 20, "x": 20})) == []
    assert ids(find_gaps(brief, config, adequate(by_platform={}))) == ["platforms"]


def test_a_trend_series_is_required_by_the_use_case_or_by_a_demand_focus() -> None:
    no_trend = adequate(trend_series=0)
    ci = config_for(UseCase.COMPETITIVE_INTELLIGENCE)
    assert find_gaps(brief_of("competitive_intelligence"), ci, no_trend) == []
    with_demand = brief_of("competitive_intelligence", focus=["pain_points", "demand"])
    [minor] = find_gaps(with_demand, ci, no_trend)
    assert (minor.id, minor.severity) == ("trend_series", "minor")
    for use_case in ("product_launch", "market_entry"):
        brief = brief_of(use_case)
        [gap] = find_gaps(brief, config_for(UseCase(use_case)), no_trend)
        assert (gap.id, gap.severity) == ("trend_series", "critical")
    assert find_gaps(brief_of("market_entry"), config_for(UseCase.MARKET_ENTRY), adequate()) == []


def test_reviews_are_missing_only_when_planned_for_named_competitors() -> None:
    none = adequate(reviews=0)
    pl = config_for(UseCase.PRODUCT_LAUNCH)
    brief = brief_of("product_launch")
    [gap] = find_gaps(brief, pl, none, review_targets_planned=True)
    assert (gap.id, gap.severity) == ("reviews", "critical")
    assert find_gaps(brief, pl, none, review_targets_planned=False) == []
    assert find_gaps(brief, pl, adequate(reviews=1), review_targets_planned=True) == []
    assert (
        find_gaps(brief_of("product_launch", competitors=[]), pl, none, review_targets_planned=True)
        == []
    )
    me = config_for(UseCase.MARKET_ENTRY)
    assert find_gaps(brief_of("market_entry"), me, none, review_targets_planned=True) == []


def test_a_requested_language_under_ten_items_is_a_minor_gap() -> None:
    brief = brief_of("competitive_intelligence")  # languages en and ar
    config = config_for(UseCase.COMPETITIVE_INTELLIGENCE)
    [gap] = find_gaps(brief, config, adequate(by_language={"en": 50, "ar": 9}))
    assert (gap.id, gap.severity) == ("language_ar", "minor")
    assert gap.description == "9 item(s) in 'ar'; at least 10 are needed"
    assert find_gaps(brief, config, adequate(by_language={"en": 50, "ar": 10})) == []
    both = find_gaps(brief, config, adequate(by_language={"fr": 80}))
    assert ids(both) == ["language_en", "language_ar"]
    assert run_status(both) == "complete"


def test_a_branch_that_reported_an_error_is_a_critical_gap() -> None:
    brief = brief_of("competitive_intelligence")
    config = config_for(UseCase.COMPETITIVE_INTELLIGENCE)
    gaps = find_gaps(
        brief, config, adequate(), branch_errors={"social": "provider_exhausted", "demand": ""}
    )
    [gap] = gaps
    assert gap.id == "branch_social" and gap.severity == "critical"
    assert "provider_exhausted" in gap.description and "social" in gap.suggested_action
    assert run_status(gaps) == "partial"


def test_gaps_come_in_a_stable_order() -> None:
    brief = brief_of("product_launch")
    config = config_for(UseCase.PRODUCT_LAUNCH)
    empty = Coverage(total=0, by_platform={}, by_language={}, reviews=0, trend_series=0)
    gaps = find_gaps(
        brief, config, empty, review_targets_planned=True, branch_errors={"reviews": "boom"}
    )
    assert ids(gaps) == [
        "branch_reviews",
        "evidence_total",
        "platforms",
        "trend_series",
        "reviews",
        "language_ar",
        "language_en",
    ]
    assert [g.id for g in critical_gaps(gaps)] == [
        "branch_reviews",
        "evidence_total",
        "platforms",
        "trend_series",
        "reviews",
    ]


def test_every_gap_says_what_to_do_about_it() -> None:
    brief = brief_of("product_launch")
    empty = Coverage(total=0, by_platform={}, by_language={}, reviews=0, trend_series=0)
    for gap in find_gaps(
        brief,
        config_for(UseCase.PRODUCT_LAUNCH),
        empty,
        review_targets_planned=True,
        branch_errors={"social": "x"},
    ):
        assert gap.description and gap.suggested_action


@pytest.mark.parametrize(
    ("gaps", "status"),
    [
        ([], "complete"),
        (
            [Gap(id="language_ar", severity="minor", description="d", suggested_action="a")],
            "complete",
        ),
        (
            [Gap(id="platforms", severity="critical", description="d", suggested_action="a")],
            "partial",
        ),
        (
            [
                Gap(id="platforms", severity="critical", description="d", suggested_action="a"),
                Gap(
                    id="evidence_total", severity="critical", description="d", suggested_action="a"
                ),
            ],
            "insufficient",
        ),
    ],
)
def test_run_status(gaps: list[Gap], status: str) -> None:
    assert run_status(gaps) == status


def test_coverage_is_read_from_the_store_without_trend_points(loaded_store: EvidenceStore) -> None:
    trend = make_evidence(
        "Search interest is up", platform=None, source_type=SourceType.TREND_POINT
    )
    batch_id, _, _ = loaded_store.add_batch(RUN_ID, TASK_ID, "trend", [trend])
    loaded_store.save_trend_series(
        RUN_ID, [trend_series("gitlab duo").model_copy(update={"batch_id": batch_id})]
    )
    coverage = coverage_of(loaded_store, RUN_ID)
    assert coverage.total == 40
    assert coverage.by_platform == {
        "reddit": 10,
        "x": 8,
        "youtube": 6,
        "facebook": 3,
        "tiktok": 3,
        "instagram": 2,
    }
    assert coverage.by_language == {"en": 25, "ar": 15}
    assert (coverage.reviews, coverage.trend_series) == (3, 1)
