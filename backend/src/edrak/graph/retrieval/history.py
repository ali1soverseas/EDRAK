"""Read back the run-scoped observations: competitor assessments, customer themes, search interest.

These nodes are written from worker metadata (ingestion/writers.py, "worker metadata"). Each one
belongs to one run, so the history of a comparison, a theme or a keyword is a list of points
ordered by ``observed_at``.

Same rules as GraphRetriever: every query filters on ``workspace_id`` and returns nothing
without one, and ``exclude_request_id`` hides the run that is asking.

    from edrak.graph.retrieval.history import assessment_history, theme_history, trend_history

    assessment_history(conn, ws, entity_ids=["amd"])            # newest assessment per (competitor, dimension)
    theme_history(conn, ws)                                     # customer themes, newest run first
    trend_history(conn, ws, entity_ids=["nvidia"])              # newest series per (keyword, geo)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..connection import GraphConnection


@dataclass(frozen=True)
class AssessmentPoint:
    competitor: str
    company_id: str
    dimension: str
    relative_to_target: str
    summary: str | None
    confidence: float | None
    observed_at: str | None
    request_id: str | None
    previous: str | None = None  # relative_to_target in the run before, when it differed

    def line(self) -> str:
        moved = f" (was {self.previous.replace('_', ' ')})" if self.previous else ""
        seen = f", seen {(self.observed_at or '')[:10]}" if self.observed_at else ""
        return f"{self.competitor} on {self.dimension}: {self.relative_to_target.replace('_', ' ')}{moved}{seen}"


@dataclass(frozen=True)
class ThemePoint:
    label: str
    theme: str | None  # the catalog Theme it matched, if any
    count: int | None
    share: float | None
    sentiment_negative: float | None
    observed_at: str | None
    request_id: str | None

    def line(self) -> str:
        share = f"{round(self.share * 100, 1)}% of items" if self.share is not None else f"{self.count} items"
        negative = f", {round(self.sentiment_negative * 100)}% negative" if self.sentiment_negative is not None else ""
        catalog = f" [{self.theme}]" if self.theme and self.theme.lower() != self.label.lower() else ""
        return f"{self.label}{catalog}: {share}{negative}, seen {(self.observed_at or '')[:10]}"


@dataclass(frozen=True)
class TrendPoint:
    keyword: str
    geo: str | None
    timeframe: str | None
    first_value: float | None
    latest_value: float | None
    peak_value: float | None
    points: int | None
    observed_at: str | None
    request_id: str | None

    def line(self) -> str:
        where = f" ({self.geo})" if self.geo else ""
        return (
            f"{self.keyword}{where}: {self.first_value:g} -> {self.latest_value:g} (peak {self.peak_value:g}, "
            f"{self.points} points, {self.timeframe or 'timeframe ?'}), seen {(self.observed_at or '')[:10]}"
            if None not in (self.first_value, self.latest_value, self.peak_value)
            else f"{self.keyword}{where}: seen {(self.observed_at or '')[:10]}"
        )


Q_ASSESSMENTS = """
MATCH (a:Assessment {workspace_id: $ws})-[:OF]->(c:Company)
WHERE ($entity_ids IS NULL OR c.id IN $entity_ids)
  AND ($exclude IS NULL OR a.request_id <> $exclude)
RETURN c.id AS company_id, coalesce(a.competitor, c.name) AS competitor, a.dimension AS dimension,
       a.relative_to_target AS relative_to_target, a.summary AS summary, a.confidence AS confidence,
       a.observed_at AS observed_at, a.request_id AS request_id
ORDER BY a.observed_at DESC
LIMIT $limit
"""

Q_THEMES = """
MATCH (o:ThemeObservation {workspace_id: $ws})
WHERE ($exclude IS NULL OR o.request_id <> $exclude)
OPTIONAL MATCH (o)-[:OF_THEME]->(t:Theme)
WITH o, head(collect(t.name)) AS theme
WHERE ($theme_ids IS NULL OR EXISTS { MATCH (o)-[:OF_THEME]->(x:Theme) WHERE x.id IN $theme_ids })
RETURN o.label AS label, theme, o.count AS count, o.share AS share,
       o.sentiment_negative AS sentiment_negative, o.observed_at AS observed_at, o.request_id AS request_id
ORDER BY o.observed_at DESC, o.count DESC
LIMIT $limit
"""

Q_TRENDS = """
MATCH (ts:TrendSeries {workspace_id: $ws})-[:FOR_TERM]->(k:SearchTerm)
WHERE ($exclude IS NULL OR ts.request_id <> $exclude)
  AND ($entity_ids IS NULL OR EXISTS { MATCH (k)-[:REFERS_TO]->(x) WHERE x.id IN $entity_ids })
RETURN ts.keyword AS keyword, ts.geo AS geo, ts.timeframe AS timeframe, ts.first_value AS first_value,
       ts.latest_value AS latest_value, ts.peak_value AS peak_value, ts.points AS points,
       ts.observed_at AS observed_at, ts.request_id AS request_id
ORDER BY ts.observed_at DESC
LIMIT $limit
"""


def _ids(values: Sequence[str] | None) -> list[str] | None:
    return list(values) if values else None


def assessment_history(
    conn: GraphConnection,
    workspace_id: str | None,
    *,
    entity_ids: Sequence[str] | None = None,
    exclude_request_id: str | None = None,
    limit: int = 8,
) -> list[AssessmentPoint]:
    """The newest assessment per (competitor, dimension), with the previous verdict when it changed."""
    if not workspace_id:
        return []
    rows = conn.read(Q_ASSESSMENTS, ws=workspace_id, entity_ids=_ids(entity_ids), exclude=exclude_request_id, limit=limit * 6)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    previous: dict[tuple[str, str], str] = {}
    for row in rows:  # newest first
        k = (row["company_id"], (row.get("dimension") or "").lower())
        if k not in latest:
            latest[k] = row
        elif k not in previous and row.get("request_id") != latest[k].get("request_id"):
            previous[k] = row.get("relative_to_target") or ""
    out = []
    for k, row in latest.items():
        before = previous.get(k)
        out.append(
            AssessmentPoint(
                competitor=row["competitor"],
                company_id=row["company_id"],
                dimension=row.get("dimension") or "",
                relative_to_target=row.get("relative_to_target") or "not_established",
                summary=row.get("summary"),
                confidence=row.get("confidence"),
                observed_at=row.get("observed_at"),
                request_id=row.get("request_id"),
                previous=before if before and before != row.get("relative_to_target") else None,
            )
        )
    return out[:limit]


def theme_history(
    conn: GraphConnection,
    workspace_id: str | None,
    *,
    theme_ids: Sequence[str] | None = None,
    exclude_request_id: str | None = None,
    limit: int = 6,
) -> list[ThemePoint]:
    """Customer themes from earlier runs, newest run first, biggest first within a run."""
    if not workspace_id:
        return []
    rows = conn.read(Q_THEMES, ws=workspace_id, theme_ids=_ids(theme_ids), exclude=exclude_request_id, limit=limit)
    return [
        ThemePoint(
            label=r["label"], theme=r.get("theme"), count=r.get("count"), share=r.get("share"),
            sentiment_negative=r.get("sentiment_negative"), observed_at=r.get("observed_at"), request_id=r.get("request_id"),
        )
        for r in rows
    ]


def trend_history(
    conn: GraphConnection,
    workspace_id: str | None,
    *,
    entity_ids: Sequence[str] | None = None,
    exclude_request_id: str | None = None,
    limit: int = 4,
) -> list[TrendPoint]:
    """The newest search-interest series per (keyword, geo)."""
    if not workspace_id:
        return []
    rows = conn.read(Q_TRENDS, ws=workspace_id, entity_ids=_ids(entity_ids), exclude=exclude_request_id, limit=limit * 4)
    seen: set[tuple[str, str | None]] = set()
    out = []
    for r in rows:
        k = ((r.get("keyword") or "").lower(), r.get("geo"))
        if k in seen:
            continue
        seen.add(k)
        out.append(
            TrendPoint(
                keyword=r["keyword"], geo=r.get("geo"), timeframe=r.get("timeframe"), first_value=r.get("first_value"),
                latest_value=r.get("latest_value"), peak_value=r.get("peak_value"), points=r.get("points"),
                observed_at=r.get("observed_at"), request_id=r.get("request_id"),
            )
        )
    return out[:limit]
