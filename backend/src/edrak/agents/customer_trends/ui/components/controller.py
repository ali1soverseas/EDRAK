"""Runs the graph on a background thread and hands its events to the UI.

The UI never waits for a run: it asks `drain` for the events that arrived since the last call.
`stop` cancels the run cooperatively, at its next await.
"""

import asyncio
import contextlib
import queue
import threading
from typing import Any

from edrak.agents.customer_trends.runner import stream_task
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings


class RunController:
    def __init__(self, brief: TaskBrief, settings: Settings) -> None:
        self.brief = brief
        self.settings = settings
        self.events: list[dict[str, Any]] = []
        self.error: str | None = None
        self.stopped = False
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread = threading.Thread(
            target=self._thread_main, name="customer-trends", daemon=True
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def drain(self) -> list[dict[str, Any]]:
        """The events that arrived since the last call, also kept in `events`."""
        fresh = []
        while True:
            try:
                fresh.append(self._queue.get_nowait())
            except queue.Empty:
                break
        self.events.extend(fresh)
        return fresh

    def stop(self) -> None:
        """Cancel a running run. Events already reported stay; no result is written for it. A run
        that has already finished is not marked stopped."""
        if self._loop is None or self._task is None or not self.running:
            return
        self.stopped = True
        with contextlib.suppress(RuntimeError):  # the loop closed as the run ended
            self._loop.call_soon_threadsafe(self._task.cancel)

    def _thread_main(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.ensure_future(self._consume())
        self._ready.set()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _consume(self) -> None:
        try:
            async for event in stream_task(self.brief, settings=self.settings):
                self._queue.put(event)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
