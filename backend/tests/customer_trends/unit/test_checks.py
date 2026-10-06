import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypedDict

import pytest
from evals.customer_trends import checks
from evals.customer_trends.checks import CheckFailure, run_checks
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from edrak.agents.customer_trends.runner import run_task
from edrak.agents.customer_trends.schemas.common import Confidence, Platform
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink
from tests.customer_trends.factories import load_brief, make_evidence
from tests.customer_trends.graph_helpers import scenario_settings

USE_CASES = ["competitive_intelligence", "product_launch", "market_entry"]


def demo_run(tmp_path: Path, use_case: str) -> tuple[CustomerTrendsResult, Settings]:
    settings = scenario_settings(tmp_path).model_copy(
        update={"edrak_provider_mode": "fixture", "edrak_fake_llm": True}
    )
    result = asyncio.run(run_task(load_brief(use_case), settings=settings))
    return result, settings


@pytest.fixture(scope="module")
def ci(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[CustomerTrendsResult, Settings]]:
    yield demo_run(tmp_path_factory.mktemp("checks"), "competitive_intelligence")


def run_on(result: CustomerTrendsResult, settings: Settings, **kwargs: Any) -> list[CheckFailure]:
    with EvidenceStore.from_settings(settings) as store:
        return run_checks(result, store, **kwargs)


def names(failures: list[CheckFailure]) -> set[str]:
    return {failure.check for failure in failures}


@pytest.mark.parametrize("use_case", USE_CASES)
def test_the_sample_runs_pass_every_check(tmp_path: Path, use_case: str) -> None:
    result, settings = demo_run(tmp_path, use_case)
    assert result.control_summary.status == "complete" and result.findings
    assert run_on(result, settings, checkpoints=settings.data_dir / "checkpoints.db") == []


def with_finding(result: CustomerTrendsResult, **changes: Any) -> CustomerTrendsResult:
    first = result.findings[0].model_copy(update=changes)
    return result.model_copy(update={"findings": [first, *result.findings[1:]]})


def test_a_result_that_does_not_validate_fails_the_schema_check(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    summary = result.control_summary.model_copy(update={"headline": "word " * 80})
    broken = CustomerTrendsResult.model_construct(**{**result.__dict__, "control_summary": summary})
    failures = run_on(broken, settings)
    assert "schema" in names(failures)
    assert any("at most 60 words" in f.detail for f in failures)


def test_a_finding_must_cite_evidence_that_exists(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    ghost = run_on(with_finding(result, evidence_ids=["f" * 16]), settings)
    assert any(
        f.check == "evidence" and "ids not in the store" in f.detail and f.subject == "f1"
        for f in ghost
    )
    none = run_on(with_finding(result, evidence_ids=[]), settings)
    assert any(f.check == "evidence" and "cites no evidence" in f.detail for f in none)


def test_every_number_in_a_claim_must_be_in_the_metrics(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    wrong = run_on(with_finding(result, claim="Support is raised in 99% of the items."), settings)
    assert any(f.check == "numbers" and "not in the metrics: 99" in f.detail for f in wrong)
    unknown = run_on(with_finding(result, metrics={"metric_id": "m_nope", "x": 1}), settings)
    assert any("unknown metric_id m_nope" in f.detail for f in unknown if f.check == "numbers")


def test_verdict_language_fails_in_a_claim_and_in_the_headline(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    claim = run_on(
        with_finding(result, claim="GitLab should enter the market.", metrics={}), settings
    )
    assert any(f.check == "verdicts" and f.subject == "f1" for f in claim)
    summary = result.control_summary.model_copy(update={"headline": "We recommend launching now."})
    headline = run_on(result.model_copy(update={"control_summary": summary}), settings)
    assert any(f.check == "verdicts" and f.subject == "headline" for f in headline)


def test_high_confidence_needs_the_evidence_it_claims(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    thin = with_finding(
        result, confidence=Confidence.HIGH, evidence_ids=result.findings[0].evidence_ids[:2]
    )
    failures = run_on(thin, settings)
    assert any(f.check == "high_confidence" and "needs 10 from 2" in f.detail for f in failures)
    assert result.findings[2].confidence is Confidence.HIGH
    assert not [f for f in run_on(result, settings) if f.check == "high_confidence"]


@pytest.mark.parametrize(
    ("update", "fragment"),
    [
        ({"findings_count": 9}, "findings_count 9"),
        ({"evidence_count": 3}, "evidence counts differ"),
        ({"status": "partial"}, "status is partial but its gaps say complete"),
        ({"gaps": ["something else"]}, "listed gaps differ"),
        ({"overall_confidence": Confidence.LOW}, "overall_confidence does not follow"),
    ],
)
def test_the_control_summary_must_agree_with_the_run(
    ci: tuple[CustomerTrendsResult, Settings], update: dict[str, Any], fragment: str
) -> None:
    result, settings = ci
    summary = result.control_summary.model_copy(update=update)
    failures = run_on(result.model_copy(update={"control_summary": summary}), settings)
    assert any(f.check == "summary" and fragment in f.detail for f in failures), failures


def test_a_critical_gap_means_partial_and_a_missing_evidence_gap_means_insufficient(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    gap = {"id": "platforms", "severity": "critical", "description": "d", "suggested_action": "a"}
    open_gaps = {**result.provenance, "open_gaps": [gap]}
    summary = result.control_summary.model_copy(update={"gaps": ["d"], "status": "partial"})
    partial = result.model_copy(update={"provenance": open_gaps, "control_summary": summary})
    assert not [f for f in run_on(partial, settings) if "status" in f.detail]
    thin = {**gap, "id": "evidence_total"}
    summary = summary.model_copy(update={"status": "partial"})
    insufficient = partial.model_copy(
        update={"provenance": {**open_gaps, "open_gaps": [thin]}, "control_summary": summary}
    )
    assert any("gaps say insufficient" in f.detail for f in run_on(insufficient, settings))


def test_arabic_text_must_be_stored_as_written(tmp_path: Path) -> None:
    result, settings = demo_run(tmp_path, "competitive_intelligence")
    with EvidenceStore.from_settings(settings) as store:
        bad = [
            make_evidence("no arabic letters here at all", platform=Platform.X, language="ar"),
            make_evidence(
                "\\u0627\\u0644\\u062f\\u0639\\u0645 escaped " + "الدعم",
                platform=Platform.X,
                language="ar",
            ),
            make_evidence("damaged � الدعم", platform=Platform.X, language="ar"),
        ]
        store.add_batch(result.run_id, result.task_id, "extra", bad)
        failures = [f for f in run_checks(result, store) if f.check == "arabic"]
    assert len(failures) == 3
    assert {f.detail for f in failures} == {
        "an Arabic item holds no Arabic letter",
        "the text is escaped or damaged",
    }


def test_a_quote_that_is_not_part_of_a_stored_item_fails(
    ci: tuple[CustomerTrendsResult, Settings],
) -> None:
    result, settings = ci
    first = result.theme_aggregates[0]
    forged = first.model_copy(update={"representative_quotes": ["a sentence nobody wrote"]})
    failures = run_on(result.model_copy(update={"theme_aggregates": [forged]}), settings)
    assert any(f.check == "arabic" and "quote is not verbatim" in f.detail for f in failures)


class Plain(TypedDict):
    text: str


def checkpoint_db(path: Path, run_id: str, state: dict[str, str]) -> Path:
    graph = StateGraph(Plain)
    graph.add_node("copy", lambda s: {"text": s["text"]})
    graph.add_edge(START, "copy")
    graph.add_edge("copy", END)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph.compile(checkpointer=saver).invoke(state, {"configurable": {"thread_id": run_id}})
    return path


def test_item_text_in_a_checkpoint_fails_the_state_check(
    tmp_path: Path, ci: tuple[CustomerTrendsResult, Settings]
) -> None:
    result, settings = ci
    with EvidenceStore.from_settings(settings) as store:
        quotes = {q for a in result.theme_aggregates for q in a.representative_quotes}
        text = next(
            i.text
            for i in store.query(result.run_id, limit=100, sample="recent").items
            if i.text not in quotes and len(i.text) >= 30
        )
    db = checkpoint_db(tmp_path / "leaky.db", result.run_id, {"text": text})
    failures = run_on(result, settings, checkpoints=db)
    assert any(f.check == "state" and "item text(s) in the state" in f.detail for f in failures)
    clean = checkpoint_db(tmp_path / "clean.db", result.run_id, {"text": "short and harmless"})
    assert not [f for f in run_on(result, settings, checkpoints=clean) if f.check == "state"]


def test_a_missing_checkpoint_file_is_not_a_failure(
    ci: tuple[CustomerTrendsResult, Settings], tmp_path: Path
) -> None:
    result, settings = ci
    assert run_on(result, settings, checkpoints=tmp_path / "nothing.db") == []


def test_the_failures_read_clearly() -> None:
    assert (
        str(CheckFailure("numbers", "not in the metrics: 99", "f1"))
        == "numbers [f1]: not in the metrics: 99"
    )
    assert str(CheckFailure("schema", "bad")) == "schema: bad"


def test_the_command_line_passes_a_sound_run_and_fails_a_broken_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result, settings = demo_run(tmp_path, "competitive_intelligence")
    monkeypatch.setenv("EDRAK_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("ARTIFACTS_PATH", str(settings.artifacts_dir))
    get_settings.cache_clear()
    assert checks.main(["--run-id", result.run_id]) == 0
    assert f"all checks passed for {result.run_id}" in capsys.readouterr().out

    forged = with_finding(result, claim="Support is raised in 99% of the items.")
    with EvidenceStore.from_settings(settings) as store:
        LocalSink(store, settings.artifacts_dir).write_result(forged)
    assert checks.main(["--run-id", result.run_id]) == 1
    out = capsys.readouterr().out
    assert "1 check failure(s)" in out and "numbers [f1]: not in the metrics: 99" in out

    assert checks.main(["--run-id", "run-nope"]) == 1
    assert "no result for run run-nope" in capsys.readouterr().err


def test_check_names_are_the_documented_ones() -> None:
    assert list(checks.CHECKS) == [
        "schema",
        "evidence",
        "numbers",
        "verdicts",
        "high_confidence",
        "summary",
        "arabic",
    ]
