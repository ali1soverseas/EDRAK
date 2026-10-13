"""One plain-text line per agent action, written to a file and published to live listeners.

Workers call ``log_action(agent, message)`` at each step. The line always goes to
``artifacts/logs/<agent>.log``. If a run is being watched (the API's run manager
subscribes for the length of a run), the same line is also handed to its listener, which
is how the live run screen shows what each worker is doing as it happens.
"""

from __future__ import annotations

import os

import contextvars
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]

Listener = Callable[[str, str], None]

_lock = threading.Lock()
_listeners: dict[str, Listener] = {}
# Which run the current code belongs to. It follows ordinary threads and tasks, but not a
# worker that starts its own thread pool, so there is a fallback in `_deliver`.
_current_run: contextvars.ContextVar[str | None] = contextvars.ContextVar("edrak_run", default=None)


def subscribe(run_id: str, listener: Listener) -> contextvars.Token[str | None]:
    """Start delivering log lines to `listener` and tie the calling context to `run_id`."""
    with _lock:
        _listeners[run_id] = listener
    return _current_run.set(run_id)


def unsubscribe(run_id: str, token: contextvars.Token[str | None] | None = None) -> None:
    with _lock:
        _listeners.pop(run_id, None)
    if token is not None:
        try:
            _current_run.reset(token)
        except ValueError:
            # Reset from a different context than the one that set it: nothing to undo.
            pass


def _deliver(agent: str, message: str) -> None:
    with _lock:
        listeners = dict(_listeners)
    if not listeners:
        return
    run_id = _current_run.get()
    if run_id in listeners:
        targets = [listeners[run_id]]
    elif len(listeners) == 1:
        # The line came from a thread that lost the run's context (a worker that runs its
        # own event loop). With a single run in flight there is no doubt whose it is.
        targets = list(listeners.values())
    else:
        return
    for listener in targets:
        try:
            listener(agent, message)
        except Exception:  # noqa: BLE001 - a listener must never break a worker
            pass


def log_action(agent: str, message: str, *, live: bool = True) -> None:
    """Append to the agent's log file and, unless `live` is False, show it on the run screen."""
    # EDRAK_LOG_DIR moves the logs (the tests point it at a temporary folder so they never mix with real runs).
    path = Path(os.environ.get("EDRAK_LOG_DIR") or _ROOT / "artifacts" / "logs") / f"{agent}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        pass
    if live:
        _deliver(agent, message)
