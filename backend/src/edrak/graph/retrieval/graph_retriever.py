"""Read the knowledge graph for the workers.

Every query is scoped to one workspace through ``Finding.workspace_id`` (or the same
property on signals, gaps and observations). Like ``InternalRetriever``, a call
without a workspace returns nothing: a lost id must never widen a search to every
company's research. Evidence is only reached through a finding of that workspace.

All methods return empty results when the graph is disabled or unreachable, so a
worker that asks for graph context never fails because of Neo4j.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from ..connection import GraphConnection, GraphUnavailable, get_connection
from ..entities import slugify
from .ranking import lucene_query

logger = logging.getLogger(__name__)

_EVIDENCE_FIELDS = (
    "evidence_id", "source_type", "source_title", "source_url", "publisher",
    "extracted_fact", "excerpt", "is_synthetic", "quality", "retrieved_at",
)


# ============================================================================= result types


@dataclass
class GraphEvidence:
    evidence_id: str
    source_type: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    extracted_fact: str | None = None
    excerpt: str | None = None
    is_synthetic: bool | None = None
    quality: str | None = None
    retrieved_at: str | None = None

    @classmethod
    def from_map(cls, data: dict[str, Any]) -> GraphEvidence:
        return cls(**{k: data.get(k) for k in _EVIDENCE_FIELDS})

    def to_contract(self, **metadata: Any) -> Any:
        """The shared Evidence contract, so graph hits can flow into a WorkerResult. None if unusable."""
        from edrak.contracts.evidence import Evidence, SourceType

        if not (self.extracted_fact or "").strip():
            return None
        try:
            source_type = SourceType(self.source_type)
        except ValueError:
            source_type = SourceType.OTHER
        # Format title to explicitly indicate Graph RAG provenance in UI while preserving channel
        base_title = (self.source_title or "").strip()
        if base_title.startswith("Graph RAG"):
            title = base_title
        elif base_title:
            title = f"Graph RAG · {base_title}"
        else:
            title = "Graph RAG · Knowledge Graph"

        kwargs: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "source_type": source_type,
            "source_title": title,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "extracted_fact": self.extracted_fact,
            "excerpt": self.excerpt,
            # the contract rejects synthetic_internal unless flagged; never present it as authentic
            "is_synthetic": bool(self.is_synthetic) or source_type is SourceType.SYNTHETIC_INTERNAL,
            "metadata": {k: v for k, v in {"retrieval_source": "graph", "quality": self.quality, **metadata}.items() if v is not None},
        }
        if self.retrieved_at:
            try:
                kwargs["retrieved_at"] = datetime.fromisoformat(self.retrieved_at)
            except ValueError:
                pass
        return Evidence(**kwargs)


@dataclass
class GraphFinding:
    finding_id: str
    statement: str
    category: str | None
    worker: str | None
    request_id: str | None
    verification_status: str | None
    evidence_quality: str | None
    confidence: float | None
    synthetic_only: bool
    observed_at: str | None
    about: list[str] = field(default_factory=list)
    evidence: list[GraphEvidence] = field(default_factory=list)
    score: float = 0.0

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> GraphFinding:
        f = rec["f"]
        return cls(
            finding_id=f["finding_id"],
            statement=f.get("statement") or "",
            category=f.get("category"),
            worker=f.get("worker"),
            request_id=f.get("request_id"),
            verification_status=f.get("verification_status"),
            evidence_quality=f.get("evidence_quality"),
            confidence=f.get("verified_confidence") if f.get("verified_confidence") is not None else f.get("confidence"),
            synthetic_only=bool(f.get("synthetic_only")),
            observed_at=f.get("observed_at"),
            about=[a for a in rec.get("about") or [] if a],
            evidence=[GraphEvidence.from_map(e) for e in rec.get("evidence") or [] if e and e.get("evidence_id")],
            score=float(rec.get("score") or 0.0),
        )

    def label(self) -> str:
        parts = [self.category or "other", self.worker or "?", f"quality {self.evidence_quality or '?'}"]
        if self.synthetic_only:
            parts.append("synthetic source")
        if self.observed_at:
            parts.append(f"seen {self.observed_at[:10]}")
        return " · ".join(parts)


@dataclass
class GraphSignal:
    signal_id: str
    signal_type: str | None
    title: str | None
    signal: str | None
    urgency: str | None
    confidence: float | None
    decision_question: str | None
    request_id: str | None
    finding_ids: list[str] = field(default_factory=list)


@dataclass
class GraphGap:
    id: str
    subject: str
    worker: str | None
    request_id: str | None
    status: str | None
    created_at: str | None


@dataclass
class MetricPoint:
    metric_id: str | None
    entity: str | None
    value: float | None
    value_text: str | None
    unit: str | None
    period: str | None
    label: str | None
    scope: str | None
    observed_at: str | None
    request_id: str | None
    source_url: str | None


# ============================================================================= queries

_FINDING_TAIL = """
WHERE ($verified_only = false OR f.verification_status = 'verified')
  AND ($include_synthetic OR coalesce(f.synthetic_only, false) = false)
  AND ($exclude_rid IS NULL OR f.request_id <> $exclude_rid)
  AND ($categories IS NULL OR f.category IN $categories)
  AND ($workers IS NULL OR f.worker IN $workers)
  AND ($entity_ids IS NULL OR EXISTS { MATCH (f)-[:ABOUT]->(x) WHERE x.id IN $entity_ids })
OPTIONAL MATCH (f)-[:ABOUT]->(x)
WITH f, score, collect(DISTINCT x.name) AS about
OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)
WITH f, score, about,
     collect(e {.evidence_id, .source_type, .source_title, .source_url, .publisher,
                .extracted_fact, .excerpt, .is_synthetic, .quality, .retrieved_at})[..$ev_per_finding] AS evidence
RETURN f {.*} AS f, score, about, evidence
ORDER BY score DESC, coalesce(f.verified_confidence, f.confidence, 0) DESC, f.observed_at DESC
LIMIT $limit
"""

Q_FINDINGS_TEXT = """
CALL {
  CALL db.index.fulltext.queryNodes('claim_text', $q) YIELD node, score
  WITH node, score WHERE node:Finding AND node.workspace_id = $ws
  RETURN node AS f, score
  UNION
  CALL db.index.fulltext.queryNodes('claim_text', $q) YIELD node, score
  WITH node, score WHERE node:Evidence
  MATCH (f:Finding {workspace_id: $ws})-[:SUPPORTED_BY]->(node)
  RETURN f, score * 0.8 AS score
}
WITH f, max(score) AS score
""" + _FINDING_TAIL

Q_FINDINGS_ALL = """
MATCH (f:Finding {workspace_id: $ws})
WITH f, 1.0 AS score
""" + _FINDING_TAIL

Q_CITING_SOURCES = """
UNWIND $urls AS url
MATCH (e:Evidence {source_url: url})<-[:SUPPORTED_BY]-(f:Finding {workspace_id: $ws})
WHERE ($verified_only = false OR f.verification_status = 'verified')
WITH url, f ORDER BY f.observed_at DESC
RETURN url, collect(f {.finding_id, .statement, .category, .verification_status, .evidence_quality, .request_id})[..$per_url] AS findings
"""

Q_SIGNALS = """
MATCH (s:CrossSignal {workspace_id: $ws})
WHERE ($exclude_rid IS NULL OR s.request_id <> $exclude_rid)
  AND ($entity_ids IS NULL OR EXISTS { MATCH (s)-[:BASED_ON]->(:Finding)-[:ABOUT]->(x) WHERE x.id IN $entity_ids })
OPTIONAL MATCH (s)-[:BASED_ON]->(f:Finding)
WITH s, collect(f.finding_id) AS finding_ids
RETURN s {.signal_id, .signal_type, .title, .signal, .urgency, .confidence, .decision_question, .request_id} AS s, finding_ids
ORDER BY coalesce(s.confidence, 0) DESC
LIMIT $limit
"""

Q_GAPS = """
MATCH (g:InformationGap {workspace_id: $ws})
WHERE g.status IN ['open', 'investigating']
  AND ($workers IS NULL OR g.worker IN $workers)
  AND ($exclude_rid IS NULL OR g.request_id <> $exclude_rid)
RETURN g {.id, .subject, .worker, .request_id, .status, .created_at} AS g
ORDER BY g.created_at DESC
LIMIT $limit
"""

Q_METRICS = """
MATCH (o:MetricObservation {workspace_id: $ws})
WHERE ($metric_id IS NULL OR o.metric_id = $metric_id)
  AND ($entity_ids IS NULL OR o.entity_id IN $entity_ids)
  AND ($exclude_rid IS NULL OR o.request_id <> $exclude_rid)
OPTIONAL MATCH (o)-[:EXTRACTED_FROM]->(e:Evidence)
RETURN o {.metric_id, .entity, .value, .value_text, .unit, .period, .label, .scope, .observed_at, .request_id} AS o,
       e.source_url AS source_url
ORDER BY o.observed_at DESC
LIMIT $limit
"""

Q_RESOLVE = """
UNWIND $terms AS t
MATCH (x)
WHERE (x:Company OR x:Product OR x:Market OR x:Topic OR x:CustomerSegment
       OR x:Feature OR x:PricingPlan OR x:Regulation OR x:Theme OR x:Platform)
  AND (x.id = t.slug OR toLower(x.name) = t.lower OR t.lower IN [a IN coalesce(x.aliases, []) | toLower(a)])
  AND (EXISTS { MATCH (:Finding {workspace_id: $ws})-[:ABOUT]->(x) }
       OR EXISTS { MATCH (:Workspace {id: $ws})-[:TRACKS]->(x) }
       OR EXISTS { MATCH (:BusinessRequest {workspace_id: $ws})-->(x) })
RETURN DISTINCT labels(x)[0] AS label, x.id AS id, x.name AS name
"""

Q_NEIGHBOURHOOD = """
MATCH (c {id: $id})
WHERE (c:Company OR c:Product OR c:Market OR c:Topic OR c:Feature OR c:PricingPlan OR c:Regulation OR c:Theme OR c:Platform)
  AND (EXISTS { MATCH (:Finding {workspace_id: $ws})-[:ABOUT]->(c) }
       OR EXISTS { MATCH (:Workspace {id: $ws})-[:TRACKS]->(c) }
       OR EXISTS { MATCH (:BusinessRequest {workspace_id: $ws})-->(c) })
OPTIONAL MATCH (c)-[r:COMPETES_WITH|OPERATES_IN]-(n)
WHERE EXISTS {
  MATCH (f:Finding {workspace_id: $ws})
  WHERE f.finding_id IN coalesce(r.finding_ids, [])
}
WITH c, n, r, [fid IN coalesce(r.finding_ids, []) WHERE EXISTS {
  MATCH (f:Finding {workspace_id: $ws, finding_id: fid})
}] AS finding_ids
RETURN c {.id, .name} AS entity, labels(c)[0] AS label,
       collect({rel: type(r), outgoing: startNode(r) = c, name: n.name, id: n.id,
                finding_ids: finding_ids}) AS links
"""


Q_ENTITIES = """
MATCH (x)
WHERE ($label IS NULL AND (x:Company OR x:Product OR x:Market OR x:Topic OR x:CustomerSegment
                           OR x:Feature OR x:PricingPlan OR x:Regulation OR x:Theme OR x:Platform))
   OR ($label IS NOT NULL AND $label IN labels(x))
OPTIONAL MATCH (f:Finding {workspace_id: $ws})-[:ABOUT]->(x)
WITH x, count(DISTINCT f) AS findings
WHERE findings > 0 OR EXISTS { MATCH (:Workspace {id: $ws})-[:TRACKS]->(x) }
   OR EXISTS { MATCH (:BusinessRequest {workspace_id: $ws})-->(x) }
RETURN labels(x)[0] AS label, x.id AS id, x.name AS name, findings
ORDER BY label, findings DESC, name
"""

Q_THEMES = """
MATCH (f:Finding {workspace_id: $ws})-[:SUPPORTED_BY]->(e:Evidence)-[x:EXPRESSES]->(t:Theme)
OPTIONAL MATCH (e)-[:POSTED_ON]->(p:Platform)
RETURN t.name AS theme, count(DISTINCT e) AS evidence, collect(DISTINCT p.name) AS platforms,
       collect(DISTINCT x.stance) AS stances
ORDER BY evidence DESC
LIMIT $limit
"""


# ============================================================================= retriever


class GraphRetriever:
    def __init__(self, conn: GraphConnection | None = None, *, strict: bool = False) -> None:
        self.conn = conn or get_connection()
        self.strict = strict  # raise query errors instead of returning [] (tests, debugging)

    def available(self) -> bool:
        return self.conn.enabled and self.conn.available()

    def _read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        if not self.conn.enabled:
            return []
        try:
            return self.conn.read(query, **params)
        except GraphUnavailable as exc:
            logger.debug("Graph read skipped: %s", exc)
            return []
        except Exception:  # noqa: BLE001 - a broken query must not break a worker
            if self.strict:
                raise
            logger.exception("Graph read failed")
            return []

    # ------------------------------------------------------------------ entities

    def resolve_entities(self, names: Iterable[str], workspace_id: str | None = None) -> list[dict[str, str]]:
        """Names or aliases -> [{label, id, name}] for entities already in the graph."""
        terms = [{"slug": slugify(n), "lower": n.strip().lower()} for n in names if n and n.strip()]
        return self._read(Q_RESOLVE, terms=terms, ws=workspace_id) if terms and workspace_id else []

    def entities(self, workspace_id: str | None, label: str | None = None) -> list[dict[str, Any]]:
        """Entities a workspace knows (from its requests, its catalog or its findings), with finding counts."""
        if not workspace_id:
            return []
        return self._read(Q_ENTITIES, ws=workspace_id, label=label)

    def themes(self, workspace_id: str | None, limit: int = 10) -> list[dict[str, Any]]:
        """Customer themes behind a workspace's findings, with the platforms they were seen on."""
        if not workspace_id:
            return []
        return self._read(Q_THEMES, ws=workspace_id, limit=limit)

    def neighbourhood(self, entity_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Return verified relationships supported by findings in the workspace."""
        if not workspace_id:
            return {}
        rows = self._read(Q_NEIGHBOURHOOD, id=entity_id, ws=workspace_id)
        if not rows:
            return {}
        row = rows[0]
        return {
            "entity": row["entity"],
            "label": row["label"],
            "links": [link for link in row["links"] if link.get("rel")],
        }

    # ------------------------------------------------------------------ findings

    def find_findings(
        self,
        workspace_id: str | None,
        *,
        query: str | None = None,
        entity_ids: Sequence[str] | None = None,
        categories: Sequence[str] | None = None,
        workers: Sequence[str] | None = None,
        verified_only: bool = True,
        include_synthetic: bool = True,
        exclude_request_id: str | None = None,
        evidence_per_finding: int = 3,
        limit: int = 20,
    ) -> list[GraphFinding]:
        """Findings of one workspace, ranked by full-text relevance when a query is given."""
        if not workspace_id:
            logger.warning("Graph retrieval refused: no workspace_id given.")
            return []
        q = lucene_query(query)
        params = {
            "ws": workspace_id,
            "verified_only": verified_only,
            "include_synthetic": include_synthetic,
            "exclude_rid": exclude_request_id,
            "categories": list(categories) if categories else None,
            "workers": list(workers) if workers else None,
            "entity_ids": list(entity_ids) if entity_ids else None,
            "ev_per_finding": evidence_per_finding,
            "limit": limit,
        }
        rows = self._read(Q_FINDINGS_TEXT, q=q, **params) if q else self._read(Q_FINDINGS_ALL, **params)
        return [GraphFinding.from_record(r) for r in rows]

    def findings_citing_sources(
        self, workspace_id: str | None, source_urls: Iterable[str], *, verified_only: bool = True, per_url: int = 3
    ) -> dict[str, list[dict[str, Any]]]:
        """For documents a vector search just returned: what earlier runs concluded from them."""
        urls = sorted({u for u in source_urls if u})
        if not workspace_id or not urls:
            return {}
        rows = self._read(Q_CITING_SOURCES, ws=workspace_id, urls=urls, verified_only=verified_only, per_url=per_url)
        return {row["url"]: row["findings"] for row in rows if row["findings"]}

    def evidence_from_findings(
        self, findings: Sequence[GraphFinding], *, source_types: Iterable[str] | None = None
    ) -> list[Any]:
        """Flatten ranked findings into ranked, de-duplicated Evidence contracts."""
        allowed = set(source_types) if source_types else None
        seen: set[str] = set()
        out = []
        for finding in findings:
            for ev in finding.evidence:
                if ev.evidence_id in seen or (allowed and ev.source_type not in allowed):
                    continue
                contract = ev.to_contract(
                    prior_finding_id=finding.finding_id,
                    prior_finding=finding.statement,
                    prior_request_id=finding.request_id,
                    prior_verification=finding.verification_status,
                    graph_score=round(finding.score, 4),
                )
                if contract is not None:
                    seen.add(ev.evidence_id)
                    out.append(contract)
        return out

    def evidence_for_query(
        self,
        query: str,
        workspace_id: str | None,
        *,
        entity_ids: Sequence[str] | None = None,
        source_types: Iterable[str] | None = None,
        exclude_request_id: str | None = None,
        verified_only: bool = True,
        limit: int = 8,
    ) -> list[Any]:
        findings = self.find_findings(
            workspace_id, query=query, entity_ids=entity_ids, verified_only=verified_only,
            exclude_request_id=exclude_request_id, limit=limit,
        )
        return self.evidence_from_findings(findings, source_types=source_types)[:limit]

    # ------------------------------------------------------------------ signals, gaps, metrics

    def signals(
        self, workspace_id: str | None, *, entity_ids: Sequence[str] | None = None,
        exclude_request_id: str | None = None, limit: int = 10,
    ) -> list[GraphSignal]:
        if not workspace_id:
            return []
        rows = self._read(
            Q_SIGNALS, ws=workspace_id, entity_ids=list(entity_ids) if entity_ids else None,
            exclude_rid=exclude_request_id, limit=limit,
        )
        return [GraphSignal(**row["s"], finding_ids=row["finding_ids"]) for row in rows]

    def open_gaps(
        self, workspace_id: str | None, *, workers: Sequence[str] | None = None,
        exclude_request_id: str | None = None, limit: int = 10,
    ) -> list[GraphGap]:
        if not workspace_id:
            return []
        rows = self._read(
            Q_GAPS, ws=workspace_id, workers=list(workers) if workers else None,
            exclude_rid=exclude_request_id, limit=limit,
        )
        return [GraphGap(**row["g"]) for row in rows]

    def metric_history(
        self, workspace_id: str | None, *, metric_id: str | None = None, entity_ids: Sequence[str] | None = None,
        exclude_request_id: str | None = None, limit: int = 50,
    ) -> list[MetricPoint]:
        if not workspace_id:
            return []
        rows = self._read(
            Q_METRICS, ws=workspace_id, metric_id=metric_id, entity_ids=list(entity_ids) if entity_ids else None,
            exclude_rid=exclude_request_id, limit=limit,
        )
        return [MetricPoint(**row["o"], source_url=row["source_url"]) for row in rows]
