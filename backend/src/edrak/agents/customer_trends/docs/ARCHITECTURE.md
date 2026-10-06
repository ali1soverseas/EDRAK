# Architecture

To be extended as the graph and tools land. The normative design is in [SPEC.md](SPEC.md).

## Provider layer

Every external data source sits behind the `Provider` protocol in `providers/base.py`:
a `name`, a set of `capabilities` and `async call(capability, params) -> ProviderResult`.
Tools never talk to a provider directly. They call `ProviderRegistry.call`, which owns routing,
fallback, caching, the budget and the circuit breaker.

```
tool -> ProviderRegistry.call(capability, params)
          budget.check_before_call()
          for provider in routing[capability]:        # config/providers.yaml
              skip if unregistered, unsupported or breaker-open
              cache hit?  -> return it
              provider.call(...)  -> success: breaker reset, cache put, return
                                  -> ProviderError: breaker failure, try the next one
          nothing served -> ProviderExhausted(failures)
          budget.record(cost)   # one tool call per registry call
```

### Capability names

`<kind>` or `<kind>:<variant>`: `web_search`, `fetch_page`, `search_interest`,
`social_search:<platform>`, `social_comments:<platform>`, `reviews:<store>`,
`news:gdelt`, `news:google_news`. `make_capability` and `parse_capability` validate them.

### Call params

A plain dict, validated by `CallParams`: `run_id`, `task_id`, `query`, `languages`, `geo`,
`since`, `until`, `max_results`, `cursor`, `post_url`, `hashtags`, `sort` (`recent` or `top`),
and for the demand and review capabilities `keywords`, `timeframe`, `target` (an app id, or a
product id or URL) and `country`.
Providers stamp `batch_id="pending"` on the evidence they build; the evidence store assigns the
real batch id on insert.

### Results

`ProviderResult` carries `items` (evidence items or trend series), `raw_count`,
`cost_estimate` (USD), `next_cursor`, `warnings`, `partial`, free-form `meta`, and, set by
the registry, `provider` and `fallback_used`. `fallback_used` is true when the provider that
served the call is not the first in the routing order, including when earlier ones are not
configured. A partial result (for example a quota ran out midway) is never cached.

### Failures

`ProviderNotConfigured` (credentials rejected), `ProviderRateLimited`, `ProviderQuotaExceeded`,
`ProviderUnavailable` and `ProviderBadResponse` are the only errors providers raise. The
registry also turns unexpected parsing errors (validation, key and type errors) into
`ProviderBadResponse` so a bad payload becomes fallback, not a crash. Error messages never
contain request URLs, because those can carry credentials.

### HTTP

`providers/http.py::request_json` retries 429, 5xx, timeouts and connection failures three
times with exponential backoff and jitter. 401 and 403 mean rejected credentials, 402 a usage
limit, other 4xx and malformed JSON a bad response; none of those are retried. A provider can
pass an `error_mapper` to classify its own error bodies (YouTube reports quota problems inside
403 responses). `RateLimiter` enforces a minimum interval between request starts, per provider.

### Budget, breaker, cache

- `BudgetTracker` refuses a call that would cross the tool-call, cost or time limit and records
  what each call used.
- `CircuitBreaker` opens a provider after three consecutive failures and keeps it open for the
  rest of the run. A missing key does not count: such providers are simply not registered.
- `DiskCache` stores successful results under `<data_dir>/cache/<sha256>.json` for
  `EDRAK_CACHE_TTL_S` seconds. The key covers the provider, the capability and the canonical
  params, but not `run_id` and `task_id`; a cache hit is re-stamped for the current run.

### Fixture mode

With `EDRAK_PROVIDER_MODE=fixture` the registry builds no providers and opens no HTTP client.
It serves recorded results from `backend/tests/customer_trends/fixtures/providers/`.

File name: the capability with `:` replaced by `.`, plus `.json`: `web_search` is
`web_search.json`, `social_search:reddit` is `social_search.reddit.json`, `news:gdelt` is
`news.gdelt.json`.

File content: one serialized `ProviderResult`. `provider` names the provider the recording
imitates (it becomes the result's provider; `fixture` if empty). Items keep the run and task ids
of the recording; the registry re-stamps them for the current run. `max_results` truncates the
items. Cost is reported as zero. A missing or invalid fixture raises `ProviderExhausted` with a
failure from the pseudo-provider `fixture`, which tools report as a gap.

### Providers

| Provider | Capabilities | Notes |
|---|---|---|
| `serper` | `web_search`, `news:google_news`, `social_search:<platform>` (x, reddit, tiktok, instagram, facebook) | Needs `SERPER_API_KEY`. Snippet-level data, `snippet_only=true`. Social search is a `site:` query and always carries a warning. Local adapter until the shared web MCP server exists (SPEC 8.4). |
| `gdelt` | `news:gdelt` | No key. About 90 days of history, `sourcelang:` filter, results are titles only. Volume by day is computed from the returned articles. |
| `youtube_api` | `social_search:youtube`, `social_comments:youtube` | Needs `YOUTUBE_API_KEY`. Local quota counter in `<data_dir>/youtube_quota.json`, reset at midnight Pacific time. |
| `apify` | `social_search:{x,tiktok,instagram,youtube,reddit}`, `social_comments:*`, `search_interest`, `reviews:{app_store,google_play,amazon}` | Needs `APIFY_TOKEN`. One actor per capability, configured in providers.yaml. |
| `socialcrawl` | `social_search:*`, `social_comments:*` (all six platforms) | Needs `SOCIALCRAWL_API_KEY`. One response shape for every platform. |
| `google_trends_api` | `search_interest` | Stub, always registered so its routing entry resolves. `ProviderNotConfigured` without `GOOGLE_TRENDS_API_KEY`, then `ProviderUnavailable("alpha api not implemented")`. |

### Apify

`providers/apify/client.py` runs an actor with `POST /v2/acts/{owner~name}/run-sync-get-dataset-items`
(bearer token, `limit` and `timeout` as query parameters, the actor input as the body). The
sync route answers 201. A 408, or a 400 `run-failed`, means the run timed out or crashed: it
is reported as `RunNotFinished` (a `ProviderUnavailable` that is never retried, because running
the actor again costs again). 401 and 403 are `ProviderNotConfigured`, 402 is
`ProviderQuotaExceeded`, 429 is retried.

Per capability, `config/providers.yaml` names the actor, an input template, value translations
and a price. A template uses placeholders such as `{query}`, `{max_results}`, `{sort}`,
`{window}`, `{url}`, `{keywords}`, `{target}`. A string that is exactly one placeholder keeps
the value's type; a key whose placeholder has no value is left out so the actor keeps its
default; `requires` lists the values a capability cannot run without. `enabled: false` switches
a capability off without removing its config.

`providers/apify/mappers.py` is the only place that knows actor output field names. Each mapper
turns one dataset item into an `EvidenceItem` (or a `TrendSeries` for Google Trends) and keeps
unmapped small fields in `metadata`. Cost is estimated as the run fee plus the per-item price
times the items returned; Apify reports no cost on this route.

### SocialCrawl

`providers/socialcrawl.py` sends `GET <base_url><path>` with an `x-api-key` header. Which path,
query parameter, sort values, page size and credit price apply to each capability is in
providers.yaml. Responses share one envelope: `data.items[]` with a `post` or `comment`,
`pagination.next_cursor` (sent back verbatim as `cursor`) and `credits_used` /
`credits_remaining`. Searches send `relevance=filter`, which drops rows that are not about the
query at no extra credit. Rows in other languages than requested are dropped with a warning.
A request that could cost five credits or more first reads the free `GET /credits/balance`
and raises `ProviderQuotaExceeded` when the balance is short; paging stops early when the
balance runs out. A comments request for a post without comments (404 `RESOURCE_NOT_FOUND`) is
an empty result.

### Routing

Effective order with every key configured (`providers.yaml` lists the configured order; names
without a registered provider that supports the capability are skipped):

| Capability | Order |
|---|---|
| `social_search:x`, `tiktok`, `instagram`, `reddit` | apify, socialcrawl, serper (snippets) |
| `social_search:facebook` | socialcrawl, serper (the Apify entry is switched off) |
| `social_search:youtube` | youtube_api, apify, socialcrawl |
| `social_comments:x`, `tiktok`, `instagram`, `facebook`, `reddit` | apify, socialcrawl |
| `social_comments:youtube` | youtube_api, apify, socialcrawl |
| `search_interest` | apify, google_trends_api |
| `reviews:app_store`, `google_play`, `amazon` | apify |
| `news:gdelt` | gdelt |
| `news:google_news`, `web_search` | serper |
| `fetch_page` | direct_http (built with the collection tools) |

`tests/customer_trends/integration/test_routing_matrix.py` checks this table against a fully
configured registry and prints it (`pytest -s`).

## Collection tools

Seven tools collect evidence: `web_search`, `fetch_page`, `social_search`, `social_comments`,
`search_interest`, `reviews_fetch`, `news_coverage`. Each lives in its own module under `tools/`
as a pydantic input model, a description for the model and an async function, bundled in a
`ToolSpec`. `tools/registry.py` is the only place nodes get tools from: `build_tools(ctx)` makes
one LangChain `StructuredTool` per spec and `tools_for_branch(branch, ctx)` returns the subset
a branch may use (social: social_search, social_comments, web_search; demand: search_interest,
news_coverage, web_search; reviews: reviews_fetch, web_search, fetch_page).

```
model tool call
  -> invoke_tool(ctx, spec, arguments)
       brief defaults + arguments + run_id/task_id from ctx -> validate the input model
       invalid -> ToolResponse(status=error, error_code=invalid_input)
  -> tool function: build provider params, clamp max_results by depth
  -> execute_collection(ctx, tool, capability, ...)
       providers.call(capability)   # budget check, routing, fallback, cache, cost
       persist: EvidenceStore.add_batch (trend series and trend points for search_interest)
       coverage by language, platform, source type; gaps; preview of at most 5
       emit one tool_called event
  -> compact JSON string for the model
```

Nothing in this path raises. `ProviderExhausted` becomes `error_code="provider_exhausted"` with
the failing providers named in `gaps`; `BudgetExceeded` becomes `budget_exceeded`; a bad
argument becomes `invalid_input`; anything unexpected becomes `tool_error`. A partial provider
result is `status="partial"`.

**Model-visible schema.** `args_schema` is the input model's JSON schema with `run_id` and
`task_id` removed, references inlined, titles dropped and optional fields flattened, so small
tool-calling models read it easily. Arguments are validated by the worker, not by LangChain, so
mistakes come back as readable `invalid_input` responses. The wrapper injects `run_id` and
`task_id`; a model cannot choose them. `ToolContext.defaults` (languages, geo, since, until,
depth, entity from the brief) fill whatever the model leaves out.

**Responses.** The model receives `ToolResponse` as compact JSON with empty fields removed:
status, `batch_id`, `count` of new items, up to five preview pointers (id, platform, snippet of
at most 200 characters, url), `coverage`, `gaps`, `warnings`, provider, cost. Full records stay
in the evidence store. A repeated identical call stores nothing: `count` is 0, `batch_id` is
null and a gap says everything was already collected.

**Per tool**

| Tool | Capability | Default results | Notes |
|---|---|---|---|
| `web_search` | `web_search` or, for `search_type="news"`, `news:google_news` | 10 (`num`) | Snippets only, so a `snippet-only` gap is always reported. |
| `fetch_page` | `fetch_page` (provider `direct_http`) | 1 | Refuses social platform domains and private or local hosts at validation. Respects robots.txt, 2 MB cap, 20 s timeout. A refused or unreadable page is an empty result with a warning, never a provider failure. |
| `social_search` | `social_search:<platform>` | 30 | `sort` recent or top; hashtags optional. |
| `social_comments` | `social_comments:<platform>` | 30 | `post_url` must be on the platform's own domains; `sort` defaults to top. |
| `search_interest` | `search_interest` | one series per keyword | Stores each `TrendSeries` and one `trend_point` evidence item per keyword so a finding can cite it. The preview gives first, last, peak value and date, direction and related queries. Direction is the least-squares slope over the period, flat inside 5 percent of the peak. The same keyword, geo and timeframe is stored once per run. |
| `reviews_fetch` | `reviews:<store>` | 30 | `target_id_or_url` is the app id, package name or ASIN; country falls back to `geo`. |
| `news_coverage` | `news:<source>` | 25 | Coverage includes a volume summary (days, total, peak day); day-by-day counts are stored in the batch meta (`EvidenceStore.batch_meta`). |

Results asked for per call are `max_results` if given, else the tool default, never above the
cap for the depth (light 50, standard 200, deep 1000).

**Events.** Each call emits one `tool_called` event through `ToolContext.emit`: tool, branch,
run and task ids, shortened args, provider, fallback flag, status, count, latency in ms, cost
and error code. A failing emitter is logged and ignored.
