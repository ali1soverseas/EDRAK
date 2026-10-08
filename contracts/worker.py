from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .task import ResearchTask, WorkerType

if TYPE_CHECKING:
    from .result import WorkerResult


@runtime_checkable
class Worker(Protocol):
    """What the orchestrator requires of a domain worker.

    Implementations live under ``edrak.agents`` and are LangGraph based. The
    orchestrator never sees a worker's private state, only this signature.
    """

    @property
    def worker_type(self) -> WorkerType: ...

    def run(self, task: ResearchTask) -> WorkerResult: ...


class WorkerNotRegisteredError(KeyError):
    """Raised when a plan references a worker the registry cannot resolve."""

    def __init__(self, worker_type: WorkerType) -> None:
        self.worker_type = worker_type
        super().__init__(f"no worker registered for {worker_type.value!r}")


class WorkerRegistry:
    """Maps each WorkerType to the worker that serves it."""

    def __init__(self, workers: Mapping[WorkerType, Worker] | None = None) -> None:
        self._workers: dict[WorkerType, Worker] = dict(workers or {})

    def register(self, worker_type: WorkerType, worker: Worker) -> None:
        self._workers[worker_type] = worker

    def get(self, worker_type: WorkerType) -> Worker:
        try:
            return self._workers[worker_type]
        except KeyError:
            raise WorkerNotRegisteredError(worker_type) from None

    def worker_types(self) -> tuple[WorkerType, ...]:
        return tuple(WorkerType)

    def missing(self, required: tuple[WorkerType, ...]) -> tuple[WorkerType, ...]:
        return tuple(worker_type for worker_type in required if worker_type not in self._workers)

    def __contains__(self, worker_type: object) -> bool:
        return worker_type in self._workers

    def __len__(self) -> int:
        return len(self._workers)