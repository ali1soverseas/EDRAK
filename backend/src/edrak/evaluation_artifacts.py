"""Persist per-analysis inputs needed to reproduce EDRAK evaluations."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edrak.contracts import BusinessRequest, ResearchPlan, WorkerResult
from edrak.contracts.verification import VerificationResult


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(_json_value(value), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


class EvaluationArtifactBundle:
    """Write evaluator-readable stage outputs beside a run manifest."""

    def __init__(
        self,
        analysis_id: str,
        request: BusinessRequest,
        plan: ResearchPlan,
    ) -> None:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", analysis_id)
        if not safe_id:
            raise ValueError("analysis_id must contain at least one safe filename character")
        configured_root = os.environ.get("EDRAK_EVALUATION_ARTIFACTS_DIR")
        root = Path(configured_root) if configured_root else Path(__file__).resolve().parents[3] / "artifacts" / "evaluation_runs"
        self.root = root / safe_id
        self.inputs = self.root / "inputs"
        self.context = self.root / "context"
        self.outputs = self.root / "outputs"
        self.manifest_path = self.root / "manifest.json"

        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        else:
            self.manifest = {
                "schema_version": 1,
                "analysis_id": analysis_id,
                "status": "running",
                "started_at_utc": datetime.now(UTC).isoformat(),
                "stages": [],
                "artifacts": {},
                "warnings": [],
            }
        self.manifest["status"] = "running"
        self.manifest.pop("completed_at_utc", None)
        self._write_request(request)
        self.write_plan(plan)
        self._save_manifest()

    def _save_manifest(self) -> None:
        self.manifest["updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_json(self.manifest_path, self.manifest)

    def _record_artifact(self, name: str, path: Path) -> None:
        self.manifest["artifacts"][name] = str(path.relative_to(self.root))

    def _write_request(self, request: BusinessRequest) -> None:
        context = request.model_dump(mode="json")
        competitors = request.business_context.competitors or request.business_context.targets
        _write_json(
            self.inputs / "initial_input.json",
            {
                **context,
                "research_goal": request.goal,
                "company": request.company_profile.name,
                "company_profile": context["company_profile"],
                "competitors": [_json_value(item) for item in competitors],
            },
        )
        self._record_artifact("initial_input", self.inputs / "initial_input.json")

    def write_plan(self, plan: ResearchPlan) -> None:
        path = self.context / "plan.json"
        _write_json(path, plan)
        self._record_artifact("plan", path)
        self._save_manifest()

    def record_node(
        self,
        node_name: str,
        node_output: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        stage: dict[str, Any] = {"node": node_name, "completed_at_utc": now}
        stage_outputs: list[str] = []

        if node_name in {"plan", "replan"} and node_output.get("plan") is not None:
            self.write_plan(node_output["plan"])
            stage_outputs.append("context/plan.json")

        if node_name == "dispatch":
            current_plan = state.get("plan")
            task_by_id = {task.task_id: task for task in current_plan.tasks} if current_plan else {}
            for result in node_output.get("results") or []:
                if not isinstance(result, WorkerResult):
                    continue
                artifact = result.model_dump(mode="json")
                task = task_by_id.get(result.task_id)
                if task:
                    metadata = dict(artifact.get("metadata") or {})
                    metadata.setdefault("research_goal", task.goal)
                    metadata.setdefault("research_focus", task.focus)
                    artifact["metadata"] = metadata
                path = self.inputs / (
                    f"worker_{result.worker.value}_attempt_{result.attempt}_"
                    f"{result.task_id[:12]}.json"
                )
                _write_json(path, artifact)
                self._record_artifact(f"worker:{result.task_id}:{result.attempt}", path)
                stage_outputs.append(str(path.relative_to(self.root)))
                if result.worker.value == "internal_intelligence":
                    stage_outputs.extend(self._write_rag_inputs(result, artifact))

        if node_name == "verify" and node_output.get("verification_result") is not None:
            result = node_output["verification_result"]
            if isinstance(result, VerificationResult):
                passes = 1 + sum(item["node"] == "verify" for item in self.manifest["stages"])
                path = self.inputs / f"verification_pass_{passes:03d}.json"
                _write_json(path, result)
                self._record_artifact(f"verification:{passes}", path)
                stage_outputs.append(str(path.relative_to(self.root)))

        if node_name == "cross_signal" and node_output.get("cross_signal") is not None:
            path = self.inputs / "cross_signal.json"
            _write_json(path, node_output["cross_signal"])
            self._record_artifact("cross_signal", path)
            stage_outputs.append(str(path.relative_to(self.root)))

        if node_name == "decision_analysis" and node_output.get("decision_analysis") is not None:
            path = self.inputs / "decision_analysis.json"
            _write_json(path, node_output["decision_analysis"])
            self._record_artifact("decision_analysis", path)
            stage_outputs.append(str(path.relative_to(self.root)))

        if node_name == "metrics":
            path = self.outputs / f"metrics_{len(self.manifest['stages']) + 1:03d}.json"
            _write_json(path, {"metrics_stage": True, **node_output})
            self._record_artifact(f"metrics:{len(self.manifest['stages']) + 1}", path)
            stage_outputs.append(str(path.relative_to(self.root)))

        if node_name == "finalize" and node_output.get("orchestration_result") is not None:
            path = self.inputs / "orchestration_result.json"
            _write_json(path, node_output["orchestration_result"])
            self._record_artifact("orchestration_result", path)
            stage_outputs.append(str(path.relative_to(self.root)))

        if node_name == "human_review":
            path = self.outputs / f"human_review_{len(self.manifest['stages']) + 1:03d}.json"
            _write_json(path, {
                "decision": node_output.get("review_decision"),
                "note": node_output.get("review_note"),
                "human_tasks": node_output.get("human_tasks"),
                "recorded_at_utc": now,
            })
            stage_outputs.append(str(path.relative_to(self.root)))

        stage["artifacts"] = stage_outputs
        self.manifest["stages"].append(stage)
        self._save_manifest()

    def _write_rag_inputs(self, result: WorkerResult, artifact: dict[str, Any]) -> list[str]:
        by_query: dict[str, list[dict[str, Any]]] = {}
        for evidence in artifact.get("evidence") or []:
            metadata = evidence.get("metadata") or {}
            query = metadata.get("search_query")
            if not isinstance(query, str) or not query.strip():
                continue
            by_query.setdefault(query.strip(), []).append(
                {
                    "chunk_id": metadata.get("chunk_id") or evidence.get("evidence_id"),
                    "text": evidence.get("excerpt") or evidence.get("extracted_fact") or "",
                    "score": metadata.get("relevance_score", metadata.get("confidence_score")),
                    "source_url": evidence.get("source_url"),
                    "metadata": metadata,
                }
            )

        paths = []
        for index, (query, chunks) in enumerate(by_query.items(), start=1):
            path = self.inputs / (
                f"rag_{result.worker.value}_attempt_{result.attempt}_"
                f"{result.task_id[:12]}_{index:03d}.json"
            )
            _write_json(
                path,
                {
                    "query": query,
                    "retrieved_chunks": chunks,
                    "worker": result.worker.value,
                    "task_id": result.task_id,
                    "attempt": result.attempt,
                },
            )
            self._record_artifact(f"rag:{result.task_id}:{result.attempt}:{index}", path)
            paths.append(str(path.relative_to(self.root)))
        return paths

    def save_brief(self, document: dict[str, Any]) -> None:
        path = self.outputs / "brief.json"
        _write_json(path, document)
        self._record_artifact("brief", path)
        self._save_manifest()

    def save_orchestration_result(self, result: Any) -> None:
        path = self.inputs / "orchestration_result.json"
        _write_json(path, result)
        self._record_artifact("orchestration_result", path)
        self._save_manifest()

    def add_warning(self, message: str) -> None:
        self.manifest["warnings"].append(message)
        self._save_manifest()

    def record_run_error(self, error_type: str, message: str) -> None:
        self.manifest["run_error"] = {"type": error_type, "message": message[:1000]}
        self._save_manifest()

    def finish(self, status: str) -> None:
        self.manifest["status"] = status
        self.manifest["completed_at_utc"] = datetime.now(UTC).isoformat()
        self._save_manifest()
