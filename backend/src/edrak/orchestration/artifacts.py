"""Persistence helpers for artifacts consumed by the evaluation pipeline."""

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def save_task_artifacts(task: Any, result: Any) -> None:
    """Persist one worker task in the evaluator's expected directory layout."""

    def safe(value: object) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._") or "unknown"

    task_dir = (
        Path("artifacts")
        / "runs"
        / safe(task.parent_request_id)
        / safe(task.worker.value)
        / f"{safe(task.task_id)}.a{task.attempt}"
    )
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "input.json").write_text(
            json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (task_dir / "output.json").write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # artifact persistence must not fail the worker run
        logger.warning("Could not save worker task artifacts to %s: %s", task_dir, exc)