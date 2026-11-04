# LLM usage tracking

Every model request EDRAK makes is recorded once, automatically: tokens,
latency, throughput and an estimated cost. Nothing else has to call into this
package for tracking to work.

## How it is wired

The only integration point is the model factory. `edrak.core.llm.get_chat_model`
(and the customer-trends worker's own factory) attaches a LangChain callback,
`edrak.usage.tracker.UsageCallback`. Because a structured-output run and a retry
reuse the same model object, one attachment covers:

- plain `invoke` / `ainvoke`
- `get_structured(...).invoke(...)` and `with_structured_output(...)`
- the retry and repair loops inside the workers

No call site was changed to track a call. A failed request is recorded too, with
`status: "failure"` and no tokens.

The handler never raises and never blocks: it reads the reply, builds one record
and hands it to a background thread that appends it to the log.

## Where the output goes

```
backend/src/edrak/usage/logs/
├── llm_calls.jsonl   one JSON object per request (the source of truth)
└── llm_calls.log     the same, as one readable line per request
```

Both are run products and are gitignored. `EDRAK_LLM_USAGE_DIR` moves them;
`EDRAK_LLM_TRACKING=0` turns tracking off.

## Viewing it

```
python scripts/llm_usage_report.py system            # totals, by stage / operation / model / use case
python scripts/llm_usage_report.py run <run_id>      # one workflow, by stage and by operation
python scripts/llm_usage_report.py run <run_id> --json
```

Or from Python:

```python
from edrak.usage import flush, report

flush()                                  # wait for queued calls to be written
print(report.render_workflow("req-1"))
summary = report.system_summary()        # the numbers behind the table
```

### As a static page

`--html` writes the same numbers as a file instead of printing them:

```
python scripts/llm_usage_report.py system --html              # into the log directory
python scripts/llm_usage_report.py system --html report/      # into a directory you choose
python scripts/llm_usage_report.py run <run_id> --html page.html
```

`system` writes `index.html`, `system.html` and one `run-<run_id>.html` per run,
so the index links to pages that exist. `run` writes the one page, to the file
you name or, if you name a directory, inside it. A page is a single file — one
inline stylesheet, no JavaScript, no external assets — so it opens straight from
the filesystem and works offline.

A run page carries a block per agent, in the order each first called a model:
the agent's totals, the operations it worked through (in that order, because
that order *is* what it did), then every call with its own timestamp, tokens,
cost, latency and retry marker. The blocks are anchored, so the links under
**agents through the run** jump straight to one agent. The same breakdown is
available as data via `report.stage_details(run_id=...)`.

The pages are built from `edrak.usage.html_report`, which reads its figures from
`edrak.usage.report`. They cannot disagree with the terminal output, including
the rules that matter: an unpriced model reads *unknown* rather than `$0.00`, and
a breakdown nothing was captured for says so instead of drawing an empty table.
Run ids are sanitised into filenames, so an id containing `/` or `..` cannot
write outside the directory you chose.

## What a record holds

| Group | Fields |
|---|---|
| Identification | `run_id`, `call_id`, `use_case`, `stage`, `operation`, `provider`, `model`, `timestamp`, `retry_attempt` |
| Tokens | `input_tokens`, `output_tokens`, `total_tokens`, `cached_input_tokens`, `reasoning_tokens` |
| Performance | `latency_ms`, `app_elapsed_ms`, `generation_ms`, `generation_tps` |
| Outcome | `status`, `error_type` |
| Cost | `estimated_cost_usd`, `pricing_known`, `pricing_source` |

### `stage` and `operation`

`stage` answers *which worker or pipeline stage*, `operation` answers *which part
of it*. They are filled two ways, and the reports break down by both.

`stage` comes from `usage_context(...)`, set in `run_task` for each worker and in
each orchestrator stage node (`supervisor`, `verification`, `metrics`,
`cross_signal`, `synthesis`).

`operation` is set by whichever applies first:

1. An explicit label where the call is made, for call sites outside the
   workflow graph: `plan_draft`, `scope_check`, `eval_faithfulness`,
   `eval_relevance`, `brief_extract`, `brief_propose`, `chat_step`,
   `chat_reply`, `action_plan_draft`.
2. Otherwise the **LangGraph node** that made the call, taken from the run
   metadata. LangGraph reports the innermost node, so a call deep inside a
   worker reports that worker's own node — `synthesize`, `search`,
   `analyze_synthesize`, `task_executor`, `hunt` — instead of the outer
   `dispatch` step.

So a competitor call reads `stage=competitor_intelligence`,
`operation=synthesize`. When a call is made with no context, it is still
recorded; `run_id`, `stage` and `operation` are simply `null`. In a breakdown,
records with no value on the grouping key are shown as `(not captured)` so every
table adds up to the total printed above it, and a dimension no record captured
at all says so instead of drawing a single meaningless `-` row.

One thing to know when adding code: a `ThreadPoolExecutor` thread starts with an
**empty** context, so a model call made there loses the stage *and* the graph
node — LangChain keeps its ambient run config in a contextvar too, which is what
carries `langgraph_node`. Hand work to a pool with the context copied from the
submitting thread (`contextvars.copy_context().run`, as the metrics agent does);
`asyncio.to_thread` already does this for you.

## Pricing

Prices live in `pricing.yaml` (USD per million tokens) and are overridable from
the environment:

```
LLM_PRICE_INPUT_PER_MILLION=...              # generic fallback
LLM_PRICE_GPT_OSS_120B_INPUT_PER_MILLION=... # per model (gpt-oss:120b)
LLM_PRICE_GPT_OSS_120B_FREE_TIER=true
```

Cost is an estimate, never a provider invoice. A model with no price is
`unknown`, never `$0`: `pricing_known` is false and the reports print
`unknown`. `gpt-oss:120b` is marked free tier, so it reports `$0.00` with
`pricing_source: "free_tier"` — distinct from an unpriced model.

Cached input tokens are billed at the cached rate when one is configured and are
removed from the billable input, so they are never double counted. Reasoning
tokens are recorded but not charged again, because the provider already counts
them inside `output_tokens`.

## Not exposed by Ollama

- `cached_input_tokens` and `reasoning_tokens` — stored as `null` for now.
- `eval_duration` and `prompt_eval_duration` — Ollama Cloud returns
  `total_duration` but leaves both null, so pure generation speed cannot be
  computed. `generation_tps` therefore stays `null` and reports fall back to a
  throughput measured over end-to-end latency, printed as
  `tok/s (end-to-end)` so it is never mistaken for generation-only speed.
- per-request transport retries inside the HTTP client — one logical model call
  yields one record. Retries that the application makes (a re-dispatched task,
  a repair loop) are separate records.
- a structured-output parse failure happens after a successful model reply, so
  it is recorded as a successful request. Look at the worker's own logs for
  parse failures.

## Guarantees

- Tracking never raises into a workflow: every failure path logs at debug level
  and returns, so a broken log directory costs telemetry, not a run.
- One writer thread owns the files, so concurrent workers cannot interleave a
  line. Records are queued, never awaited.
- The queue is drained on interpreter exit, so a short-lived script still
  persists its calls.
