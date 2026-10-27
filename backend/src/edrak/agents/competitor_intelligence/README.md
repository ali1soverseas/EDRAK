# Competitor Intelligence Agent

This guide describes the competitor worker as it is implemented today: how a
`ResearchTask` enters the worker, which graph nodes run, how the research loop
is bounded, how source evidence becomes findings, and what the orchestrator
receives. It is intended as an implementation and operations reference; the
Python modules remain the source of truth.

## Purpose and boundaries

The worker researches named competitors (or discovers a short list when none
were provided), grounds claims in public sources, checks remaining research
requirements, and compares the findings with the target company's supplied
baseline. It supports business decision-making; it does not make final business
decisions or recommendations.

The shared boundary is:

- Input: [`ResearchTask`](../../contracts/task.py), including `company_profile`,
  `business_context`, `goal`, `focus`, `attempt`, and optional typed metric
  requirements.
- Output: [`WorkerResult`](../../contracts/result.py), with standard findings,
  evidence, gaps, confidence and metadata. The worker does not expose a
  competitor-specific result contract.
- Registration: [`get_default_registry`](../../orchestration/registry.py)
  registers `CompetitorAgent` under `WorkerType.COMPETITOR_INTELLIGENCE`.

The worker owns domain research. It does not plan or coordinate the other
workers, and its result can be consumed by the platform's separate verification,
synthesis and briefing stages.

## End-to-end flow

```text
Orchestrator
  -> CompetitorAgent.run(ResearchTask)
  -> run_research()
       -> validate worker type
       -> adapt task to CompetitorState
       -> use named competitors OR discover competitors
       -> save initial state when EDRAK_DEBUG is enabled
       -> LangGraph app.invoke()
            START
              -> identify_requirements
              -> generate_queries
              -> [no valid queries] -> final_report
              -> search
              -> synthesize
              -> ground
              -> check_requirements
              -> [open requirements and budget/progress allow]
                   -> prepare_next_stage -> generate_queries ...
              -> [otherwise] -> final_report
              -> comparison
              -> END
       -> build_worker_result()
  -> orchestrator receives WorkerResult
```

### 1. Entry and task adaptation

`CompetitorAgent.run(task, print_result=False)` adapts the orchestration call
to `run_research`. `run_research` rejects a task whose worker is not
`COMPETITOR_INTELLIGENCE`, then `_task_to_initial_state` converts Pydantic
contract objects into graph-friendly dictionaries and lists.

`_competitors_for` first uses `business_context.competitors` and the shared
`competitor_names` vocabulary helper. It carries known website, social links,
discovery attribution and aliases into `competitor_info`, and registers known
official website domains for source filtering. If there are no names, it calls
`discover_competitors` with the target company, its products/notes, offering,
markets and target customers; discovery is limited to four candidates for this
worker entry path. If no competitors are available after this step,
`run_research` returns `NO_EVIDENCE` without invoking the graph.

The initial state includes the task and attempt IDs, current date, company and
business context, goal/focus, metric requirements, competitor details, and
empty collections for requirements, queries, results, findings, checks, gaps
and discarded sources. The graph state schema is `CompetitorState` in
[`state.py`](state.py).

### 2. Requirements and query generation

`identify_research_requirements_node` asks the model to derive factual
information needs for each competitor based on the goal, focus and target
company baseline. Typed metric requirements are inserted first so they receive
the limited budget first. Model-proposed requirements for unknown competitors
are discarded; duplicates are removed; at most five requirements per
competitor are retained. A missing/invalid requirements response is an explicit
runtime error rather than a successful empty result.

`generate_queries_node` gets targets from all requirements in stage 1 and from
only the unresolved requirements in later stages. It supplies prior verified
findings and past queries to help prevent repetition, validates generated
requirement IDs, deduplicates queries, and enforces a maximum of two queries per
requirement and 24 executed queries per run.

Current strategy labels in `state.py` are:

| Stage | Strategy | Purpose |
|---|---|---|
| 1 | `official_sources` | Seek first-party documentation and product/pricing details first. |
| 2 | `discovery` | Use broader sources to find relevant product, capability and market facts for remaining gaps. |
| 3 | `targeted_missing_requirements` | Search only for facts still missing after earlier stages. |

If there are no valid queries, `after_queries_router` routes directly to
`final_report`.

### 3. Search and source selection

`search_node` runs each query through `run_tavily`. In the official-source
strategy (stage 1) it restricts the search to the registered competitor domains first;
if that produces no results, it retries the query without the domain
restriction. If the Tavily operation fails, this node attempts a direct Serper
HTTP request when `SERPER_API_KEY` is configured. Each attempted query is
recorded, including a query that returned no usable sources.

Results are deduplicated by competitor and normalized URL. Search text is
sanitized as untrusted content before storage and capped at 6,000 characters.
New results are passed through `sources.judge`; rejected results are recorded
with a reason. Official competitor domains are eligible with at least 80
characters of text. Other sources must have at least 200 characters, name the
competitor, and not be a social/community site, low-value host, or source
classified as an aggregator/reseller. High-risk content that attempts to
instruct the AI is rejected. Social/community research is deliberately left to
the Customer worker.

The search node retains source provenance (competitor, requirement, query,
stage, provider, title, URL, publisher, date and search score) for later
grounding and metadata.

### 4. Synthesis, grounding and retry loop

`synthesize_node` works on the current stage's accepted sources, grouped by
competitor and batched to fit the configured evidence size. The model returns
claims with a requirement ID, category and cited source IDs. Application code
resolves IDs against the actual source batch and attaches source passages; an
unknown source ID cannot create evidence.

`verify_node` is the worker's local grounding check, not the platform-wide
verification stage. It checks each proposed statement against the text of its
cited source, rejects unsupported or invented claims, attaches confidence and
evidence-quality information, deduplicates/merges claims, and updates the
verified findings carried between stages. Numbers and factual wording must be
supported by cited text. The broader platform verification stage still runs
after worker dispatch.

`check_research_requirements_node` deterministically marks each requirement
fulfilled when a grounded finding maps to it, otherwise missing. For metric
requirements marked as requiring an official source, a third-party-only result
does not fulfill the requirement. Missing items, including a reason, become the
next stage's query targets.

`research_router` stops when there are no gaps, the three-stage limit is
reached, the 24-query budget is exhausted, or the most recent stage added fewer
than two new grounded findings. Otherwise it routes to
`prepare_next_stage_node`, which increments the stage before generating
targeted queries. The loop therefore has explicit stage, query and progress
bounds.

### 5. Profiles, comparison and result construction

`final_report_node` consolidates one profile per competitor from the verified
findings and requirement checks. A profile contains an overview, per-requirement
status/summary/release status/confidence/evidence quality/source references,
and caveats. If model consolidation fails, requirement-level verified findings
are retained as a deterministic fallback rather than being discarded.

`comparison_node` asks for a comparison across focus areas and default
use-case dimensions. It validates each assessment against known competitors and
requirement IDs. A comparison cell without cited requirements and evidence is
forced to `not_established`; confidence cannot exceed the supporting evidence
confidence. If structured comparison output is unavailable, the report records
a comparison error and still returns the worker result.

`build_worker_result` converts only cited/verified source passages into shared
`Evidence` records and creates shared `Finding` objects with resolving evidence
references. Established comparison cells are also emitted as `POSITIONING`
findings and represented in the `metadata.comparison_matrix` for consumers such
as the brief builder. Result status is:

- `COMPLETED`: one or more findings and no unresolved requirements.
- `PARTIAL`: one or more findings with unresolved requirements.
- `NO_EVIDENCE`: no supported findings.

The result metadata includes competitor identity/website/domain/social
information, discovery attribution, stage and query counts, sources read/set
aside, requirement checks and the structured comparison matrix. Gaps carry
unresolved competitor requirements and, when applicable, a summary that sources
were set aside.

## State and model schemas

The typed state and model response schemas live in [`state.py`](state.py):

- Requirements: `ResearchRequirement`, `ResearchRequirements`.
- Query plan: `SearchQuery`, `SearchQueryList`.
- Model claims: `LLMFinding`, `FindingList`; real evidence is attached by Python
  after the model response.
- Competitor profile: `ConsolidatedRequirement`, `CompetitorProfile`.
- Comparison: `CompetitorAssessment`, `DimensionComparison`,
  `ComparisonReport`.
- Graph payload: `CompetitorState`, a `TypedDict` whose fields are optional
  because nodes return partial state updates.

Key limits are defined there: 3 search results per query; 3 research stages;
2 queries per requirement; 24 queries total; 5 requirements per competitor;
2 new verified findings to justify continuing; 6,000 stored source characters;
3,500 source characters supplied per source; 60,000 characters per synthesis
batch; no more than 3 evidence items per finding; and a 0.85 duplicate
similarity threshold. Comparison dimensions vary by use case and include
requested focus areas, capped at eight dimensions.

The state module's `_coerce_str_list` validator helper normalizes model output
that supplies a single string where a list is expected.
`comparison_dimensions(use_case, focus_areas)` selects the use-case defaults,
adds non-duplicate user focus areas, and caps the result at eight.

## Function inventory

### Package entry point: `__init__.py`

| Function | Responsibility |
|---|---|
| `CompetitorAgent.run` | Orchestrator adapter; delegates the shared `ResearchTask` to `run_research`. |
| `_competitors_for` | Builds competitor names/details from the task or runs bounded competitor discovery; registers known official domains. |
| `_task_to_initial_state` | Adapts task contracts and context into the graph's initial dictionary state. |
| `run_research` | Validates worker type, handles the no-competitor case, invokes the graph, builds `WorkerResult`, and optionally prints it. |

### Graph and workflow: `graph.py`

| Function/object | Responsibility |
|---|---|
| `graph` | Declares nodes, unconditional edges and conditional routing for the workflow described above. |
| `app` | Compiled LangGraph invoked by `run_research`. |

### Nodes and core helpers: `nodes.py`

| Function | Responsibility |
|---|---|
| `_require` | Raises a clear configuration error when a required value is absent. |
| `_tavily_key` | Reloads project environment configuration and resolves the competitor Tavily key. |
| `_banner` | Prints a labeled console banner for a node/stage. |
| `_compat_httpx2_brotli` | Applies a compatibility wrapper for the optional `httpx2` Brotli decoder dependency. |
| `tavily` | Lazily creates/caches the Tavily client and refreshes it when the API key changes. |
| `llm` | Lazily obtains the shared competitor chat model at temperature zero. |
| `llm_strong` | Compatibility/intent wrapper; currently returns the same shared model as `llm`. |
| `save_node_output` | Writes per-stage JSON diagnostics only when `EDRAK_DEBUG` is enabled, under the configured artifacts path. |
| `call_structured` | Requests JSON-schema structured model output, retries, attempts JSON recovery after a `None` response, optionally raises on failure. |
| `normalize_url` | Trims URL whitespace and trailing slashes. |
| `host_of` | Extracts a lower-cased URL hostname, returning an empty string for invalid input. |
| `host_matches` | Tests whether a hostname equals or is a subdomain of one of the supplied domains. |
| `result_text` | Combines search `content` and `raw_content`, then caps the text length. |
| `requirements_by_id` | Indexes current research requirements by their generated IDs. |
| `competitor_requirements` | Selects requirements belonging to one competitor. |
| `source_index_for` | Indexes source results by normalized URL for one competitor only. |
| `canonical_url` | Normalizes URL casing and removes fragments, query strings, schemes and `www` for citation matching. |
| `build_evidence` | Formats source text with stable `S1`, `S2`… labels and returns the label-to-source map for model citation. |
| `chunk_results` | Splits sources into batches no larger than the evidence character budget. |
| `resolve_source` | Resolves a model citation expressed as an `S#` ID or URL to a supplied source result. |
| `attach_evidence` | Converts valid model source citations into capped, deduplicated evidence records; ignores unresolved citations. |
| `normalize_confidence_value` | Converts supported numeric, percentage or named confidence representations to a normalized 0–1 value. |
| `source_evidence_score` | Estimates evidence reliability using official status, search score and available text; third-party sources remain eligible. |
| `finding_evidence_quality` | Labels a finding's evidence ownership mix: first-party, third-party, mixed or none. |
| `calculate_finding_confidence` | Calculates confidence from unique cited sources, allowing modest corroboration without letting count alone dominate. |
| `ensure_finding_confidence` | Preserves/limits supplied confidence against evidence and attaches evidence-quality classification. |
| `requirement_confidence` | Returns the strongest normalized confidence among findings for a requirement. |
| `requirement_evidence_quality` | Aggregates the evidence ownership quality for a requirement's findings. |
| `finding_view` | Produces a compact finding representation for prompt context, with bounded text. |
| `is_duplicate` | Determines whether two claims are sufficiently similar to merge. |
| `merge_findings` | Combines duplicate findings while preserving supported evidence and the stronger usable claim details. |
| `is_official` | Checks source ownership using the per-run official-domain registry in `sources.py`. |
| `identify_research_requirements_node` | Generates, validates, deduplicates and caps competitor-specific requirements; seeds typed metric requirements first. |
| `generate_queries_node` | Creates and validates stage-aware queries for new or unresolved requirements under the run budget. |
| `after_queries_router` | Routes to search when usable queries exist; otherwise skips to report generation. |
| `run_tavily` | Runs Tavily search for a query/stage, optionally scoped to official domains. |
| `search_node` | Executes queries, applies fallback search, sanitizes/deduplicates results, filters source quality, and records query/source state. |
| `synthesize_node` | Generates evidence-cited candidate findings from current-stage sources in bounded batches. |
| `grounding_problem` | Checks whether a proposed statement contains unsupported factual/number details relative to source text. |
| `verify_node` | Grounds, filters, scores and merges candidate findings into the verified set. |
| `check_research_requirements_node` | Marks requirement coverage and records missing information for targeted follow-up. |
| `prepare_next_stage_node` | Advances the research stage and initializes stage-local progress values. |
| `research_router` | Stops or continues based on gaps, stage/query bounds and minimum progress. |
| `consolidate_competitor` | Builds one evidence-backed profile and requirement summaries for one competitor. |
| `render_comparison_lines` | Renders the structured comparison portion of the report as Markdown lines. |
| `render_markdown` | Renders the overall final research report as Markdown. |
| `final_report_node` | Consolidates profiles and assembles report totals, requirements, profiles and unresolved gaps. |
| `comparison_node` | Builds evidence-constrained comparison dimensions and records comparison output/error in the report. |
| `_source_type_for` | Maps an evidence URL and official flag to a shared `SourceType`. |
| `_numeric_confidence` | Converts normalized/named confidence into a numeric value suitable for comparison metadata. |
| `build_worker_result` | Builds the shared result, findings/evidence links, comparison matrix, status, gaps and metadata. |
| `map_finding_category` | Maps worker competitive-area labels to shared `FindingCategory` values. |
| `map_confidence` | Backwards-compatible wrapper that normalizes numeric, percentage or named confidence to a 0–1 float. |
| `calculate_overall_confidence` | Aggregates finding confidence values into a worker-level confidence. |

### Discovery: `discovery.py`

| Function/type | Responsibility |
|---|---|
| `Candidate`, `Candidates` | Structured model response schema for candidate competitor names, websites, reasons and source numbers. |
| `CompetitorDiscoveryConfigurationError` | Signals missing search/model configuration for discovery. |
| `CompetitorDiscoveryServiceError` | Signals configured search/model service failures. |
| `_queries` | Produces at most four distinct discovery queries from company, offering, industry, market and description. |
| `web_search` | Searches with Tavily and falls back to Serper; returns normalized title/URL/snippet dictionaries. |
| `_prompt` | Constrains the discovery model to names and evidence present in supplied results. |
| `relevance_terms` | Extracts useful business terms for candidate-site relevance checks. |
| `_site_matches` | Checks that a candidate website loads and matches the candidate name and business context. |
| `_find_site` | Searches for a candidate's likely official website when no usable website was provided. |
| `_confirm` | Confirms/fetches a candidate website and extracts official social links from it. |
| `discover_competitors` | Orchestrates bounded search, source-grounded candidate selection, website validation, socials, confidence and evidence URLs. |

Discovery is also used by onboarding and other application flows; it is not
exclusive to the competitor worker. Candidate confidence is a discovery
heuristic, not a verified finding confidence.

### Source policy: `sources.py`

| Function | Responsibility |
|---|---|
| `compact` | Removes punctuation/spacing and lowercases text for resilient name matching. |
| `host_of` | Safely extracts a normalized hostname. |
| `domain_of` | Normalizes a URL or bare host to a hostname. |
| `_matches` | Checks domain equality/subdomain relation. |
| `register_official_domains` | Adds official domains to the process-local, lock-protected registry for a competitor. |
| `official_domains` | Returns sorted domains currently registered for a competitor. |
| `is_official` | Recognizes registered official domains; if none are registered, allows a conservative competitor-name host-label fallback. |
| `_names` | Deduplicates a competitor name and aliases case-insensitively. |
| `mentions` | Checks whether text contains the competitor name or alias despite punctuation/spacing differences. |
| `judge` | Accepts/rejects a search result based on risk, official ownership, source class, text length and competitor relevance; returns a reason. |
| `best_passage` | Selects sentences with the most lexical overlap with a claim, preserving original sentence order and enforcing a character cap. |

Official domains live in process memory and are registered from task-provided or
discovered websites. The fallback match is used only when there is no registered
domain for that competitor.

### Official social and homepage helpers: `socials.py`

| Function | Responsibility |
|---|---|
| `platform_of` | Identifies supported social platforms from URL hosts. |
| `normalise_profile` | Canonicalizes profile URLs and rejects posts, shares, search and non-profile paths. |
| `profiles_in_html` | Extracts the first valid linked profile per platform from one HTML page. |
| `_fetch` | Fetches HTML with the supplied HTTP client and returns bounded response text; HTTP failures are logged. |
| `find_official_socials` | Checks a company's homepage, then contact/about pages, for links to official profile pages. |
| `homepage_title` | Reads and normalizes the homepage `<title>` for competitor-site confirmation. |
| `homepage_text` | Extracts bounded homepage metadata and visible text for site/company relevance checks. |

Social links are discovered from company-owned sites, not from search-result
lookalike accounts. The competitor research source filter excludes social sites;
the links are retained as competitor metadata for other consumers.

### Prompt builders: `prompts.py`

| Function/object | Responsibility |
|---|---|
| `EVIDENCE_AND_ATTRIBUTION_RULES` | Shared rules for evidence-only claims, source attribution, release status, pricing and product ownership. |
| `STAGE_INSTRUCTIONS` | Stage-specific query guidance. The runtime strategy keys are defined by `STAGE_STRATEGY` in `state.py`. |
| `STAGE_SYNTH_GUIDANCE` | Stage-specific synthesis cautions against overclaiming. |
| `_company_context` | Selects the company profile and business context passed into prompts. |
| `requirements_prompt` | Builds the research-planning prompt for competitor-specific information needs. |
| `queries_prompt` | Builds stage-aware queries from target requirements, known findings, prior queries and competitor scope. |
| `synthesis_prompt` | Builds source-grounded synthesis instructions for a competitor's source batch. |
| `consolidation_prompt` | Builds the per-competitor profile/requirement consolidation prompt. |
| `comparison_prompt` | Builds the target-versus-competitor comparison prompt using dimensions and consolidated profiles. |

## Configuration and execution

The worker uses `TAVILY_API_KEY` for normal search, with Serper fallback when
configured. Competitor discovery requires a configured search provider and
`OLLAMA_API_KEY` (or the worker-specific `OLLAMA_API_KEY_COMPETITOR`) for
candidate evaluation. Model access is routed through EDRAK's shared
`get_chat_model` / `get_structured` helpers. API credentials should be provided
through the project environment configuration and must never be added to source
control.

Intermediate JSON and the optional Markdown report are written below the
configured artifacts directory only when `EDRAK_DEBUG` is enabled. Runtime
artifacts are diagnostics, not source files.

Example CLI invocation (from the backend environment):

```powershell
python -m edrak.agents.competitor_intelligence "Nile Ledger" `
  "How do we compare on SME invoicing?" `
  --competitors "Kanz Pay,Sada" --markets Egypt --offering "SME invoicing"
```

Omitting `--competitors` enables discovery. The CLI is implemented in
[`__main__.py`](__main__.py).
Its `main()` parses CLI arguments into `BusinessContext`, `CompanyProfile` and
`ResearchTask`, then calls `run_research`.

## Testing and change guide

The focused regression coverage is
[`tests/test_competitor_worker.py`](../../../../../tests/test_competitor_worker.py).
It covers:

- Official, low-value, social and off-topic source selection.
- Official-domain fallback and passage extraction.
- Social profile normalization and rejection of share/post URLs.
- Grounding of pricing/numeric claims and refusal of unsupported statements.
- Structured-output recovery and fatal model-provider errors.
- Stop conditions for stages, query budget and research progress.
- An offline end-to-end graph run, including shared result structure, evidence
  references, comparison matrix and bounded work.

When changing the worker, preserve the `ResearchTask` / `WorkerResult` boundary,
source provenance, bounded search loop and evidence referential integrity.
Update this guide when the graph sequence, externally visible metadata, bounds
or operational configuration changes.
