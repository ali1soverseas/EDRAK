"""Hand-off of the worker's result package to the stages that read it (SPEC section 7)."""

import json
import os
import re
from pathlib import Path
from typing import Protocol

from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ResultSink(Protocol):
    def write_result(self, result: CustomerTrendsResult) -> str:
        """Persist the result and return its location."""
        ...

    def read_result(self, run_id: str) -> CustomerTrendsResult: ...


class LocalSink:
    """Writes `<artifacts_dir>/runs/<run_id>/customer_trends/result.json` and registers it."""

    def __init__(self, store: EvidenceStore, artifacts_dir: Path) -> None:
        self._store = store
        self._root = artifacts_dir

    def result_path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        return self._root / "runs" / run_id / "customer_trends" / "result.json"

    def write_result(self, result: CustomerTrendsResult) -> str:
        path = self.result_path(result.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, path)
        self._store.register_result(result.run_id, str(path))
        return str(path)

    def read_result(self, run_id: str) -> CustomerTrendsResult:
        text = self.result_path(run_id).read_text(encoding="utf-8")
        return CustomerTrendsResult.model_validate_json(text)
