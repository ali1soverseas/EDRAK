# EDRAK knowledge graph (`edrak.graph`)

A Neo4j view of EDRAK's contracts. Each pipeline run writes its request, plan, worker results, verdicts, cross-signals and decision analysis into the graph. The next run's workers read back what earlier runs verified and what they left open. SQLite stays the store of record. With the graph off or unreachable, every function here is a no-op and the pipeline behaves exactly as before.

```text
edrak/graph/
├── schema.cypher                 constraints, indexes, Worker seed (+ node/relationship catalog in comments)
├── connection.py                 1. GraphSettings, GraphConnection, get_connection
├── entities.py                   slugs, stable ids, EntityIndex (pure, no imports)
├── ingestion/
│   ├── writers.py                2. one writer per contract (MERGE only, idempotent)
│   └── pipeline.py                  GraphIngestor.ingest_run, record_run
├── retrieval/
│   ├── ranking.py                   Lucene query building, RRF (pure)
│   ├── graph_retriever.py        3. findings, evidence, signals, gaps, metric history, neighbourhoods
│   ├── history.py                   assessments, customer themes, search interest across runs
│   └── hybrid_retriever.py       4. ChromaDB + graph, drop-in for InternalRetriever
├── agent_context.py              5. PriorKnowledge per worker, GraphAwareWorker, wrap_registry
└── loader.py                        test runs -> Neo4j, then demo queries
```

## 1. Setup

Install the project's Python dependencies and start a local Neo4j container as described
in [NEO4J_SETUP.md](../../../../NEO4J_SETUP.md). The root Docker Compose file does
not start Neo4j. Configure the repository-root `.env`:

```dotenv
GRAPH_ENABLED=true
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-local-password
NEO4J_DATABASE=neo4j
```

Restart the backend after changing the settings. Keep the password out of version control.
The loader uses the root `.env` regardless of the current working directory.

## 2. Load test data

The runs live in `sample_data.json`, next to `loader.py`. Each run holds the contracts EDRAK produces (request, plan, worker results, verification, cross-signal, decision analysis) as JSON.

```powershell
$env:PYTHONPATH = "backend\src"
.\venv\Scripts\python.exe -m edrak.graph.loader --apply-schema
```

The loader validates every request, plan, result, verdict and metric observation against the real contract classes before it connects. A malformed file fails with the run number and the contract's own error. Plan tasks in the file leave out `parent_request_id`, `company_profile` and `business_context`, because the loader copies them from the run's request, as the planner does.

`sample_data.json` holds two legacy fictional runs and a separate profile-aligned example.
The first two compare NVIDIA with Broadcom, Intel and AMD in Saudi Arabia, the United Arab
Emirates and Egypt. The third captures the saved NVIDIA products, strategic goal, industries,
competitors and served markets, with clearly marked synthetic competitor findings. It uses a
per-run empty `catalog`, rather than the legacy global catalog. Optional `profile_snapshot`
data is stored as a workspace- and request-scoped snapshot; `PROFILE_MARKET` relationships
retain whether a market is served or considered. These snapshots are not automatically
synchronized with future frontend profile changes. To add runs, append to `runs` and keep ids
unique across runs.

The legacy competitor, market and customer facts are invented test data, and some legacy
sources are incorrectly marked as non-synthetic. They load into the file's `workspace_id`
(`sandbox`); do not load them into a real workspace. The new example explicitly marks all
its evidence synthetic. `--workspace` overrides the file workspace.

Then open `http://localhost:7474` and try:

```cypher
// one run, end to end
MATCH p = (:BusinessRequest {request_id:'req-nvidia-001'})-[:HAS_ANALYSIS]->(:DecisionAnalysis)
          -[:RECOMMENDS]->(:RecommendedAction)-[:SUPPORTED_BY_SIGNAL]->(:CrossSignal)
          -[:BASED_ON]->(:Finding)-[:SUPPORTED_BY]->(:Evidence)
RETURN p;

// AMD memory per accelerator across runs, with the product each figure is for
MATCH (o:MetricObservation {metric_id:'hbm_capacity_gb', entity_id:'amd'})
OPTIONAL MATCH (o)-[:OF_ITEM]->(item)
RETURN o.observed_at, o.value_text, o.label, item.name, o.request_id ORDER BY o.observed_at;

// how the comparison with AMD moved
MATCH (a:Assessment {workspace_id:'sandbox'})-[:OF]->(:Company {id:'amd'}), (a)-[:ON_DIMENSION]->(t:Topic)
RETURN t.name, a.observed_at, a.relative_to_target ORDER BY t.name, a.observed_at;

// which database table backs an internal finding
MATCH (f:Finding)-[:SUPPORTED_BY]->(:Evidence)-[r:RETRIEVED_FROM]->(a:DataAsset)-[:IN_SOURCE]->(d:DataSource)
RETURN f.statement, d.name, a.name, r.query;
```

## 3. Entity labels

There are 11 entity labels, and they come from two sources.

| From the `BusinessRequest` | From the curated catalog |
|---|---|
| `Company` (your company, competitors), `Product` (your products, the offering), `Market`, `Topic` (focus areas), `CustomerSegment` | `Product` (others' products), `Feature`, `PricingPlan`, `Regulation`, `Theme`, `Platform`, `Industry` |

`Industry` is the graph side of `company_profiles.industry` and `entities.sector`. Neither is in the `CompanyProfile` contract, so it comes through the catalog like the other curated labels.

Catalog entities come from a list that people maintain, never from model output. Pass the list once with `GraphIngestor().ingest_run(..., catalog=[...])` (or put it in the data file's `"catalog"`). The workspace then `TRACKS` those entities, and later runs read them back automatically, so you only pass the list again when it changes.

```json
{"label": "Feature",     "name": "High-bandwidth memory", "of": ["B200", "AMD Instinct"], "aliases": ["HBM3E"]}
{"label": "PricingPlan", "name": "NVIDIA AI Enterprise", "of": "NVIDIA"}
{"label": "Product",     "name": "AMD Instinct", "of": "AMD", "aliases": ["MI355X", "MI400"]}
{"label": "Regulation",  "name": "Saudi Personal Data Protection Law", "applies_in": ["Saudi Arabia"], "aliases": ["Saudi PDPL"]}
{"label": "Theme",       "name": "Supply lead times", "aliases": ["lead times"]}
{"label": "Platform",    "id": "reddit", "name": "Reddit"}
{"label": "Industry",    "name": "Semiconductors", "of": ["NVIDIA", "AMD"], "aliases": ["chipmakers"]}
```

- **Owners.** `of` writes `(owner)-[:OFFERS|HAS_FEATURE|HAS_PLAN|IN_INDUSTRY]->(entity)` and is only allowed for products, features, plans and industries. `applies_in` writes `(Regulation)-[:APPLIES_IN]->(Market)`. An owner or market the request doesn't know is an error, never a new node.
- **Links.** Findings whose statement contains a name or alias, as a whole word, get `ABOUT` edges to that entity.
- **Themes and platforms.** Evidence whose text matches a theme gets `EXPRESSES {stance}`, with the stance taken from `metadata.stance` or `metadata.sentiment`. Evidence with `metadata.platform` gets `POSTED_ON` to that `Platform`, which is created even if it isn't in the catalog.
- **Retrieval.** `GraphRetriever.entities(ws, label=None)` lists a workspace's entities with finding counts, and `GraphRetriever.themes(ws)` lists themes with their platforms and stances. `neighbourhood(id, ws)` returns verified competitor and market relationships supported by findings from that workspace.

If your database was created before these labels existed (`Industry` is the latest), drop and recreate the full-text index so it covers them: `DROP INDEX entity_names;`, then run `--apply-schema` again. The new constraints and indexes are added by `--apply-schema` on its own.

## 3a. Labels from worker metadata and the company profile

Some of what the workers return is not a finding: the competitor worker's comparison matrix, the customer worker's theme counts, search-interest series and stance tallies, and the tables and endpoints the internal worker read. `WorkerResult.metadata` carries these, and the graph now keeps them as their own nodes, each tied to the run that produced it (`request_id`, `workspace_id`, `worker`, `observed_at`).

| Label | Written from | Links |
|---|---|---|
| `DataSource` | `CompanyProfile.db_config` / `api_config`, and SQL/REST evidence | `(Workspace)-[:CONNECTS]->`, `<-[:IN_SOURCE]-(DataAsset)` |
| `DataAsset` | evidence `metadata.channel` = `sql_database` (`table`) or `rest_api` (`endpoint`) | `(Evidence)-[:RETRIEVED_FROM {query, row_count}]->` |
| `Assessment` | competitor `metadata.comparison_matrix.dimensions[].cells[]` | `(WorkerResult)-[:ASSESSED]->`, `-[:OF]->(competitor)`, `-[:RELATIVE_TO]->(pilot)`, `-[:ON_DIMENSION]->(Topic)`, `-[:CITES]->(Evidence)` |
| `ThemeObservation` | customer `metadata.themes[]` | `(WorkerResult)-[:OBSERVED_THEME]->`, `-[:OF_THEME]->(Theme)` when the label matches a catalog theme |
| `SearchTerm` | customer `metadata.trend_series[].keyword` | `-[:REFERS_TO]->(entity)` when the keyword names a known entity |
| `TrendSeries` | customer `metadata.trend_series[]` (points as `dates[]` and `values[]`) | `(WorkerResult)-[:OBSERVED_TREND]->`, `-[:FOR_TERM]->(SearchTerm)` |

New relationships between existing labels:

- `(WorkerResult)-[:MEASURED_STANCE {positive, neutral, negative, mixed, total}]->(Company|Product)` from `metadata.stance_counts`, which the customer worker counts in code.
- `(Company)-[:OWNS_DOMAIN]->(Source)` from the request's websites and the competitor worker's `official_domains`. Evidence `PUBLISHED_BY` that host, or a subdomain of it, is first-party.
- `COMPARES_AGAINST` now also carries `discovery_reason`, `discovery_confidence` and the run's `overview` of the competitor.
- `(MetricObservation)-[:OF]->` now reaches products, plans, features and segments, not only companies and markets. `(MetricObservation)-[:OF_ITEM]->(Product|PricingPlan|Feature)` names the item the figure is for, from `MetricObservation.label` (for example "B200", or "MI355X", an alias of AMD Instinct).
- `(MetricRequirement)-[:FOR_ENTITY]->` now reaches markets and products as well as companies.
- A comparison dimension is a `Topic`. Dimensions are the focus areas plus a few defaults per use case, so "AI accelerators" from the matrix and the focus area "AI accelerators" are the same node.

Two fixes came with this. SQL and REST evidence used to become `Document` nodes, because their `sql://` and `api://` URLs fell into the internal-document branch; they are now `DataAsset`s. And a market evidence item with an `endpoint` key (a public API) is not mistaken for a company API: only `metadata.channel` decides.

## 4. Rules the code enforces

- **Workspace isolation.** Every read filters on `workspace_id`. A call without one returns nothing, the same rule as `InternalRetriever`. Evidence is reached only through a finding of that workspace.
- **No invented entities.** `ABOUT` and `MENTIONS` edges only point at entities the request named (the company, its products, competitors, markets, focus areas, offering), matched on whole words.
- **Domain edges need verified findings.** `COMPETES_WITH` and `OPERATES_IN` are written only from findings with `verification_status = 'verified'`. Each edge lists `finding_ids` and `request_ids`.
- **Latest verdict wins.** `Finding.verification_status` is the newest `JUDGED` verdict, even if verifications are ingested out of order.
- **Idempotent.** Re-ingesting a run updates nodes. `WorkerResult` is keyed by `task_id|attempt`, so retries are kept side by side.
- **Model-written labels are not entities.** Theme labels, search keywords and comparison summaries from the workers become `ThemeObservation`, `SearchTerm` and `Assessment` nodes. A `ThemeObservation` links to a `Theme` only when it matches a catalog theme.
- **Run results stay off shared nodes.** `Company`, `Product` and the other entity nodes are shared across workspaces. A competitor overview or an assessment is one workspace's research, so it lives on `COMPARES_AGAINST` (one request) or on an `Assessment` (one run), never on the `Company` node.
- **Not stored:** `Evidence.page_text`, nested metadata maps, and data-source secrets (`connection_uri`, `base_url`, the OpenAPI spec, `auth_headers`). A `DataSource` has its symbolic id and description only. Primitive metadata is kept as `meta_*`.

## 5. Pipeline ingestion

When enabled, the API records the current run at the verification review checkpoint and
again when it finishes. Ingestion failures are logged and do not break the analysis.
Other callers may use the hook explicitly:

```python
from edrak.graph import record_run

record_run(
    request,                         # BusinessRequest
    orchestration=result,            # plan, results, cross_signal, decision_analysis
    verification=verification,      # VerificationResult (OrchestrationResult does not carry it)
    workspace_id=workspace_id,
    user_id=user_id,
)
```

In the async API service, use `await GraphIngestor().aingest_run(request, ...)`. `record_run` never raises. Read `report.stages` if you want to log failures.

## 6. Agent integration

### Level 1: API workers

The API's default registry wraps its workers automatically when the graph is enabled.
Other entry points can call `wrap_registry(registry)` explicitly.

`GraphAwareWorker` then runs before each task:

- It calls `prior_knowledge_for_task(task)`. This looks up verified findings, figures, cross-signals and open gaps from **earlier** runs in the same workspace, scoped per worker by `WORKER_PROFILES`. Competitor tasks get findings about the competitors, market tasks get findings about the markets and offering, and so on.
- If `GRAPH_FOCUS_HINTS=true`, it appends at most 500 characters to `task.focus` for workers that use focus hints. The internal worker is excluded because its fallback planner splits `focus` into queries; the competitor worker instead receives a separate planning context.
- It exposes the full `PriorKnowledge` to the worker's nodes through `current_prior_knowledge(task)`.
- It adds a count summary to `result.metadata["graph_prior"]`.
- Competitor tasks also get the latest assessment per competitor and dimension, with the earlier verdict when it changed ("AMD on AI accelerators: broader (was comparable)"). A changed comparison is also named in the focus hint. Customer tasks also get the theme counts and search interest from earlier runs. `WorkerProfile.history` picks which.

### Level 2: optional deeper integration

The internal worker does not currently use hybrid retrieval automatically. The example
below illustrates an optional integration. If `where_filter` is provided, hybrid
retrieval returns vector evidence only: graph evidence cannot yet prove that it satisfies
the Chroma metadata filter. It may still annotate vector evidence with prior findings.

`agents/internal_intelligence/nodes.py`, in `retrieve_evidence_node`:

```python
from edrak.graph.agent_context import WORKER_PROFILES, task_entity_ids
from edrak.graph.retrieval.hybrid_retriever import HybridRetriever

# was: retriever = InternalRetriever()
retriever = HybridRetriever()
graph_filters = dict(
    entity_ids=task_entity_ids(task, WORKER_PROFILES["internal_intelligence"].entity_scope) if task else None,
    source_types=["internal_document", "synthetic_internal"],   # keep this worker on internal sources
    exclude_request_id=task.parent_request_id if task else None,
)
# in the loop, the existing retriever.retrieve(query=..., top_k=..., workspace_id=...) call keeps working;
# add **graph_filters to it
```

Each returned `Evidence` carries `metadata["retrieval_sources"]`. Passages that earlier runs already drew conclusions from also carry `metadata["prior_findings"]`. The existing reranker only reads `rank` and `relevance_score`, so nothing else changes.

In `plan_queries_node`, add the prior knowledge to the planning prompt and the open gaps to the queries:

```python
from edrak.graph.agent_context import current_prior_knowledge

prior = current_prior_knowledge(task)
if prior and not prior.is_empty:
    user_prompt += "\n\n" + prior.as_prompt()
...
if prior:
    queries += prior.gap_queries()
```

### Level 2: planning prompts

The competitor worker uses workspace-scoped prior graph context in its requirement and
query planning prompts, including for competitors discovered from the task. It excludes
the current run. Separately, it can reuse an earlier competitor finding when it was verified,
is mapped to the task, and is grounded in fresh, non-synthetic public evidence. Each reused
source keeps its original retrieval date and records its original evidence ID under a new ID,
so reuse neither refreshes nor overwrites the original. Partial, weak, stale, unverified or
otherwise unresolved coverage remains a search target and an eventual gap if not resolved.
Company documents are planning context, not independent evidence. When the graph is off or
unavailable, normal web research continues.

Verification and relevance mapping are not guarantees of correctness, and synthetic content
incorrectly marked as real cannot be detected reliably. Keep the fictional sample data in an
isolated test database rather than a production workspace.

The remaining workers can use the same pattern: read `current_prior_knowledge(task)`
where the task enters the graph, keep `prior.as_prompt()` in state, and add it to the
query-planning prompt.
- **Customer trends:** pass the block as an extra variable to `render(PLAN_QUERIES, ...)` and add a `{prior_knowledge}` slot to that prompt.
- **Market:** add it to the planning node's prompt.

The planning prompt block itself is untrusted context, not evidence. Reusable graph findings
pass the separate checks described above, and the verification pipeline still reviews worker findings.

## 7. Using the retrievers directly

```python
from edrak.graph import GraphRetriever

g = GraphRetriever()
g.find_findings(ws, query="Gaudi 3 pricing", entity_ids=["intel-corp"], categories=["pricing_packaging"])
g.metric_history(ws, metric_id="hbm_capacity_gb", entity_ids=["amd"])
g.signals(ws, entity_ids=["nvidia"])
g.open_gaps(ws, workers=["market_intelligence"])
g.neighbourhood("nvidia", ws)        # verified relationships supported by this workspace's findings
g.resolve_entities(["NVIDIA"], ws)   # resolve only entities visible to this workspace

from edrak.graph import assessment_history, theme_history, trend_history
from edrak.graph.connection import get_connection

conn = get_connection()
assessment_history(conn, ws, entity_ids=["amd"])   # newest per (competitor, dimension), with what it moved from
theme_history(conn, ws)                            # customer themes, newest run first
trend_history(conn, ws, entity_ids=["nvidia"])     # newest search-interest series per (keyword, geo)
```

## 8. Tests

Run `python -m pytest tests\test_graph_integration.py` from the repository root.
The tests do not require a Neo4j server; Cypher execution requires a live database.
