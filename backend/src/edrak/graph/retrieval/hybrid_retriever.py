"""Hybrid retrieval: ChromaDB passages + knowledge-graph evidence, fused by rank.

``HybridRetriever.retrieve`` has the same signature and return type as
``InternalRetriever.retrieve`` (a list of ``Evidence``), so it is a drop-in
replacement inside a worker. Two things are added on top of the vector search:

1. **Graph evidence.** Evidence behind verified findings from earlier runs that
   match the query (full-text over findings and evidence), restricted to the
   same workspace. Fused with the vector hits by reciprocal rank fusion.
2. **Annotations.** Each vector passage whose document earlier runs already
   cited gets ``metadata["prior_findings"]``: what was concluded from it and
   whether verification accepted it.

Every returned item says where it came from in ``metadata["retrieval_sources"]``
(``["vector"]``, ``["graph"]`` or both). With the graph disabled or down, the
result is exactly the vector result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .graph_retriever import GraphFinding, GraphGap, GraphRetriever, GraphSignal
from .ranking import evidence_key, rrf

logger = logging.getLogger(__name__)


@dataclass
class HybridResult:
    evidence: list[Any]  # list[edrak.contracts.evidence.Evidence]
    findings: list[GraphFinding] = field(default_factory=list)
    signals: list[GraphSignal] = field(default_factory=list)
    gaps: list[GraphGap] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


class HybridRetriever:
    def __init__(
        self,
        vector: Any = None,
        graph: GraphRetriever | None = None,
        *,
        vector_weight: float = 1.0,
        graph_weight: float = 0.8,
        rrf_k: int = 60,
        annotate: bool = True,
    ) -> None:
        self._vector = vector
        self.graph = graph or GraphRetriever()
        self.weights = {"vector": vector_weight, "graph": graph_weight}
        self.rrf_k = rrf_k
        self.annotate = annotate

    @property
    def vector(self) -> Any:
        if self._vector is None:  # lazy: building it loads the embedding model
            from edrak.rag.retriever import InternalRetriever

            self._vector = InternalRetriever()
        return self._vector

    # ------------------------------------------------------------------ public API

    def retrieve(
        self,
        query: str,
        top_k: int = 4,
        where_filter: dict[str, Any] | None = None,
        min_score: float | None = None,
        workspace_id: str | None = None,
        allow_all_workspaces: bool = False,
        *,
        entity_ids: Sequence[str] | None = None,
        source_types: Iterable[str] | None = None,
        exclude_request_id: str | None = None,
    ) -> list[Any]:
        """Same contract as InternalRetriever.retrieve, plus optional graph filters."""
        return self.retrieve_with_context(
            query, top_k, where_filter, min_score, workspace_id, allow_all_workspaces,
            entity_ids=entity_ids, source_types=source_types, exclude_request_id=exclude_request_id,
            with_context=False,
        ).evidence

    def retrieve_with_context(
        self,
        query: str,
        top_k: int = 4,
        where_filter: dict[str, Any] | None = None,
        min_score: float | None = None,
        workspace_id: str | None = None,
        allow_all_workspaces: bool = False,
        *,
        entity_ids: Sequence[str] | None = None,
        source_types: Iterable[str] | None = None,
        exclude_request_id: str | None = None,
        with_context: bool = True,
    ) -> HybridResult:
        """Evidence plus the graph findings, signals and open gaps that matched."""
        if not (query or "").strip():
            return HybridResult(evidence=[])

        graph_on = bool(workspace_id) and self.graph.available()
        if not graph_on:
            vector_hits = self.vector.retrieve(
                query=query, top_k=top_k, where_filter=where_filter, workspace_id=workspace_id,
                allow_all_workspaces=allow_all_workspaces, min_score=min_score,
            )
            return HybridResult(evidence=vector_hits, counts={"vector": len(vector_hits), "graph": 0, "returned": len(vector_hits)})

        vector_hits = self._vector_search(query, top_k * 2, where_filter, workspace_id, allow_all_workspaces, min_score)

        findings: list[GraphFinding] = []
        graph_hits: list[Any] = []
        if graph_on:
            findings = self.graph.find_findings(
                workspace_id, query=query, entity_ids=entity_ids,
                exclude_request_id=exclude_request_id, limit=top_k * 3,
            )
            graph_hits = self.graph.evidence_from_findings(findings, source_types=source_types)[: top_k * 2]

        evidence = self._fuse(vector_hits, graph_hits, top_k)
        if graph_on and self.annotate:
            self._annotate(evidence, workspace_id)

        result = HybridResult(
            evidence=evidence,
            findings=findings,
            counts={
                "vector": len(vector_hits),
                "graph": len(graph_hits),
                "returned": len(evidence),
                "from_graph_only": sum(1 for e in evidence if e.metadata.get("retrieval_sources") == ["graph"]),
            },
        )
        if with_context and graph_on:
            result.signals = self.graph.signals(workspace_id, entity_ids=entity_ids, exclude_request_id=exclude_request_id, limit=5)
            result.gaps = self.graph.open_gaps(workspace_id, exclude_request_id=exclude_request_id, limit=5)
        logger.info("hybrid retrieve(%r): %s", query[:80], result.counts)
        return result

    # ------------------------------------------------------------------ internals

    def _vector_search(
        self, query: str, k: int, where_filter: Any, workspace_id: str | None, allow_all: bool, min_score: float | None,
    ) -> list[Any]:
        try:
            return list(
                self.vector.retrieve(
                    query=query, top_k=k, where_filter=where_filter, workspace_id=workspace_id,
                    allow_all_workspaces=allow_all, min_score=min_score,
                )
            )
        except Exception:  # noqa: BLE001 - the graph half can still answer
            logger.exception("Vector retrieval failed inside hybrid retrieval")
            return []

    def _fuse(self, vector_hits: list[Any], graph_hits: list[Any], top_k: int) -> list[Any]:
        items: dict[str, Any] = {}
        sources: dict[str, list[str]] = {}
        rankings: dict[str, list[str]] = {"vector": [], "graph": []}
        for name, hits in (("vector", vector_hits), ("graph", graph_hits)):
            for ev in hits:
                key = evidence_key(ev.source_url, ev.extracted_fact, ev.evidence_id)
                if key in rankings[name]:
                    continue
                rankings[name].append(key)
                items.setdefault(key, ev)  # vector first: its object carries chunk_id for the file viewer
                sources.setdefault(key, []).append(name)

        scores = rrf(rankings, self.weights, self.rrf_k)
        ordered = sorted(scores, key=scores.get, reverse=True)

        out, ids = [], set()
        for key in ordered:
            ev = items[key]
            if ev.evidence_id in ids:  # WorkerResult rejects duplicate evidence ids
                continue
            ids.add(ev.evidence_id)
            ev.metadata["retrieval_sources"] = sources[key]
            ev.metadata["hybrid_score"] = round(scores[key], 6)
            out.append(ev)
            if len(out) >= top_k:
                break
        return out

    def _annotate(self, evidence: list[Any], workspace_id: str) -> None:
        cited = self.graph.findings_citing_sources(workspace_id, [e.source_url for e in evidence if e.source_url])
        for ev in evidence:
            prior = cited.get(ev.source_url or "")
            if prior:
                ev.metadata["prior_findings"] = [
                    f"{p.get('statement')} [{p.get('verification_status') or 'unverified'}]" for p in prior
                ]
