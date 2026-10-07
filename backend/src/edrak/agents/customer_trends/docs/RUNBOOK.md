# Runbook

What to do when a run does not go as expected. Commands run from `backend/`.

## Find out what happened

A run tells you in four places, from the quickest.

1. **The result.** `uv run customer-trends show --run-id RUN_ID` (or the Summary tab of the UI)
   prints the status, the gaps and the warnings. A gap names a coverage problem or a failed branch;
   a warning names a smaller thing (a chunk skipped, a finding dropped, a text cut).
2. **The event stream.** `customer-trends run` prints one line per event. A `tool_called` line with
   `status=error` and an `error=` code is a tool that failed; the UI shows the same in the tool call
   table and the node timeline.
3. **The provenance.** The Provenance tab (or `result.provenance`) lists the providers used, the
   fallbacks, the tool calls with errors and cost, the timings per node and the provider health at
   the end of the run.
4. **The log.** `--verbose` (CLI) or the terminal of `make ui`. Every line has `run_id` and
   `task_id`; lines from a node have `node`, from a tool `tool`, from a provider `provider`. Filter
   on `run_id`. Keys and tokens are never logged.

The tool error codes you will meet: `provider_exhausted` (no provider could serve the call),
`budget_exceeded`, `invalid_input` (the model sent bad arguments), `unknown_batch`, `no_data`,
`llm_unavailable`, `analysis_failed`, `tool_error` (a bug: the log has the exception name).

## Missing key

| Where | What it looks like |
|---|---|
| Model key | CLI: `OLLAMA_API_KEY is not set`, exit 1. In a run that got past the CLI: warnings "the planner failed (LLMConfigError); a default plan was used" and "the writer failed", tool error `llm_unavailable` from `analyze_text`. UI sidebar: `ollama_api_key` is `missing`. |
| Provider key | The provider is not set up: a call lists it as `unregistered` in the `provider_exhausted` error, and the next provider in the order is used. With no key for any provider of a capability the call fails and the branch reports a gap. UI: the key shows `missing`; Provider health lists only the providers that are set up. |

Fix: add the key to `.env` at the repository root (never commit it). To run with no keys at all,
use `--fixture --fake-llm` (CLI) or `EDRAK_PROVIDER_MODE=fixture EDRAK_FAKE_LLM=true make ui`.

## Quota or credits used up

Apify (monthly dollars per account) and SocialCrawl (one-time credits per key) run out;
YouTube has a daily quota.

- **Log:** `api_key_rotated` (with the position, never the key) when a key is passed over for the
  next one; `provider_failed ... error=ProviderQuotaExceeded` when every key is used up.
- **Result:** the call is served by the next provider in the order (the tool event shows
  `fallback=yes`), usually at a higher price or lower quality; the Provenance tab lists the
  fallbacks. If every provider is out, the branch reports a gap.
- **UI:** Provider health shows `key_in_use: 2 of 4` and, for SocialCrawl, `credits_last_seen`.

Fix: add fallback keys (`APIFY_FALLBACK_TOKENS`, `SOCIALCRAWL_FALLBACK_API_KEYS`), wait for the
monthly reset, or accept the fallback order. SocialCrawl's free credits do not renew, so the cost
table in ARCHITECTURE.md keeps Apify first where the two cost about the same. Fixture mode needs
none of them.

## An Apify actor returns "no results" for everything

A free Apify account has a monthly run limit on some actors. `apidojo/tweet-scraper` (X search
and replies) allows 5 runs a month per account, measured on 2026-10-07. Past it, the actor still answers `succeeded`, with one placeholder row `{"noResults": true}` and
no tweets, so a call looks like a query nobody posted about.

- **Log:** `api_key_rotated` (the next token is tried), then `provider_failed ... error=ActorLimitReached`
  once every token is over the limit.
- **Result:** the call is served by the next provider (X search: SocialCrawl, then Serper) and
  the actor is left out for the rest of the run. UI Provider health lists it under
  `actors_over_their_monthly_limit`. The actor's own run log says "Monthly run limit exceeded
  per user".
- When the placeholder is genuine (no tweets match), the provider looks at the run log, finds no
  limit and returns an empty result.

Fix: wait for the monthly reset, add a token of another account to `APIFY_FALLBACK_TOKENS`, or
accept the fallback order. Costs of a refused run: a few seconds and no credit.

## An Apify actor changed its input

- **Log:** `provider_failed ... error=ProviderBadResponse` or `RunNotFinished` for the Apify
  provider, and the tool event has `status=error` or a count of 0 with the same actor every time.
- **Result:** the capability falls back to SocialCrawl or Serper, or the branch reports a gap.

Fix: read the actor's current input schema (`GET https://api.apify.com/v2/acts/{id}/builds/default`
with your token), correct its `input` template and prices in `config/providers.yaml`, run
`uv run python ../scripts/customer_trends/smoke_providers.py` (it spends about a cent), and
refresh the recorded fixture in `tests/customer_trends/fixtures/providers/apify/` if the output
shape changed. `verify: true` in the YAML marks values that were assumed, not checked.

## GDELT rate limit

GDELT often answers 429 ("limit requests to one every 5 seconds") even when requests are spaced
further apart, and it can stay that way for minutes. It has no fallback key.

- **Log:** `provider_failed ... provider=gdelt error=ProviderRateLimited`; after three failures in
  a run the breaker opens (Provider health: `breaker open`).
- **Result:** `news_coverage` with `source="gdelt"` fails with `provider_exhausted`; the demand
  branch can still use `news_coverage` with `source="google_news"` (Serper) or `web_search` with
  `search_type="news"`. The result carries a gap only if the run is thin as a whole.

Fix: wait and run again; the adapter already waits at least five seconds between requests and
honors `Retry-After`.

## YouTube quota or search cap

`search.list` costs 100 of the 10,000 daily units and is capped at `YOUTUBE_SEARCH_DAILY_CAP`
searches a day (counted in `<EDRAK_DATA_DIR>/youtube_quota.json`).

- **Log:** `ProviderQuotaExceeded: youtube_api: daily search cap reached (100 searches)`.
- **Result:** YouTube search and comments fall back to SocialCrawl, then Apify.
- **UI:** Provider health shows `searches: 100 of 100` and `quota_units`.

Fix: wait for the reset (midnight Pacific time), raise the cap if the quota allows, or let the
fallback serve it. A key whose quota is used up for the day shows the same.

## The run stopped at its time limit, or a branch did

- **Result:** the gap "the run stopped at its time limit of N s" with status `insufficient` or
  `partial`, a warning with the same text, and `provenance.ending = "time_limit"`; or the gap "the
  social branch reported an error: the branch did not finish in N s" for a single branch (what it
  had stored before is kept).
- **Log:** `branch_timed_out`, `node_finished status=error`.

Fix: raise `budget.max_seconds` in the brief or `BRANCH_TIMEOUT_S`, lower the `depth`, or look for a
slow provider in the tool call table (latency per call).

## Budget used up

`tool_called` lines with `error=budget_exceeded`; a warning-free result with gaps for what was
not collected; no replan (the graph only replans while budget remains). Raise `budget.max_tool_calls`
or `max_cost_usd` in the brief.

## A provider's breaker is open

After three failures in a row a provider is skipped for the rest of the run (Provider health:
`breaker open`, `failures in a row: 3`). A new run starts closed. If it keeps happening, the
provider is down or the key is wrong: check the `provider_failed` lines for the error name.

## Not enough evidence

Status `insufficient`, the gap "only N evidence items were collected; at least 30 are needed",
usually with "N platform(s) have at least 20 items". The run replans once; if the second pass does
not close the gap it ends this way. Check the queries (the plan is in the first model call of
`plan_queries`), the languages and the `since` and `until` window, and whether a provider failed.
This result is valid: the worker reports what it could not establish.

## The model's output is rejected

- **Result:** warnings "finding f2 was dropped: ..." (the reason is spelled out: an unknown evidence
  id, a number that is not in the metrics, verdict language), "chunk 2 of 6 skipped (no valid
  answer)", "the writer failed (...)", "the planner failed (...); a default plan was used".
- **Log:** `structured_call_invalid` (the repair attempt), `analyze_chunk_skipped`, `writer_failed`.

The worker repairs once and then drops or skips, so a flaky model costs coverage, not the run. If
it happens on every call, check `OLLAMA_MODEL`, the endpoint (the native `/api/chat` route, not
`/v1`) and run `uv run python ../scripts/customer_trends/smoke_llm.py`.

## A run crashed

`run_failed` event, the gap "the run failed with ...: ...", `provenance.ending = "run_failed"`,
and `customer-trends run` exits 1 (or 0 with a partial result when findings were already stored).
The cause is in the log (`run_failed`, `error=`). Run the same brief again with the same `run_id`:
the graph resumes from its last checkpoint without collecting again. A different `run_id` starts
over. A task from the orchestrator maps to `<task_id>.a<attempt>`, so a retry starts a new run.

## The database is locked

`database is locked` from SQLite means another process holds the file. Opening a new store or
checkpoint file from several runs at once is handled (retried, and the schema is created inside one
transaction); a long-running external reader of `evidence.db` is not. Close it, or copy the file.

## Arabic text looks wrong

Text is stored and written as UTF-8 exactly as collected. If a viewer shows boxes or reversed
words, the viewer lacks an Arabic font or right-to-left support (the UI renders Arabic with
`dir="rtl"`). `evals.customer_trends.checks` verifies that stored Arabic items hold Arabic letters
and no escape sequences.

## Checks

`uv run python -m evals.customer_trends.checks --run-id RUN_ID` re-checks a stored run: schema,
evidence ids, numbers, verdict language, high-confidence rule, summary consistency, Arabic text and
the checkpoints. It names each failure with the finding or item it concerns.
