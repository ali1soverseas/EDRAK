"""The worker through the shared contracts: a ResearchTask in, a valid WorkerResult out."""

import json
from pathlib import Path
from typing import Any

from edrak.agents.customer_trends.runner import arun_worker, run_worker
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink
from edrak.agents.customer_trends.worker import CustomerTrendsWorker, register
from edrak.contracts import (
    Worker,
    WorkerRegistry,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from tests.customer_trends.contract_helpers import research_task
from tests.customer_trends.graph_helpers import (
    PLAN,
    World,
    WriterScript,
    models,
    scenario_settings,
    world_registry,
)


def worker_for(
    tmp_path: Path, world: World | None = None, **model_options: Any
) -> CustomerTrendsWorker:
    return CustomerTrendsWorker(
        providers=world_registry(world),
        llm=models(**model_options).factory,
        settings=scenario_settings(tmp_path),
    )


def assert_valid(result: WorkerResult) -> None:
    assert WorkerResult.model_validate(result.model_dump()) == result
    assert WorkerResult.model_validate_json(result.model_dump_json()) == result


def test_a_task_in_gives_a_valid_compact_worker_result(tmp_path: Path) -> None:
    task = research_task()
    result = worker_for(tmp_path).run(task)
    assert_valid(result)
    assert (result.task_id, result.worker, result.attempt) == (
        task.task_id,
        WorkerType.CUSTOMER_TRENDS,
        1,
    )
    assert result.status is WorkerStatus.COMPLETED and result.error is None
    assert len(result.findings) == 3 and result.evidence and result.confidence is not None
    cited = {ref.evidence_id for f in result.findings for ref in f.evidence_refs}
    assert cited == {e.evidence_id for e in result.evidence}
    assert all(len(e.extracted_fact) <= 280 for e in result.evidence)
    assert all(f.finding_id.startswith(f"{task.task_id}:") for f in result.findings)
    assert all(e.is_synthetic is False for e in result.evidence)
    # compact: nothing near the size of the stored evidence
    assert len(result.model_dump_json()) < 60_000


def test_the_result_points_to_the_stored_artifact(tmp_path: Path) -> None:
    result = worker_for(tmp_path).run(research_task())
    artifact = result.metadata["artifact"]
    settings = scenario_settings(tmp_path)
    assert Path(artifact["result_location"]).is_file()
    assert artifact["evidence_store"] == str(settings.data_dir / "evidence.db")
    assert artifact["evidence_count"] > 60
    run_id = result.metadata["run_id"]
    with EvidenceStore.from_settings(settings) as store:
        full = LocalSink(store, settings.artifacts_dir).read_result(run_id)
        assert isinstance(full, CustomerTrendsResult)
        assert full.control_summary.headline == result.metadata["control_summary"]["headline"]
        assert store.existing_ids(run_id, [e.evidence_id for e in result.evidence]) == {
            e.evidence_id for e in result.evidence
        }
    stored_finding_ids = {f.id for f in full.findings}
    assert {f.finding_id.split(":", 1)[1] for f in result.findings} == stored_finding_ids
    assert set(result.metadata["finding_metrics"]) == {f.finding_id for f in result.findings}


def test_the_worker_is_a_shared_worker_registered_under_its_name(tmp_path: Path) -> None:
    registry = WorkerRegistry()
    worker = register(
        registry,
        providers=world_registry(),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert isinstance(worker, Worker) and worker.worker_type is WorkerType.CUSTOMER_TRENDS
    assert WorkerType.CUSTOMER_TRENDS.value == "customer_trends"
    assert WorkerType.CUSTOMER_TRENDS in registry and len(registry) == 1
    assert registry.get(WorkerType.CUSTOMER_TRENDS) is worker
    # what the orchestrator does with a worker: run it and validate what comes back
    task = research_task()
    assert_valid(WorkerResult.model_validate(registry.get(task.worker).run(task)))


def test_no_evidence_is_a_valid_result_not_a_crash(tmp_path: Path) -> None:
    world = World(
        failing=set(
            __import__("edrak.agents.customer_trends.providers.config", fromlist=["x"])
            .load_providers_config()
            .routing
        )
    )
    result = worker_for(
        tmp_path, world, plans=[PLAN, PLAN], writer=WriterScript(headline=None)
    ).run(research_task())
    assert_valid(result)
    assert result.status is WorkerStatus.NO_EVIDENCE and result.confidence is None
    assert result.findings == [] and result.evidence == []
    assert any("only 0 evidence items" in gap for gap in result.gaps)
    assert result.metadata["control_summary"]["status"] == "insufficient"
    assert Path(result.metadata["artifact"]["result_location"]).is_file()


def test_a_run_that_cannot_be_started_is_a_failed_result_with_the_reason(tmp_path: Path) -> None:
    result = worker_for(tmp_path).run(research_task(context={"time_window_days": 10**9}))
    assert_valid(result)
    assert result.status is WorkerStatus.FAILED
    assert result.error is not None and "OverflowError" in result.error
    assert result.findings == [] and result.evidence == []


def test_a_crashing_run_without_findings_is_reported_as_failed(tmp_path: Path) -> None:
    class BrokenSink:
        def write_result(self, result: CustomerTrendsResult) -> str:
            raise OSError("disk full")

        def read_result(self, run_id: str) -> CustomerTrendsResult:
            raise OSError("disk full")

    worker = CustomerTrendsWorker(
        sink=BrokenSink(),
        providers=world_registry(),
        llm=models(writer=WriterScript(fail=True)).factory,
        settings=scenario_settings(tmp_path),
    )
    result = worker.run(research_task())
    assert_valid(result)
    assert result.status is WorkerStatus.FAILED
    assert result.error is not None and result.error.startswith("the run failed with OSError")


def test_a_retry_is_a_new_run_and_the_same_attempt_is_idempotent(tmp_path: Path) -> None:
    world = World()
    first = run_worker(
        research_task(),
        providers=world_registry(world),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    calls = dict(world.calls)
    again = run_worker(
        research_task(),
        providers=world_registry(world),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert again.metadata["run_id"] == first.metadata["run_id"] and world.calls == calls
    retry = run_worker(
        research_task(attempt=2),
        providers=world_registry(world),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert retry.attempt == 2 and retry.metadata["run_id"] != first.metadata["run_id"]
    assert world.calls != calls
    assert_valid(retry)


async def test_run_worker_also_works_from_inside_a_running_event_loop(tmp_path: Path) -> None:
    result = run_worker(
        research_task(),
        providers=world_registry(),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert result.status is WorkerStatus.COMPLETED
    assert_valid(result)


async def test_the_async_entry_returns_the_same_kind_of_result(tmp_path: Path) -> None:
    result = await arun_worker(
        research_task(),
        providers=world_registry(),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert_valid(result)
    assert json.loads(result.model_dump_json())["worker"] == "customer_trends"


def test_the_orchestrator_can_run_several_tasks_at_once_on_threads(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    tasks = [research_task(task_id=f"task-par-{n}") for n in range(3)]
    settings: Settings = scenario_settings(tmp_path)

    def run(task: Any) -> WorkerResult:
        return run_worker(task, providers=world_registry(), llm=models().factory, settings=settings)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, tasks))
    assert [r.task_id for r in results] == [t.task_id for t in tasks]
    assert all(r.status is WorkerStatus.COMPLETED for r in results)
    assert len({r.metadata["run_id"] for r in results}) == 3
