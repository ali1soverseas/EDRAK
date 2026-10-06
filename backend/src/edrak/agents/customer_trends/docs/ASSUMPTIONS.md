# Assumptions

## Batch 1

- The repository structure changed: the worker lives in `backend/src/edrak/agents/customer_trends/`, imported as `edrak.agents.customer_trends`. The standalone `agents/customer_trends/` project from the first attempt is gone.
- `backend/pyproject.toml` was an empty placeholder, so it now defines the shared `edrak` project (src layout, hatchling) with the worker's dependencies. Other components add their own dependencies there.
- `make` targets, lint and mypy are scoped to the worker's paths so other components are not checked or reformatted. Run them from `backend/`.
- Tests live in `backend/tests/customer_trends/`, evals in `backend/evals/customer_trends/`, scripts in `scripts/customer_trends/`, config YAML in the package's `config/`.
- The repository `.gitignore` ignores `docs/`. The worker docs directory is re-included, and only `docs/SPEC.md` is ignored, at the owner's request.
- The worker settings in `.env.example` are appended to the existing root file. `EDRAK_ENV` already exists there, so the spec's duplicate line is omitted.

## Batch 1

- `Settings` ignores empty values (`env_ignore_empty`), so `KEY=` lines in `.env.example` fall back to defaults or `None`.
- `EDRAK_ENV` is the shared key (`development` in the root `.env.example`); `prod` or `production` selects JSON logs, anything else console logs.
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
- `ThemeAggregate` has no quotes field (SPEC 6.6), but `Theme` does. Batch 6 has to decide where `analyze_text` keeps representative quotes (for example as a second stored record).
- `find_numbers` skips digits glued to a Latin letter (Q3, B2B, GPT4) and treats years as ordinary numbers. Batch 6 can exempt years from the claim-versus-metrics rule if findings need them.
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
  - SocialCrawl's USD price per credit is not published on the pages read; `usd_per_credit: 0.001` is an assumption (the free tier is 100 credits).
  - Apify prices are the FREE-plan pay-per-event rates read from each actor on that day and can change.
  - `social_search:facebook` through Apify: switched off (see DEVIATIONS.md). Candidates seen: `apify/facebook-posts-scraper` (page URLs only), `powerai/facebook-post-search-scraper` (no results for "GitLab" or "coffee", charges a 9 cent start fee, needs `maxResults` of at least 10).
  - The Google Trends API alpha stub does not call anything. Apify's Google Trends actor is a browser scraper: one live run took 80 to 95 seconds and an earlier one timed out at 240.
  - The Reddit actor (`trudax/reddit-scraper-lite`) returns no vote or comment counts, so Reddit evidence from Apify has no engagement. SocialCrawl returns them.
  - Instagram has no keyword search: the query is turned into one hashtag (letters and digits only).
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

