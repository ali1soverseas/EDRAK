"""A second research round adds to the first; verification reads prices with thousands separators."""

from __future__ import annotations

from edrak.contracts import Evidence, EvidenceRef, Finding, WorkerResult, WorkerStatus, WorkerType
from edrak.contracts.evidence import SourceType
from edrak.orchestration.state import RESET, merge_by_task_id
from edrak.verification.quality import quantities_missing


def _result(attempt: int, fid: str, statement: str, findings: bool = True) -> WorkerResult:
    ev = Evidence(evidence_id=f"e{fid}", source_type=SourceType.WEB_PAGE, extracted_fact="x")
    return WorkerResult(task_id="t1", worker=WorkerType.COMPETITOR_INTELLIGENCE, status=WorkerStatus.COMPLETED, attempt=attempt,
                        evidence=[ev], findings=[Finding(finding_id=fid, statement=statement,
                                                         evidence_refs=[EvidenceRef(evidence_id=ev.evidence_id)])] if findings else [])


def test_a_second_round_keeps_the_first_rounds_findings():
    first = merge_by_task_id([], [_result(1, "f1", "AMD data-center revenue grew 107%.")])
    both = merge_by_task_id(first, [_result(2, "f2", "Intel bundles eight Gaudi 3 accelerators.")])
    assert len(both) == 1 and both[0].attempt == 2
    assert [f.statement for f in both[0].findings] == ["AMD data-center revenue grew 107%.", "Intel bundles eight Gaudi 3 accelerators."]
    assert {e.evidence_id for e in both[0].evidence} == {"ef1", "a2-ef2"}


def test_an_empty_second_round_does_not_erase_the_first():
    first = merge_by_task_id([], [_result(1, "f1", "AMD data-center revenue grew 107%.")])
    after = merge_by_task_id(first, [_result(2, "f2", "", findings=False)])
    assert [f.statement for f in after[0].findings] == ["AMD data-center revenue grew 107%."]


def test_the_same_round_still_replaces_and_reset_still_clears():
    first = merge_by_task_id([], [_result(1, "f1", "old")])
    assert [f.statement for f in merge_by_task_id(first, [_result(1, "f9", "enriched")])[0].findings] == ["enriched"]
    assert merge_by_task_id(first, RESET) == []


def test_prices_with_thousands_separators_match_their_source():
    ev = Evidence(evidence_id="e", source_type=SourceType.WEB_PAGE, extracted_fact="x",
                  excerpt="| H200 GPU | Enterprise / Cloud | $25,000 - $30,000 | 70-73% |")
    assert quantities_missing("The H200 target ASP is $25,000 to $30,000.", [ev]) == []
    assert quantities_missing("The H200 target ASP is $26,000.", [ev]) == ["26000"]
