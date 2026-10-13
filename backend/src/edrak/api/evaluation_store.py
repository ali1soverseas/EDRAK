"""Save each agent output from a frontend orchestrator run as its own JSON file.

Files land in ``<repo>/evaluation_data/<analysis_id>/``. A later pass by the
same agent (a replan) is written beside the first file instead of replacing it.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from edrak.core.config import settings

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def dump_node_outputs(analysis_id: str, node_name: str, node_output: dict[str, Any]) -> None:
    """Write the agent payloads this graph node just produced. Never raises."""
    try:
        if node_name == "dispatch":
            for result in node_output.get("results") or []:
                worker = getattr(getattr(result, "worker", None), "value", None) or "worker"
                _write(analysis_id, worker, result)
            return
        if node_name == "verify":
            result = node_output.get("verification_result")
            if result is not None:
                _write(analysis_id, "verification", result)
            return
        if node_name == "cross_signal":
            result = node_output.get("cross_signal")
            if result is not None:
                _write(analysis_id, "cross_signal", result)
            return
        if node_name == "decision_analysis":
            result = node_output.get("decision_analysis")
            if result is not None:
                _write(analysis_id, "decision_analysis", result)
    except Exception:
        logger.exception("Could not save evaluation output for %s/%s", analysis_id, node_name)


def _write(analysis_id: str, agent: str, payload: Any) -> None:
    folder = settings.BASE_DIR / "evaluation_data" / _SAFE.sub("_", analysis_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique(folder, _SAFE.sub("_", agent) or "agent")
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Saved %s output -> %s", agent, path)


def _unique(folder: Path, name: str) -> Path:
    path = folder / f"{name}.json"
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = folder / f"{name}_{index}.json"
        if not candidate.exists():
            return candidate
        index += 1


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
