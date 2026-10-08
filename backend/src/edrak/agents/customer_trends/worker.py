"""The worker as the orchestrator sees it: a registered `Worker` named `customer_trends`."""

from edrak.agents.customer_trends.deps import Listener
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.runner import LlmSource, run_worker
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.sink import ResultSink
from edrak.contracts import ResearchTask, WorkerRegistry, WorkerResult, WorkerType

__all__ = ["CustomerTrendsWorker", "Listener", "register"]


class CustomerTrendsWorker:
    """Implements the shared `Worker` protocol. Each task gets its own budget and breaker unless
    `providers` is given, which is meant for tests that serve fixtures."""

    def __init__(
        self,
        *,
        sink: ResultSink | None = None,
        providers: ProviderRegistry | None = None,
        llm: LlmSource = None,
        settings: Settings | None = None,
    ) -> None:
        self._sink = sink
        self._providers = providers
        self._llm = llm
        self._settings = settings

    @property
    def worker_type(self) -> WorkerType:
        return WorkerType.CUSTOMER_TRENDS

    def run(self, task: ResearchTask) -> WorkerResult:
        return run_worker(
            task,
            sink=self._sink,
            providers=self._providers,
            llm=self._llm,
            settings=self._settings,
        )


def register(registry: WorkerRegistry, **dependencies: object) -> CustomerTrendsWorker:
    """Add the worker to the shared registry under `customer_trends` and return it."""
    worker = CustomerTrendsWorker(**dependencies)  # type: ignore[arg-type]  # keyword dependencies
    registry.register(WorkerType.CUSTOMER_TRENDS, worker)
    return worker
