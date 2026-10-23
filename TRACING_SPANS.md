# EDRAK OpenTelemetry Spans

This document describes the active OpenTelemetry span-creation points in the backend.
It distinguishes those spans from Customer & Trends node activity events, which use a
separate local wrapper and are not OpenTelemetry spans.

## Span hierarchy

For a typical execution, the trace has this shape:

```text
edrak.orchestration.graph
└── edrak.worker.run
    └── tool.call
```

The exact number and nesting of worker and tool spans depends on the plan and on which
tools each worker calls. A run resumed after a pause starts a new orchestration root
span for that execution segment.

## 1. Orchestration root span

- **Span name:** `edrak.orchestration.graph`
- **Created in:** `backend/src/edrak/api/run_manager.py`, inside
  `RunManager._execute_run`.
- **When:** At the start of graph execution, after `configure_telemetry()` is called.
- **Coverage:** The run manager drives the LangGraph execution using `graph.astream(...)`.
- **Attributes set directly:** `edrak.run.id` and `edrak.run.resuming`.
- **End and export:** The `finally` block closes the span scope, obtains its trace ID,
  collects the run's spans with `spans_for_run(...)`, and saves them into the evaluation
  artifact bundle.

This root span is created directly by the run manager. It is not created by either
decorator.

## 2. Worker execution spans

- **Span name:** `edrak.worker.run`
- **Created by:** `@traced("edrak.worker.run")` on `run_task` in
  `backend/src/edrak/orchestration/nodes.py`.
- **When:** For each dispatched research task, when the orchestrator invokes `run_task`.
- **Coverage:** Worker lookup and execution, including conversion of worker errors or
  invalid results into a failed `WorkerResult`.
- **Attributes and errors:** The decorator records available task/run/worker metadata
  and marks exceptions or failed results on the span.

`run_task` is synchronous, and the current `observability.traced` implementation wraps
synchronous functions.

## 3. Tool-call spans

- **Span name:** `tool.call`
- **Created by:** `@traced_tool(...)` in `backend/src/edrak/observability.py`.
- **Tool identity:** The specific tool name is stored in the `edrak.tool.name` attribute;
  it is not used as the span name.
- **Coverage:** One span is created each time a decorated tool function is invoked.
- **Metadata:** The decorator records tool identity, argument metadata, result summaries,
  and errors. It supports both synchronous and asynchronous functions.

### Active call sites

| Tool identity | Call site | Kind |
|---|---|---|
| `internal_rag.retrieve` | `backend/src/edrak/rag/retriever.py` — `Retriever.retrieve` | Synchronous |
| `market_search` | `backend/src/edrak/agents/market_intelligence/nodes.py` — `_invoke_market_search` | Synchronous |
| `competitor_web_search` | `backend/src/edrak/agents/competitor_intelligence/discovery.py` — `web_search` | Synchronous |
| `competitor_web_search` | `backend/src/edrak/agents/competitor_intelligence/nodes.py` — `run_tavily` | Synchronous |
| Per-tool `ToolSpec.name` | `backend/src/edrak/agents/customer_trends/tools/registry.py` — the dynamically created `run` coroutine in `_structured_tool` | Asynchronous |

Customer & Trends registers these tool identities through `ALL_SPECS`:

- Collection tools: `web_search`, `fetch_page`, `social_search`, `social_comments`,
  `search_interest`, `reviews_fetch`, `news_coverage`, and `account_posts`.
- Processing tools: `analyze_text`, `compute_metrics`, `evidence_query`, and
  `submit_findings`.

The registry decorates each generated `run` coroutine with `@traced_tool(spec.name)`.
Consequently, each invocation creates a `tool.call` span labeled with that tool's
specification name.

Provider and outcome helpers such as `set_tool_provider(...)` and `set_tool_outcome(...)`
annotate the current tool span; they do not create additional spans.

## Customer & Trends node activity is separate

`backend/src/edrak/agents/customer_trends/nodes.py` defines its own local decorator named
`traced`. It wraps async worker-graph nodes and emits `node_started` and `node_finished`
activity events, including duration and success/error information.

It is used for nodes such as `intake`, `plan_queries`, `accounts`, `social`, `demand`,
`reviews`, `join`, `analyze`, `gap_check`, `write_findings`, and `submit`.

Despite sharing the name `traced`, this local decorator is not
`edrak.observability.traced` and does **not** create OpenTelemetry spans.

## Telemetry setup and collection

- `configure_telemetry()` configures the OpenTelemetry provider and the JSON span exporter
  used for evaluation telemetry.
- `spans_for_run(analysis_id, trace_id)` flushes pending telemetry and returns spans for
  the root trace or explicitly tagged with the run ID.
- `JsonFileSpanExporter` serializes exported spans and filters attributes through the
  safe-attribute allowlist.

These functions support span creation, export, and collection; they are not extra span
creation sites.

