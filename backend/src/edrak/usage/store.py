"""Append-only usage log.

One JSON object per line, plus a human-readable line, both written by a single
background thread that owns the files. Many workers finishing calls at once only
ever enqueue a line, so a run is never blocked, concurrent writes cannot
interleave, and a logging failure is swallowed rather than raised into a
workflow. Call ``flush()`` before reading in a test or a short-lived script.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

JSONL_NAME = "llm_calls.jsonl"
LOG_NAME = "llm_calls.log"

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parent / "logs"
_REPO_ROOT = Path(__file__).resolve().parents[4]


def enabled() -> bool:
    """Tracking is on unless explicitly switched off."""
    return os.getenv("EDRAK_LLM_TRACKING", "1").strip().lower() not in {"0", "false", "no", "off"}


def log_dir() -> Path:
    """Where the logs live: ``EDRAK_LLM_USAGE_DIR`` or ``usage/logs`` by default."""
    override = os.getenv("EDRAK_LLM_USAGE_DIR", "").strip()
    if not override:
        return _DEFAULT_DIR
    path = Path(override).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def _fmt_line(record: dict[str, Any]) -> str:
    tokens = f"in={_h(record.get('input_tokens'))} out={_h(record.get('output_tokens'))}"
    latency = record.get("latency_ms")
    latency_s = f"{latency / 1000:.2f}s" if isinstance(latency, (int, float)) else "?"
    cost = record.get("estimated_cost_usd")
    if record.get("pricing_known") and isinstance(cost, (int, float)):
        cost_s = f"${cost:.6f}" if cost else f"$0 ({record.get('pricing_source') or 'free'})"
    else:
        cost_s = "cost unknown"
    retry = record.get("retry_attempt") or 0
    retry_s = f" retry={retry}" if retry else ""
    return " ".join(
        part
        for part in (
            str(record.get("timestamp") or ""),
            str(record.get("run_id") or "-"),
            str(record.get("stage") or "-"),
            str(record.get("status") or "-") + retry_s,
            tokens,
            latency_s,
            cost_s,
            str(record.get("model") or "-"),
            ("error=" + str(record["error_type"])) if record.get("error_type") else "",
        )
        if part
    )


def _h(value: Any) -> str:
    return str(value) if value is not None else "?"


class UsageLog:
    """A queue drained by one daemon thread into ``llm_calls.jsonl``/``.log``."""

    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._start_lock = threading.Lock()
        self._cond = threading.Condition()
        self._pending = 0
        self._thread: threading.Thread | None = None

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                thread = threading.Thread(
                    target=self._run, name="edrak-usage-log", daemon=True
                )
                self._thread = thread
                thread.start()

    def _run(self) -> None:
        directory = log_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            jsonl = (directory / JSONL_NAME).open("a", encoding="utf-8")
            text = (directory / LOG_NAME).open("a", encoding="utf-8")
        except OSError:
            # A directory we cannot write to drops tracking, not the run.
            return
        try:
            with jsonl, text:
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    try:
                        jsonl.write(json.dumps(item, ensure_ascii=False) + "\n")
                        jsonl.flush()
                        text.write(_fmt_line(item) + "\n")
                        text.flush()
                    except (OSError, TypeError, ValueError):
                        logger.debug("Could not write a usage record", exc_info=True)
                    finally:
                        with self._cond:
                            self._pending -= 1
                            self._cond.notify_all()
        finally:
            with self._cond:
                self._pending = 0
                self._cond.notify_all()

    def emit(self, record: dict[str, Any]) -> None:
        """Queue one record. Never blocks and never raises."""
        if not enabled():
            return
        try:
            self._ensure_thread()
            with self._cond:
                self._pending += 1
            self._queue.put_nowait(record)
        except Exception:
            logger.debug("Could not queue a usage record", exc_info=True)

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until everything queued is written. Returns False on timeout."""
        with self._cond:
            return self._cond.wait_for(lambda: self._pending == 0, timeout=timeout)


log = UsageLog()

# A process can exit with a record still queued. The writer is a daemon thread,
# which the interpreter kills on shutdown, so drain the queue first: without
# this, a short-lived script (a CLI, a one-off job) silently loses its last
# calls.
atexit.register(log.flush, 5.0)


def emit(record: dict[str, Any]) -> None:
    log.emit(record)


def flush(timeout: float = 5.0) -> bool:
    return log.flush(timeout)


def read_records(path: Path | None = None) -> list[dict[str, Any]]:
    """Every record written so far. Flush first if a call may still be in flight."""
    target = path or (log_dir() / JSONL_NAME)
    if not target.is_file():
        return []
    out: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def iter_records(path: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from read_records(path)
