"""Structural and grounding checks over a `CustomerTrendsResult` and the evidence store (SPEC 13).

A small check, not an evaluation framework: it tells whether the output of a run is well formed
and every claim can be traced to stored evidence and computed numbers. It says nothing about
whether the findings are any good.

    uv run python -m evals.customer_trends.checks --run-id run-ci-gitlab-001

run from `backend/`; the exit code is 0 when every check passes and 1 when any fails.
"""

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from edrak.agents.customer_trends.assembly import overall_confidence
from edrak.agents.customer_trends.gaps import EVIDENCE_TOTAL, Gap
from edrak.agents.customer_trends.schemas.analysis import MAX_QUOTE_CHARS
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters
from edrak.agents.customer_trends.schemas.findings import (
    CustomerTrendsResult,
    to_worker_result,
)
from edrak.agents.customer_trends.settings import get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink
from edrak.agents.customer_trends.tools.selection import load_items
from edrak.agents.customer_trends.tools.submit_findings import (
    HIGH_MIN_EVIDENCE,
    HIGH_MIN_SPREAD,
    cited_metric_ids,
    claim_numbers,
    load_verdict_phrases,
    matches,
    own_numbers,
    verdict_phrases_in,
)
from edrak.agents.customer_trends.ui.components.forms import task_from_brief

MIN_STATE_TEXT_CHARS = 30
_ARABIC = re.compile("[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_ESCAPED_ARABIC = re.compile(r"\\u0[67][0-9a-fA-F]{2}")
_REPLACEMENT = "�"
_MAX_DETAIL_ITEMS = 5


@dataclass(frozen=True)
class CheckFailure:
    check: str
    detail: str
    subject: str | None = None

    def __str__(self) -> str:
        where = f" [{self.subject}]" if self.subject else ""
        return f"{self.check}{where}: {self.detail}"


Check = Callable[[CustomerTrendsResult, EvidenceStore], list[CheckFailure]]


def _flatten(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, dict):
        return [n for inner in value.values() for n in _flatten(inner)]
    if isinstance(value, list):
        return [n for inner in value for n in _flatten(inner)]
    return []


def check_schema(result: CustomerTrendsResult, store: EvidenceStore) -> list[CheckFailure]:
    """The result validates, and so does the shared `WorkerResult` made from it."""
    try:
        CustomerTrendsResult.model_validate(result.model_dump())
    except ValidationError as exc:
        return [CheckFailure("schema", f"{error['loc']}: {error['msg']}") for error in exc.errors()]
    cited = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
    try:
        to_worker_result(
            result, store.get_items(result.run_id, cited), task=task_from_brief(result.brief)
        )
    except ValidationError as exc:
        return [CheckFailure("schema", f"WorkerResult: {error['msg']}") for error in exc.errors()]
    return []


def check_evidence_exists(result: CustomerTrendsResult, store: EvidenceStore) -> list[CheckFailure]:
    """Every finding cites evidence, and every cited id is stored for this run."""
    failures = []
    for finding in result.findings:
        if not finding.evidence_ids:
            failures.append(CheckFailure("evidence", "the finding cites no evidence", finding.id))
            continue
        found = store.existing_ids(result.run_id, finding.evidence_ids)
        missing = [i for i in finding.evidence_ids if i not in found]
        if missing:
            shown = ", ".join(missing[:_MAX_DETAIL_ITEMS])
            failures.append(CheckFailure("evidence", f"ids not in the store: {shown}", finding.id))
    return failures


def check_numbers(result: CustomerTrendsResult, store: EvidenceStore) -> list[CheckFailure]:
    """Every number in a claim is in the finding's metrics or in a stored metric it cites."""
    failures = []
    for finding in result.findings:
        known = own_numbers(finding.metrics)
        for metric_id in cited_metric_ids(finding.metrics):
            record = store.get_metric(result.run_id, metric_id)
            if record is None:
                failures.append(
                    CheckFailure("numbers", f"unknown metric_id {metric_id}", finding.id)
                )
            else:
                known += _flatten(record.values)
        unmatched = [
            n for n in claim_numbers(finding.claim) if not any(matches(n, k) for k in known)
        ]
        if unmatched:
            listed = ", ".join(f"{n:g}" for n in unmatched)
            failures.append(CheckFailure("numbers", f"not in the metrics: {listed}", finding.id))
    return failures


def check_no_verdicts(result: CustomerTrendsResult, store: EvidenceStore) -> list[CheckFailure]:
    """No claim and no headline tells the reader what to decide."""
    phrases = load_verdict_phrases()
    texts = [(f.id, f.claim) for f in result.findings]
    texts.append(("headline", result.control_summary.headline))
    return [
        CheckFailure("verdicts", f"contains {', '.join(map(repr, found))}", subject)
        for subject, text in texts
        if (found := verdict_phrases_in(text, phrases))
    ]


def check_high_confidence(result: CustomerTrendsResult, store: EvidenceStore) -> list[CheckFailure]:
    """A high confidence finding has enough evidence from enough platforms or source types, and no
    open gap that weakens it."""
    failures = []
    for finding in result.findings:
        if finding.confidence.value != "high":
            continue
        items = store.get_items(result.run_id, finding.evidence_ids)
        platforms = {i.platform for i in items if i.platform is not None}
        spread = max(len(platforms), len({i.source_type for i in items}))
        if len(finding.evidence_ids) < HIGH_MIN_EVIDENCE or spread < HIGH_MIN_SPREAD:
            failures.append(
                CheckFailure(
                    "high_confidence",
                    f"{len(finding.evidence_ids)} evidence ids from {spread} platform(s) or "
                    f"source type(s); needs {HIGH_MIN_EVIDENCE} from {HIGH_MIN_SPREAD}",
                    finding.id,
                )
            )
        elif finding.related_gaps:
            failures.append(
                CheckFailure(
                    "high_confidence",
                    f"high confidence with open gaps: {', '.join(finding.related_gaps)}",
                    finding.id,
                )
            )
    return failures


def check_summary(result: CustomerTrendsResult, store: EvidenceStore) -> list[CheckFailure]:
    """The control summary agrees with the findings, the store and the gaps."""
    summary = result.control_summary
    stored = store.run_summary(result.run_id).evidence_count
    failures = []
    if summary.findings_count != len(result.findings):
        failures.append(
            CheckFailure(
                "summary",
                f"findings_count {summary.findings_count} but {len(result.findings)} findings",
            )
        )
    if not summary.evidence_count == result.evidence_ref.count == stored:
        failures.append(
            CheckFailure(
                "summary",
                f"evidence counts differ: summary {summary.evidence_count}, reference "
                f"{result.evidence_ref.count}, store {stored}",
            )
        )
    open_gaps = result.provenance.get("open_gaps")
    if open_gaps is None:
        return failures
    gaps = [Gap.model_validate(g) for g in open_gaps]
    critical = [g for g in gaps if g.severity == "critical"]
    expected = (
        "insufficient"
        if any(g.id == EVIDENCE_TOTAL for g in gaps)
        else "partial"
        if critical
        else "complete"
    )
    if summary.status != expected:
        failures.append(
            CheckFailure("summary", f"status is {summary.status} but its gaps say {expected}")
        )
    if summary.gaps != [g.description for g in gaps]:
        failures.append(CheckFailure("summary", "the listed gaps differ from the open gaps"))
    if summary.overall_confidence != overall_confidence(result.findings, gaps):
        failures.append(CheckFailure("summary", "overall_confidence does not follow the findings"))
    return failures


def check_arabic_preserved(
    result: CustomerTrendsResult, store: EvidenceStore
) -> list[CheckFailure]:
    """Arabic text is stored as written, and every quote is a verbatim part of a stored item."""
    failures = []
    items, _ = load_items(store, result.run_id, EvidenceFilters(language="ar"))
    for item in items:
        if not _ARABIC.search(item.text):
            failures.append(
                CheckFailure("arabic", "an Arabic item holds no Arabic letter", item.id)
            )
        elif _ESCAPED_ARABIC.search(item.text) or _REPLACEMENT in item.text:
            failures.append(CheckFailure("arabic", "the text is escaped or damaged", item.id))
    for aggregate in result.theme_aggregates:
        texts = [i.text for i in store.get_items(result.run_id, aggregate.evidence_ids)]
        for quote in aggregate.representative_quotes:
            if len(quote) > MAX_QUOTE_CHARS or not any(quote in text for text in texts):
                failures.append(
                    CheckFailure(
                        "arabic", f"quote is not verbatim: {quote[:40]!r}", aggregate.theme_label
                    )
                )
    return failures


def check_state_has_no_bulk_text(
    result: CustomerTrendsResult, store: EvidenceStore, checkpoints: Path
) -> list[CheckFailure]:
    """No checkpoint of the graph holds the text of a stored item. The quotes of the theme
    aggregates, in the final result, are the only evidence text the state may carry."""
    texts, _ = load_items(store, result.run_id, EvidenceFilters())
    long_texts = [i.text for i in texts if len(i.text) >= MIN_STATE_TEXT_CHARS]
    quotes = {q for a in result.theme_aggregates for q in a.representative_quotes}
    failures = []
    with SqliteSaver.from_conn_string(str(checkpoints)) as saver:
        for saved in saver.list({"configurable": {"thread_id": result.run_id}}):
            if saved.config["configurable"].get("checkpoint_ns"):
                continue
            values = saved.checkpoint["channel_values"]
            blob = json.dumps(values, ensure_ascii=False, default=str)
            found = [t for t in long_texts if t in blob and t not in quotes]
            if found:
                step = saved.metadata.get("step") if saved.metadata else None
                failures.append(
                    CheckFailure("state", f"{len(found)} item text(s) in the state", f"step {step}")
                )
    return failures


CHECKS: dict[str, Check] = {
    "schema": check_schema,
    "evidence": check_evidence_exists,
    "numbers": check_numbers,
    "verdicts": check_no_verdicts,
    "high_confidence": check_high_confidence,
    "summary": check_summary,
    "arabic": check_arabic_preserved,
}


def run_checks(
    result: CustomerTrendsResult, store: EvidenceStore, *, checkpoints: Path | None = None
) -> list[CheckFailure]:
    """Every failure of every check; an empty list means the output is sound. The checkpoint check
    runs when the path of the checkpoint database is given and the file exists."""
    failures = [failure for check in CHECKS.values() for failure in check(result, store)]
    if checkpoints is not None and checkpoints.is_file():
        failures += check_state_has_no_bulk_text(result, store, checkpoints)
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.customer_trends.checks")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    with EvidenceStore.from_settings(settings) as store:
        try:
            result = LocalSink(store, settings.artifacts_dir).read_result(args.run_id)
        except (OSError, ValueError):
            print(f"no result for run {args.run_id}", file=sys.stderr)
            return 1
        failures = run_checks(result, store, checkpoints=settings.data_dir / "checkpoints.db")
    if failures:
        print(f"{len(failures)} check failure(s) for {args.run_id}:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"all checks passed for {args.run_id}: {', '.join([*CHECKS, 'state'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
