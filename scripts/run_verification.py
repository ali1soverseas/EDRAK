"""Run the verification agent against mock worker outputs and print I/O."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT))

from backend.src.edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Conflict,
    Evidence,
    EvidenceRef,
    EvidenceRelation,
    Finding,
    FindingCategory,
    SourceType,
    UseCase,
    VerificationInput,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from backend.src.edrak.verification import run


def mock_request() -> BusinessRequest:
    return BusinessRequest(
        request_id="run_001",
        goal="Evaluate expansion opportunities for AI and agentic software development.",
        company_profile=CompanyProfile(name="GitLab", products=["GitLab Duo"]),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub Copilot"],
            focus_areas=["agentic software development"],
        ),
    )


def mock_official_competitor() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_comp_official",
        source_type=SourceType.OFFICIAL_DOCUMENTATION,
        source_title="GitHub Copilot docs",
        source_url="https://docs.github.com/copilot",
        publisher="GitHub",
        extracted_fact=(
            "GitHub Copilot provides agentic software development capabilities "
            "in the IDE and on github.com."
        ),
    )
    return WorkerResult(
        task_id="task-competitor",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        findings=[
            Finding(
                finding_id="comp_001",
                statement="GitHub provides agentic software development capabilities.",
                category=FindingCategory.PRODUCT_FEATURE,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
                confidence=0.9,
            )
        ],
        evidence=[evidence],
        error="none",
    )


def mock_weak_competitor() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_comp_snippet",
        source_type=SourceType.SEARCH_RESULT,
        source_url="https://example.com/search",
        extracted_fact="Search result mentions GitHub is expanding its AI coding agent.",
    )
    return WorkerResult(
        task_id="task-competitor-weak",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=WorkerStatus.PARTIAL,
        findings=[
            Finding(
                finding_id="comp_002",
                statement="GitHub's agent can autonomously handle enterprise development.",
                category=FindingCategory.PRODUCT_FEATURE,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
                confidence=0.4,
            )
        ],
        evidence=[evidence],
        gaps=["Official enterprise documentation was not retrieved."],
        error="none",
    )


def mock_conflicting_market() -> WorkerResult:
    listed = Evidence(
        evidence_id="ev_price_list",
        source_type=SourceType.NEWS_ARTICLE,
        extracted_fact="GitHub Copilot enterprise feature costs $39 per user each month.",
    )
    included = Evidence(
        evidence_id="ev_price_page",
        source_type=SourceType.PRICING_PAGE,
        extracted_fact="GitHub pricing page says the same feature is included in plan Y at no extra cost.",
    )
    finding = Finding(
        finding_id="mkt_001",
        statement="GitHub Copilot feature costs $39 per user.",
        category=FindingCategory.PRICING_PACKAGING,
        evidence_refs=[
            EvidenceRef(evidence_id=listed.evidence_id, relation=EvidenceRelation.SUPPORTS),
            EvidenceRef(evidence_id=included.evidence_id, relation=EvidenceRelation.CONTRADICTS),
        ],
    )
    return WorkerResult(
        task_id="task-market",
        worker=WorkerType.MARKET_INTELLIGENCE,
        status=WorkerStatus.PARTIAL,
        findings=[finding],
        evidence=[listed, included],
        conflicts=[
            Conflict(
                finding_id=finding.finding_id,
                contradicting_evidence_ids=[included.evidence_id],
                description="Two sources report different pricing.",
            )
        ],
        error="Official pricing page could not be retrieved.",
    )


def mock_customer_trend() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_cust",
        source_type=SourceType.NEWS_ARTICLE,
        extracted_fact=(
            "Enterprise buyers are asking for AI coding agents that stay inside "
            "existing DevSecOps workflows."
        ),
    )
    return WorkerResult(
        task_id="task-customer",
        worker=WorkerType.CUSTOMER_TRENDS,
        status=WorkerStatus.COMPLETED,
        findings=[
            Finding(
                finding_id="cust_001",
                statement="Customers want AI coding agents inside existing DevSecOps workflows.",
                category=FindingCategory.CUSTOMER_SENTIMENT,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
            )
        ],
        evidence=[evidence],
        error="none",
    )


def build_payload() -> VerificationInput:
    return VerificationInput(
        request=mock_request(),
        agent_outputs=[
            mock_official_competitor(),
            mock_weak_competitor(),
            mock_conflicting_market(),
            mock_customer_trend(),
        ],
    )


def main() -> None:
    payload = build_payload()
    result = run(payload)

    print("=" * 72)
    print("  VERIFICATION INPUT")
    print("=" * 72)
    print(json.dumps(payload.model_dump(mode="json"), indent=2))
    print()
    print("=" * 72)
    print("  VERIFICATION OUTPUT")
    print("=" * 72)
    print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()

"""
# mocked unit tests
python -m pytest tests -q

# live 1-task smoke
python scripts/run_verification.py --max-tasks 1

# full 4–7 task run (several minutes)
python scripts/run_verification.py
"""