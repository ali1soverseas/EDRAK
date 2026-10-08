"""What the graph's nodes need from outside: settings, store, providers, models, sink, events."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel

from edrak.agents.customer_trends.llm.client import Role
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import ResultSink
from edrak.agents.customer_trends.tools.base import ToolContext
from edrak.agents.customer_trends.usecases import UseCases

log = get_logger(__name__)

LlmFactory = Callable[[Role], BaseChatModel]
Listener = Callable[[dict[str, Any]], None]


class EventBus:
    """Collects every event of a run in order and hands each to the listeners as it happens.

    Safe to call from any thread: the developer UI runs the graph on a worker thread and reads
    the events from another.
    """

    def __init__(self, run_id: str, task_id: str) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.events: list[dict[str, Any]] = []
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def emit(self, event: dict[str, Any]) -> dict[str, Any]:
        """Stamp, record and deliver an event; returns the stamped event."""
        with self._lock:
            stamped = {
                "seq": len(self.events) + 1,
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "run_id": self.run_id,
                "task_id": self.task_id,
                **event,
            }
            self.events.append(stamped)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(stamped)
            except Exception:
                log.warning("event_listener_failed", event_type=stamped.get("type"))
        return stamped

    def __call__(self, event: dict[str, Any]) -> None:
        self.emit(event)


@dataclass
class WorkerDeps:
    settings: Settings
    store: EvidenceStore
    providers: ProviderRegistry
    budget: BudgetTracker
    breaker: CircuitBreaker
    sink: ResultSink
    llm: LlmFactory
    bus: EventBus
    use_cases: UseCases
    _models: dict[str, BaseChatModel] = field(default_factory=dict)

    def model(self, role: Role) -> BaseChatModel:
        """The model of a role, built once per run."""
        if role not in self._models:
            self._models[role] = self.llm(role)
        return self._models[role]

    @property
    def models_used(self) -> dict[str, str]:
        return {role: model_name(model) for role, model in self._models.items()}

    def tool_context(self, brief: TaskBrief) -> ToolContext:
        """A tool context for this run: brief values fill what the model leaves out."""
        return ToolContext(
            settings=self.settings,
            store=self.store,
            providers=self.providers,
            budget=self.budget,
            breaker=self.breaker,
            run_id=brief.run_id,
            task_id=brief.task_id,
            emit=self.bus,
            defaults={
                "languages": brief.languages,
                "geo": brief.geo,
                "since": brief.since,
                "until": brief.until,
                "depth": brief.depth,
                "entity": brief.entity,
            },
            analyst=self.model("analyst"),
        )


def model_name(model: BaseChatModel) -> str:
    return str(
        getattr(model, "model", None) or getattr(model, "model_name", None) or model._llm_type
    )
