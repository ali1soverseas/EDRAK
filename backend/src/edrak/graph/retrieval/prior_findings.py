"""Earlier findings about one company, with the evidence that supports them.

The competitor worker reads this before it searches the web (agents/competitor_intelligence/prior.py):
a finding verified recently enough, with its stored evidence still supporting it, is reused instead
of searched again. Everything else only steers the new searches.

Same rules as GraphRetriever: filtered on ``workspace_id`` (nothing without one), and
``exclude_request_id`` hides the run that is asking.

    from edrak.graph.retrieval.prior_findings import findings_about

    findings_about(conn, "sandbox", "amd", exclude_request_id="req-nvidia-003")
"""

from __future__ import annotations

import re
from typing import Any

from ..connection import GraphConnection

Q_FINDINGS_ABOUT = """
MATCH (f:Finding {workspace_id: $ws})-[:ABOUT]->(:Company {id: $company_id})
WHERE ($exclude IS NULL OR f.request_id <> $exclude)
WITH DISTINCT f ORDER BY f.observed_at DESC LIMIT $limit
OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)
OPTIONAL MATCH (o:MetricObservation {workspace_id: $ws})-[:EXTRACTED_FROM]->(e)
WHERE o.request_id = f.request_id AND o.entity_id = $company_id
WITH f, e, collect(DISTINCT o {.metric_id, .entity_id, .value_text, .label, .status}) AS obs
WITH f, collect(CASE WHEN e IS NULL THEN NULL ELSE e {
       .evidence_id, .source_url, .source_title, .publisher, .source_type, .extracted_fact,
       .excerpt, .retrieved_at, .is_synthetic, observations: obs} END) AS evidence
RETURN f.finding_id AS finding_id, f.statement AS statement, f.category AS category, f.worker AS worker,
       f.request_id AS request_id, f.verification_status AS verification_status,
       coalesce(f.verified_confidence, f.confidence) AS confidence, f.evidence_quality AS evidence_quality,
       f.observed_at AS observed_at, coalesce(f.synthetic_only, false) AS synthetic_only,
       coalesce(f.limitations, []) AS limitations, evidence
ORDER BY observed_at DESC
"""


def _norm(statement: str | None) -> str:
    return re.sub(r"\W+", " ", (statement or "").lower()).strip()


def findings_about(
    conn: GraphConnection,
    workspace_id: str | None,
    company_id: str,
    *,
    exclude_request_id: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Findings ABOUT a company, newest first, one per distinct statement.

    The same statement verified in several runs comes back once, from the newest run, so its
    ``observed_at`` is the latest time a run saw it.
    """
    if not workspace_id or not company_id:
        return []
    rows = conn.read(Q_FINDINGS_ABOUT, ws=workspace_id, company_id=company_id, exclude=exclude_request_id, limit=limit)
    seen: set[str] = set()
    out = []
    for row in rows:
        key = _norm(row.get("statement"))
        if not key or key in seen:
            continue
        seen.add(key)
        row = dict(row)
        row["evidence"] = [dict(e, observations=[o for o in e.get("observations") or [] if o]) for e in row.get("evidence") or [] if e]
        out.append(row)
    return out
