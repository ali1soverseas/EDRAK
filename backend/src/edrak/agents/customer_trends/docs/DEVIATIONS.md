# Deviations from SPEC

SPEC.md was aligned with the repository conventions (AGENTS.md, CONTRIBUTING.md) before implementation started. Open points that still need a team decision are listed in SPEC section 15; their status after Batch 9 is at the end of this file.

## Batch 3

- SPEC 8.2 routes `social_comments` for x, tiktok, instagram and facebook to apify, socialcrawl and serper. Serper can only run a `site:` search and cannot fetch the comments of one post, so `social_comments:*` is routed to apify and socialcrawl only. Reddit comments follow the same rule.

## Batch 4

- SPEC 8.3 lists `apify/facebook-posts-scraper` for Facebook. That actor reads the posts of one page URL and cannot search by keyword, and the one keyword-search actor tried (`powerai/facebook-post-search-scraper`) returned no items for two queries while charging its start fee. `social_search:facebook` is therefore switched off for Apify (`enabled: false` in providers.yaml, mapper kept) and served by SocialCrawl, then Serper.
- SPEC 8.3 lists a single `apify/instagram-scraper` for Instagram. Its keyword `search` returns hashtag pages, not posts, so Instagram search reads the posts of the hashtag made from the query. Comments use `apify/instagram-comment-scraper`.
- SPEC 8.3 lists `streamers/youtube-scraper` and `clockworks/tiktok-scraper` for comments too. Comments use the vendors' dedicated comment actors (`streamers/youtube-comments-scraper`, `clockworks/tiktok-comments-scraper`, `apify/facebook-comments-scraper`), which are cheaper per item.

## Batch 5

- SPEC section 9 gives `social_comments(platform, post_url, + RequestBase)`. A `sort` argument (`top` or `recent`, default `top`) was added, because the providers can order comments and the most-liked ones are the most useful default.

## Cost-based routing

- SPEC 8.2 lists Apify first for social search and comments, and routes `search_interest` to Apify and the Google Trends API only. Measured costs and speed (see ARCHITECTURE.md, Routing and cost) put SocialCrawl first for TikTok, Instagram, Reddit and Facebook search and for every comments capability except YouTube, and YouTube's API, then SocialCrawl, then Apify for YouTube. `search_interest` gains SocialCrawl as a second provider between Apify and the stub. X search keeps Apify first.

## Batch 6

- SPEC 6.6 defines `ThemeAggregate` without quotes. It now has `representative_quotes` (default empty), so quotes are saved with the aggregates and reach the final result. The shared contract is not touched.
- SPEC 6.7 and the batch ask for a loose numeric match of 1 percent or 0.5 absolute. A flat 0.5 lets 0.3 match 0.7 for shares and rates, so the absolute part is the rounding of the number as written: 0.5 for a whole number (12 matches 12.4 but not 12.6), 0.05 for one decimal, 0.005 for two. The 1 percent relative match is unchanged. Whole numbers behave as specified.
- SPEC section 9 gives `compute_metrics(metric, batch_ids, params={})`. `batch_ids` is optional here (default: every batch of the run) and `params` takes a `filters` entry for the evidence metrics.

## Batch 7

- SPEC section 10 lists `gaps: list[str]` in the state. Gaps are records with a severity and a suggested action, held as JSON dicts, because the replan edge and the replan prompt need both. The result's `gaps` field is still a list of strings.
- SPEC section 10 names `plan: QueryPlan | None` and `brief: TaskBrief` in the state. They are stored as JSON dicts (see ASSUMPTIONS, Batch 7). Three fields were added: `branch_errors`, `metric_ids`, `warnings`.
- SPEC section 10 says "bounded tool-using sub-agents" without naming a builder. `langchain.agents.create_agent` is used and `langchain` was added to `pyproject.toml`, because `langgraph.prebuilt.create_react_agent` is deprecated in the installed version.
- SPEC section 11 requires `run_worker` and the shared adapters. They were pending while the contracts were empty in this branch and were built in Batch 9 (below).
- `tool_called` events gained a `batch_id` field, and `run_task` / `stream_task` a `settings` argument; neither is in SPEC section 11.

## Batch 8

- SPEC section 3 names `ui/components/` files "form, progress, findings, themes, trends, evidence, raw". They are four modules by what they do, not by tab: `forms.py` (presets, parsing, validation), `format.py` (pure formatters and table builders), `controller.py` (the background run) and `views.py` (all the Streamlit layout).
- SPEC section 8.1 says fixture mode serves recordings from `tests/customer_trends/fixtures/`. The registry's default still does, but runs through the runner (CLI, UI) read `evals/customer_trends/fixtures/providers/` unless `EDRAK_FIXTURES_DIR` says otherwise, because the test recordings hold three items each. `EDRAK_FIXTURES_DIR` is a new setting.
- SPEC section 5 lists the worker keys. Added: `EDRAK_FIXTURES_DIR`, `BRANCH_MAX_STEPS`, `BRANCH_TIMEOUT_S`, `APIFY_FALLBACK_TOKENS`, `SOCIALCRAWL_FALLBACK_API_KEYS`. The environment name is read from the shared `ENV` (which `develop` now uses) or `EDRAK_ENV`.

## Batch 9

- SPEC section 3 lists `tools/social_comments.py`; the comments tool lives in `tools/social_search.py` next to the search tool (they share their input checks). It is registered under its own name, `social_comments`.
- SPEC section 3 lists no `assembly.py`, `deps.py`, `usecases.py`, `worker.py`, `providers/costs.py`, `providers/keys.py`, `providers/config.py`, `providers/direct_http.py`, `tools/selection.py`, `tools/urls.py` or `utils/labels.py`. Each is a small helper with one job, added where a batch needed it. `nodes.py` stayed one module (about 650 lines), so the rule about raising it before splitting did not apply.
- SPEC section 11 says `run_worker(task: ResearchTask, *, sink=None, providers=None, llm=None)`. It also takes `settings=None` (like `run_task`), and is synchronous with an async twin `arun_worker`.
- The shared `Evidence` has no field for the platform, the engagement or the language, and no social source type, so the adapter puts them in `metadata` and uses `other`. The shared `Finding` has no metrics field, so the metrics ride in `WorkerResult.metadata["finding_metrics"]`.

### Tools: implemented against SPEC section 9

`RequestBase` adds `languages`, `geo`, `since`, `until`, `depth`, `max_results` and `entity`; the injected `run_id` and `task_id` are hidden from the model. Output of the collection tools is `ToolResponse` (SPEC 6.3) as compact JSON; of the processing tools, `ProcessingResponse` (`status`, `count`, `warnings`) plus the fields below.

| Tool | Spec inputs | Implemented inputs | Output fields |
|---|---|---|---|
| `web_search` | query, search_type, num, RequestBase | the same | `ToolResponse` |
| `fetch_page` | url, max_chars=20000 | the same (no RequestBase) | `ToolResponse` |
| `social_search` | platform, query, hashtags, sort, RequestBase | the same | `ToolResponse` |
| `social_comments` | platform, post_url, RequestBase | plus `sort` (`top` default) | `ToolResponse` |
| `search_interest` | keywords (1 to 5), timeframe, granularity, include_related, RequestBase | the same | `ToolResponse`; the preview holds the series summaries |
| `reviews_fetch` | store, target_id_or_url, country, RequestBase | `country` optional, falls back to `geo` | `ToolResponse` |
| `news_coverage` | query, source, RequestBase | the same | `ToolResponse` |
| `analyze_text` | batch_ids, tasks, taxonomy, max_items=300 | the same; `tasks` defaults to all three, `max_items` at most 1000 | `analyzed`, `total_items`, `themes_total`, `themes` (top 15), `sentiment`, `languages` |
| `compute_metrics` | metric, batch_ids, params | `batch_ids` optional; `params.filters` | `metric_id`, `metric`, `values`, `params`, `batch_ids` |
| `evidence_query` | filters, limit=10, sample | `limit` above 20 is lowered with a warning | `total`, `items` |
| `submit_findings` | findings | at most 20 | `accepted`, `rejected` (id, reasons), `adjusted` |

Names and the eleven-tool count match the spec. The collection tools persist evidence and return
pointers; the evidence the spec sketch calls `preview` is at most five items of 200 characters.

### SPEC section 15 after Batch 9

1. **Shared contract shapes.** Resolved on the contract side (`develop` carries `ResearchTask`,
   `WorkerResult`, `Evidence`, `Finding`) and absorbed by the adapters. Still for the contracts
   owner: a social source type and a place for platform and engagement in `Evidence`, a metrics
   field on `Finding`, and the `market_entry_expansion` versus `market_entry` naming.
2. **Web MCP server.** Still open: `mcp_servers/web_server.py` and `edrak/mcp/` hold no
   implementation, so the worker keeps its Serper and page-fetch adapters behind `Provider`.
3. **Customer-specific providers.** Still open by design: they stay in the worker until a second
   worker needs them.
4. **Worker registry and dispatch by name.** `WorkerRegistry`, `Worker` and `WorkerType.CUSTOMER_TRENDS`
   exist in the contracts and the worker conforms (`worker.register`). Still pending outside this
   branch: the composition root that builds the registry and registers the workers (the pipeline
   script builds an empty `WorkerRegistry`).
5. **Evidence store and checkpointer.** Still open: SQLite under the data directory, as before; no
   shared store has been offered.

## After the first live run

- SPEC 6.7 gives the rule for `high` confidence (10 evidence ids, 2 platforms or source types). `submit_findings` also lowers a `high` finding that lists `related_gaps` to `medium`, and the `high_confidence` check reports one that kept it. This is stricter than SPEC and follows its own principle that gaps are a first-class output.
- `ActorSpec.timeout_s` and the per-run memory of an actor over its monthly limit are additions to the Apify configuration described in SPEC section 8; `provenance.calls` is an addition to the provenance of SPEC 6.8.
- SPEC section 10 replans once "while budget remains". The replan edge also needs enough collection time (`MIN_REPLAN_S` 30 s after the 30 percent kept for analysis and writing), and a branch's time limit is the smaller of `BRANCH_TIMEOUT_S` and that remaining time.
