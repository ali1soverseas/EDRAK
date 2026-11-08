"""Pass knowledge from the graph into EDRAK's four workers.

Two levels of integration:

1. **No worker changes** - ``wrap_registry(registry)`` wraps every registered worker
   in ``GraphAwareWorker``. Before a task runs it looks up what earlier runs already
   verified and what they left open, and:
     * stores the full ``PriorKnowledge`` where the worker's nodes can read it
       (``current_prior_knowledge()``), and
     * when ``GRAPH_FOCUS_HINTS`` is on, appends a short note to ``task.focus`` -
       the field the contract defines as the worker's instruction surface - so
       planning prompts steer toward what changed or is still missing.
   The internal worker is excluded from the focus note: its fallback planner splits
   ``focus`` into search queries.

2. **Deeper hooks** - a worker's planning node calls ``prior.as_prompt()`` and adds
   it to its prompt, and the internal worker swaps ``InternalRetriever`` for
   ``HybridRetriever``. See README.md, "Agent integration".

Prior knowledge is context, not evidence: the prompt says so explicitly. A new
finding still needs a source collected (or re-retrieved) in the current run, and
verification still judges it.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from .connection import GraphConnection, get_connection, get_graph_settings
from .entities import EntityIndex, enum_value, get, slugify
from .retrieval.graph_retriever import GraphFinding, GraphGap, GraphRetriever, GraphSignal, MetricPoint
from .retrieval.history import (
    AssessmentPoint,
    ThemePoint,
    TrendPoint,
    assessment_history,
    theme_history,
    trend_history,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerProfile:
    entity_scope: tuple[str, ...]  # which task entities to look up: pilot, competitors, markets, offering
    categories: tuple[str, ...] | None  # FindingCategory values worth reusing; None = all
    with_metrics: bool = True
    focus_hint: bool = True
    # Run-scoped history to read back (retrieval/history.py): "assessments", "themes", "trends".
    history: tuple[str, ...] = ()


WORKER_PROFILES: dict[str, WorkerProfile] = {
    "internal_intelligence": WorkerProfile(
        ("pilot", "offering"),
        ("product_feature", "pricing_packaging", "positioning", "strength", "gap"),
        focus_hint=False,
    ),
    "competitor_intelligence": WorkerProfile(
        ("competitors",),
        ("product_feature", "pricing_packaging", "positioning", "target_customer", "strength", "gap"),
        focus_hint=False,
        history=("assessments",),
    ),
    "market_intelligence": WorkerProfile(
        ("markets", "offering"),
        ("market_signal", "risk", "opportunity", "target_customer"),
        with_metrics=False,
    ),
    "customer_trends": WorkerProfile(
        ("pilot", "competitors"),
        ("customer_sentiment", "target_customer", "gap"),
        history=("themes", "trends"),
    ),
}
_DEFAULT_PROFILE = WorkerProfile(("pilot", "competitors", "markets"), None)


# ============================================================================= prior knowledge


@dataclass
class PriorKnowledge:
    worker: str
    findings: list[GraphFinding] = field(default_factory=list)
    signals: list[GraphSignal] = field(default_factory=list)
    gaps: list[GraphGap] = field(default_factory=list)
    metrics: list[MetricPoint] = field(default_factory=list)
    assessments: list[AssessmentPoint] = field(default_factory=list)
    themes: list[ThemePoint] = field(default_factory=list)
    trends: list[TrendPoint] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.findings or self.signals or self.gaps or self.metrics or self.assessments or self.themes or self.trends
        )

    def gap_queries(self, limit: int = 3) -> list[str]:
        """Open gaps from earlier runs, usable as extra search queries."""
        return [g.subject for g in self.gaps[:limit]]

    def as_prompt(self, max_chars: int = 3000) -> str:
        """A block to add to a planning or synthesis prompt. Empty string when there is nothing."""
        if self.is_empty:
            return ""
        lines = [
            "PRIOR KNOWLEDGE FROM EARLIER EDRAK RUNS (verified then; may be out of date)",
            "Use it to avoid repeating research and to look for what changed or is still missing.",
            "Do not cite it as evidence: every new finding needs a source collected in this run.",
        ]
        if self.findings:
            lines.append("\nVerified findings:")
            lines += [f"- {f.statement} ({f.label()})" for f in self.findings]
        if self.metrics:
            lines.append("\nFigures seen before:")
            lines += [
                f"- {m.metric_id or 'metric'} for {m.entity}: {m.value_text}"
                f"{f' ({m.label})' if m.label else ''}, {m.scope or 'scope ?'}, seen {(m.observed_at or '')[:10]}"
                for m in self.metrics
            ]
        if self.assessments:
            lines.append("\nEarlier comparisons with the company (competitor vs company, per dimension):")
            lines += [f"- {a.line()}" for a in self.assessments]
        if self.themes:
            lines.append("\nCustomer themes counted before:")
            lines += [f"- {t.line()}" for t in self.themes]
        if self.trends:
            lines.append("\nSearch interest measured before:")
            lines += [f"- {t.line()}" for t in self.trends]
        if self.signals:
            lines.append("\nCross-domain signals:")
            lines += [f"- [{s.signal_type}] {s.title}: {s.signal}" for s in self.signals]
        if self.gaps:
            lines.append("\nStill open from earlier runs (worth targeting):")
            lines += [f"- {g.subject}" for g in self.gaps]
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 15].rstrip() + "\n- (truncated)"

    def focus_hint(self, max_chars: int = 500) -> str:
        """A one-paragraph note short enough to append to ResearchTask.focus."""
        parts = []
        if self.findings:
            known = "; ".join(f.statement.rstrip(".") for f in self.findings[:3])
            parts.append(f"Earlier runs already verified: {known}.")
        if self.gaps:
            parts.append("Still missing from earlier runs: " + "; ".join(g.subject.rstrip(".") for g in self.gaps[:2]) + ".")
        moved = [a for a in self.assessments if a.previous]
        if moved:
            parts.append(
                "Comparisons that changed between earlier runs: "
                + "; ".join(f"{a.competitor} on {a.dimension} ({a.previous} -> {a.relative_to_target})" for a in moved[:2])
                + "."
            )
        if not parts:
            return ""
        parts.append("Prioritise what changed or is still missing.")
        text = " ".join(parts)
        return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def task_entity_ids(task: Any, scope: Sequence[str]) -> list[str]:
    """Slugs of the entities a task is about, in the same form ingestion used."""
    profile, context = get(task, "company_profile"), get(task, "business_context")
    names: list[str] = []
    if "pilot" in scope:
        names.append(get(profile, "name"))
    if "competitors" in scope:
        names += [get(c, "name") for c in get(context, "competitors") or []]
        names += list(get(context, "targets") or [])
    if "markets" in scope:
        names += list(get(context, "markets") or [])
    if "offering" in scope and get(context, "offering"):
        names.append(get(context, "offering"))
    return sorted({slugify(n) for n in names if n})


def prior_knowledge_for_task(
    task: Any,
    *,
    retriever: GraphRetriever | None = None,
    max_findings: int = 8,
    include_current_run: bool = False,
    conn: GraphConnection | None = None,
) -> PriorKnowledge:
    """What the graph already knows that is relevant to this ResearchTask.

    ``conn`` is only used for the run-scoped history (retrieval/history.py); it defaults to the
    retriever's connection, then to the process-wide one.
    """
    worker = enum_value(get(task, "worker")) or ""
    profile = WORKER_PROFILES.get(worker, _DEFAULT_PROFILE)
    workspace_id = get(get(task, "company_profile"), "workspace_id")
    prior = PriorKnowledge(worker=worker)
    if not workspace_id:
        return prior
    retriever = retriever or GraphRetriever()
    if not retriever.available():
        return prior

    entity_ids = task_entity_ids(task, profile.entity_scope) or None
    if worker == "competitor_intelligence" and not entity_ids:
        return prior
    exclude = None if include_current_run else get(task, "parent_request_id")
    query = f"{get(task, 'goal') or ''} {get(task, 'focus') or ''}"
    common = dict(entity_ids=entity_ids, categories=profile.categories, exclude_request_id=exclude)
    if worker == "competitor_intelligence":
        common["include_synthetic"] = False

    findings = retriever.find_findings(workspace_id, query=query, limit=max_findings, **common)
    if len(findings) < max_findings:  # top up with the most recent verified findings about the same entities
        seen = {f.finding_id for f in findings}
        for f in retriever.find_findings(workspace_id, limit=max_findings, **common):
            if f.finding_id not in seen and len(findings) < max_findings:
                findings.append(f)
                seen.add(f.finding_id)
    prior.findings = findings
    prior.signals = retriever.signals(workspace_id, entity_ids=entity_ids, exclude_request_id=exclude, limit=4)
    gaps = retriever.open_gaps(
        workspace_id, workers=[worker], exclude_request_id=exclude,
        limit=20 if worker == "competitor_intelligence" else 5,
    )
    if worker == "competitor_intelligence":
        index = EntityIndex.from_request(task)
        gaps = [
            gap for gap in gaps
            if any(ref.id in entity_ids for ref in index.match(gap.subject))
        ]
    prior.gaps = gaps[:5]
    if profile.with_metrics:
        prior.metrics = retriever.metric_history(workspace_id, entity_ids=entity_ids, exclude_request_id=exclude, limit=8)
    if profile.history:
        _add_history(prior, profile, conn or getattr(retriever, "conn", None) or get_connection(),
                     workspace_id, entity_ids, exclude)
    return prior


def _add_history(
    prior: PriorKnowledge, profile: WorkerProfile, conn: GraphConnection, workspace_id: str,
    entity_ids: list[str] | None, exclude: str | None,
) -> None:
    """Assessments, themes and trends. Optional context: a failure here never hides the findings."""
    try:
        if "assessments" in profile.history:
            prior.assessments = assessment_history(conn, workspace_id, entity_ids=entity_ids, exclude_request_id=exclude)
        if "themes" in profile.history:
            prior.themes = theme_history(conn, workspace_id, exclude_request_id=exclude)
        if "trends" in profile.history:
            prior.trends = trend_history(conn, workspace_id, entity_ids=entity_ids, exclude_request_id=exclude)
    except Exception:  # noqa: BLE001
        logger.warning("Run history lookup failed; continuing with findings only", exc_info=True)


# ============================================================================= worker wrapper

_CURRENT: contextvars.ContextVar[PriorKnowledge | None] = contextvars.ContextVar("edrak_prior_knowledge", default=None)
_BY_TASK: dict[tuple[str, int], PriorKnowledge] = {}


def current_prior_knowledge(task: Any = None) -> PriorKnowledge | None:
    """Inside a worker node: the prior knowledge GraphAwareWorker computed for this task.

    Falls back to a per-task cache when the node runs in a thread without the context.
    """
    found = _CURRENT.get()
    if found is None and task is not None:
        found = _BY_TASK.get((get(task, "task_id"), get(task, "attempt") or 1))
    return found


def update_current_prior_knowledge(task: Any, prior: PriorKnowledge) -> None:
    """Replace the wrapper's context after a worker discovers its task entities."""
    cache_key = (get(task, "task_id"), get(task, "attempt") or 1)
    if _CURRENT.get() is not None:
        _CURRENT.set(prior)
    if cache_key in _BY_TASK:
        _BY_TASK[cache_key] = prior


class GraphAwareWorker:
    """Wraps any ``Worker``: looks up prior knowledge before the run, leaves the result untouched
    except for a ``metadata["graph_prior"]`` summary."""

    def __init__(self, inner: Any, *, retriever: GraphRetriever | None = None, focus_hints: bool | None = None) -> None:
        self.inner = inner
        self.retriever = retriever
        self.focus_hints = get_graph_settings().GRAPH_FOCUS_HINTS if focus_hints is None else focus_hints

    @property
    def worker_type(self) -> Any:
        return self.inner.worker_type

    def run(self, task: Any) -> Any:
        try:
            prior = prior_knowledge_for_task(task, retriever=self.retriever)
        except Exception:  # noqa: BLE001 - never block a worker on the graph
            logger.exception("Prior knowledge lookup failed; running without it")
            prior = PriorKnowledge(worker=str(enum_value(get(task, "worker"))))

        run_task = task
        profile = WORKER_PROFILES.get(prior.worker, _DEFAULT_PROFILE)
        hint = prior.focus_hint() if (self.focus_hints and profile.focus_hint) else ""
        if hint and hasattr(task, "model_copy"):
            run_task = task.model_copy(update={"focus": f"{task.focus}\n\n{hint}"})

        cache_key = (get(task, "task_id"), get(task, "attempt") or 1)
        _BY_TASK[cache_key] = prior
        token = _CURRENT.set(prior)
        try:
            result = self.inner.run(run_task)
            prior = current_prior_knowledge(task) or prior
        finally:
            _CURRENT.reset(token)
            _BY_TASK.pop(cache_key, None)

        meta = get(result, "metadata")
        if isinstance(meta, dict):
            meta["graph_prior"] = {
                "findings": len(prior.findings),
                "signals": len(prior.signals),
                "gaps": len(prior.gaps),
                "metrics": len(prior.metrics),
                "assessments": len(prior.assessments),
                "themes": len(prior.themes),
                "trends": len(prior.trends),
                "focus_hint": bool(hint),
            }
        return result


def wrap_registry(registry: Any, *, retriever: GraphRetriever | None = None) -> Any:
    """Wrap every worker in a WorkerRegistry with GraphAwareWorker. Returns the same registry."""
    if not get_graph_settings().GRAPH_ENABLED:
        return registry
    for worker_type in tuple(registry.worker_types()):
        if worker_type in registry:
            inner = registry.get(worker_type)
            if not isinstance(inner, GraphAwareWorker):
                registry.register(worker_type, GraphAwareWorker(inner, retriever=retriever))
    return registry
