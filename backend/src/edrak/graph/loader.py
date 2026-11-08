"""Load sample EDRAK runs from sample_data.json into Neo4j.

    python -m edrak.graph.loader --apply-schema                 # schema + every run in sample_data.json + demo
    python -m edrak.graph.loader --file my_runs.json --runs 1   # another file, first run only
    python -m edrak.graph.loader --wipe --apply-schema          # clear the database first (asks to confirm)

File format (see sample_data.json)::

    {
      "workspace_id": "sandbox",
      "catalog": [                                   optional: curated entities (see ingestion.writers.ingest_catalog)
        {"label": "Feature", "name": "High-bandwidth memory", "of": ["B200", "AMD Instinct"], "aliases": ["HBM3E"]},
        {"label": "Regulation", "name": "...", "applies_in": ["Saudi Arabia"]},
        {"label": "Industry", "name": "Semiconductors", "of": ["NVIDIA", "AMD"]},
        ...                                          labels: Product, Feature, PricingPlan, Regulation, Theme, Platform, Industry
      ],
      "runs": [
        {
          "request":      BusinessRequest as JSON,
          "plan":         {"plan_id", "created_at", "rationale", "tasks": [{"task_id", "worker", "goal", "focus", "requirements"}]},
          "results":      [WorkerResult as JSON, metrics in metadata.metrics; the competitor and customer
                           results may carry the metadata their workers write (comparison_matrix, themes, ...)],
          "verification": VerificationResult as JSON,
          "cross_signal": CrossSignalOutput as JSON (kept as a dict, like OrchestrationResult does),
          "decision":     DecisionAnalysisResult as JSON (kept as a dict)
        }
      ]
    }

Plan tasks leave out parent_request_id, company_profile and business_context: the loader
copies them from the run's request, as the planner does. Every request, plan, result,
verification and metric observation is validated against the real contract classes before
loading, so a malformed file fails here with the contract's own error message.

The runs go into their own workspace ("sandbox" unless the file or --workspace says otherwise),
because workers read prior knowledge per workspace and must never see this data in a real analysis.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edrak.contracts import BusinessRequest, ResearchPlan, ResearchTask, VerificationResult, WorkerResult, WorkerType
from edrak.contracts.metrics import MetricObservation, MetricRequirement

from .agent_context import prior_knowledge_for_task
from .connection import GraphConnection, get_graph_settings
from .entities import COMPANY, EntityIndex
from .ingestion.pipeline import GraphIngestor
from .ingestion.writers import catalog_rows
from .profile_snapshot import ProfileSnapshot
from .retrieval.graph_retriever import GraphRetriever
from .retrieval.history import assessment_history, theme_history, trend_history

logger = logging.getLogger("edrak.graph.loader")

DEFAULT_DATA = Path(__file__).with_name("sample_data.json")
SANDBOX_WORKSPACE = "sandbox"


@dataclass
class SampleRun:
    request: BusinessRequest
    plan: ResearchPlan
    results: list[WorkerResult]
    verification: VerificationResult | None
    cross_signal: dict[str, Any] | None
    decision: dict[str, Any] | None
    catalog: list[dict[str, Any]]
    profile_snapshot: ProfileSnapshot | None = None


# ============================================================================= reading the file


def _request(data: dict[str, Any], workspace_id: str) -> BusinessRequest:
    data = json.loads(json.dumps(data))  # deep copy: the workspace override must not touch the caller's dict
    data.setdefault("company_profile", {})["workspace_id"] = workspace_id
    return BusinessRequest.model_validate(data)


def _plan(data: dict[str, Any], request: BusinessRequest) -> ResearchPlan:
    tasks = [
        ResearchTask(
            task_id=t["task_id"],
            parent_request_id=request.request_id,
            worker=WorkerType(t["worker"]),
            goal=t["goal"],
            focus=t["focus"],
            attempt=t.get("attempt", 1),
            company_profile=request.company_profile,
            business_context=request.business_context,
            requirements=[MetricRequirement.model_validate(r) for r in t.get("requirements", [])],
        )
        for t in data["tasks"]
    ]
    return ResearchPlan(
        plan_id=data["plan_id"],
        request_id=request.request_id,
        tasks=tasks,
        created_at=data.get("created_at") or request.created_at,
        rationale=data.get("rationale"),
    )


def _result(data: dict[str, Any]) -> WorkerResult:
    result = WorkerResult.model_validate(data)
    metrics = result.metadata.get("metrics") or []
    # WorkerResult.metadata is a free dict, so check the observations against their own contract here
    result.metadata["metrics"] = [MetricObservation.model_validate(m).model_dump(mode="json") for m in metrics]
    return result


def load_runs(path: Path | str = DEFAULT_DATA, workspace_id: str | None = None, limit: int | None = None) -> list[SampleRun]:
    """Read and validate the runs in a sample data file."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    workspace_id = workspace_id or doc.get("workspace_id") or SANDBOX_WORKSPACE
    catalog = list(doc.get("catalog") or [])
    runs = []
    for i, raw in enumerate(doc["runs"][:limit] if limit else doc["runs"], start=1):
        try:
            request = _request(raw["request"], workspace_id)
            run_catalog = list(raw.get("catalog", catalog))
            runs.append(
                SampleRun(
                    request=request,
                    plan=_plan(raw["plan"], request),
                    results=[_result(r) for r in raw.get("results", [])],
                    verification=VerificationResult.model_validate(raw["verification"]) if raw.get("verification") else None,
                    cross_signal=raw.get("cross_signal"),
                    decision=raw.get("decision"),
                    catalog=run_catalog,
                    profile_snapshot=ProfileSnapshot.model_validate(raw["profile_snapshot"]) if raw.get("profile_snapshot") is not None else None,
                )
            )
            catalog_rows(run_catalog, EntityIndex.from_request(request))  # owners and markets must exist in this run
        except Exception as exc:
            raise ValueError(f"{path}: run {i} ({raw.get('request', {}).get('request_id', '?')}) is invalid: {exc}") from exc
    return runs


def demo_task(run: SampleRun, worker: WorkerType = WorkerType.COMPETITOR_INTELLIGENCE) -> ResearchTask:
    """A task for a request that is not in the graph yet, as the next real run would send."""
    template = next(t for t in run.plan.tasks if t.worker is worker)
    return template.model_copy(update={"task_id": "demo-next-task", "parent_request_id": "demo-next-request"})


# ============================================================================= CLI


def _demo_subject(runs: list[SampleRun]) -> tuple[str, str, str | None]:
    """A competitor that has a metric observation in the file, so every demo query has something to show.

    Returns (company id, company name, metric id). Falls back to the first competitor without a metric.
    """
    index = EntityIndex.from_request(runs[0].request)
    for run in runs:
        for result in run.results:
            for obs in result.metadata.get("metrics") or []:
                ref = index.resolve(obs.get("entity"))
                if ref is not None and ref.label == COMPANY and ref.role == "competitor":
                    return ref.id, ref.name, obs.get("metric_id")
    first = index.by_role("competitor")[0]
    return first.id, first.name, None


Q_DEMO_DATA_SOURCES = """
MATCH (:Workspace {id: $ws})-[:CONNECTS]->(d:DataSource)
OPTIONAL MATCH (a:DataAsset)-[:IN_SOURCE]->(d)
OPTIONAL MATCH (e:Evidence)-[:RETRIEVED_FROM]->(a)
RETURN d.name AS source, d.kind AS kind, a.name AS asset, count(DISTINCT e) AS evidence
ORDER BY source, asset
"""

Q_DEMO_STANCES = """
MATCH (wr:WorkerResult {workspace_id: $ws})-[s:MEASURED_STANCE]->(x)
RETURN x.name AS entity, s.request_id AS request_id, s.positive AS positive, s.negative AS negative, s.total AS total
ORDER BY entity, request_id
"""

Q_DEMO_DOMAINS = """
MATCH (r:BusinessRequest {request_id: $rid})-[:ABOUT_COMPANY|COMPARES_AGAINST]->(c:Company)-[:OWNS_DOMAIN]->(s:Source)
RETURN c.name AS company, collect(s.host) AS hosts ORDER BY company
"""


def _print_demo(conn: GraphConnection, workspace_id: str, runs: list[SampleRun]) -> None:
    retriever = GraphRetriever(conn, strict=True)
    company_id, company_name, metric_id = _demo_subject(runs)
    pilot_id = EntityIndex.from_request(runs[0].request).pilot.id

    print("\nNode counts:")
    for label, n in conn.counts().items():
        print(f"  {label:<20} {n}")

    print("\nEntities by label (findings about each):")
    current = None
    for row in retriever.entities(workspace_id):
        if row["label"] != current:
            current = row["label"]
            print(f"  {current}")
        print(f"    {row['name']:<40} {row['findings']}")

    print("\nCustomer themes (catalog):")
    for row in retriever.themes(workspace_id):
        print(f"  {row['theme']:<20} evidence={row['evidence']}  platforms={row['platforms']}  stances={row['stances']}")

    print(f"\nVerified findings about {company_name}:")
    for f in retriever.find_findings(workspace_id, query=company_name, entity_ids=[company_id], limit=5):
        print(f"  - {f.statement}  [{f.label()}]")

    if metric_id:
        print(f"\nMetric history: {metric_id} for {company_name}")
        for m in retriever.metric_history(workspace_id, metric_id=metric_id, entity_ids=[company_id]):
            print(f"  {(m.observed_at or '')[:10]}  {m.value_text}  ({m.request_id})")

    print(f"\nComparison with {company_name} across runs (newest per dimension):")
    for a in assessment_history(conn, workspace_id, entity_ids=[company_id]):
        print(f"  - {a.line()}")

    print("\nCustomer theme observations (newest first):")
    for t in theme_history(conn, workspace_id, limit=10):
        print(f"  - {t.line()}  ({t.request_id})")

    print("\nSearch interest (newest series per keyword):")
    for t in trend_history(conn, workspace_id):
        print(f"  - {t.line()}")

    print("\nStance tallies per run:")
    for row in conn.read(Q_DEMO_STANCES, ws=workspace_id):
        print(f"  {row['entity']:<12} {row['request_id']:<16} positive={row['positive']} negative={row['negative']} total={row['total']}")

    print("\nCompany data sources:")
    for row in conn.read(Q_DEMO_DATA_SOURCES, ws=workspace_id):
        print(f"  {row['source']} ({row['kind']}) -> {row['asset']}  evidence={row['evidence']}")

    print("\nOfficial domains:")
    for row in conn.read(Q_DEMO_DOMAINS, rid=runs[-1].request.request_id):
        print(f"  {row['company']:<12} {', '.join(row['hosts'])}")

    print(f"\n{EntityIndex.from_request(runs[0].request).pilot.name} neighbourhood:")
    for link in retriever.neighbourhood(pilot_id, workspace_id).get("links", []):
        print(f"  {link['rel']:<14} {link['name']}  findings={len(link.get('finding_ids') or [])}")

    for worker in (WorkerType.COMPETITOR_INTELLIGENCE, WorkerType.CUSTOMER_TRENDS):
        print(f"\nPrior knowledge a NEW {worker.value} task would receive:")
        task = demo_task(runs[0], worker)
        print(prior_knowledge_for_task(task, retriever=retriever, conn=conn).as_prompt() or "  (nothing)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, default=DEFAULT_DATA, help="JSON file with the runs (default: sample_data.json).")
    parser.add_argument("--runs", type=int, default=None, help="Load only the first N runs of the file.")
    parser.add_argument("--workspace", default=None, help="Workspace id (default: the file's, else sandbox). Never use a real one.")
    parser.add_argument("--apply-schema", action="store_true", help="Create constraints and indexes first.")
    parser.add_argument("--wipe", action="store_true", help="Delete EVERYTHING in the database first (asks to confirm).")
    parser.add_argument("--no-demo", action="store_true", help="Skip the demo queries.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:  # read and validate the file before connecting, so a bad file fails fast
        runs = load_runs(args.file, args.workspace, args.runs)
    except (OSError, ValueError) as exc:
        print(f"Cannot load {args.file}: {exc}", file=sys.stderr)
        return 1
    if not runs:
        print(f"{args.file} has no runs.", file=sys.stderr)
        return 1
    workspace_id = runs[0].request.company_profile.workspace_id

    settings = get_graph_settings().model_copy(update={"GRAPH_ENABLED": True})  # the loader always talks to Neo4j
    conn = GraphConnection(settings)
    if not conn.available():
        print(f"Cannot reach Neo4j at {settings.NEO4J_URI}. Is it running? Check NEO4J_* in .env.", file=sys.stderr)
        return 1

    try:
        if args.wipe:
            if input("This deletes EVERY node in the database. Type 'wipe' to continue: ").strip() != "wipe":
                print("Cancelled.")
                return 1
            conn.wipe()
        if args.apply_schema:
            conn.apply_schema()

        ingestor = GraphIngestor(conn, strict=True)
        for run in runs:
            report = ingestor.ingest_run(
                run.request, workspace_id=workspace_id, status="completed", plan=run.plan, results=run.results,
                verification=run.verification, cross_signal=run.cross_signal, decision=run.decision,
                catalog=run.catalog, profile_snapshot=run.profile_snapshot,
            )
            print(f"Loaded {run.request.request_id} into workspace {workspace_id}: {'ok' if report.ok else report.summary()}")

        if not args.no_demo:
            _print_demo(conn, workspace_id, runs)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
