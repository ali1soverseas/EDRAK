# Assumptions

## Batch 0

- The repository structure changed: the worker lives in `backend/src/edrak/agents/customer_trends/`, imported as `edrak.agents.customer_trends`. The standalone `agents/customer_trends/` project from the first attempt is gone.
- `backend/pyproject.toml` was an empty placeholder, so it now defines the shared `edrak` project (src layout, hatchling) with the worker's dependencies. Other components add their own dependencies there.
- `make` targets, lint and mypy are scoped to the worker's paths so other components are not checked or reformatted. Run them from `backend/`.
- Tests live in `backend/tests/customer_trends/`, evals in `backend/evals/customer_trends/`, scripts in `scripts/customer_trends/`, config YAML in the package's `config/`.
- The repository `.gitignore` ignores `docs/`. The worker docs directory is re-included, and only `docs/SPEC.md` is ignored, at the owner's request.
- The worker settings in `.env.example` are appended to the existing root file. The shared environment name is `ENV` there (it was `EDRAK_ENV` before Batch 9 merged `develop`), so `Settings` reads `EDRAK_ENV` or `ENV`, and the spec's duplicate line is omitted.

## Batch 1

- `Settings` ignores empty values (`env_ignore_empty`), so `KEY=` lines in `.env.example` fall back to defaults or `None`.
- The environment name is the shared `ENV` key (`EDRAK_ENV` also works and wins); `prod` or `production` selects JSON logs, anything else console logs.
- `structured_call`, `invoke_with_retry` and the fake model are async, matching the async-first rule (SPEC section 4). The smoke script wraps them with `asyncio.run`.
- `structured_call` asks for `include_raw=True` so the repair attempt can show the model its own invalid output.
- `ScriptedChatModel.bind_tools` returns the same instance, so the script cursor and recorded calls are shared across bound copies.
- The worker's `tests/**` lint config also ignores S105 and S106 (fake secrets in assertions), in addition to S101.
- The lint paths in `backend/Makefile` include `../scripts/customer_trends`.
- Against the live endpoint (`gpt-oss:120b` on Ollama Cloud) the `format` schema constraint is not reliably honored, so `structured_call` also states the JSON schema in a closing user message. With that, the smoke script passes all three checks.

## Batch 2

- Evidence ids are unique per run, so `EvidenceStore.get_items` and `existing_ids` take a `run_id` (the spec sketch lists only ids).
- `add_batch` stamps `run_id`, `task_id` and `batch_id` on every item it stores, because providers build items before a batch exists. A batch always gets a row, even when empty, so trend series and summaries can hang off it. `batch_id` in a tool response is exposed only when something was stored.
- `Finding` applies only the store-free rules: distinct evidence ids, and fewer than two forces `low` plus the `single_source` caveat. It does not require non-empty `evidence_ids`; `submit_findings` (Batch 6) rejects that case with a reason, so one bad finding does not invalidate a whole model draft.
- `ToolResponse.preview` is coerced (first five items, snippets cut to 200 characters), not rejected, so a tool can never fail on it.
- `EvidenceItem.engagement_total` sums interaction counts and leaves out `views` and `rating`. It drives the `top` sample and `min_engagement`.
- `id` (16 hex) and `content_hash` (40 hex) are enforced on `EvidenceItem`. `run_id` must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` because it becomes a directory name in the sink.
- Datetimes are normalized to UTC; a naive datetime is taken as UTC. Date filters (`since`, `until`) use `published_at`, so undated items never match them.
- `TaskBrief.focus` defaults to empty: `intake` fills it from `use_cases.yaml`.
- Models added beyond the spec's list, each needed by the store: `MetricResult` (what `save_metric` stores), `EvidenceFilters`, `QueryResult`, `RunSummary`, `ReviewTarget`, `ReviewStore`, `Sentiment`.
- `ThemeAggregate` had no quotes field (SPEC 6.6); Batch 6 added `representative_quotes` (see DEVIATIONS).
- `find_numbers` skips digits glued to a Latin letter (Q3, B2B, GPT4) and treats years as ordinary numbers; `submit_findings` exempts whole numbers from 1900 to 2100 from the claim check (Batch 6).
- `detect_language` returns `None` for texts with fewer than five letters (outside the Arabic script rule) and for short text where langdetect is under 90 percent sure. Arabic-dominant short text is `ar`.
- `langdetect` ships without type stubs: `backend/pyproject.toml` has a mypy override for it.
- Search for `text_contains` compares normalized, case-folded text, so Arabic diacritics and case do not matter.

## Batch 3

- `mcp_servers/web_server.py` and `backend/src/edrak/mcp/` are still empty, so Serper is the local adapter from SPEC 8.4. It sits behind the `Provider` interface, so Batch 5 can swap in the shared MCP client without touching tools.
- Providers are keyed by name in `config/providers.yaml`; the YouTube provider is `youtube_api` (as in SPEC 8.2), and a `direct_http` entry exists for `fetch_page`, which Batch 5 implements.
- Providers receive a plain dict validated by `CallParams`; `run_id` and `task_id` are required keys. They stamp `batch_id="pending"` on evidence, and the evidence store assigns the real batch id.
- Evidence ids come from the platform and the canonical URL, not from the provider, so the same post found through two providers gets one id.
- `BudgetTracker` counts one tool call per `ProviderRegistry.call`, including failed ones, so a model that keeps calling a failing tool still hits the limit. The registry checks the budget before routing; Batch 5 does not need a second check.
- The registry never raises for a provider failure except `ProviderExhausted` (and `BudgetExceeded`); tools turn those into `ToolResponse` errors in Batch 5.
- The YouTube API key is sent in the `X-Goog-Api-Key` header, not the query string, so it never appears in URLs or logs.
- YouTube quota resets at midnight Pacific time; the counter uses that day, falling back to UTC when the time zone database is missing.
- The Batch 3 `config/providers.yaml` listed Apify actors and SocialCrawl endpoints as placeholders; Batch 4 replaced them with verified values (see below).
- GDELT volume by day is computed from the returned articles (at most `maxrecords`), not from the timeline API, and is labelled `volume_basis: returned_articles`.
- Serper dates are free text; unparseable dates become `None`. Relative dates ("3 days ago") are resolved against the provider clock.
- Live check of GDELT from the development machine: a single request with the documented parameters returns 200, but the service often answers 429 ("limit requests to one every 5 seconds") even when requests are well over five seconds apart, and it can stay that way for minutes. The adapter spaces requests by at least five seconds and retries 429 three times, then reports `ProviderRateLimited`. `news:gdelt` has no fallback in the routing table, so a throttled run records a gap. `smoke_providers.py` reports this as FAIL.
- Live check with real keys (Serper, YouTube): web, news, Google News, the `site:` social fallback and YouTube search and comments all returned real results. Serper localizes its `date` field to the `hl` language, which is the first requested language, so with the default `["ar", "en"]` dates arrive in Arabic ("قبل 6 ساعات", "29/08/2025" with direction marks). `parse_serper_date` reads English and Arabic relative, written-out and numeric day/month/year forms; other languages give `None` rather than a guess. Many web results carry no date at all.

## Batch 4

Verification (2026-10-06, Apify FREE plan, SocialCrawl 100 free credits):

- Every Apify actor id in providers.yaml was checked against its live build (`GET /v2/acts/{id}/builds/default`) for input fields and enums, and run once with at most 10 items to capture real output. The captures became the fixtures, with authors, URLs and texts replaced. Inputs that were run through the provider itself against the live API: X search, App Store reviews and Google Trends. The other actors were run with equivalent hand-built inputs; the templates are covered by tests that assert the exact input they produce.
- Every SocialCrawl endpoint in providers.yaml (six searches, six comment endpoints) was called live once, and the paths, parameters and response shapes come from its published `llms.txt` and per-platform docs. The balance endpoint, relevance filter and `since:`/`until:` operators for X were also exercised.
- Not verified, and flagged where they live:
  - SocialCrawl's USD price per credit was not published on the pages read; the later pricing page gave it (see "Cost-based routing"), and the config now uses 0.008.
- Apify's synchronous route answers 201 and returns the dataset as a JSON list. Runs that time out or fail are not retried (a retry would run, and bill, the actor again); 429 and 5xx are.
- The HTTP layer now waits at least `Retry-After` (up to a minute) after a 429 and accepts a per-request timeout; Apify requests use the actor timeout plus 30 seconds.
- Naive timestamps from providers are read as UTC (a test caught them being read as local time).
- `detect_language` returns `None` for text under 15 letters unless it is mostly Arabic, because langdetect answered confidently and wrongly for strings such as "Great product" (Romanian) and "bad" (Somali).
- `CallParams` gained `keywords`, `timeframe`, `target` and `country` for the demand and review capabilities.
- SocialCrawl search sends `relevance=filter` and drops rows whose detected language is not requested (rows with no detected language are kept); both are config options. `raw_count` is the number of rows fetched before filtering, so the caller can tell how much was dropped.
- SocialCrawl also serves Google Trends and the three review stores, and Apify's Google Trends actor was slow in testing. SPEC 8.2 routes those capabilities to Apify only, so SocialCrawl was not added to them. It would be a one-line routing change if you want it as a fallback.
- `fallback_used` is true whenever the serving provider is not first in the configured order, so a Facebook search served by SocialCrawl counts as a fallback because the Apify entry is switched off.
- The provider layer never spends money on its own: the smoke script is the only code that calls Apify live, and it runs the cheapest actor for three items.

## Batch 5

- `mcp_servers/web_server.py` and `backend/src/edrak/mcp/` are still empty, so `web_search` uses the local Serper adapter and `fetch_page` the local direct fetcher. Both sit behind the `Provider` interface, so moving to the shared MCP client later changes providers, not tools.
- `fetch_page` is served by a `direct_http` provider (`providers/direct_http.py`, capability `fetch_page`, the routing entry that already existed), so it shares the registry's budget, breaker, cost and fixture-mode handling with every other tool. Pages that cannot be read (robots.txt, 4xx, not a text page, under 30 characters of main text) return an empty result with a warning instead of raising, so refusals never open the provider's circuit breaker. Rate limits (429), 5xx, timeouts and connection errors are provider errors.
- robots.txt follows RFC 9309: a missing file (4xx) allows everything; a 5xx or unreachable file disallows everything. Rules are read once per site per provider instance.
- The page text is capped at 2 MB downloaded and `max_chars` kept (500 to 50,000, default 20,000). Its evidence id is built from `fetch|<canonical url>`, so a full page and a search snippet of the same URL are separate evidence.
- The private-host check for `fetch_page` looks at names and literal IP addresses only. A public name that resolves to a private address, or a redirect to one, is not caught.
- `ToolContext` has the fields the batch asked for (the registry is named `providers`) plus `defaults` (brief values the wrapper fills in when the model omits them) and `branch` (recorded on events). Batch 7 `intake` fills `defaults` from the brief. The budget is checked once, inside `ProviderRegistry.call`, which also counts the tool call and cost; `execute_collection` does not check it again.
- Default results per call when the model gives no `max_results`: web 10 (its `num`), social 30, reviews 30, news 25, all capped by depth.
- `social_comments` has an extra `sort` argument (default `top`), which the SPEC does not list; top comments are more informative than the newest.
- `search_interest`: `granularity` is accepted as a hint (Google picks the resolution), `include_related=false` empties the stored related queries, and the timeframe must look like a Google Trends period. Its `max_results` is the number of keywords.
- `news_coverage` computes the volume by day from the items when the provider gives none (Google News). `fetch_page`, `search_interest` and the news batch keep provider metadata in the batch meta.
- A call whose items were all stored earlier still creates an empty batch row in the store (`add_batch` always creates one); the response reports `batch_id: null` and `count: 0`.
- Coverage by platform leaves out items without a platform (articles, news, reviews, trend points); they appear under `source_types`.
- Tool arguments are validated by the worker, not by LangChain: `args_schema` is a plain JSON schema, so a malformed call comes back as an `invalid_input` response the model can read, never as an exception.
- Live check with the real keys (2026-10-06): `web_search` (web and news), `fetch_page` (a GitLab blog page; a Reddit URL refused), `social_comments` (YouTube), `reviews_fetch` (App Store) and `search_interest` all ran through `build_tools` and stored evidence; each response was under 2.2 KB.

## Fallback keys (between batches 5 and 6)

- Four Apify tokens and four SocialCrawl keys are configured (the first and three fallbacks each), all valid on 2026-10-06: separate FREE Apify accounts with 5 USD of monthly credit each, and SocialCrawl balances of 74 and three times 100. They live in the untracked `.env`; `.env.example` has empty entries.
- Rotation is sticky and one-way for the life of the provider object (one run): a key that ran out is not retried, and a new run starts again from the first key. A key that recovers (a monthly reset) is picked up by the next run.
- Both rejected keys (401, 403) and exhausted ones (402, or a SocialCrawl balance below the cost of the request) rotate. A 403 that only means one actor is not allowed would also rotate; with equivalent accounts this costs a retry, not a failure.
- Rotation is verified live: a dead primary key for each service fell over to the first real fallback and returned results.
- The Apify and SocialCrawl budgets of the run (`max_cost_usd`) are not multiplied by the number of keys; the budget counts spend per run, not per account.

## Cost-based routing (between batches 5 and 6)

- SocialCrawl's price per credit, read from its pricing page (shown in GBP): Starter 2,500 credits for 15 pounds, Growth 20,000 for 49, Pro 150,000 for 299, which is 0.006, 0.00245 and 0.002 pounds a credit. At an assumed 1.35 USD per pound that is 0.008, 0.0033 and 0.0027 USD. `usd_per_credit` is 0.0033 (Growth); it replaces the earlier guess of 0.001. Credits never expire; each key gets 100 free credits once.
- Live comparison on 2026-10-06 (same queries on both providers): X search equal quality (both 100 percent on topic, dated, with counts); TikTok search Apify 217 seconds and 48 percent on topic against SocialCrawl 6 seconds; Reddit search Apify timed out, SocialCrawl 20 posts in 7 seconds; Instagram search SocialCrawl undated, Apify dated; Instagram comments identical fields on both. Yields used in the cost model (`expected_yield`) are what those calls returned after SocialCrawl's relevance and language filters: X 0.6, TikTok 0.45, Instagram 0.5, Facebook 0.5, YouTube 0.7, Reddit 0.35.
- SocialCrawl's TikTok search returns no rows (and still bills one credit) when `sort_by` is combined with `relevance=filter`, so TikTok search sends no sort. Recency and popularity sorting for TikTok are therefore not available through SocialCrawl.
- `search_interest` through SocialCrawl uses `/google_trends/explore` (5 credits per call for up to 5 keywords, one shared scale). It supports the eight preset timeframes only; a custom date range or `all` is refused so Apify serves it. It returns no related queries (SocialCrawl sells those separately at 5 credits per keyword). Its first point is often `null` (no data) and is skipped.
- Not done: SocialCrawl review endpoints (Amazon would be cheaper than Apify's actor, app stores dearer), a Facebook keyword-search actor on Apify, and automatic re-ordering of providers at run time by measured spend. The order is fixed in config and checked by a test against the cost model.
- The cost model counts what one capability costs for 100 usable items. It ignores Apify run start fees except where the actor lists one, SocialCrawl cache hits (free) and the one-off free credits.
- Only free allowances are used; no plan or credit pack will be bought. A SocialCrawl credit is therefore priced at the cheapest pack (0.008 USD) instead of the Growth price: free credits never renew, Apify's monthly 5 USD does. This moved Apify ahead for X and Instagram comments, where the two cost about the same, and keeps the credits for TikTok, Reddit and Facebook. Estimated costs, and the per-run cost budget, are counted at this price. It supersedes the 0.0033 figure above.

## Batch 6

- "Retry a failed chunk once through `structured_call`" is read as `structured_call`'s own single repair attempt (the invalid answer and its error are sent back once). A chunk is therefore tried at most twice before it is skipped. A transient transport error is retried by the model client first (three attempts); a chunk that still cannot be reached is skipped like an invalid one. Any other exception is a bug and is reported as `tool_error`.
- Chunks are labelled one after another, not in parallel: a run of 300 items is 12 model calls. Concurrency is a Batch 9 tuning question once live latency is measured.
- `ThemeAggregate` gained `representative_quotes` (up to 3, each at most 240 characters). SPEC 6.6 puts quotes on `Theme` only, but `Theme` is never stored and the batch asks for quotes saved with the aggregates. Recorded in DEVIATIONS.
- `save_aggregates` replaces the run's aggregates (Batch 2), so `analyze_text` is meant to be called once over all batches. A later call replaces the earlier themes and warns; a call that yields no themes leaves them untouched.
- `analyze_text` and the metrics skip `trend_point` items; `evidence_query` does not.
- `recent_growth` needs at least 6 dated analyzed items and at least 3 items in the theme. The batch does not ask for these minimums; without them a two-item theme reports growth of plus or minus 100 percent or more, which a writer would be tempted to quote.
- Theme `share` counts the items the model labelled (analyzed), not the items selected: an item in a skipped chunk or one the model left out is not in the denominator.
- A theme label found by `evidence_query(theme=...)` is matched by the same normalization as the merge step, so plural, case and punctuation differences do not matter.
- `submit_findings` checks claim numbers against the finding's own `metrics` as the batch specifies. A model can still write a made-up number into both claim and metrics; only a cited `metric_id` ties a number to computed data. The verification stage (a later batch) is where self-reported metrics should be cross-checked.
- Year-like numbers (whole numbers from 1900 to 2100) in a claim are not checked, so a claim can say "in 2026" without a metric. The same rule lets a claim state "2000 posts" without a metric; the other checks (evidence ids, verdict phrases) still apply.
- The verdict phrase list is deliberately conservative (phrases that only make sense as advice, such as "should enter" or "do not launch"). Words like "recommend" alone are not listed because users recommend things in reviews and a claim may report that.
- `compute_metrics` and `analyze_text` read the whole run when `batch_ids` is left out (compute_metrics) or must be given (analyze_text); an id that is not a batch of the run is an `unknown_batch` error, not silently ignored.
- Processing tools do not count against the run's tool-call budget: only provider calls do (the budget tracks provider spend and time). The analyst calls are not costed (Ollama Cloud, no per-call price).

## Batch 7

- The shared contracts reached this branch when `develop` was merged in Batch 9, and the adapters and `run_worker` were built then (see Batch 9). Notes from reading them, which shaped the adapters:
  - `ResearchTask` has `goal`, `focus` (both free text), `company_profile` (name, aliases, products), `business_context` (use case, `targets`, `focus_areas`, `time_window_days`, constraints) and `attempt`. It has no entity, competitors, geo, languages, market, depth or budget: entity comes from the company profile, competitors from `targets`, `since` from `time_window_days`, the rest from defaults or the task text. Its `UseCase` value for market entry is `market_entry_expansion`; this worker's is `market_entry`.
  - `Finding` there has `finding_id`, `statement`, `category` (a different enum from this worker's `FindingType`), `evidence_refs` with a relation, a float `confidence` and `limitations`. `Evidence` needs `extracted_fact` and uses another set of source types. `WorkerStatus` is completed, partial, no_evidence or failed; `WorkerResult.confidence` is a float.
  - `WorkerResult` checks that every evidence reference resolves inside the result, so the adapter carries the cited evidence in compact form (a fact and a short excerpt), not the stored text.
- `intake` accepts a `TaskBrief`; a `ResearchTask` is converted before the graph starts (`brief_from_task`, Batch 9).
- The collection branches use `langchain.agents.create_agent`, because LangGraph's `create_react_agent` is marked deprecated in the installed version. This adds one dependency (`langchain`). The step cap is a model call limit that ends the run gracefully instead of raising, so a branch keeps what it collected.
- State values are plain JSON, not pydantic objects: the SQLite checkpointer logs that unregistered classes "will be blocked in a future version". State fields beyond SPEC section 10: `branch_errors`, `metric_ids` and `warnings`. `gaps` hold gap records (id, severity, description, suggested action) rather than strings, because the replan needs the severity and the suggested action.
- `gaps` has the union reducer the batch asks for, and `gap_check` overwrites it with `Overwrite` so that a gap which has been closed is removed. A failing branch adds its own gap to the state and announces it; `gap_check` rebuilds the same gap from `branch_errors`.
- The platform gap counts social platforms only (as in SPEC section 10), so news, web and review items do not count toward "2 platforms with 20 items".
- Severities: a branch error, too little evidence and too few platforms are critical. A missing trend series is critical when the use case requires one and minor when only `demand` is in the focus. Reviews follow the use case rule (critical for competitive intelligence and product launch, not required for market entry) and apply only when review targets were planned, because "review stores apply" is decided by the plan. A thin language is minor.
- Status: `insufficient` when the evidence total gap is open, `partial` when another critical gap is open, else `complete`. SPEC says "complete if no critical gaps, partial if gaps remain": minor gaps are listed in the result but do not lower the status.
- `analyze` reads the social and review batches only (as specified); news and web snippets from the demand branch are counted in the metrics but not themed. `analyze_text` is capped at 50, 200 or 400 items for light, standard and deep, which keeps a run to at most 16 sequential model calls.
- `QueryPlan` keeps its limit of five trend keywords. The planner answers with `PlanDraft` (same shape, no limit) and a sixth keyword is trimmed instead of failing the answer.
- Added prompts beyond the list in the batch: `REPLAN_NOTE` (the second-pass text inserted into `PLAN_QUERIES`) and `WRITE_HEADLINE` (the headline call). Added event field `batch_id` to `tool_called` so a branch can name the batches it created even when it stops early.
- `EvidenceStore.count_by` gained `exclude_source_type`, used to leave trend point sentences out of coverage counts.
- `run_task` and `stream_task` take an extra `settings` argument (data and artifact directories); the store is opened from it. Given `providers` bring their own `BudgetTracker` and `CircuitBreaker`, which the run then uses, so a test controls the budget through the registry it passes. `brief.budget.max_seconds` is the wall-clock limit of the whole graph.
- Resume: a run whose checkpoint is unfinished continues from it when started again with the same `run_id`; one that finished returns its stored result. The branch conversations are not checkpointed, so a branch that was interrupted starts again (items already stored are reported as duplicates).
- A run that fails inside the graph (for example the sink cannot write) returns a result built from the store with the gap `run_failed`, and tries to write it. If that write fails too, the result is returned and the failure is logged.
- The overall confidence rule (high when half the findings are high and no critical gap is open) is this worker's own convention; SPEC does not define it.
- Not done in Batch 7: parallel chunk labelling in `analyze_text` (still sequential, see Batch 6), live runs against the real model and providers (opt-in live tests, Batch 9). The scripted models of the tests are test helpers; the demo script (Batch 8) is the one shipped.

## Batch 8

- The sample briefs moved from `tests/customer_trends/fixtures/` to `evals/customer_trends/briefs/`, where SPEC section 3 puts them; the tests read them from there (`factories.BRIEFS`), so the UI presets and the tests use the same files. The GitLab pilot is first.
- Fixture mode used for runs (`--fixture`, the UI toggle, `EDRAK_PROVIDER_MODE=fixture`) reads `evals/customer_trends/fixtures/providers/`, richer files than the three-item recordings in `tests/customer_trends/fixtures/providers/`, which the registry tests depend on. `EDRAK_FIXTURES_DIR` points it elsewhere. The registry's own default is unchanged.
- The demo fixtures are synthetic: eight product-neutral phrases in English and Arabic (`llm/demo_script.py`), cycled over posts, reviews and news by `scripts/customer_trends/build_demo_fixtures.py`, and a test keeps the files equal to the script's output. They serve every demo brief the same posts, so a demo run of the product launch brief finds the same themes as the GitLab one; the numbers it states are the demo's, not a market's. Evidence from a fixture run is marked `is_synthetic` in the shared `WorkerResult`.
- The demo model answers each role from the prompt it gets (a plan from the brief, labels by phrase, findings from the aggregates and metrics in the writer's context). It is not a model of anything: it exists to run the whole graph with no keys.
- The CLI keeps the structured log off the terminal unless `--verbose`, so the event lines stay readable; the log level otherwise follows `EDRAK_LOG_LEVEL`. `run` refuses to start without `OLLAMA_API_KEY` unless `--fake-llm` is given, rather than run a graph that cannot plan.
- A UI run always gets a free run id (`run-id-2` when `run-id` was used), because running a finished run id returns its stored result instead of running. Stop cancels the run without writing a result.
- The UI shows the shared `WorkerResult` by building a stand-in `ResearchTask` from the brief (`parent_request_id` `developer-ui`). It is a display aid, not what the orchestrator will send.
- The UI uses Streamlit's own tables and charts on plain lists of dicts, not pandas frames, and a `streamlit.testing.v1.AppTest` smoke test drives it in fixture and fake-LLM mode. `make ui` was started once in that mode and answered its health check with no error in its log.

## Batch 9

- The shared contracts came from merging `origin/develop` into this branch (a merge commit; no history was rewritten, nothing was pushed to another branch). The contract files are untouched. The merge also brought in the orchestrator, which imports `langchain_openai`; that package is not a dependency of this project, so the orchestrator's dispatch function is not imported by the tests. The tests do what it does with a worker (`WorkerResult.model_validate(worker.run(task))`) through the shared `WorkerRegistry`.
- Adapters: `brief_from_task` (entity is the company name, competitors the targets, `since` from `time_window_days`, the country read from a short list of country names in the task text, the focus from its words) and `to_worker_result` (see ARCHITECTURE, "Mapping to the shared contracts"). Evidence from social posts, comments and trend points maps to the shared source type `other`, because the shared enum has no social type; the platform and engagement travel in `metadata`. That is a point for the contracts owner.
- `run_id` of a task from the orchestrator is `<task_id>.a<attempt>`: a retry is a new run, the same attempt is idempotent. `run_worker` is synchronous for the orchestrator's thread pool and works from a thread inside a running event loop; `arun_worker` is its async twin. Neither raises.
- The shared `WorkerStatus` is derived: `completed` for complete, `no_evidence` only when there is neither evidence nor a finding, `failed` only when the run crashed before it had findings, `partial` otherwise. A `failed` or `no_evidence` result carries no confidence.
- Hardening: an item text over 5,000 characters is cut with a note (`metadata.text_cut_from`) and a warning, the content hash stays that of the whole text; each collection branch has a wall-clock limit (`BRANCH_TIMEOUT_S`, 100 s) because LangGraph turns a cancelled agent into a normal end, so the limit is checked after the call; `ProviderRegistry.health()` reports capabilities, breaker state and what a provider knows of its own keys and quota; every log line carries `run_id` and `task_id`, node lines `node`, tool lines `tool`, provider lines `provider`.
- The evidence store opens a new file safely when several runs start at once (a test found two runs colliding on the schema). A rerun after the checkpoints were deleted collects only duplicates, so `analyze` falls back to the run's own batches rather than skip the analysis.
- The cost totals in the provenance (`provider_calls`, `cost_usd`) are what the tool events report, and equal the budget tracker's snapshot, which the provenance repeats under `budget`. The Ollama calls are not priced.
- The live tests were written and skip cleanly without keys; they were not run with the real keys while this was written, because they spend free-plan credits.
- The `evals` checks read checkpoints with the synchronous SQLite saver and compare only the top graph (the branch conversations are not checkpointed).

## After the first live run

- The first live run (`run-ci-gitlab-001`, 2026-10-07) found: X search returned nothing on every call because the first Apify token had used up `apidojo/tweet-scraper`'s monthly runs for a free account (the actor answers with one `{"noResults": true}` row). Measured on two tokens on 2026-10-07: the first 5 runs of the month work and every later one is refused, so a free account has 5 X runs a month and four accounts have 20; the other tokens still had their runs. The actor does apply `start` and `end` (10 of 10 rows inside the window). A placeholder-only answer is now checked against the run log of the account's latest run of that actor. When it says the monthly limit was exceeded the provider raises `ActorLimitReached` (a `ProviderQuotaExceeded`), the key ring moves to the next token, and when every token is over the limit the actor's capabilities are left out for the rest of the run, so routing goes on to SocialCrawl without opening Apify's breaker. A placeholder with no such message is an empty result. The memory is per run: a new run asks the actor again (one refused run per token, no credit).
- An actor entry can have its own `timeout_s`. `search_interest` has 60 (the actor took 80 s in one run and timed out at 240 in another), so the demand branch, which has 100 s, gets SocialCrawl's answer (about 20 s) instead of timing out. A real Apify answer between 60 and 80 s is therefore given up; the other order costs 5 SocialCrawl credits per call from a pool that does not renew.
- The provenance keeps every tool call (`calls`, at most 200) so a stored run can be read without the UI; before, only totals survived the run.
- A finding that lists `related_gaps` is at most `medium`: the writer prompt already said to lower confidence for open gaps, and the run produced a `high` finding that listed two. SPEC's rule (10 ids, 2 platforms or source types) is unchanged and still applies first.
- The second planning pass is given the queries of the first, so "do not repeat queries that already ran" can be followed. The planner is asked for queries of 2 to 4 words; the run's long phrases returned 0 to 1 items on Reddit and X.
- The writer prompt says what `recent_growth` and `share_of_voice` measure (the collected sample, not search interest or market share). The run wrote "interest rising rapidly" from a theme's growth.
- An empty collection answer carries a hint to use shorter words or another platform; the model repeated empty X queries until it reached its step cap.

## After the second live run

- The second live run (`run-ci-gitlab-001-2`) ended at its 300 s limit with 84 evidence items and no findings: pass one took about 165 s (planning, 100 s of collection, analysis), `gap_check` replanned because time was not yet used up, and the second pass plus its analysis used the rest, so `write_findings` never ran. The last 30 percent of `max_seconds` is now kept for analysis and writing: a branch gets the smaller of `BRANCH_TIMEOUT_S` and the collection time left, and a replan needs at least 30 s of it. With the default 300 s the second pass gets about 45 s.
- 5 of the 21 tool calls failed validation because the model sent `geo: ""` or `granularity: ""` for fields it meant to leave out. An empty or blank string, or null, is now treated as not given before validation, so the brief's default applies.
- The call log keeps the error message of a failed call (`message`).
- Instagram search takes its hashtag page from the first planned hashtag, else from the query run together; before, the query and every hashtag were joined into one meaningless tag. Hashtags make no difference on Reddit search (2 items with and without one, checked on 2026-10-07).
- Measured: the Apify search interest actor did not finish within 60 s in either pass, so every demand branch pays 60 s before SocialCrawl answers (about 10 s when the key has credits). Reddit through SocialCrawl costs 4 to 5 credits a call for 2 to 18 items.
