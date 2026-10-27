from __future__ import annotations

import json
from contextlib import asynccontextmanager

import edrak.api.run_manager as run_manager_module
import pytest
from edrak.api.run_manager import (
    RunManager,
    _planning_artifact_path,
    _verification_from_planning_artifact,
)
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    CrossSignalOutput,
    CrossSignalSummary,
    DecisionReadyContext,
    FindingCheckStatus,
    FindingVerdict,
    UseCase,
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
    WorkerType,
)
from edrak.contracts.verification import EvidenceQuality


def _request(request_id: str) -> BusinessRequest:
    return BusinessRequest(
        request_id=request_id,
        goal="Assess competitive positioning.",
        company_profile=CompanyProfile(name="Example"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )


def _verification(request_id: str) -> VerificationResult:
    return VerificationResult(
        research_run_id=request_id,
        decision=VerificationDecision(
            status=VerificationStatus.VERIFIED,
            summary="The finding is verified.",
        ),
        findings=[
            FindingVerdict(
                finding_id="verified-finding",
                worker=WorkerType.COMPETITOR_INTELLIGENCE,
                statement="A verified competitor claim.",
                verification_status=FindingCheckStatus.VERIFIED,
                evidence_quality=EvidenceQuality.HIGH,
                confidence=0.9,
            )
        ],
    )


def _planning_artifact(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "trace_events": [
            {
                "stage": "post_verification_routing",
                "selected_route": "cross_signal",
                "verification_result": _verification(request_id).model_dump(mode="json"),
            }
        ],
    }


def test_planning_artifact_path_is_limited_to_run_id() -> None:
    path = _planning_artifact_path("run-123")

    assert path.parts[-2:] == ("run-123", "orchestrator_planning.json")
    assert path.parts[-4:-2] == ("artifacts", "runs")
    with pytest.raises(ValueError, match="invalid_analysis_id"):
        _planning_artifact_path("..\\outside")


def test_verification_loader_requires_cross_signal_route(tmp_path) -> None:
    path = tmp_path / "orchestrator_planning.json"
    artifact = _planning_artifact("run-123")
    artifact["trace_events"][0]["selected_route"] = "finalize"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ValueError, match="cross_signal_not_selected_by_orchestrator"):
        _verification_from_planning_artifact(path, "run-123")


@pytest.mark.asyncio
async def test_start_cross_signal_from_planning_uses_verified_artifact_findings(
    tmp_path, monkeypatch
) -> None:
    request_id = "run-123"
    path = tmp_path / "orchestrator_planning.json"
    path.write_text(json.dumps(_planning_artifact(request_id)), encoding="utf-8")
    request = _request(request_id)
    captured = {}

    @asynccontextmanager
    async def fake_get_db():
        yield object()

    async def fake_get_request(_db, _request_id):
        return {"request_id": request_id}

    async def fake_run_cross_signal(cross_signal_input):
        captured["input"] = cross_signal_input
        return CrossSignalOutput(
            research_run_id=request_id,
            status="completed",
            summary=CrossSignalSummary(),
            decision_ready_context=DecisionReadyContext(business_goal=request.goal),
        )

    monkeypatch.setattr(run_manager_module, "_planning_artifact_path", lambda _id: path)
    monkeypatch.setattr(run_manager_module, "get_db", fake_get_db)
    monkeypatch.setattr(run_manager_module.analyses_repo, "get_request", fake_get_request)
    monkeypatch.setattr(run_manager_module.analyses_repo, "request_contract", lambda _row: request)
    monkeypatch.setattr("edrak.CrossSignal.graph.run_cross_signal", fake_run_cross_signal)
    monkeypatch.setattr("edrak.CrossSignal.tracing.trace_cross_signal", lambda *_args, **_kwargs: None)

    result = await RunManager().start_cross_signal_from_planning(request_id)

    assert result["status"] == "completed"
    assert [
        finding.finding_id for finding in captured["input"].verified_findings
    ] == ["verified-finding"]
