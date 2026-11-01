"""Measure the filter against comments a person labeled.

The input is a CSV with the columns id, platform, parent_text, comment_text and label (relevant or
irrelevant). The filter runs on it exactly as it runs in a worker (the same free stages and the same
batched model call), and the verdicts are compared with the labels. Nothing here reads or writes the
evidence store.
"""

import csv
import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from edrak.agents.customer_trends.relevance.accounting import ESTIMATE_NOTE, estimate_tokens
from edrak.agents.customer_trends.relevance.cascade import free_stages
from edrak.agents.customer_trends.relevance.classify import ModelFor, classify
from edrak.agents.customer_trends.relevance.models import UNKNOWN_PLATFORM, Comment, Verdict
from edrak.agents.customer_trends.relevance.normalize import normalize
from edrak.agents.customer_trends.relevance.pipeline import prompt_values
from edrak.agents.customer_trends.relevance.terms import TermMatcher, match_terms
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings

COLUMNS = ("id", "platform", "parent_text", "comment_text", "label")
LABELS = ("relevant", "irrelevant")
NOT_CLASSIFIED = "not_classified"  # a comment the model did not see because the run used no model


class EvaluationError(ValueError):
    """The CSV cannot be used. The message says which row and why."""


@dataclass(frozen=True)
class LabeledComment:
    id: str
    platform: str
    parent_text: str
    comment_text: str
    label: str


def read_rows(path: Path) -> list[LabeledComment]:
    """The labeled comments of a CSV file. Raises EvaluationError for a missing column, a label that
    is not relevant or irrelevant, or an id used twice."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise EvaluationError(f"{path.name}: missing column(s) {', '.join(missing)}")
        rows: list[LabeledComment] = []
        seen: set[str] = set()
        for number, record in enumerate(reader, start=2):  # row 1 is the header
            label = (record["label"] or "").strip().lower()
            if label not in LABELS:
                raise EvaluationError(
                    f"row {number}: label must be relevant or irrelevant, got {record['label']!r}"
                )
            row_id = (record["id"] or "").strip()
            if not row_id or row_id in seen:
                raise EvaluationError(f"row {number}: id {row_id!r} is empty or already used")
            seen.add(row_id)
            rows.append(
                LabeledComment(
                    id=row_id,
                    platform=(record["platform"] or UNKNOWN_PLATFORM).strip().lower(),
                    parent_text=record["parent_text"] or "",
                    comment_text=record["comment_text"] or "",
                    label=label,
                )
            )
    return rows


@dataclass
class Report:
    rows: int = 0
    labeled_relevant: int = 0
    labeled_irrelevant: int = 0
    predicted_relevant: int = 0
    predicted_irrelevant: int = 0
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    by_stage: dict[str, int] = field(default_factory=dict)
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    sent_to_llm: int = 0
    llm_used: bool = False
    tokens_before: int = 0
    tokens_after: int = 0
    warnings: list[str] = field(default_factory=list)
    missed: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # relevant comments that were dropped
    leaked: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # irrelevant comments that were kept

    @property
    def precision(self) -> float | None:
        kept = self.true_positive + self.false_positive
        return self.true_positive / kept if kept else None

    @property
    def recall(self) -> float | None:
        wanted = self.true_positive + self.false_negative
        return self.true_positive / wanted if wanted else None

    @property
    def drop_rate(self) -> float | None:
        return self.predicted_irrelevant / self.rows if self.rows else None

    @property
    def token_reduction(self) -> float | None:
        return 1 - self.tokens_after / self.tokens_before if self.tokens_before else None


def _comments(rows: Sequence[LabeledComment]) -> list[Comment]:
    """Comments with the same parent text share a group, as comments under one post do."""
    out = []
    for row in rows:
        key = hashlib.sha1(normalize(row.parent_text).encode(), usedforsecurity=False).hexdigest()
        out.append(
            Comment(
                id=row.id,
                text=row.comment_text,
                platform=row.platform,
                parent_key=key if row.parent_text.strip() else "",
                parent_text=row.parent_text,
            )
        )
    return out


async def evaluate(
    rows: Sequence[LabeledComment],
    brief: TaskBrief,
    settings: Settings,
    *,
    model_for: ModelFor | None,
) -> Report:
    """Run the filter on `rows` and compare its verdicts with the labels.

    With `model_for=None` no model is used: what the free stages cannot decide is counted as kept,
    which is how the filter treats a comment it cannot classify, and the report says so.
    """
    comments = _comments(rows)
    verdicts, pending, _ = free_stages(comments, TermMatcher(match_terms(brief)))
    report = Report(rows=len(rows), llm_used=model_for is not None)
    if pending and model_for is not None:
        outcome = await classify(
            pending,
            model_for=model_for,
            prompt_values=prompt_values(brief),
            batch_size=settings.relevance_batch_size,
            parent_chars=settings.relevance_parent_chars,
            comment_chars=settings.relevance_comment_chars,
            concurrency=settings.relevance_concurrency,
            timeout_s=settings.relevance_timeout_s,
        )
        verdicts.update(outcome.verdicts)
        report.sent_to_llm = outcome.sent
        report.warnings = outcome.warnings
    for comment in pending:
        verdicts.setdefault(comment.id, Verdict("relevant", NOT_CLASSIFIED, "c"))

    stages: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for row in rows:
        verdict = verdicts[row.id]
        kept = verdict.relevance == "relevant"
        stages[verdict.stage] += 1
        report.tokens_before += len(row.comment_text)
        if kept:
            report.predicted_relevant += 1
            report.tokens_after += len(row.comment_text)
        else:
            report.predicted_irrelevant += 1
            reasons[verdict.reason] += 1
        if row.label == "relevant":
            report.labeled_relevant += 1
            if kept:
                report.true_positive += 1
            else:
                report.false_negative += 1
                report.missed.append((row.id, verdict.reason, row.comment_text))
        else:
            report.labeled_irrelevant += 1
            if kept:
                report.false_positive += 1
                report.leaked.append((row.id, verdict.reason, row.comment_text))
            else:
                report.true_negative += 1
    report.by_stage = dict(sorted(stages.items()))
    report.dropped_by_reason = dict(reasons.most_common())
    report.tokens_before, report.tokens_after = (
        estimate_tokens(report.tokens_before),
        estimate_tokens(report.tokens_after),
    )
    return report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_report(report: Report, *, show: int = 10) -> str:
    """The report as plain text."""
    lines = [
        f"comments: {report.rows} ({report.labeled_relevant} labeled relevant, "
        f"{report.labeled_irrelevant} labeled irrelevant)",
        f"model: {'used' if report.llm_used else 'not used (undecided comments counted as kept)'}",
        f"kept: {report.predicted_relevant}   dropped: {report.predicted_irrelevant}"
        f"   sent to the model: {report.sent_to_llm}",
        f"decided at stage: {report.by_stage}",
        f"dropped by reason: {report.dropped_by_reason}",
        "",
        "relevant class",
        f"  precision: {_pct(report.precision)}   (kept comments that are labeled relevant)",
        f"  recall:    {_pct(report.recall)}   (labeled relevant comments that were kept)",
        f"  true positive {report.true_positive}, false positive {report.false_positive}, "
        f"false negative {report.false_negative}, true negative {report.true_negative}",
        "",
        f"drop rate: {_pct(report.drop_rate)}",
        f"token reduction: {_pct(report.token_reduction)}   "
        f"({report.tokens_before} to {report.tokens_after}; {ESTIMATE_NOTE})",
    ]
    for warning in report.warnings:
        lines.append(f"warning: {warning}")
    for title, found in (
        ("relevant comments that were dropped", report.missed),
        ("irrelevant comments that were kept", report.leaked),
    ):
        if found and show:
            lines += ["", f"{title} ({len(found)}, first {min(show, len(found))}):"]
            lines += [f"  {i}  [{reason}]  {text[:100]}" for i, reason, text in found[:show]]
    return "\n".join(lines)
