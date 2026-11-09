"""Hand-off of the worker's result package to the stages that read it (SPEC section 7)."""

import json
import re
from pathlib import Path
from typing import Protocol

from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tracing import save_customer_result

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ResultSink(Protocol):
    def write_result(self, result: CustomerTrendsResult) -> str:
        """Persist the result and return its location."""
        ...

    def read_result(self, run_id: str) -> CustomerTrendsResult: ...


class LocalSink:
    """Stores attempt results in the shared trace file and registers each result.

    `parent_request_id` names the orchestrator request the run belongs to. A run started on its
    own (the CLI, the UI, the tests) has none, and its run id stands in for it.
    """

    def __init__(
        self,
        store: EvidenceStore,
        artifacts_dir: Path,
        *,
        parent_request_id: str | None = None,
    ) -> None:
        self._store = store
        self._root = artifacts_dir
        self._parent_request_id = parent_request_id

    def result_path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        request_id = self._request_id(run_id)
        if not _RUN_ID.fullmatch(request_id):
            raise ValueError(f"invalid parent_request_id: {request_id!r}")
        return self._root / "runs" / request_id / "customer_trends_tracing.json"

    def _request_id(self, run_id: str) -> str:
        return self._parent_request_id or run_id

    def write_result(self, result: CustomerTrendsResult) -> str:
        self.result_path(result.run_id)
        path = save_customer_result(
            result,
            request_id=self._request_id(result.run_id),
            artifacts_dir=self._root,
        )
        self._store.register_result(result.run_id, str(path))
        return str(path)

    def read_result(self, run_id: str) -> CustomerTrendsResult:
        path = self.result_path(run_id)
        record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        try:
            result = record["results"][run_id]
        except KeyError:
            legacy_path = path.with_name(f"customer_trends_{run_id}_result.json")
            return CustomerTrendsResult.model_validate_json(
                legacy_path.read_text(encoding="utf-8")
            )
        return CustomerTrendsResult.model_validate(result)
