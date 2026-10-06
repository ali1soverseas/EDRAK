import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from edrak.agents.customer_trends.ui.components import controller as controller_module
from edrak.agents.customer_trends.ui.components.controller import RunController
from tests.customer_trends.factories import load_brief
from tests.customer_trends.graph_helpers import scenario_settings


def wait_until(condition: Callable[[], bool], seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def demo_settings(tmp_path: Path) -> Any:
    return scenario_settings(tmp_path).model_copy(
        update={"edrak_provider_mode": "fixture", "edrak_fake_llm": True}
    )


def test_a_run_reports_its_events_in_batches_and_keeps_them(tmp_path: Path) -> None:
    controller = RunController(load_brief(), demo_settings(tmp_path))
    controller.start()
    seen: list[dict[str, Any]] = []
    wait_until(lambda: (seen.extend(controller.drain()) or True) and not controller.running)
    seen.extend(controller.drain())
    assert seen[0]["type"] == "node_started" and seen[0]["node"] == "intake"
    assert seen[-1]["type"] == "run_finished" and seen[-1]["status"] == "complete"
    assert controller.events == seen and controller.drain() == []
    assert controller.error is None and controller.stopped is False


def slow_stream(delay: float) -> Callable[..., AsyncIterator[dict[str, Any]]]:
    async def stream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "node_started", "node": "intake"}
        await asyncio.sleep(delay)
        yield {"type": "run_finished", "status": "complete"}

    return stream


def test_stop_cancels_the_run_at_its_next_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(controller_module, "stream_task", slow_stream(30.0))
    controller = RunController(load_brief(), demo_settings(tmp_path))
    controller.start()
    wait_until(lambda: bool(controller.drain()) or bool(controller.events))
    assert controller.running
    started = time.monotonic()
    controller.stop()
    wait_until(lambda: not controller.running, seconds=5)
    assert time.monotonic() - started < 5
    assert controller.stopped is True and controller.error is None
    assert [e["type"] for e in controller.events] == ["node_started"]


def test_a_failure_of_the_stream_is_kept_for_the_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        raise RuntimeError("the stream broke")
        yield {}

    monkeypatch.setattr(controller_module, "stream_task", broken)
    controller = RunController(load_brief(), demo_settings(tmp_path))
    controller.start()
    wait_until(lambda: not controller.running)
    assert controller.error == "RuntimeError: the stream broke"


def test_stopping_a_finished_run_does_nothing(tmp_path: Path) -> None:
    controller = RunController(load_brief(), demo_settings(tmp_path))
    controller.start()
    wait_until(lambda: not controller.running)
    controller.stop()
    assert controller.stopped is False and not controller.running
