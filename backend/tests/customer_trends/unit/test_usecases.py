from pathlib import Path

import pytest
from pydantic import ValidationError

from edrak.agents.customer_trends.schemas.common import UseCase
from edrak.agents.customer_trends.usecases import load_use_cases


@pytest.mark.parametrize("use_case", list(UseCase))
def test_every_use_case_has_the_default_thresholds(use_case: UseCase) -> None:
    config = load_use_cases().for_use_case(use_case)
    assert (config.min_evidence_total, config.min_platforms, config.min_platform_items) == (
        30,
        2,
        20,
    )
    assert config.min_language_items == 10
    assert config.default_focus and config.query_hints and config.emphasis


def test_the_competitive_intelligence_emphasis_matches_the_pilot() -> None:
    config = load_use_cases().for_use_case(UseCase.COMPETITIVE_INTELLIGENCE)
    for phrase in (
        "GitLab",
        "GitHub Copilot",
        "Atlassian",
        "share of voice",
        "reviews",
        "news coverage",
    ):
        assert phrase in config.emphasis, phrase
    assert "pain points" in config.emphasis and "unmet needs" in config.emphasis
    assert config.default_focus == ["pain_points", "sentiment", "competitor_gaps"]
    assert config.require_trend_series is False
    assert config.reviews.required_with_competitors and config.reviews.severity == "critical"


def test_the_product_launch_emphasis_is_the_evidence_chain() -> None:
    config = load_use_cases().for_use_case(UseCase.PRODUCT_LAUNCH)
    for phrase in ("problem exists", "competitors address it poorly", "demand is rising"):
        assert phrase in config.emphasis, phrase
    assert config.default_focus == ["pain_points", "demand", "competitor_gaps"]
    assert config.require_trend_series is True
    assert config.reviews.required_with_competitors


def test_the_market_entry_emphasis_is_demand_sentiment_and_competitor_presence() -> None:
    config = load_use_cases().for_use_case(UseCase.MARKET_ENTRY)
    for phrase in ("demand trend", "local sentiment", "present", "target country and languages"):
        assert phrase in config.emphasis, phrase
    assert config.default_focus == ["demand", "sentiment", "competitor_gaps"]
    assert config.require_trend_series is True
    assert not config.reviews.required_with_competitors


def test_no_emphasis_tells_the_reader_what_to_decide() -> None:
    for use_case in UseCase:
        emphasis = load_use_cases().for_use_case(use_case).emphasis
        assert "never" in emphasis and chr(0x2014) not in emphasis


def test_a_missing_use_case_or_unknown_key_is_refused(tmp_path: Path) -> None:
    text = Path("src/edrak/agents/customer_trends/config/use_cases.yaml").read_text(
        encoding="utf-8"
    )
    broken = tmp_path / "use_cases.yaml"
    broken.write_text(text.replace("market_entry:", "market_exit:"), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_use_cases(broken)
    extra = tmp_path / "extra.yaml"
    extra.write_text(
        text.replace("min_platforms: 2", "min_platforms: 2\n  surprise: 1", 1), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_use_cases(extra)
