"""Persist input and output snapshots for the final analysis stages."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(_jsonable(value), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def save_stage_artifacts(run_id: Any, stage: str, stage_input: Any, stage_output: Any) -> None:
    """Write the latest input and output for a stage under its run directory."""
    try:
        safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id or ""))[:120]
        if not safe_run_id:
            logger.warning("Skipping %s stage artifacts: run id is missing", stage)
            return
        if stage not in {"verification", "cross_signal", "decision"}:
            raise ValueError(f"Unsupported run artifact stage: {stage}")

        configured_root = os.getenv("EDRAK_ARTIFACTS_DIR")
        runs_root = (
            Path(configured_root).expanduser()
            if configured_root
            else Path(__file__).resolve().parents[4] / "artifacts" / "runs"
        )
        stage_dir = runs_root / safe_run_id / "stages" / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _write_json(stage_dir / "input.json", stage_input)
            _write_json(stage_dir / "output.json", stage_output)
    except Exception:
        logger.exception("Could not save %s stage artifacts for run %s", stage, run_id)
