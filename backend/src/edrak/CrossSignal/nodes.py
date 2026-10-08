"""Nodes for the Cross-Signal LangGraph agent."""

from __future__ import annotations

import json
import logging
from collections import Counter
from enum import Enum
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Send
from pydantic import ValidationError

from ..contracts.base import new_id
from ..contracts.CrossSignal import (
    CrossSignal,
    CrossSignalOutput,
    CrossSignalSummary,
)
from ..contracts.task import WorkerType

from .prompts import (
    LENSES,
    SUMMARY_SYSTEM_PROMPT,
    build_detection_prompt,
    build_summary_prompt,
    build_system_prompt,
)
from .state import (
    CrossSignalSettings,
    CrossSignalState,
    LensState,
    SignalDetectionResult,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Defensive accessors: FindingVerdict's exact field names live in the
# verification contract. Adjust the candidate attribute names here if needed.
# --------------------------------------------------------------------------
_ID_ATTRS = ("finding_id", "id")
_DOMAIN_ATTRS = ("worker_type", "domain", "source_worker", "worker")


def _finding_id(finding: Any) -> str | None:
    for attr in _ID_ATTRS:
        value = getattr(finding, attr, None)
        if value:
            return str(value)
    return None


def _finding_domain(finding: Any) -> str | None:
    for attr in _DOMAIN_ATTRS:
        value = getattr(finding, attr, None)
        if value is None:
            continue
        return value.value if isinstance(value, Enum) else str(value)
    return None


def _to_worker_type(value: str) -> WorkerType | None:
    try:
        return WorkerType(value)
    except ValueError:
        return None


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop empty lists/strings and fields the LLM does not need."""
    drop = {"verification_status"}
    return {
        k: v
        for k, v in payload.items()
        if k not in drop and v not in ([], "", {}, None)
    }


class CrossSignalNodes:
    """Holds the LLM + settings; each public method is a graph node or router."""

    def __init__(self, llm: BaseChatModel, settings: CrossSignalSettings):
        self.settings = settings
        self._system_prompt = build_system_prompt()
        self._detector = llm.with_structured_output(
            SignalDetectionResult
        ).with_retry(stop_after_attempt=settings.max_retries)
        self._summarizer = llm.with_structured_output(
            CrossSignalSummary
        ).with_retry(stop_after_attempt=settings.max_retries)

    # ------------------------------------------------------------------
    # 1. prepare: index findings, build compact digests
    # ------------------------------------------------------------------
    async def prepare(self, state: CrossSignalState) -> dict[str, Any]:
        inp = state["input"]
        warnings: list[str] = []
        findings_by_id: dict[str, dict[str, Any]] = {}
        domains: dict[str, str] = {}

        # Evidence records ride along in metadata["evidence"] (see loader.py);
        # they let the model see source type/provenance for each finding.
        evidence_map = {
            e.get("evidence_id"): e
            for e in inp.metadata.get("evidence", [])
            if isinstance(e, dict)
        }

        for idx, finding in enumerate(inp.verified_findings, start=1):
            fid = _finding_id(finding)
            if fid is None:
                fid = f"finding_{idx}"
                warnings.append(f"Finding #{idx} has no id; assigned '{fid}'.")
            if fid in findings_by_id:
                warnings.append(f"Duplicate finding id '{fid}' skipped.")
                continue

            payload = _compact(finding.model_dump(mode="json", exclude_none=True))
            payload["finding_id"] = fid
            sources = []
            for eid in payload.pop("evidence_ids", []):
                ev = evidence_map.get(eid)
                sources.append(
                    {k: ev.get(k) for k in ("source_type", "source", "title")}
                    if ev
                    else {"evidence_id": eid}
                )
            if sources:
                payload["sources"] = sources
            domain = _finding_domain(finding)
            if domain:
                payload.pop("worker", None)
                payload.pop("worker_type", None)
                payload["domain"] = domain
                domains[fid] = domain
            findings_by_id[fid] = payload

        business_digest = json.dumps(
            inp.business_request.model_dump(mode="json", exclude_none=True),
            indent=2,
            ensure_ascii=False,
        )
        findings_digest = json.dumps(
            list(findings_by_id.values()), indent=2, ensure_ascii=False
        )

        return {
            "findings_by_id": findings_by_id,
            "finding_domains": domains,
            "business_digest": business_digest,
            "findings_digest": findings_digest,
            "warnings": warnings,
            "candidate_signals": [],
        }

    # ------------------------------------------------------------------
    # router: enough findings? -> fan out to lenses in parallel
    # ------------------------------------------------------------------
    def route_after_prepare(self, state: CrossSignalState):
        if len(state["findings_by_id"]) < self.settings.min_findings:
            return "finalize_empty"
        return [
            Send(
                "detect_signals",
                LensState(
                    lens=name,
                    business_digest=state["business_digest"],
                    findings_digest=state["findings_digest"],
                ),
            )
            for name in LENSES
        ]

    # ------------------------------------------------------------------
    # 2. detect_signals (runs once per lens, in parallel)
    # ------------------------------------------------------------------
    async def detect_signals(self, state: LensState) -> dict[str, Any]:
        lens = LENSES[state["lens"]]
        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(
                content=build_detection_prompt(
                    lens, state["business_digest"], state["findings_digest"]
                )
            ),
        ]
        try:
            result: SignalDetectionResult = await self._detector.ainvoke(messages)
        except Exception as exc:  # one failed lens must not kill the run
            logger.exception("Lens '%s' failed", lens.name)
            return {"warnings": [f"Lens '{lens.name}' failed: {exc}"]}

        allowed = set(lens.signal_types)
        signals = [s for s in result.signals if s.signal_type in allowed]
        stray = len(result.signals) - len(signals)
        out: dict[str, Any] = {"candidate_signals": signals}
        if stray:
            out["warnings"] = [
                f"Lens '{lens.name}' returned {stray} signal(s) of a disallowed type; dropped."
            ]
        return out

    # ------------------------------------------------------------------
    # 3. merge_validate: deterministic checks, dedup, rank
    # ------------------------------------------------------------------
    async def merge_validate(self, state: CrossSignalState) -> dict[str, Any]:
        valid_ids = set(state["findings_by_id"])
        domains = state["finding_domains"]
        drops: Counter[str] = Counter()
        best: dict[tuple, CrossSignal] = {}

        candidates = state.get("candidate_signals", [])
        for cand in candidates:
            # 1) keep only evidence that points at real, distinct findings
            seen: set[str] = set()
            evidence = []
            for ev in cand.supporting_findings:
                if ev.finding_id in valid_ids and ev.finding_id not in seen:
                    seen.add(ev.finding_id)
                    evidence.append(ev)
            if len(evidence) < 2:
                drops["fewer_than_two_valid_findings"] += 1
                continue

            # 2) cross-domain requirement (only when domains are known)
            support_domains = {
                domains[e.finding_id] for e in evidence if e.finding_id in domains
            }
            all_known = all(e.finding_id in domains for e in evidence)
            if (
                self.settings.require_cross_domain
                and all_known
                and len(support_domains) < 2
                and cand.signal_type not in self.settings.single_domain_ok
            ):
                drops["single_domain"] += 1
                continue

            # 3) rebuild: fresh id, filtered evidence, domains from the evidence
            data = cand.model_dump()
            data["signal_id"] = new_id()
            data["supporting_findings"] = [e.model_dump() for e in evidence]
            if support_domains:
                wt = [_to_worker_type(d) for d in sorted(support_domains)]
                wt = [w for w in wt if w is not None]
                if wt:
                    data["domains_involved"] = wt
            try:
                signal = CrossSignal.model_validate(data)
            except ValidationError:
                drops["schema_invalid"] += 1
                continue

            # 4) dedup: same type + same evidence set -> keep higher confidence
            key = (signal.signal_type, frozenset(e.finding_id for e in evidence))
            current = best.get(key)
            if current is None or signal.confidence > current.confidence:
                best[key] = signal
            if current is not None:
                drops["duplicate"] += 1

        ranked = sorted(best.values(), key=lambda s: s.confidence, reverse=True)
        kept = ranked[: self.settings.max_signals]
        if len(ranked) > len(kept):
            drops["over_max_signals"] += len(ranked) - len(kept)

        stats = {
            "findings_in": len(valid_ids),
            "candidate_signals": len(candidates),
            "validated_signals": len(kept),
            "dropped": dict(drops),
            "lenses_run": list(LENSES),
            "model": self.settings.model,
        }
        return {"signals": kept, "stats": stats}

    # ------------------------------------------------------------------
    # 4. summarize
    # ------------------------------------------------------------------
    async def summarize(self, state: CrossSignalState) -> dict[str, Any]:
        signals = state.get("signals", [])
        if not signals:
            return {"summary": CrossSignalSummary(), "status": "no_signals"}

        signals_json = json.dumps(
            [s.model_dump(mode="json") for s in signals],
            indent=2,
            ensure_ascii=False,
        )
        messages = [
            SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
            HumanMessage(
                content=build_summary_prompt(state["business_digest"], signals_json)
            ),
        ]
        try:
            summary: CrossSignalSummary = await self._summarizer.ainvoke(messages)
        except Exception as exc:
            logger.exception("Summary failed")
            return {
                "summary": CrossSignalSummary(),
                "status": "completed_without_summary",
                "warnings": [f"Summary generation failed: {exc}"],
            }
        return {"summary": summary, "status": "completed"}

    # ------------------------------------------------------------------
    # 4b. finalize_empty: too few findings to relate
    # ------------------------------------------------------------------
    async def finalize_empty(self, state: CrossSignalState) -> dict[str, Any]:
        n = len(state.get("findings_by_id", {}))
        return {
            "signals": [],
            "summary": CrossSignalSummary(),
            "status": "insufficient_findings",
            "stats": {
                "findings_in": n,
                "candidate_signals": 0,
                "validated_signals": 0,
                "min_findings_required": self.settings.min_findings,
            },
        }

    # ------------------------------------------------------------------
    # 5. assemble the contract output
    # ------------------------------------------------------------------
    async def assemble(self, state: CrossSignalState) -> dict[str, Any]:
        stats = dict(state.get("stats", {}))
        if state.get("warnings"):
            stats["warnings"] = state["warnings"]

        output = CrossSignalOutput(
            research_run_id=state["input"].research_run_id,
            status=state.get("status", "completed"),
            signals=state.get("signals", []),
            summary=state.get("summary", CrossSignalSummary()),
            input_statistics=stats,
        )
        return {"output": output}
