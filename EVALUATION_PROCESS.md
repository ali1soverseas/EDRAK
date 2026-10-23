# EDRAK Evaluation Process

This guide explains what the EDRAK evaluation agent evaluates, how it produces scores, and where to find the results. The evaluator audits artifacts from an EDRAK run; it does not rerun workers or make business decisions.

## At a glance

```text
EDRAK run artifacts
        |
        v
Load and classify input JSON + telemetry
        |
        v
Keep final worker attempts (default for run bundles)
        |
        +--> Deterministic integrity and domain checks
        |
        +--> Optional DeepEval quality judgments
        |
        +--> Execution-quality checks from recorded tool traces
        |
        v
Per-agent reports + pipeline checks + observability
        |
        +--> Optional golden ground-truth comparison
        |
        v
Read-only evaluation API and dashboard
```

## OpenTelemetry to evaluation agent flow

The evaluation pipeline starts with OpenTelemetry spans emitted by the backend, not from a separate agent replay. In `backend/src/edrak/observability.py`, `configure_telemetry()` installs a tracer provider and adds a JSON-file span exporter. Each operation or tool call is wrapped with `@traced(...)` or `@traced_tool(...)`, which records attributes such as `edrak.run.id`, `edrak.task.id`, `edrak.task.attempt`, `edrak.tool.name`, `edrak.tool.status`, and result counts without capturing raw tool payloads.

When the run is complete, `spans_for_run()` reads the merged export file, filters spans by `trace_id` and `edrak.run.id`, and returns only the spans relevant to that analysis. The run manager then bundles those spans with the run manifest, worker outputs, verification results, and cross-signal artifacts under `artifacts/evaluation_runs/<analysis-id>/`.

```text
Backend execution
  │
  ├─ @traced() for workflows and agent stages
  ├─ @traced_tool() for tool calls
  ├─ safe OTEL attributes: run/task/tool identity, status, durations, result counts
  └─ JsonFileSpanExporter -> data/telemetry/observability-<pid>.json
            │
            ▼
     EDRAK_TELEMETRY_EXPORT_PATH (optional override)
            │
            ▼
  spans_for_run(analysis_id, trace_id)
            │
            ▼
  EvaluationArtifactBundle + run manifest + inputs/outputs
            │
            ▼
  Evaluation agent loads run bundle + telemetry
            │
            ├─ deterministic integrity checks
            ├─ execution traces and tool-use checks
            ├─ DeepEval quality metrics (Faithfulness, Relevancy, etc.)
            └─ report JSON / summary.md dashboard output
```

This flow means the evaluator can reconstruct what happened in a run from the recorded trace and the persisted artifacts, even without rerunning the pipeline. The exporter is intentionally lightweight: it keeps only a safe subset of attributes, avoids raw argument values, and optionally mirrors to OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

## 1. Inputs and scope

Backend runs save evaluator inputs under:

```text
artifacts/evaluation_runs/<run-id>/
├── inputs/       # workers, RAG retrievals, verification, synthesis, decisions, telemetry
├── outputs/      # stage outputs such as metrics and human review
├── manifest.json # run status and timestamps
└── evaluation/   # generated evaluation reports
```

The evaluator recognizes artifacts by their JSON structure. It builds shared run context from inputs such as the research goal, worker findings, evidence, verified findings, signals, and telemetry. That context lets checks compare stages and keeps each downstream evaluation grounded in what was available at that point in the run.

For run bundles, the default is to evaluate the final worker attempt and latest verification/downstream artifacts. Superseded worker and retrieval artifacts are skipped where they belong to an earlier attempt. `--all-attempts` opts into evaluating prior attempts and verification passes too.

The metrics stage is intentionally not evaluated as a separate agent. Its artifact may still be used as pipeline context.

## 2. Two kinds of evaluation

### Deterministic checks

These checks use the recorded JSON and telemetry directly. They do not require an LLM and still run in `--dry-run` mode. Examples include:

- Whether findings cite evidence and meet structural requirements.
- Whether verification covers worker findings and whether downstream references resolve.
- Whether RAG returned non-empty chunks, valid retrieval scores, and non-duplicate context.
- Whether planned orchestration tasks have results, dependencies are declared consistently, tasks failed, retries occurred, and run duration is available.
- Whether observed tool calls match declared tool policies, satisfy available argument schemas, follow an expected sequence, or repeat identical work.

Integrity checks are separate from quality scores. A passed integrity check does not mean an LLM judged the output as high quality.

### LLM-judged quality

DeepEval judges selected output units against a goal, evidence, or retrieval context. The usual worker and downstream-agent metrics are **Faithfulness** (support from evidence) and **Relevancy** (response to the task). Additional metrics such as specificity, insight, and actionability are opt-in with `--extra-metrics`; tool-payload judgments require `--include-tool-payloads`.

RAG metrics are evaluated when their inputs are available:

- **Context relevancy** judges retrieved context against the query.
- **Context precision** and **Context recall** need an expected/reference answer. Without one, they are explicitly not assessable.
- **Faithfulness** and **Answer relevancy** need an answer as well as retrieved context.
- **Retrieval latency** is calculated from matching telemetry spans, not an LLM judgment.

Scores and thresholds are reported independently by metric. An unavailable score is not treated as zero.

## 3. What each agent evaluation does

Each agent type has its own evaluator. The evaluator uses the artifact's structure and relevant earlier-stage artifacts; it does not apply one generic checklist to every agent.

### Workers

Worker evaluation is unit-based: it checks individual findings and, where present, requirement answers, competitor profiles, and comparison-matrix cells. A finding is judged against the evidence references attached to that finding, rather than against every document in the run. The evaluator also checks whether evidence IDs resolve and whether a finding's statements, numbers, source mix, confidence, limitations, and status are internally consistent.

| Worker type | Additional emphasis |
|---|---|
| Internal intelligence | Internal evidence and synthetic/adapted evidence are allowed, but synthetic-only findings need an explicit limitation. |
| Competitor intelligence | Competitor coverage, profiles, requirement-to-finding links, and evidence behind comparison-matrix cells. |
| Market intelligence | Grounding, sources, claims and caveats for market, industry, economic, regulatory, or geographic findings. |
| Customer & trends intelligence | Grounding and source diversity for customer needs, sentiment, trends, and behavior claims. |

All worker types use Faithfulness and Relevancy by default. `--extra-metrics` enables the additional configured metrics by worker type (for example, specificity, insight, or actionability). `--context-relevancy` adds a separate judgment about whether a finding's evidence context is relevant to the research goal. Competitor profile overviews are checked against that competitor's findings; matrix-cell judgments require `--deep`.

Common worker measurements include:

- **Evidence utilization:** how much collected evidence is cited.
- **Evidence resolution/grounding:** whether cited IDs exist and each finding has support.
- **Source coverage:** distinct cited domains, top-domain share, first-/third-party evidence, and retrieval scores where present.
- **Requirement coverage:** whether fulfilled requirements have a grounded answer/finding, missing requirements explain why, and claimed grounded-finding counts match.
- **Claim quality warnings:** unsupported numbers, absence claims stated as facts, competitor names not reflected in the finding, non-supporting evidence relations, or synthetic evidence without a limitation.
- **Competitor-specific structure:** whether competitor profiles exist and matrix claims have evidence.

Some measures are only meaningful when the input provides the corresponding field. Missing metadata is not automatically evidence of poor research; inspect each check's status and detail.

### RAG retrieval

RAG evaluation operates on each recorded retrieval: a query plus the chunks returned for that query. It has two layers:

1. **Retrieval integrity and diagnostics (deterministic):** checks query presence, whether any chunks were returned, empty chunk text, score coverage and score range `[0, 1]`, duplicate/near-duplicate chunks, and source-domain coverage. It records chunk count, mean available retrieval score, and the number of unique source domains.
2. **Retrieved-context quality (LLM, when inputs allow):** Context Relevancy assesses the retrieved text in relation to the query. Context Precision and Context Recall require a reference/expected answer. RAG retrieval records do not score Answer Relevancy or Faithfulness because they represent retrievals, not generated answers; those metrics remain available for worker and downstream outputs that include an answer. If required inputs for a metric are absent, it is not run.

Retrieval latency is drawn from matching `internal_rag.retrieve` telemetry spans and reported separately from relevance scores. Matching uses task ID and attempt when present. If telemetry does not contain a matching span or duration, latency is unavailable. RAG evaluation audits the recorded retrieval; it does not make another retrieval call.

For example, high retrieval scores do not prove that the chunks answer the query. Conversely, a low Context Relevancy score says the retrieved text did not relate well to the query, even if retrieval returned non-empty chunks and valid numeric scores. Read the deterministic retrieval checks and the LLM score together.

### Verification

Verification evaluation checks the verifier's labels and the provenance behind them. It compares verifier findings with worker findings by ID, resolves their cited evidence, and examines evidence quality, confidence, contradictions, missing information, rewrites, and near-duplicates.

When Faithfulness judgments are available, the evaluator compares them with the verifier's `verified`/not-verified labels:

- **False verification:** verifier marked a finding verified, but the independent Faithfulness judgment failed.
- **Rejected but faithful:** verifier did not mark a finding verified, but the independent Faithfulness judgment passed.

These are diagnostic comparisons, not a replacement for human review. The evaluator also checks counts, coverage of worker findings, open contradictions/gaps on verified findings, confidence calibration, and whether a later verification pass downgrades previously verified findings.

### Cross-signal synthesis

Cross-signal evaluation checks whether each signal's supporting finding IDs resolve and whether the support is appropriately diverse and verified. It records the number of supporting findings/workers, support resolution, signal confidence relative to supporting confidence, evidence gaps, domains, and how many verified findings are used.

Checks flag signals with unresolved support, convergence claims based on one worker, unverified support, entities/domains not present in the cited support, confidence above the support, and mismatches between the signal list and decision-ready context. Faithfulness and Relevancy are the default quality judgments; Insight is available with `--extra-metrics`.

### Decision and recommendations

Decision evaluation follows recommendations back to their supporting signal IDs, then through those signals to findings and evidence. It checks recommendation coverage, opportunities, risks, priorities, options, action prerequisites, success criteria, human-approval markers, duplicate actions, and dangling cross-references.

Faithfulness and Relevancy are the default judgments. Actionability is available with `--extra-metrics` and evaluates the full recommendation, including actions, success criteria, and limitations. `--deep` additionally judges each recommended action and expands judgment to comparison-matrix cells upstream. Deterministic checks also flag new unsupported numbers, past timelines, missing limitations/success criteria, and references to invalid signals.

Recommendations support human decision-making; the evaluator does not approve or execute them.

### Orchestration

Orchestration evaluation uses the plan, task results, run manifest, and any recorded retry events:

- **Task completion rate:** planned tasks with a successful result divided by planned tasks.
- **Workflow success rate:** whether the manifest/run status is explicitly successful and there are no failed result tasks. Missing planned results are separately flagged by the integrity check and reduce task completion; they are not included in the failed-result count.
- **Dependency integrity:** whether declared dependency edges refer to planned tasks and the prerequisite completed before its dependent began. If no edges or required timestamps exist, it is not assessable.
- **Failure rate:** recorded failed result tasks divided by planned tasks. Missing results are reported separately and reduce task completion, but are not included in this numerator.
- **Retry rate:** observed retries divided by planned tasks plus retries.
- **Execution duration:** end-to-end manifest start-to-completion duration, falling back to task timestamps when available.

Partial, failed, errored, or missing results do not count as successful task completion. Because the workflow-success metric uses the manifest status and failed result tasks, a missing result is surfaced by the separate planned-task integrity check rather than automatically counted as a failed result. A run can have a high task completion rate and still have a failed workflow status; these metrics answer different questions.

### Telemetry and pipeline-wide checks

Telemetry evaluation checks the run's recorded spans/events: whether they are recognizable, whether observed errors are recorded, whether worker spans include agent/task identity and valid status/duration, and what tool names, error rates, and latency distributions appear. It is the run-level trace record, not a worker output-quality score.

The pipeline report performs cross-stage checks such as decision → signal → finding → evidence traceability, verifier coverage, research-run ID consistency, and stability of finding IDs/statements across reruns. Checks only run when the relevant stages and evidence are present.

The metrics stage itself is intentionally excluded as a separate evaluation row. Its input may still be used as pipeline context.

## 4. Execution-quality dimensions

The evaluator can report up to six dimensions for an artifact with a structured tool-call trace:

| Dimension | Evidence needed |
|---|---|
| Tool selection | Declared allowed/expected tools or an applicable worker tool policy; LLM judgment is only used when deterministic policy evidence is insufficient. |
| Tool arguments | Recorded argument names/types and, where available, an input schema. Judging argument value suitability requires the explicit payload option. |
| Result use | Tool result summaries, final output, and the explicit `--include-tool-payloads` option. |
| Workflow sequence | An expected ordered sequence plus observed tool calls. |
| Error handling | Observed failures and evidence that they were handled, retried, or surfaced. |
| Efficiency | Complete argument evidence (or fingerprints) to determine whether equivalent calls were repeated. |

Dimensions with missing evidence are marked **not assessable** rather than inferred. Worker and retrieval traces are correlated to telemetry by task ID and attempt where those identifiers are present. Tool argument values can be fingerprinted locally for duplicate checks; by default, raw values are not sent to DeepEval.

## 5. Runtime and rate-limit safeguards

The judge cache reuses successful results for the same metric/model/input combination. Its location is controlled by `--cache` (default: `edrak_judge_cache.json`).

Current defaults are intentionally conservative:

- One judge request at a time (`--workers 1`).
- At most two attempts per metric (`--retries 2`).
- After repeated rate-limit responses for a metric, a run-scoped circuit breaker skips subsequent judge requests instead of spending the remainder of the run retrying.

Observability distinguishes judge attempts, successful scores, cache hits, failed metrics, and skipped requests. The circuit breaker limits wasted time during rate limiting; it cannot guarantee faster evaluation when the judge service is responding normally.

## 6. Reports and dashboard

The evaluator writes reports to `evaluation/` for a run bundle, or to the path supplied with `--out`.
A comparison requested with `--compare-to` is saved by default as
`reports/compare/<run-id>_<ground-truth-name>.json`. Typical evaluation outputs include:

- `<agent>_report.json` — per-file metrics, checks, flagged units, and execution-quality details.
- `<agent>_units.csv` — unit-level rows and scores.
- `overview.csv` — compact per-agent/file summary.
- `pipeline_report.json` — cross-stage checks and provenance/traceability information.
- `observability.json` — judge counts, failures, latency, and per-file tool traces.
- `summary.md` — human-readable report.

Orchestration execution metrics count a planned task as completed when its final result is
completed/successful, or when it contains generated worker output (findings, evidence, signals, gaps,
conflicts, or a summary), even if the result also records an error for skipped work. A failed/error
result with no generated output is a task failure; a planned task with no result is missing. `partial_tasks`
separately records partial worker outputs; these count as completed executions, not complete research coverage.
For a successful terminal workflow, workflow success percentage follows task execution completion;
failed, cancelled, and partial terminal workflows score 0%. Successful retry rate is successfully
executed tasks that were re-dispatched by a recorded replan (matched by worker when replanning creates
new task IDs), divided by planned tasks. `retry_events` separately counts observed retry/replan events.
For RAG context precision and recall, the evaluator uses an explicit reference answer on the RAG record
when present; otherwise, it matches the run's `business_context.use_case` to
`golden/ground_truth/*/inputs/initial_input.json` and uses the corresponding golden worker file's
`expected_output` (falling back to its curated findings) as the reference answer. These scores remain
not assessable if that use case has no matching golden worker file.
The Competitive Intelligence golden record includes an explicit scope-constraint reference for internal
RAG queries: no confidential NVIDIA data was supplied, so use only public, source-supported evidence.

The dashboard Overview shows run-level health and metadata from the selected reports. Include `initial_input.json`, `manifest.json`, and `observability.json` with the agent reports when available to populate the goal, run ID, and judge totals. Include `pipeline_report.json` to show cross-stage traceability checks. Use Pipeline for per-agent judge scores, domain metrics (including RAG/orchestration/telemetry), integrity checks, and flagged units. The comparison view is a separate ground-truth comparison and should not be confused with integrity checks or LLM-judged averages.
Execution-quality/tool-trace checks are included for tool-using workers and RAG stages. Verification,
cross-signal, decision, and orchestration are not treated as tool-execution stages and do not receive
the "no structured trace evidence" check.
When opened with `?api=http://localhost:8000`, the dashboard polls the backend for new comparison
reports and opens a new result automatically after the evaluator command completes.
For local reports, use **Upload run reports** to select one run's agent `*_report.json` files from
its `eval_results` folder. Include that run's `inputs/initial_input.json` to display its goal, and
optionally include `manifest.json`, `observability.json`, `pipeline_report.json`, and
`compare_report.json` in the same selection. Each upload creates a saved run
page in the sidebar; the currently open page is marked **Opened**. Uploaded reports are stored in
the browser's IndexedDB and restored after refresh. They stay in that browser profile and are not
sent to the backend. Clearing site data removes saved pages.

The **Overall performance** page displays the aggregate report at
`Evaluation/Evaluation frontend/data/overall_performance_report.json` when served through a local
web server. If the dashboard is opened directly as a `file://` URL, use **Load aggregate report
JSON** to select that report manually.

## 7. Running an evaluation

### Deterministic run (no model calls)

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id> `
  --dry-run
```

This is useful for checking deterministic metrics, trace coverage, and report structure without using the judge. The command can still exit with status `1` when deterministic error-severity integrity checks fail; inspect the generated report to see which checks failed.

### Run with LLM judgments

Configure `OPENAI_API_KEY`, then omit `--dry-run`:

```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id>
```

To run the golden comparison immediately after evaluation completes, pass the folder name under
`golden/ground_truth`. The comparison report is written under `reports/compare/` so the evaluation
API and dashboard can list it.
With `--dry-run`, the comparison automatically uses word overlap and makes no judge-model calls:

```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id> `
  --dry-run `
  --compare-to nvidia_product_launch_inputs
```

Use `--compare-no-llm` to force word-overlap comparison during a normal judged evaluation, or
`--compare-out PATH` to choose the comparison report path. `--compare-to` requires `--run-bundle`.

Ground-truth folders may include `ground_truth.json` curation status and `sources.json` provenance.
Only treat a comparison as a benchmark score when its dataset is marked curated. The
`nvidia_product_launch_inputs` reference is currently partially curated: its competitor items cite
primary sources, its internal items are explicitly synthetic, and customer/market items remain
provisional pending source review. Do not edit reference items to match the run being evaluated.

Useful options:

| Option | Purpose |
|---|---|
| `--agents worker verification decision` | Restrict evaluation to selected kinds or agent names. |
| `--extra-metrics` | Also run slower specificity, insight, and actionability judgments where applicable. |
| `--context-relevancy` | Add context relevancy checks for workers. RAG inputs are separately judged for context relevancy when eligible. |
| `--workers N` | Set judge concurrency; default is `1`. |
| `--retries N` | Set attempts per metric; default is `2`. |
| `--cache PATH` | Choose the judge cache file. |
| `--include-tool-payloads` | Permit redacted tool arguments/results to be sent for argument and result-use judgments. |
| `--all-attempts` | Evaluate earlier attempts instead of final-only run-bundle behavior. |
| `--out PATH` | Write reports somewhere other than the run's `evaluation/` directory. |

### View the dashboard

For static snapshot data:

```powershell
Set-Location "Evaluation frontend"
python -m http.server 5500
```

Open `http://localhost:5500/`. For API-backed data, start and sign in to the EDRAK backend, then open:

```text
http://localhost:5500/?api=http://localhost:8000
```

The API provides read-only run summaries and ground-truth comparisons.

## 8. Interpreting results carefully

- **Integrity checks** are deterministic pass/fail checks.
- **LLM scores** are model judgments against a metric-specific threshold, not a guarantee of correctness.
- **Not assessable** means the artifact lacks required evidence; it is different from a zero score or a failed judgment.
- **Judge failures** mean no score was obtained for those attempts. A rate-limited historical run is not fixed by changing the evaluator; rerun it to obtain new judge results.
- **Execution-quality** measures what can be established from the recorded trace. It does not execute tools again or prove that missing trace events did not happen.
- **Ground-truth comparison** measures coverage/alignment against a selected reference bundle and is distinct from the per-agent quality evaluation.
