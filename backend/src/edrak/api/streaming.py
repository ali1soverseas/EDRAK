"""Server-Sent Events (SSE) streaming helpers for live run updates."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator


def format_sse_event(event_type: str, data: Any) -> str:
    """Format payload as a standard SSE message.

    Sends both event name and a JSON payload containing {type, data}
    so clients can use either `onmessage` or `addEventListener(event_type)`.
    """
    payload = json.dumps({"type": event_type, "data": data}, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


class EventStream:
    """Live events of one run, delivered to every open listener.

    Each listener gets its own queue, so two open tabs both see every event. `queue` keeps the
    most recent events for code that inspects the stream without listening (tests do).
    """

    RECENT = 500

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.closed: bool = False
        self._listeners: list[asyncio.Queue[str]] = []

    def _put(self, message: str) -> None:
        if self.queue.qsize() >= self.RECENT:
            self.queue.get_nowait()
        self.queue.put_nowait(message)
        for listener in self._listeners:
            listener.put_nowait(message)

    async def emit(self, event_type: str, data: Any) -> None:
        """Push an event to listeners."""
        if not self.closed:
            self._put(format_sse_event(event_type, data))

    def emit_nowait(self, event_type: str, data: Any) -> None:
        """`emit` for code already running on the event loop that cannot await."""
        if not self.closed:
            self._put(format_sse_event(event_type, data))

    def close(self) -> None:
        """Mark stream as closed. Listeners drain what they have and stop."""
        self.closed = True

    async def stream(self) -> AsyncGenerator[str, None]:
        """Yield events as they arrive until the run ends."""
        mine: asyncio.Queue[str] = asyncio.Queue()
        self._listeners.append(mine)
        try:
            while not self.closed or not mine.empty():
                try:
                    yield await asyncio.wait_for(mine.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    if self.closed:
                        break
                    yield ": ping\n\n"
        finally:
            self._listeners.remove(mine)
