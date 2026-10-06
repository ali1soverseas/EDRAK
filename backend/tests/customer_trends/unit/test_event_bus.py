import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from edrak.agents.customer_trends.deps import EventBus, model_name
from edrak.agents.customer_trends.llm.fake import ScriptedChatModel
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import load_brief
from tests.customer_trends.graph_helpers import models, worker_deps


def test_events_are_numbered_stamped_and_recorded_in_order() -> None:
    bus = EventBus("run-1", "task-1")
    first = bus.emit({"type": "node_started", "node": "intake"})
    second = bus.emit({"type": "node_finished", "node": "intake"})
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["run_id"] == "run-1" and first["task_id"] == "task-1"
    assert datetime.fromisoformat(first["ts"]).utcoffset().total_seconds() == 0  # type: ignore[union-attr]  # an aware timestamp
    assert bus.events == [first, second]


def test_an_event_keeps_the_ids_it_carries() -> None:
    bus = EventBus("run-1", "task-1")
    assert bus.emit({"type": "tool_called", "run_id": "other"})["run_id"] == "other"


def test_listeners_hear_each_event_and_a_failing_listener_does_not_stop_the_rest() -> None:
    bus = EventBus("r", "t")
    heard: list[str] = []

    def broken(event: dict[str, Any]) -> None:
        raise RuntimeError("listener bug")

    bus.subscribe(broken)
    bus.subscribe(lambda event: heard.append(event["type"]))
    bus.emit({"type": "a"})
    bus(dict(type="b"))
    assert heard == ["a", "b"]
    assert [e["seq"] for e in bus.events] == [1, 2]


def test_many_threads_get_distinct_consecutive_numbers() -> None:
    bus = EventBus("r", "t")
    threads = [
        threading.Thread(target=lambda: [bus.emit({"type": "x"}) for _ in range(50)])
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(e["seq"] for e in bus.events) == list(range(1, 401))


def test_a_role_model_is_built_once_and_named_in_the_provenance(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps = worker_deps(load_brief(), tmp_path, store)
    assert deps.models_used == {}
    assert deps.model("writer") is deps.model("writer")
    assert deps.models_used == {"writer": "scripted-chat"}


def test_the_tool_context_carries_the_brief_the_bus_and_the_analyst(
    tmp_path: Path, store: EvidenceStore
) -> None:
    brief = load_brief("product_launch")
    deps = worker_deps(brief, tmp_path, store, scripted=models())
    ctx = deps.tool_context(brief)
    assert (ctx.run_id, ctx.task_id) == (brief.run_id, brief.task_id)
    assert ctx.defaults == {
        "languages": ["ar", "en"],
        "geo": "EG",
        "since": None,
        "until": None,
        "depth": brief.depth,
        "entity": "Budgeting app for freelancers",
    }
    ctx.emit({"type": "tool_called"})
    assert deps.bus.events[-1]["type"] == "tool_called"
    assert ctx.analyst is deps.model("analyst")
    assert ctx.budget is deps.budget and ctx.providers is deps.providers


def test_a_model_without_a_name_attribute_is_named_by_its_type() -> None:
    assert model_name(ScriptedChatModel(responses=[])) == "scripted-chat"
