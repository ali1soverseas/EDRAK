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
