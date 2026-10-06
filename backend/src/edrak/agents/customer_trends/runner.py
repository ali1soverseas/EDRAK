"""Entry points of the worker: `run_task` and `stream_task` run one `TaskBrief` through the graph.

Both use the same graph and the same event stream. A run always ends with a
`CustomerTrendsResult` written through the sink: a finished run, one stopped at its time limit and
one that crashed differ only in status, gaps and warnings. Nothing here raises for a failing run.
"""

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from edrak.agents.customer_trends.assembly import build_result
from edrak.agents.customer_trends.deps import EventBus, Listener, LlmFactory, WorkerDeps
from edrak.agents.customer_trends.gaps import Gap, coverage_of, find_gaps
from edrak.agents.customer_trends.graph import build_graph, open_checkpointer
from edrak.agents.customer_trends.llm.client import Role, get_chat_model
from edrak.agents.customer_trends.llm.demo_script import demo_llm
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.cache import DiskCache
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult, to_worker_result
from edrak.agents.customer_trends.schemas.task import QueryPlan, TaskBrief, brief_from_task
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.agents.customer_trends.state import initial_state
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink, ResultSink
from edrak.agents.customer_trends.usecases import load_use_cases
from edrak.contracts import ResearchTask, WorkerResult, WorkerStatus, WorkerType

log = get_logger(__name__)

RECURSION_LIMIT = 100
DEMO_FIXTURES_RELATIVE = Path("backend/evals/customer_trends/fixtures/providers")
LlmSource = BaseChatModel | LlmFactory | None


def _llm_factory(llm: LlmSource, settings: Settings, brief: TaskBrief) -> LlmFactory:
    if llm is None and settings.edrak_fake_llm:
        return demo_llm(brief)
    if llm is None:

        def build(role: Role) -> BaseChatModel:
            return get_chat_model(role, settings=settings)

        return build
    if isinstance(llm, BaseChatModel):
        shared = llm
        return lambda role: shared
    return llm


def make_deps(
    brief: TaskBrief,
    *,
    store: EvidenceStore,
    settings: Settings,
    sink: ResultSink | None,
    providers: ProviderRegistry | None,
    llm: LlmSource,
) -> WorkerDeps:
    """The dependencies of one run. Given providers bring their own budget and breaker."""
    if providers is None:
        budget, breaker = BudgetTracker(brief.budget), CircuitBreaker()
        providers = ProviderRegistry.from_config(
            settings,
            budget,
            breaker,
            DiskCache.from_settings(settings),
            fixtures_dir=settings.edrak_fixtures_dir or settings.repo_root / DEMO_FIXTURES_RELATIVE,
        )
    return WorkerDeps(
        settings=settings,
        store=store,
        providers=providers,
        budget=providers.budget,
        breaker=providers.breaker,
        sink=sink or LocalSink(store, settings.artifacts_dir),
        llm=_llm_factory(llm, settings, brief),
        bus=EventBus(brief.run_id, brief.task_id),
        use_cases=load_use_cases(),
    )


def _run_gaps(deps: WorkerDeps, brief: TaskBrief, values: dict[str, Any], extra: Gap) -> list[Gap]:
    """The coverage gaps as they stand now, plus the gap that ended the run."""
    plan = values.get("plan")
    gaps = find_gaps(
        brief,
        deps.use_cases.for_use_case(brief.use_case),
        coverage_of(deps.store, brief.run_id),
        review_targets_planned=bool(plan and QueryPlan.model_validate(plan).review_targets),
        branch_errors=values.get("branch_errors", {}),
    )
    return [*gaps, extra]


def _conclude(
    deps: WorkerDeps, brief: TaskBrief, values: dict[str, Any], gap: Gap
) -> CustomerTrendsResult:
    """A result for a run that did not finish: what is stored, the open gaps, why it stopped."""
    warnings = [*values.get("warnings", []), gap.description]
    result = build_result(
        deps, brief, gaps=_run_gaps(deps, brief, values, gap), warnings=warnings, ending=gap.id
    )
    try:
        deps.sink.write_result(result)
    except Exception:
        log.error("result_write_failed", run_id=brief.run_id)
    return result


async def _execute(brief: TaskBrief, deps: WorkerDeps) -> CustomerTrendsResult:
    """Run the graph under the time limit; every outcome is a result and a closing event."""
    deps.store.create_run(brief.run_id, brief.task_id, brief)
    config = RunnableConfig(
        configurable={"thread_id": brief.run_id}, recursion_limit=RECURSION_LIMIT
    )
    values: dict[str, Any] = {}
    try:
        async with open_checkpointer(deps.settings) as saver:
            graph = build_graph(deps, saver)
            snapshot = await graph.aget_state(config)
            if snapshot.values and not snapshot.next and snapshot.values.get("result"):
                result = CustomerTrendsResult.model_validate(snapshot.values["result"])
                deps.bus.emit({"type": "run_finished", "status": result.control_summary.status})
                return result
            try:
                async with asyncio.timeout(brief.budget.max_seconds) as deadline:
                    graph_input = None if snapshot.next else initial_state(brief)
                    final = await graph.ainvoke(graph_input, config)
                values = dict(final)
            except TimeoutError:
                if not deadline.expired():
                    raise
                values = dict((await graph.aget_state(config)).values)
                result = _conclude(
                    deps,
                    brief,
                    values,
                    Gap(
                        id="time_limit",
                        severity="critical",
                        description=(
                            f"the run stopped at its time limit of {brief.budget.max_seconds:g} s"
                        ),
                        suggested_action="run again with a larger max_seconds or a lower depth",
                    ),
                )
                _finished(deps, result)
                return result
    except Exception as exc:
        log.error("run_failed", run_id=brief.run_id, error=type(exc).__name__)
        deps.bus.emit(
            {"type": "run_failed", "error": type(exc).__name__, "message": str(exc)[:300]}
        )
        return _conclude(
            deps,
            brief,
            values,
            Gap(
                id="run_failed",
                severity="critical",
                description=f"the run failed with {type(exc).__name__}: {str(exc)[:200]}",
                suggested_action="read the log for this run_id and run it again",
            ),
        )
    result = CustomerTrendsResult.model_validate(values["result"])
    _finished(deps, result)
    return result


def _finished(deps: WorkerDeps, result: CustomerTrendsResult) -> None:
    deps.bus.emit(
        {
            "type": "run_finished",
            "status": result.control_summary.status,
            "findings": result.control_summary.findings_count,
            "evidence": result.control_summary.evidence_count,
        }
    )


@asynccontextmanager
async def _session(
    brief: TaskBrief,
    sink: ResultSink | None,
    providers: ProviderRegistry | None,
    llm: LlmSource,
    settings: Settings,
    listener: Listener | None = None,
) -> AsyncIterator[WorkerDeps]:
    """The dependencies of one run, with the store and any providers built here closed after."""
    store = EvidenceStore.from_settings(settings)
    deps = make_deps(brief, store=store, settings=settings, sink=sink, providers=providers, llm=llm)
    if listener is not None:
        deps.bus.subscribe(listener)
    try:
        yield deps
    finally:
        if providers is None:
            await deps.providers.aclose()
        store.close()


async def run_task(
    brief: TaskBrief,
    *,
    sink: ResultSink | None = None,
    providers: ProviderRegistry | None = None,
    llm: LlmSource = None,
    settings: Settings | None = None,
) -> CustomerTrendsResult:
    """Run a brief to completion. `providers` and `llm` let tests and the UI inject fixtures and
    the scripted model; `llm` is one model for every role or a function from role to model."""
    async with _session(brief, sink, providers, llm, settings or get_settings()) as deps:
        return await _execute(brief, deps)


async def stream_task(
    brief: TaskBrief,
    *,
    sink: ResultSink | None = None,
    providers: ProviderRegistry | None = None,
    llm: LlmSource = None,
    settings: Settings | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """The events of a run as they happen, ending with `run_finished` or `run_failed`. The result
    is in the sink (and in the last `run_finished` event's status)."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def listener(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def go() -> CustomerTrendsResult:
        async with _session(
            brief, sink, providers, llm, settings or get_settings(), listener
        ) as deps:
            return await _execute(brief, deps)

    task = asyncio.create_task(go())
    task.add_done_callback(lambda _: queue.put_nowait(None))
    try:
        while (event := await queue.get()) is not None:
            yield event
        await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def arun_worker(
    task: ResearchTask,
    *,
    sink: ResultSink | None = None,
    providers: ProviderRegistry | None = None,
    llm: LlmSource = None,
    settings: Settings | None = None,
) -> WorkerResult:
    """The orchestrator's entry, async: a shared `ResearchTask` in, a shared `WorkerResult` out.

    Never raises. A task that cannot be run, or a run that crashes before it has a result, comes
    back as a `failed` result with the reason; the full artifact stays with the sink and the store.
    """
    settings = settings or get_settings()
    try:
        brief = brief_from_task(task)
        async with _session(brief, sink, providers, llm, settings) as deps:
            result = await _execute(brief, deps)
            cited = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
            return to_worker_result(
                result,
                deps.store.get_items(brief.run_id, cited),
                task=task,
                location=deps.store.result_location(brief.run_id),
                synthetic=settings.edrak_provider_mode == "fixture",
            )
    except Exception as exc:
        log.error(
            "worker_failed", task_id=task.task_id, error=type(exc).__name__, detail=str(exc)[:200]
        )
        return WorkerResult(
            task_id=task.task_id,
            worker=WorkerType.CUSTOMER_TRENDS,
            status=WorkerStatus.FAILED,
            attempt=task.attempt,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )


def run_worker(
    task: ResearchTask,
    *,
    sink: ResultSink | None = None,
    providers: ProviderRegistry | None = None,
    llm: LlmSource = None,
    settings: Settings | None = None,
) -> WorkerResult:
    """`arun_worker` for the orchestrator, whose workers are plain functions run on threads."""
    coroutine = arun_worker(task, sink=sink, providers=providers, llm=llm, settings=settings)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()
