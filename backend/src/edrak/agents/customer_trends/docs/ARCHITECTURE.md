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
`since`, `until`, `max_results`, `cursor`, `post_url`, `hashtags`, `sort` (`recent` or `top`).
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

### Providers in this batch

| Provider | Capabilities | Notes |
|---|---|---|
| `serper` | `web_search`, `news:google_news`, `social_search:<platform>` (x, reddit, tiktok, instagram, facebook) | Needs `SERPER_API_KEY`. Snippet-level data, `snippet_only=true`. Social search is a `site:` query and always carries a warning. Local adapter until the shared web MCP server exists (SPEC 8.4). |
| `gdelt` | `news:gdelt` | No key. About 90 days of history, `sourcelang:` filter, results are titles only. Volume by day is computed from the returned articles. |
| `youtube_api` | `social_search:youtube`, `social_comments:youtube` | Needs `YOUTUBE_API_KEY`. Local quota counter in `<data_dir>/youtube_quota.json`, reset at midnight Pacific time. |

Apify, SocialCrawl and the Google Trends stub arrive in the next batch; their routing entries
and placeholders are already in `config/providers.yaml`.
