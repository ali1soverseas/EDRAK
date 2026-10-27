import json

from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    CompetitorRef,
    Evidence,
    EvidenceRef,
    EvidenceRelation,
    Finding,
    SourceType,
    UseCase,
    VerificationInput,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.verification.tracing import _json_safe


def test_json_safe_keeps_normal_verification_contract_fields_before_depth_limit():
    evidence = Evidence(
        source_type=SourceType.WEB_PAGE,
        source_title="Pricing information",
        source_url="https://example.com/pricing",
        extracted_fact="The annual subscription costs $120.",
        metadata={"pricing": {"currency": "USD", "period": "annual"}},
    )
    finding = Finding(
        statement="The annual subscription costs $120.",
        evidence_refs=[
            EvidenceRef(
                evidence_id=evidence.evidence_id,
                relation=EvidenceRelation.SUPPORTS,
            )
        ],
    )
    payload = VerificationInput(
        request=BusinessRequest(
            goal="Verify competitor pricing.",
            company_profile=CompanyProfile(name="Example Corp"),
            business_context=BusinessContext(
                use_case=UseCase.COMPETITIVE_INTELLIGENCE,
                competitors=[CompetitorRef(name="Competitor", website="https://example.com")],
            ),
        ),
        agent_outputs=[
            WorkerResult(
                task_id="task-1",
                worker=WorkerType.COMPETITOR_INTELLIGENCE,
                status=WorkerStatus.COMPLETED,
                findings=[finding],
                evidence=[evidence],
            )
        ],
    )

    safe_payload = _json_safe(payload)

    assert safe_payload["request"]["business_context"]["competitors"][0]["name"] == "Competitor"
    worker_result = safe_payload["agent_outputs"][0]
    assert worker_result["findings"][0]["statement"] == finding.statement
    assert worker_result["evidence"][0]["extracted_fact"] == evidence.extracted_fact
    assert worker_result["evidence"][0]["metadata"]["pricing"]["currency"] == "USD"
    assert "<max-depth>" not in json.dumps(safe_payload)
