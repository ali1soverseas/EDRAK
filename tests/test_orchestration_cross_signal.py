import asyncio

from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Evidence,
    EvidenceQuality,
    EvidenceRef,
    Finding,
    FindingCategory,
    FindingCheckStatus,
    ResearchPlan,
    ResearchTask,
    SourceType,
    UseCase,
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
    WorkerRegistry,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.contracts.verification import FindingVerdict
from edrak.orchestration import graph as orchestration_graph
from edrak.orchestration.routing import decide_after_verification


def _request() -> BusinessRequest:
    return BusinessRequest(
        request_id="cross-signal-run",
        goal="Evaluate GitHub Copilot capabilities.",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub"],
        ),
    )


def test_verified_decision_routes_to_cross_signal():
    decision = VerificationDecision(
        status=VerificationStatus.VERIFIED,
        summary="All findings are verified.",
    )

    assert decide_after_verification({"verification": decision}) == "cross_signal"


def test_orchestrator_runs_cross_signal_after_verification(monkeypatch):
    request = _request()
    task = ResearchTask(
        task_id="competitor-task",
        parent_request_id=request.request_id,
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Research GitHub Copilot.",
        focus="AI development capabilities",
        company_profile=request.company_profile,
        business_context=request.business_context,
    )
    evidence = Evidence(
        evidence_id="copilot-doc",
        source_type=SourceType.OFFICIAL_DOCUMENTATION,
        extracted_fact=(
            "GitHub Copilot provides AI coding assistance in supported "
            "development environments."
        ),
    )
    finding = Finding(
        finding_id="copilot-finding",
        statement="GitHub Copilot provides AI coding assistance.",
        category=FindingCategory.PRODUCT_FEATURE,
        evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
    )
    worker_result = WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=WorkerStatus.COMPLETED,
        findings=[finding],
        evidence=[evidence],
    )
    verification_result = VerificationResult(
        research_run_id=request.request_id,
        decision=VerificationDecision(
            status=VerificationStatus.VERIFIED,
            summary="All findings are verified.",
        ),
        findings=[
            FindingVerdict(
                finding_id=finding.finding_id,
                worker=task.worker,
                statement=finding.statement,
                verification_status=FindingCheckStatus.VERIFIED,
                evidence_quality=EvidenceQuality.HIGH,
                evidence_ids=[evidence.evidence_id],
                confidence=0.95,
            )
        ],
    )
    plan = ResearchPlan(request_id=request.request_id, tasks=[task])

    class _Worker:
        worker_type = WorkerType.COMPETITOR_INTELLIGENCE

        def run(self, _task):
            return worker_result

    class _CrossSignalOutput:
        status = "completed"
        signals = []

        def model_dump(self, *, mode):
            assert mode == "json"
            return {"status": self.status, "signals": self.signals}

    registry = WorkerRegistry({task.worker: _Worker()})
    monkeypatch.setattr(
        orchestration_graph,
        "plan_node",
        lambda state: {**state, "plan": plan},
    )
    monkeypatch.setattr(
        "edrak.verification.graph.run",
        lambda _payload: verification_result,
    )

    cross_signal_inputs = []

    async def fake_run_cross_signal(payload):
        cross_signal_inputs.append(payload)
        return _CrossSignalOutput()

    monkeypatch.setattr(
        "edrak.CrossSignal.graph.run_cross_signal",
        fake_run_cross_signal,
    )

    state = asyncio.run(
        orchestration_graph.build_graph(registry).ainvoke({"request": request})
    )

    assert len(cross_signal_inputs) == 1
    assert cross_signal_inputs[0].research_run_id == request.request_id
    assert [item.finding_id for item in cross_signal_inputs[0].verified_findings] == [
        finding.finding_id
    ]
    assert state["orchestration_result"].cross_signal == {
        "status": "completed",
        "signals": [],
    }
