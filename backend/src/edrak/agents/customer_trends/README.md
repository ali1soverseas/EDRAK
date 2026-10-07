# Customer and Trends worker

One of the four parallel research workers of EDRAK. It collects public customer and demand
signals (social posts and comments, reviews, search interest, news), analyzes them in Arabic and
English, and writes findings with their evidence for the verification and synthesis stages. It
reports what the evidence shows, how strong it is and what could not be established. It never
says "go" or "no-go".

The first target is `competitive_intelligence` (the GitLab pilot: GitLab against GitHub, Atlassian
and Microsoft Azure DevOps, around AI-assisted development). `product_launch` and `market_entry`
run through the same graph with their own configuration.

Python import path: `edrak.agents.customer_trends`. Run every command below from `backend/`.

## Architecture

A LangGraph graph with three parallel collection branches (social, demand, reviews), then
analysis, a coverage check with one bounded replan, findings and a result. The diagram, the node
table, the data flow, the storage layout, the provider routing and the mapping to the shared
contracts are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Where the rest is: [docs/RUNBOOK.md](docs/RUNBOOK.md) (when something fails),
[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) and [docs/DEVIATIONS.md](docs/DEVIATIONS.md) (decisions
and where the implementation differs from the spec).

## Quickstart

```bash
cp ../.env.example ../.env     # once, from backend/; then add the keys you have
uv sync                        # installs the project and the dev tools
make check                     # ruff, mypy (strict) and the offline tests

# a full run with no keys: recorded provider data and a scripted model
uv run customer-trends run \
    --brief evals/customer_trends/briefs/competitive_intelligence.json --fixture --fake-llm
```

That prints one line per event and ends with the result: status, headline, findings and the
location of `result.json`.

## Run the UI

The Streamlit developer UI is a harness for exercising the worker as if a task arrived from the
orchestrator and for looking at exactly what it hands downstream. It is not the product frontend.

```bash
# no keys: fixtures and the scripted model
EDRAK_FAKE_LLM=true EDRAK_PROVIDER_MODE=fixture make ui

# live: put the keys in ../.env first (OLLAMA_API_KEY and the provider keys you have)
EDRAK_PROVIDER_MODE=live make ui
```

Pick a preset (the GitLab pilot is first) or edit the form or its JSON tab, then press Run. The
page shows the node timeline, each tool call (provider, fallback, count, latency, cost), the
budget meter and gap and replan notices while the graph runs on a background thread, and Stop
cancels it. When it ends there are tabs for the summary, findings with their cited evidence,
themes, search interest trends, an evidence explorer, the raw `CustomerTrendsResult` and shared
`WorkerResult`, and the provenance. The sidebar shows which keys are set (never their values),
provider health and the history of past runs; loading a past run reads it and runs nothing.
Arabic text is shown right to left.

## Run from the CLI

```bash
uv run customer-trends run --brief PATH [--fixture] [--fake-llm] [--out result.json] [--verbose]
uv run customer-trends show --run-id RUN_ID [--json]
uv run customer-trends runs
```

`run` streams the events as one line each and prints a summary. Exit codes: 0 for a complete or
partial result, 2 for an insufficient one (too little evidence), 1 for a failure (an unreadable
brief, a missing `OLLAMA_API_KEY`, or a run that crashed). The structured log goes to stderr only
with `--verbose`. The briefs are `TaskBrief` JSON files; the three samples are in
`evals/customer_trends/briefs/`.

From Python the entries are `run_task(brief)`, `stream_task(brief)` (events as they happen) and,
for the orchestrator, `run_worker(task)` / `arun_worker(task)`, which take a shared `ResearchTask`
and return a shared `WorkerResult`. `register(registry)` adds the worker to a shared
`WorkerRegistry` under the name `customer_trends`.

## What the output is and where it goes

A run produces three things:

- **`CustomerTrendsResult`**, the full result: findings, theme aggregates with quotes, search
  interest series, a control summary (status, headline, confidence, coverage, gaps, warnings,
  budget used) and the provenance (models, providers, fallbacks, timings, tool calls). It is
  written to `<ARTIFACTS_PATH>/runs/<run_id>/customer_trends/result.json` by the sink
  (`ResultSink`, `LocalSink`) and read back with `ResultSink.read_result(run_id)`.
- **The evidence store**, `<EDRAK_DATA_DIR>/evidence.db` (SQLite): every collected item with its
  source, theme aggregates, metrics and findings. Findings cite evidence ids that exist here.
- **`WorkerResult`**, the compact shared contract returned to the orchestrator: findings, the
  evidence they cite (a short fact each), gaps, confidence, and a pointer to the two above in
  `metadata["artifact"]`. Raw text and intermediate artifacts do not travel through the
  orchestrator; verification and synthesis follow the pointer when they need more.

The status of a result is `complete`, `partial` (a critical gap remains) or `insufficient` (too
little evidence); a run with no evidence at all is a valid result, not an error.

## Configuration

Settings come from the environment or the repository `.env` (never committed; `.env.example` has
every name). Secrets are never logged.

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_BASE_URL` | `https://ollama.com` | Ollama Cloud endpoint (native `/api/chat`) |
| `OLLAMA_MODEL` | `gpt-oss:120b` | model for every role |
| `OLLAMA_API_KEY` | none | model key; required unless `EDRAK_FAKE_LLM` is true |
| `OLLAMA_MODEL_ANALYSIS` | none | model of the `analyst` role, else `OLLAMA_MODEL` |
| `OLLAMA_TEMPERATURE` | `0.2` | sampling temperature |
| `OLLAMA_TIMEOUT_S` | `120` | one model request |
| `APIFY_TOKEN` | none | Apify token (social, reviews, search interest) |
| `APIFY_FALLBACK_TOKENS` | none | comma separated backups used when a token runs out |
| `SOCIALCRAWL_API_KEY` | none | SocialCrawl key (social search and comments) |
| `SOCIALCRAWL_FALLBACK_API_KEYS` | none | comma separated backups |
| `SOCIALCRAWL_BASE_URL` | `https://www.socialcrawl.dev/v1` | SocialCrawl endpoint |
| `YOUTUBE_API_KEY` | none | YouTube Data API key |
| `SERPER_API_KEY` | none | Serper key (web and news search) |
| `GOOGLE_TRENDS_API_KEY` | none | alpha, usually empty |
| `EDRAK_ENV` | `development` | `prod` or `production` selects JSON logs; the shared `ENV` of `.env` also sets it |
| `EDRAK_DATA_DIR` | `./data` | store, checkpoints and cache; relative to `backend/` |
| `ARTIFACTS_PATH` | `artifacts` | result files; relative to the repository root |
| `EDRAK_PROVIDER_MODE` | `live` | `live` or `fixture` (recorded data, no keys needed) |
| `EDRAK_FIXTURES_DIR` | none | where fixture mode reads; default `evals/customer_trends/fixtures/providers` |
| `EDRAK_CACHE_TTL_S` | `86400` | provider result cache, seconds |
| `EDRAK_LOG_LEVEL` | `INFO` | log level |
| `EDRAK_FAKE_LLM` | `false` | true: the scripted demo model, no key |
| `BRANCH_MAX_STEPS` | `8` | model calls each collection branch may make |
| `BRANCH_TIMEOUT_S` | `100` | wall clock each collection branch may take |
| `YOUTUBE_DAILY_QUOTA` | `10000` | local YouTube unit budget per day |
| `YOUTUBE_SEARCH_DAILY_CAP` | `100` | local cap on YouTube searches per day |
| `LANGSMITH_TRACING` | `false` | optional tracing |
| `LANGSMITH_API_KEY` | none | optional tracing key |

Provider order, actor ids, endpoint paths and prices are in `config/providers.yaml`; coverage
thresholds and prompt emphasis per use case in `config/use_cases.yaml`; the phrases a claim may not
contain in `config/verdict_phrases.yaml`. A run's limits come from the brief:
`budget.max_tool_calls`, `max_cost_usd` and `max_seconds`.

## Tests

```bash
make check                      # ruff, mypy strict, and the offline tests (no network, no keys)
uv run pytest -m live           # opt-in tests against the real services
uv run python -m evals.customer_trends.checks --run-id RUN_ID   # check a stored run
```

`make check` never touches the network: providers are served by fakes and fixtures, the model by
a scripted fake, and the tests ignore the repository `.env`. The `live` tests are skipped without
the relevant key (and always by a plain `pytest`): a model smoke test (plain, tool call,
structured output), one real call per provider with tiny limits (Serper, GDELT, YouTube, Apify on
one platform, the SocialCrawl balance), and one full low-depth run of the GitLab brief within 20
tool calls, 0.50 USD and 180 seconds whose result must pass the checks. They spend free-plan
credits, so run them on purpose. `scripts/customer_trends/smoke_llm.py` and `smoke_providers.py`
are the older hand-run versions of the same smoke tests.

The checks (`evals/customer_trends/checks.py`) are a small structural check of one run: the result
validates, every finding cites stored evidence, every number in a claim is in its metrics, no
verdict language, high confidence only with enough evidence, a control summary that agrees with the
gaps, Arabic text stored as written, and no evidence text in the graph checkpoints.

## Troubleshooting

| Symptom | Likely cause | See |
|---|---|---|
| `OLLAMA_API_KEY is not set` | no model key | add it to `.env`, or use `--fake-llm` |
| status `insufficient`, gap "only N evidence items" | providers failed or returned little | RUNBOOK: providers |
| a provider is skipped with `unregistered` | its key is missing | RUNBOOK: keys |
| `ProviderQuotaExceeded` or `INSUFFICIENT_CREDITS` | a free allowance ran out | RUNBOOK: quota |
| GDELT answers 429 | its own rate limit | RUNBOOK: GDELT |
| the run stopped at its time limit | slow providers or model | RUNBOOK: time |

The full list, with what each failure looks like in the log and in the UI, is in
[docs/RUNBOOK.md](docs/RUNBOOK.md). Every log line carries `run_id` and `task_id`; lines from a
node, a tool or a provider also carry `node`, `tool` or `provider`.
