from edrak.agents.internal_intelligence.nodes import _parse_conflicts
from edrak.contracts.evidence import Evidence, EvidenceRef, EvidenceRelation, SourceType
from edrak.contracts.result import Finding, FindingCategory, WorkerResult, WorkerStatus
from edrak.contracts.task import WorkerType


def _evidence(evidence_id: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type=SourceType.INTERNAL_DOCUMENT,
        extracted_fact=f"Fact from {evidence_id}",
    )


def test_conflict_excludes_evidence_already_supporting_its_finding():
    finding = Finding(
        finding_id="finding-1",
        statement="The feature is available.",
        category=FindingCategory.PRODUCT_FEATURE,
        evidence_refs=[
            EvidenceRef(evidence_id="support-1", relation=EvidenceRelation.SUPPORTS)
        ],
    )
    evidence = [_evidence("support-1"), _evidence("refute-1")]

    conflicts, unlinked = _parse_conflicts(
        [{
            "description": "A later document says the feature is planned.",
            "finding_statement": "The feature is available.",
            "contradicting_evidence_ids": ["support-1", "refute-1"],
        }],
        [finding],
        {item.evidence_id for item in evidence},
    )

    assert not unlinked
    assert len(conflicts) == 1
    assert conflicts[0].finding_id == finding.finding_id
    assert conflicts[0].contradicting_evidence_ids == ["refute-1"]

    WorkerResult(
        task_id="task-1",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        findings=[finding],
        evidence=evidence,
        conflicts=conflicts,
    )


def test_ambiguous_legacy_conflict_is_not_arbitrarily_linked():
    findings = [
        Finding(
            finding_id="finding-1",
            statement="Claim one.",
            evidence_refs=[
                EvidenceRef(evidence_id="support-1", relation=EvidenceRelation.SUPPORTS)
            ],
        ),
        Finding(
            finding_id="finding-2",
            statement="Claim two.",
            evidence_refs=[
                EvidenceRef(evidence_id="support-2", relation=EvidenceRelation.SUPPORTS)
            ],
        ),
    ]

    conflicts, unlinked = _parse_conflicts(
        [{
            "description": "Two sources disagree.",
            "evidence_ids": ["support-1", "support-2", "refute-1"],
        }],
        findings,
        {"support-1", "support-2", "refute-1"},
    )

    assert not conflicts
    assert unlinked == ["Two sources disagree."]
