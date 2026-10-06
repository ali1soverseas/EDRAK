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

### Fallback API keys

Apify and SocialCrawl accept more than one key. `APIFY_TOKEN` and `SOCIALCRAWL_API_KEY` are the
first choice; `APIFY_FALLBACK_TOKENS` and `SOCIALCRAWL_FALLBACK_API_KEYS` hold comma separated
backups (`Settings.key_list` joins them, dropping blanks and repeats; a provider is also set up
from fallbacks alone). Each provider holds a `KeyRing` (`providers/keys.py`) and runs every call
through `with_failover`: a key that answers with `ProviderQuotaExceeded` (usage or credit limit,
or a SocialCrawl balance too low for the request) or `ProviderNotConfigured` (rejected key) is
passed over, the call is repeated with the next key, and the ring stays on the working key for
later calls. Rate limits, server errors and failed actor runs do not rotate. When every key has
failed, the last key's error is raised and the registry falls back to the next provider as usual.
A rotation is logged as `api_key_rotated` with the position in the list, never the key.

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
| `socialcrawl` | `social_search:*`, `social_comments:*` (all six platforms), `search_interest` | Needs `SOCIALCRAWL_API_KEY`. One response shape for every platform; Google Trends through its own endpoint. |
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

### Routing and cost

The order of providers per capability in `config/providers.yaml` is cheapest first among
results of the same quality. Run `uv run python ../scripts/customer_trends/print_costs.py` from
`backend/` for the table below; it is computed from the prices in the config, and
`tests/customer_trends/unit/test_costs.py` fails if a provider is placed before one that costs
more than twice as much (Serper, a snippets-only last resort for social search, is exempt).

Only free allowances are used, so these are not what is paid but how fast an allowance is
spent. Cost per 100 usable items in USD (Apify FREE-plan item prices; SocialCrawl credits
priced at the cheapest pack, 0.008 USD, because free credits never renew; after SocialCrawl's
relevance and language filters; YouTube's API is free inside its daily quota):

| Capability | 1st | 2nd | 3rd |
|---|---|---|---|
| `social_search:x` | apify 0.040 | socialcrawl 0.072 | serper (snippets) |
| `social_search:tiktok` | socialcrawl 0.064 | apify 0.371 | serper |
| `social_search:instagram` | socialcrawl 0.136 | apify 0.270 | serper |
| `social_search:facebook` | socialcrawl 0.320 | (apify switched off) | serper |
| `social_search:reddit` | socialcrawl 0.096 | apify 0.420 | serper |
| `social_search:youtube` | youtube_api free | socialcrawl 0.032 | apify 0.400 |
| `social_comments:x` | apify 0.040 | socialcrawl 0.040 | |
| `social_comments:tiktok` | socialcrawl 0.040 | apify 0.125 | |
| `social_comments:instagram` | apify 0.260 | socialcrawl 0.280 | |
| `social_comments:facebook` | socialcrawl 0.040 | apify 0.251 | |
| `social_comments:reddit` | socialcrawl 0.200 | apify 0.420 | |
| `social_comments:youtube` | youtube_api free | socialcrawl 0.008 | apify 0.200 |
| `search_interest` (per 100 keywords) | apify 0.300 | socialcrawl 0.800 | trends API stub |
| `reviews:app_store`, `reviews:google_play` | apify 0.010 | | |
| `reviews:amazon` | apify 0.600 | | |
| `web_search`, `news:google_news` | serper 0.001 | | |
| `news:gdelt`, `fetch_page` | free | | |

Reasons behind the placement:

- X search stays on Apify first: both cost about the same (SocialCrawl is 1.4 times cheaper) and
  Apify returns every matching tweet, where SocialCrawl's relevance filter keeps about 60
  percent of a page.
- TikTok, Reddit, Facebook and Instagram search, TikTok, Reddit and Facebook comments:
  SocialCrawl is 2 to 6 times cheaper and as good. Apify's TikTok actor took 217 seconds for
  30 videos (48 percent about the query), and its Reddit actor timed out on 25 posts.
- Instagram search on SocialCrawl returns no publication dates; Apify's returns dates and likes
  at five times the price. Posts without a date are kept, but not placed in time.
- Instagram and X comments: the two are about equal (Instagram comments 0.28 against 0.26,
  X replies 0.04 against 0.04), so Apify goes first. Its 5 USD a month renews and
  SocialCrawl's credits do not; the credits are kept for the capabilities where they save
  the most. Saving per credit, from largest: Facebook comments, TikTok search, Reddit search,
  TikTok comments, Reddit comments, Instagram search, then X and Instagram comments last.
- Search interest: Apify's actor is slightly cheaper for one to three keywords but a browser
  scraper (80 to 240 seconds, one timeout in three runs); SocialCrawl answers in about 20
  seconds for 5 credits and puts up to five keywords on one scale, so it is the fallback.
- App store reviews: Apify costs 0.01 USD per 100 against about 0.03 on SocialCrawl, so there is
  no second provider. SocialCrawl also has Amazon reviews, cheaper than Apify's, but no adapter
  is built for them yet.

Free allowances, and what each one means for the order. SocialCrawl: 100 credits once per
key, never renewed, so a key that is used up stays empty. Apify: 5 USD each month per
account, renewed. Serper: a one-time grant of queries. YouTube Data API: 10,000 units a day.
GDELT: free. When every SocialCrawl key is out of credit the registry moves on to the next
provider in the list on its own, so a run still completes, but TikTok and Reddit then come
from the slower Apify actors and Facebook search has no source left except Serper snippets.

`tests/customer_trends/integration/test_routing_matrix.py` checks the effective order against a
fully configured registry and prints it (`pytest -s`).

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

## Processing tools

Four tools turn stored evidence into findings. They follow the collection tools' conventions
(inputs validated by the worker, `run_id` and `task_id` injected, one `tool_called` event per call
with no provider) and answer with a `ProcessingResponse`: `status`, `count`, `warnings` and the
fields of the tool, as compact JSON. Failures are the same error `ToolResponse` as before
(`invalid_input`, `unknown_batch`, `no_data`, `tool_error`). They are not part of any collection
branch; `tools_for_stage("analyze")` gives `compute_metrics` and `analyze_text`,
`tools_for_stage("write")` gives `evidence_query` and `compute_metrics`. `submit_findings` is in
neither list: the graph calls it with the findings the writer produced.

Trend point items (one sentence per search interest series) are not something people wrote, so
counting and theme analysis skip them; `evidence_query` still returns them so a finding can cite
a trend.

**`compute_metrics`** is plain code. `batch_ids` defaults to every batch of the run. Each result
is stored with a deterministic `metric_id` (`m_` and 12 hex digits of a hash of run, metric,
options and sorted batch ids), so the same call twice gives the same id.

| Metric | Values |
|---|---|
| `volume_over_time` | Items per week (Monday start, UTC) or day, only buckets with items, `undated` counted apart, peak bucket. |
| `engagement_stats` | Mean, median and 90th percentile (linear interpolation) of interactions per item, overall and per platform. Likes, replies, shares and upvotes count; ratings and views do not. |
| `trend_growth` | Per stored series: percent change from the mean of the first N points to the mean of the last N, N = max(2, points // 4). Under 8 points: `insufficient_data`. A zero first mean gives `zero_baseline`. |
| `share_of_voice` | Items mentioning the entity and each competitor (an item counts once per name) and each name's percent of all mentions. Matching ignores case and Arabic spelling variants (`search_key`); Latin names match whole words, Arabic names match inside words. |
| `platform_mix`, `language_mix` | Counts and percent. Items with no platform are listed under their source type; no language is `unknown`. |

The evidence metrics accept `params.filters` (the `evidence_query` filters).

**`evidence_query`** reads through `EvidenceStore.query`: at most 20 items (`limit` above that is
lowered with a warning), each text cut to 500 characters, the number of matches as `total`. A
`theme` filter is matched to a stored theme by normalized label, so "Slow customer supports"
finds "Slow customer support"; an unknown theme answers with the stored labels.

**`analyze_text`** is map-reduce.

1. Items come from the given batches, most engaged first (then newest), at most `max_items`.
2. Map: chunks of 25 go to the analyst model one after another with `ANALYZE_THEMES` and a strict
   JSON schema (`structured_call`, which repairs an invalid answer once). A chunk that still
   fails, or whose model call drops, is skipped and named in the warnings with its item count.
   Ids the model invents are ignored; items it leaves out are counted in a warning.
3. Reduce (code): each label is normalized (`utils/labels.py`: case, punctuation, stopwords, plain
   plurals, Arabic spelling folding) and labels whose word sets overlap by a Jaccard of at least
   0.6 are merged. Groups are anchored on their most used label, so a chain of loose matches
   cannot drag two unlike labels together. The group's label is a taxonomy label if one matches,
   else its most used wording (shortest, then alphabetical).
4. Each group becomes a `ThemeAggregate`: `count`, `share` of the analyzed items, `sentiment_mix`,
   `by_platform`, `by_language`, `evidence_ids` and up to three `representative_quotes`.
   Quotes come from the most engaged items of the theme, are cut at a word to at most 240
   characters and are always a verbatim part of the stored text; texts shorter than 20
   characters are used only to fill a gap.
5. `recent_growth` is the relative change of the theme's share of items between the earlier
   two thirds and the latest third of the date range of the analyzed items (0.5 is 50 percent
   more). It is null with fewer than 6 dated items, a theme under 3 items, or a theme absent from
   the earlier period.
6. Aggregates replace the run's stored aggregates, so analyze all batches in one call; a call that
   replaces earlier themes says so, and one that finds no themes keeps the stored ones.

The answer lists the 15 biggest themes (label, count, share, sentiment mix, growth, a short
description from the model's candidate themes), the sentiment overview, the languages and
warnings. Quotes are not in the answer: the writer reads them through `evidence_query`.

**`submit_findings`** validates each `Finding` and stores the ones that pass (an id sent again
replaces the stored finding). A finding is rejected, with every reason listed, when:

- `evidence_ids` is empty or names an item that is not stored for this run;
- a number in the claim has no match in `metrics` or in the stored metric cited as `metric_id` (or
  any `*_metric_id`); a cited metric must exist. A match is within 1 percent, or within rounding at
  the decimals the claim shows (0.5 for a whole number). Numbers are read by `find_numbers`
  (Arabic digits included); four digit years from 1900 to 2100 are dates, not quantities;
- the claim contains a phrase from `config/verdict_phrases.yaml` (English and Arabic, compared
  without case or Arabic spelling variants).

Accepted findings may be adjusted, and the response says so in `adjusted`: one evidence id means
low confidence and the caveat `single_source`; `high` needs at least 10 evidence ids and at least
two platforms or two source types, else it becomes `medium` with a caveat.

