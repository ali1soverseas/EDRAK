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
    """An asynchronous event stream for a single run."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.closed: bool = False

    async def emit(self, event_type: str, data: Any) -> None:
        """Push an event to listeners."""
        if not self.closed:
            formatted = format_sse_event(event_type, data)
            await self.queue.put(formatted)

    def close(self) -> None:
        """Mark stream as closed."""
        self.closed = True

    async def stream(self) -> AsyncGenerator[str, None]:
        """Yield events as they arrive until finished."""
        while not self.closed or not self.queue.empty():
            try:
                # Wait with timeout to send keep-alive comment if idle
                msg = await asyncio.wait_for(self.queue.get(), timeout=15.0)
                yield msg
            except asyncio.TimeoutError:
                if self.closed:
                    break
                # Keep-alive comment
                yield ": ping\n\n"
