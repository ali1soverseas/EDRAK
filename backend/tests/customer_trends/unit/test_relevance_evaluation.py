import csv
import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from edrak.agents.customer_trends.llm.fake import FunctionChatModel, Scripted
from edrak.agents.customer_trends.relevance.evaluation import (
    COLUMNS,
    EvaluationError,
    LabeledComment,
    evaluate,
    format_report,
    read_rows,
)
from edrak.agents.customer_trends.settings import Settings
from tests.customer_trends.factories import BRIEFS, load_brief

REPO = BRIEFS.parents[3]
TEMPLATE = REPO / "backend/evals/customer_trends/relevance/template.csv"
SCRIPT = REPO / "scripts/customer_trends/eval_relevance_filter.py"
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings fills the other fields
BRIEF = load_brief().model_copy(update={"aliases": ["GitLab Inc."], "products": ["Duo"]})
POST = "Meet the new GitLab Duo. Preorders are open."


def write(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return path


def row(
    id_: str, text: str, label: str, platform: str = "instagram", parent: str = POST
) -> dict[str, str]:
    return {
        "id": id_,
        "platform": platform,
        "parent_text": parent,
        "comment_text": text,
        "label": label,
    }


# a labeled set small enough to check by hand
ROWS = [
    row("r1", "😂😂", "irrelevant"),  # empty: dropped by stage A, correctly
    row("r2", "ههههههههههه", "irrelevant"),  # spam: dropped by stage A, correctly
    row("r3", "GitLab", "relevant"),  # a match term: kept by stage B, correctly
    row("r4", "how much is the monthly plan", "relevant"),  # the model keeps it, correctly
    row("r5", "congratulations to the whole team", "irrelevant"),  # the model drops it, correctly
    row("r6", "price?", "relevant"),  # under two words and no term: dropped by stage A, wrongly
    row("r7", "great photo of the team lunch", "irrelevant"),  # the model keeps it, wrongly
]


def model_keeping(*needles: str) -> FunctionChatModel:
    def respond(messages: list[BaseMessage]) -> Scripted:
        ids = re.findall(r"^(c\d+): (.*)$", str(messages[1].content), flags=re.MULTILINE)
        return {"relevant": [i for i, text in ids if any(n in text for n in needles)]}

    return FunctionChatModel(responses=[], responder=respond)


# ------------------------------------------------------------------ reading the CSV


def test_the_template_is_just_the_header_row() -> None:
    assert TEMPLATE.read_text(encoding="utf-8").splitlines() == [
        "id,platform,parent_text,comment_text,label"
    ]
    assert read_rows(TEMPLATE) == []


def test_rows_are_read_with_quoted_commas_and_newlines(tmp_path: Path) -> None:
    path = write(tmp_path / "c.csv", [row("a", 'he said "wow", then\nleft', "Relevant", "X", "")])
    assert read_rows(path) == [
        LabeledComment("a", "x", "", 'he said "wow", then\nleft', "relevant")
    ]


def test_a_missing_column_is_named(tmp_path: Path) -> None:
    path = tmp_path / "c.csv"
    path.write_text("id,platform,comment_text,label\n1,x,hi,relevant\n", encoding="utf-8")
    with pytest.raises(EvaluationError, match="missing column.*parent_text"):
        read_rows(path)


def test_a_bad_label_and_a_repeated_id_are_reported_with_their_row(tmp_path: Path) -> None:
    with pytest.raises(
        EvaluationError, match="row 3: label must be relevant or irrelevant, got 'maybe'"
    ):
        read_rows(write(tmp_path / "a.csv", [row("1", "hi", "relevant"), row("2", "hi", "maybe")]))
    with pytest.raises(EvaluationError, match="row 3: id '1' is empty or already used"):
        read_rows(
            write(tmp_path / "b.csv", [row("1", "hi", "relevant"), row("1", "hello", "irrelevant")])
        )


# ------------------------------------------------------------------ the measurements


async def test_precision_recall_drop_rate_and_token_reduction_from_a_hand_checked_set() -> None:
    rows = [
        LabeledComment(r["id"], r["platform"], r["parent_text"], r["comment_text"], r["label"])
        for r in ROWS
    ]
    report = await evaluate(
        rows, BRIEF, SETTINGS, model_for=lambda: model_keeping("monthly plan", "team lunch")
    )

    # kept: r3 (term), r4 (model), r7 (model); dropped: r1, r2, r5, r6
    assert (
        report.true_positive,
        report.false_positive,
        report.false_negative,
        report.true_negative,
    ) == (2, 1, 1, 3)
    assert report.precision == pytest.approx(2 / 3) and report.recall == pytest.approx(2 / 3)
    assert report.drop_rate == pytest.approx(4 / 7)
    assert report.by_stage == {"a": 3, "b": 1, "c": 3}
    assert report.dropped_by_reason == {"empty": 1, "spam": 1, "too_short": 1, "llm_irrelevant": 1}
    assert report.sent_to_llm == 3 and report.llm_used
    before = sum(len(r["comment_text"]) for r in ROWS)
    after = sum(len(r["comment_text"]) for r in (ROWS[2], ROWS[3], ROWS[6]))
    assert report.tokens_before == -(-before // 4) and report.tokens_after == -(-after // 4)
    assert report.token_reduction == pytest.approx(1 - report.tokens_after / report.tokens_before)
    assert [m[:2] for m in report.missed] == [("r6", "too_short")]
    assert [m[:2] for m in report.leaked] == [("r7", "llm_relevant")]


async def test_without_a_model_what_the_free_stages_cannot_decide_counts_as_kept() -> None:
    rows = [
        LabeledComment(r["id"], r["platform"], r["parent_text"], r["comment_text"], r["label"])
        for r in ROWS
    ]
    report = await evaluate(rows, BRIEF, SETTINGS, model_for=None)
    assert not report.llm_used and report.sent_to_llm == 0
    assert (report.predicted_relevant, report.predicted_irrelevant) == (4, 3)  # r3, r4, r5, r7 kept
    assert report.recall == pytest.approx(2 / 3) and report.precision == pytest.approx(2 / 4)


async def test_a_model_failure_keeps_the_batch_and_the_report_says_so() -> None:
    def broken(messages: list[BaseMessage]) -> Scripted:
        raise RuntimeError("down")

    rows = [
        LabeledComment(r["id"], r["platform"], r["parent_text"], r["comment_text"], r["label"])
        for r in ROWS
    ]
    report = await evaluate(
        rows, BRIEF, SETTINGS, model_for=lambda: FunctionChatModel(responses=[], responder=broken)
    )
    assert report.predicted_relevant == 4 and report.warnings and "kept" in report.warnings[0]


async def test_metrics_that_cannot_be_computed_are_none_not_zero() -> None:
    only_irrelevant = [LabeledComment("a", "x", "", "😂", "irrelevant")]
    report = await evaluate(only_irrelevant, BRIEF, SETTINGS, model_for=None)
    assert report.precision is None and report.recall is None
    assert "precision: n/a" in format_report(report) and "recall:    n/a" in format_report(report)
    empty = await evaluate([], BRIEF, SETTINGS, model_for=None)
    assert empty.drop_rate is None and empty.token_reduction is None


async def test_comments_under_the_same_post_text_are_sent_together() -> None:
    seen: list[str] = []

    def respond(messages: list[BaseMessage]) -> Scripted:
        seen.append(str(messages[1].content))
        return {"relevant": []}

    rows = [
        LabeledComment("1", "x", "Post A", "first thing to say", "relevant"),
        LabeledComment("2", "x", "Post B", "second thing to say", "relevant"),
        LabeledComment("3", "x", "Post A", "third thing to say", "relevant"),
    ]
    await evaluate(
        rows, BRIEF, SETTINGS, model_for=lambda: FunctionChatModel(responses=[], responder=respond)
    )
    assert seen == [
        "POST P1: Post A\nc1: first thing to say\nc2: third thing to say\n\n"
        "POST P2: Post B\nc3: second thing to say"
    ]


async def test_the_report_text_names_the_measures_and_labels_the_estimate() -> None:
    rows = [
        LabeledComment(r["id"], r["platform"], r["parent_text"], r["comment_text"], r["label"])
        for r in ROWS
    ]
    text = format_report(
        await evaluate(
            rows, BRIEF, SETTINGS, model_for=lambda: model_keeping("monthly plan", "team lunch")
        )
    )
    for needle in (
        "precision: 66.7%",
        "recall:    66.7%",
        "drop rate: 57.1%",
        "token reduction:",
        "an estimate",
        "r6  [too_short]  price?",
        "r7  [llm_relevant]",
    ):
        assert needle in text, needle


# ------------------------------------------------------------------ the script


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("eval_relevance_filter", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_prints_the_report_for_a_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = write(tmp_path / "labeled.csv", ROWS)
    code = load_script().main(
        [
            str(csv_path),
            "--brief",
            str(BRIEFS / "competitive_intelligence.json"),
            "--alias",
            "GitLab Inc.",
            "--product",
            "Duo",
            "--no-llm",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "comments: 7 (3 labeled relevant, 4 labeled irrelevant)" in out
    assert (
        "model: not used" in out
        and "precision:" in out
        and "drop rate:" in out
        and "token reduction:" in out
    )


def test_the_script_explains_a_bad_csv_and_a_missing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("id,label\n1,relevant\n", encoding="utf-8")
    brief = str(BRIEFS / "competitive_intelligence.json")
    assert load_script().main([str(bad), "--brief", brief]) == 2
    assert "missing column" in capsys.readouterr().err
    good = write(tmp_path / "good.csv", ROWS)
    assert load_script().main([str(good), "--brief", brief]) == 2  # no OLLAMA_API_KEY in tests
    err = capsys.readouterr().err
    assert "OLLAMA_API_KEY" in err and "--no-llm" in err
