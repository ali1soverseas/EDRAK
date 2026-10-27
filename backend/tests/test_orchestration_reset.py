from types import SimpleNamespace

from edrak.api.run_manager import _checkpoint_serializer
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Evidence,
    EvidenceRef,
    EvidenceRelation,
    Finding,
    FindingCategory,
    FindingCheckStatus,
    OrchestrationResult,
    ResearchPlan,
    ResearchTask,
    RunStatus,
    SourceType,
    TriggerType,
    UseCase,
    WorkerOutcome,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.contracts.verification import (
    EvidenceQuality,
    FindingVerdict,
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
)
from edrak.orchestration.review import _auto_decision
from edrak.orchestration.state import RESET, merge_by_task_id
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def test_reset_sentinel_is_checkpoint_serializable():
    # A full replan writes RESET into channel writes; the SQLite checkpointer
    # must be able to msgpack it.
    kind, _ = JsonPlusSerializer().dumps_typed(RESET)
    assert kind == "msgpack"


def test_orchestration_checkpoint_types_are_explicitly_allowlisted():
    request = BusinessRequest(
        request_id="request-1",
        goal="Assess competition.",
        company_profile=CompanyProfile(name="Example"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            trigger=TriggerType.ON_DEMAND,
        ),
    )
    task = ResearchTask(
        parent_request_id=request.request_id,
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal="Assess market.",
        focus="Market demand",
        company_profile=request.company_profile,
        business_context=request.business_context,
    )
    evidence = Evidence(
        source_type=SourceType.WEB_PAGE,
        extracted_fact="An evidenced product capability.",
    )
    finding = Finding(
        statement="The product has a documented capability.",
        category=FindingCategory.PRODUCT_FEATURE,
        evidence_refs=[
            EvidenceRef(
                evidence_id=evidence.evidence_id,
                relation=EvidenceRelation.SUPPORTS,
            )
        ],
    )
    result = WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=WorkerStatus.COMPLETED,
        findings=[finding],
        evidence=[evidence],
    )
    verification = VerificationResult(
        research_run_id=request.request_id,
        decision=VerificationDecision(
            status=VerificationStatus.VERIFIED,
            summary="The finding was verified.",
        ),
        findings=[
            FindingVerdict(
                finding_id=finding.finding_id,
                worker=task.worker,
                statement=finding.statement,
                verification_status=FindingCheckStatus.VERIFIED,
                evidence_quality=EvidenceQuality.HIGH,
                evidence_ids=[evidence.evidence_id],
                confidence=0.9,
            )
        ],
    )
    orchestration = OrchestrationResult(
        request_id=request.request_id,
        status=RunStatus.PARTIAL,
        plan=ResearchPlan(request_id=request.request_id, tasks=[task]),
        results=[result],
    )
    state = {
        "request": request,
        "plan": orchestration.plan,
        "results": [result],
        "outcomes": [
            WorkerOutcome(
                task_id=task.task_id,
                worker=task.worker,
                status=WorkerStatus.COMPLETED,
            )
        ],
        "verification": verification.decision,
        "verification_result": verification,
        "orchestration_result": orchestration,
    }

    serializer = _checkpoint_serializer()
    kind, payload = serializer.dumps_typed(state)
    restored = serializer.loads_typed((kind, payload))

    assert isinstance(restored["request"], BusinessRequest)
    assert isinstance(restored["plan"], ResearchPlan)
    assert isinstance(restored["results"][0], WorkerResult)
    assert isinstance(restored["outcomes"][0], WorkerOutcome)
    assert isinstance(restored["verification"], VerificationDecision)
    assert isinstance(restored["verification_result"], VerificationResult)
    assert isinstance(restored["orchestration_result"], OrchestrationResult)
    assert restored["request"].business_context.use_case is UseCase.COMPETITIVE_INTELLIGENCE
    assert restored["request"].business_context.trigger is TriggerType.ON_DEMAND
    assert restored["results"][0].findings[0].category is FindingCategory.PRODUCT_FEATURE
    assert (
        restored["verification_result"].findings[0].verification_status
        is FindingCheckStatus.VERIFIED
    )
    assert restored["orchestration_result"].status is RunStatus.PARTIAL


def test_auto_review_accepts_request_and_verification_checkpoint_dicts():
    state = {
        "request": {"extras": {"auto_review": True}},
        "verification": {"status": "verified"},
    }

    assert _auto_decision(state) == {"decision": "continue", "auto": True}


def test_reset_clears_the_channel_and_normal_merge_replaces_by_task_id():
    left = [SimpleNamespace(task_id="a"), SimpleNamespace(task_id="b")]
    assert merge_by_task_id(left, RESET) == []
    merged = merge_by_task_id(left, [SimpleNamespace(task_id="b"), SimpleNamespace(task_id="c")])
    assert sorted(item.task_id for item in merged) == ["a", "b", "c"]
