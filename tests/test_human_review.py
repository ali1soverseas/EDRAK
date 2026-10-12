"""Checkpoint 2: a run stops after verification until a person decides, also across a restart."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from edrak.contracts import (
    EvidenceQuality,
    Evidence,
    EvidenceRef,
    Finding,
    FindingCheckStatus,
    FindingVerdict,
    SourceType,
    TargetedAction,
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.contracts.worker import WorkerRegistry
from edrak.core.config import settings
from edrak.db import connection

FORM = {
    "use_case": "competitive_intelligence",
    "goal": "How do we compare with Kanz Pay?",
    "competitors": ["Kanz Pay"],
    "market": "Egypt",
    "offering": "",
    "focus": ["pricing"],
    "time_window_days": 90,
    "sources": {"internal_files": False, "competitor_web": True, "news_open_data": False, "reviews_social": False},
}


class FakeCompetitorWorker:
    worker_type = WorkerType.COMPETITOR_INTELLIGENCE

    def __init__(self) -> None:
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        ev = Evidence(evidence_id=f"ev-{task.attempt}", source_type=SourceType.PRICING_PAGE,
                      source_url="https://kanzpay.example/pricing", extracted_fact="Starter costs 99 EGP.")
        return WorkerResult(
            task_id=task.task_id, worker=task.worker, status=WorkerStatus.COMPLETED, attempt=task.attempt,
            evidence=[ev],
            findings=[Finding(finding_id=f"f-{task.attempt}", statement="Kanz Pay Starter costs 99 EGP.",
                              evidence_refs=[EvidenceRef(evidence_id=ev.evidence_id)])],
        )


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EDRAK_DB_PATH", tmp_path / "edrak.db")
    monkeypatch.setattr(settings, "UPLOADS_PATH", tmp_path / "uploads")
    monkeypatch.setattr(settings, "VECTOR_STORE_PATH", tmp_path / "vectors")
    monkeypatch.setattr(settings, "ORCHESTRATION_CHECKPOINT_PATH", tmp_path / "checkpoints.db")
    connection.reset_ready_cache()

    from edrak.api import run_manager as rm
    from edrak.orchestration import planner

    worker = FakeCompetitorWorker()
    monkeypatch.setattr(rm, "get_default_registry", lambda: WorkerRegistry({WorkerType.COMPETITOR_INTELLIGENCE: worker}))
    monkeypatch.setattr(planner.LlmPlanner, "plan", lambda self, request: (_ for _ in ()).throw(RuntimeError("no model")))

    def fake_verification(payload):
        result = payload.agent_outputs[0]
        verdict = FindingVerdict(
            finding_id=result.findings[0].finding_id, worker=result.worker, statement=result.findings[0].statement,
            verification_status=FindingCheckStatus.VERIFIED, evidence_quality=EvidenceQuality.HIGH,
            evidence_ids=[result.evidence[0].evidence_id], confidence=0.9,
            contradictions=["Two sources disagree on the Starter price."] if result.attempt == 1 else [],
        )
        decision = (
            VerificationDecision(status=VerificationStatus.REPLAN_REQUIRED, summary="Conflicting prices.",
                                 targeted_actions=[TargetedAction(worker=result.worker, reason="Check the Starter price.")])
            if result.attempt == 1 else
            VerificationDecision(status=VerificationStatus.VERIFIED, summary="All findings verified.")
        )
        return VerificationResult(research_run_id=payload.request.request_id, decision=decision, findings=[verdict])

    monkeypatch.setattr("edrak.verification.graph.run", fake_verification)

    async def no_cross_signal(_payload):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr("edrak.CrossSignal.graph.run_cross_signal", no_cross_signal)
    rm.run_manager._runs.clear()

    from edrak.main import app

    with TestClient(app) as client:
        resp = client.post("/api/auth/create-account",
                           json={"email": f"u{uuid.uuid4().hex[:6]}@example.com", "password": "long enough"})
        assert resp.status_code == 200
        yield client, worker, rm
    rm.run_manager._runs.clear()
    connection.reset_ready_cache()


def _wait(client, aid, states, tries=300):
    view = {}
    for _ in range(tries):
        view = client.get(f"/api/analyses/{aid}/run").json()
        if view.get("state") in states:
            return view
        time.sleep(0.05)
    raise AssertionError(f"run stayed {view.get('state')}")


def _start(client) -> str:
    aid = client.post("/api/analyses/draft", json={"form": FORM}).json()["analysis_id"]
    assert client.post(f"/api/analyses/{aid}/approve").status_code == 200
    return aid


def test_the_run_waits_for_review_then_continues_to_a_brief(app_client):
    client, worker, _ = app_client
    aid = _start(client)
    view = _wait(client, aid, {"awaiting_review"})
    review = view["review"]
    assert view["stage"] == "review"
    assert review["verification_status"] == "replan_required"
    assert review["findings_verified"] == 1 and review["recommended"] == "replan"
    assert review["proposed_tasks"][0]["worker"] == "competitor_intelligence"
    assert client.get(f"/api/analyses/{aid}").json()["status"] == "awaiting_review"
    # Nothing moves on by itself.
    time.sleep(0.3)
    assert client.get(f"/api/analyses/{aid}/run").json()["state"] == "awaiting_review"

    assert client.post(f"/api/analyses/{aid}/review", json={"decision": "continue"}).status_code == 200
    done = _wait(client, aid, {"completed", "partial"})
    assert done["brief_id"] and len(worker.tasks) == 1
    assert client.post(f"/api/analyses/{aid}/review", json={"decision": "continue"}).status_code == 409
    assert client.get(f"/api/briefs/{done['brief_id']}").status_code == 200


def test_replan_sends_out_the_tasks_the_person_edited(app_client):
    client, worker, _ = app_client
    aid = _start(client)
    review = _wait(client, aid, {"awaiting_review"})["review"]
    task = {**review["proposed_tasks"][0], "goal": "Find Kanz Pay's official Starter price on its own site."}
    client.post(f"/api/analyses/{aid}/review", json={"decision": "replan", "tasks": [task], "note": "Official site only"})
    second = _wait(client, aid, {"awaiting_review"} )
    assert second["review"]["round"] == 2 and second["review"]["verification_status"] == "verified"
    assert worker.tasks[-1].goal == task["goal"] and worker.tasks[-1].attempt == 2
    client.post(f"/api/analyses/{aid}/review", json={"decision": "continue"})
    assert _wait(client, aid, {"completed", "partial"})["brief_id"]


def test_a_review_resumes_after_a_restart_and_stop_writes_a_partial_brief(app_client):
    client, _, rm = app_client
    aid = _start(client)
    _wait(client, aid, {"awaiting_review"})
    rm.run_manager._runs.clear()          # what a restart loses: everything in memory
    view = client.get(f"/api/analyses/{aid}/run").json()
    assert view["state"] == "awaiting_review" and view["review"]["round"] == 1
    assert client.post(f"/api/analyses/{aid}/review", json={"decision": "stop"}).status_code == 200
    done = _wait(client, aid, {"completed", "partial"})
    assert done["state"] == "partial" and done["brief_id"]
