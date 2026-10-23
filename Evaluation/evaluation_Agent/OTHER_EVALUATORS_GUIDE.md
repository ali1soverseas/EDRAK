# EDRAK Evaluation Functions: Beyond `eval_worker()`

This guide explains the other active evaluation functions in
[`edrak_eval_agent_enhanced.py`](./edrak_eval_agent_enhanced.py). It focuses on what
each evaluator reads, what it checks, when it calls an LLM judge, and what it returns.
For the worker-output evaluator, see `eval_worker()` in the Python file.

## The overall flow

The evaluator first loads JSON files and classifies each one by its shape. It builds
shared context from the loaded run, then calls the evaluator registered for each
recognized output type:

```text
JSON files
  -> detect file type
  -> build context for the run
  -> call the matching evaluator
  -> collect rows, metrics, and checks
  -> write stage reports
  -> run pipeline-wide traceability checks
```

The active registered types are `worker`, `verification`, `cross_signal`,
`decision`, `orchestration`, `rag`, and `telemetry`. The runner evaluates those in
pipeline order where applicable. Input files provide context but are not evaluated
as output stages. Runtime `metrics` files are also currently skipped by the runner.

An evaluator usually returns:

- **Rows:** results for individual findings, signals, recommendations, trace events,
  or retrieved chunks.
- **Metrics:** stage-level counts, score averages, or other summary values.
- **Checks:** named pass/fail tests with a severity and optional details.
- **Flags:** labels attached to individual rows that need attention.

## Deterministic checks and LLM judgments

These are separate mechanisms:

- **Deterministic checks** are Python rules, such as comparing IDs, counts, statuses,
  source references, scores, or duplicate text. They do not need an API call.
- **LLM judgments** send text and context to DeepEval when judging is enabled. They
  assess qualities such as faithfulness or relevancy that are difficult to verify
  with simple exact rules.

An LLM score is compared with its configured threshold to produce a pass/fail value.
If `--dry-run` is set, LLM judging is disabled but deterministic checks still run.
Some checks are only assessable when their required input data exists.

## 1. Verification evaluator

Function: [`eval_verification()`](./edrak_eval_agent_enhanced.py#L1083)

### What it evaluates

It evaluates each finding emitted by the verification stage. It reads the finding's
verification status, evidence quality, confidence, cited evidence IDs, contradictions,
and missing-information entries. It looks up cited evidence in the shared run context
and compares the verifier's statement with the original worker finding where possible.

### Per-finding work

For each finding, it:

1. Resolves cited evidence IDs against the shared evidence and extracts evidence text.
2. Checks for numbers in the statement that do not appear in the evidence context.
3. Detects near-duplicate statements within the verification output.
4. Notes whether the verifier rewrote the original worker statement.
5. Calls `judge_unit()` for the configured verification metrics. The verification
   metric set is faithfulness and relevancy; faithfulness compares the statement with
   evidence, while relevancy checks its relation to the research goal.
6. Adds flags for inconsistencies, such as:
   - marked verified but failing faithfulness;
   - marked not verified but passing faithfulness;
   - verified using only synthetic evidence;
   - verified despite open contradictions or missing information;
   - verified using low-quality evidence;
   - confidence inconsistent with the status;
   - a near duplicate or rewritten statement.

### Whole-stage checks

It checks that:

- reported finding, verified-finding, and insufficient-finding counts match;
- verifier decision status and targeted actions are consistent;
- any numeric summary agrees with the findings;
- every worker finding is covered and there are no unknown finding IDs;
- all cited evidence IDs resolve;
- verified findings do not retain contradictions or missing-information items;
- confidence behaves sensibly across statuses and evidence-quality levels;
- control-summary conflict and gap information is consistent;
- there are no near-duplicate findings.

Some confidence and evidence-quality checks are warnings, not hard integrity errors.

### LLM use

Faithfulness and relevancy are LLM-judged when enabled. The evaluator's deterministic
checks separately verify IDs, counts, statuses, confidence relationships, and other
explicit structure.

## 2. Cross-signal evaluator

Function: [`eval_cross_signal()`](./edrak_eval_agent_enhanced.py#L1202)

### What it evaluates

It evaluates each signal produced by the cross-signal stage. A signal is a synthesis
of supporting findings, such as a convergence, dependency, or tension across research
areas.

### Per-signal work

For each signal, it:

1. Reads the supporting finding IDs and resolves them using shared context.
2. Uses the supporting finding statements as the signal's evidence context.
3. Records supporting workers, stated domains, confidence values, implications, and
   evidence gaps.
4. Checks whether named entities appear in the supporting findings and whether the
   cited findings were verified when verification data is available.
5. Checks whether signal numbers appear in the supporting finding text.
6. Calls `judge_unit()` for the configured signal metrics. `insight` uses the signal
   title, statement, interpretation, and implications; faithfulness and relevancy
   use the signal statement and supporting finding text.
7. Adds flags for thin support, convergence based on one worker, unsupported named
   entities/domains, confidence higher than its support, unverified support, or
   missing relevance notes on support references.

The cross-signal metric set includes faithfulness, relevancy, and insight. Since
insight is a custom GEval metric, it is included when extra metrics are enabled.

### Whole-stage checks

It checks signal counts, reported verified-finding counts, whether supporting IDs
resolve, whether signals rely on verified findings, and whether decision-ready
context has consistent key signals and decision questions. It also warns about
unsupported entities/domains, convergence from one worker, signal confidence above
support confidence, and stage warnings.

## 3. Decision evaluator

Function: [`eval_decision()`](./edrak_eval_agent_enhanced.py#L1305)

### What it evaluates

It evaluates decision-stage recommendations and, when present, the executive summary,
opportunities, risks, recommended actions, priorities, and options.

### Recommendation rows

For each question recommendation, it:

1. Gets the recommendation's supporting signal IDs and resolves their signal text.
2. Combines the decision and rationale into the main statement.
3. Builds a fuller text including actions, success criteria, and limitations.
4. Checks for numbers not present in the supporting signals and mentions of past
   years.
5. Calls `judge_unit()` for the configured decision metrics. The actionability metric
   receives the fuller recommendation text; other metrics use the main statement.
6. Adds flags for unsupported numbers, past timelines, missing limitations, missing
   success criteria, or judge scores below threshold.

The decision metric set includes faithfulness, relevancy, and actionability.
Actionability is a custom GEval metric and therefore requires extra metrics to be
enabled.

### Executive summary

If the decision output has an executive summary, it is judged only for faithfulness
and relevancy against the recommendation statements. It also receives a deterministic
number-grounding check against those recommendations.

### Optional deep action judging

With `--deep`, each recommended action also gets a row and judge evaluation. The
action text is compared with its supporting signals, and the row records items such
as success indicators, human-approval requirement, and past-year references.

### Whole-stage checks

It checks that:

- referenced signal IDs are valid;
- reported counts match the recommendation, opportunity, risk, priority, and action
  lists;
- every opportunity, risk, and action has a priority entry, and priority entries
  do not point to unknown items;
- action links to opportunities and risks resolve;
- decision metadata's valid signal IDs match the cross-signal output;
- all decision questions from cross-signal are answered;
- priorities are not mostly undetermined;
- actions have success indicators and require human approval;
- cross-signals are used by recommendations;
- actions are not near-duplicates;
- options include a proposed action or trade-offs;
- recommendation numbers are grounded in signals and timelines are not in the past;
- the decision stage reports no warnings.

## 4. Orchestration evaluator

Function: [`eval_orchestration()`](./edrak_eval_agent_enhanced.py#L2057)

### What it evaluates

It compares the tasks in `d["plan"]["tasks"]` with the entries in `d["results"]`.
It is deterministic: `eval_orchestration()` does not call `judge_unit()` or the LLM
judge.

### 1. Match each planned task to a result

For both a task and a result, `task_key` chooses the first non-empty identifier in
this order:

```text
task_id -> worker -> name -> empty string
```

The evaluator makes a set of planned keys and a lookup of results by key. It then
identifies:

- **Missing results:** planned task keys with no result key.
- **Unexpected results:** result keys not in the plan. These are only reported if
  there is at least one planned task.

Because the result lookup is keyed by task key, duplicate results with the same key
overwrite one another in that lookup. `result_tasks`, however, counts all result
entries in the original list.

### 2. Decide which planned tasks count as successful

A planned task counts as successful if a matching result exists and either:

- it has any non-empty generated output in `findings`, `evidence`, `signals`, `gaps`,
  `conflicts`, `output`, `result`, or `summary`; or
- its status is `completed`, `success`, `succeeded`, `ok`, or `partial`, and its
  `error` field is empty, `None`, `"none"`, or `"null"` (case-insensitive).

The first rule means that a result with generated output counts as successful even
if its status is absent or otherwise unusual. The accepted `partial` status also
counts as a successful task for task-completion percentage.

A planned task is put in the failed set if it has no generated output and either
its status is `failed`, `error`, or `timeout`, or its `error` field is non-empty.
Missing task keys are also added to that set, but only actual result objects are
included in the `failed` list. Missing results are separately reported by the
missing-results check and task-row flag.

### 3. Validate declared dependencies

For each planned task, the evaluator reads `depends_on`, or `dependencies` if
`depends_on` is absent/empty. A string dependency is treated as one dependency;
otherwise the value is iterated as a list. Object dependencies use their `worker`
or `task_id`; other values are converted to strings.

It flags a dependency if its key is not in the set of planned task keys. This checks
only that the dependency was **declared in the plan**. It does not check dependency
execution order or whether the dependency completed successfully.

### 4. Create one report row per planned task

Each row includes:

- task key and its goal or description;
- the matched result's status, lowercased;
- declared dependencies;
- whether a matching result exists;
- `task_result_missing` if no result was found;
- `task_failed` if the matching result is in the failed-results list.

If a result has no status, the row displays `"completed"` when a result exists and
`"missing"` otherwise. This display fallback is separate from the success calculation:
a result without output and without an accepted status may display as completed but
will not count as successful.

### 5. Calculate summary metrics

- `planned_tasks` is `len(tasks)`, including task entries without a usable key.
- `result_tasks` is the number of result entries in the input list.
- `successful_tasks` is the number of unique planned keys counted as successful.
- `task_completion_pct` is successful planned keys divided by the number of unique
  planned keys. It is `None` if there are no planned keys.
- `workflow_success_pct` uses the workflow status from the orchestration result, or
  falls back to the run manifest's status. It equals task completion percentage for
  `completed`, `success`, `succeeded`, or `ok`; it is `0` for `failed`, `error`,
  `partial`, `cancelled`, or `canceled`; otherwise it is `None`.

Thus, a task with status `partial` can count as successful in `task_completion_pct`,
while an overall workflow status of `partial` produces `workflow_success_pct = 0`.

### 6. Run the final checks

The evaluator checks:

- every planned key has a result (error severity);
- no unexpected result keys exist (warning; only when the plan has keys);
- every dependency key refers to a planned task (error);
- no returned result is classified as failed (warning);
- `d["status"]` is not `failed`, `error`, or `partial` (warning).

The last check uses the orchestration object's status, defaulting to `"completed"` if
the field is missing. Unlike `workflow_success_pct`, this check does **not** fall
back to the manifest status.

The function returns task rows, summary metrics, and checks for the runner to place
in the orchestration report.

## 5. RAG evaluator

Function: [`eval_rag()`](./edrak_eval_agent_enhanced.py#L2148)

### What it evaluates

It evaluates a retrieval record: the query, returned chunks/documents, optional
answer, optional expected/reference answer, and retrieval scores.

### Per-chunk work

For every retrieved chunk, it records the chunk ID, query, a text excerpt, retrieval
score, and source. It flags chunks with empty text, a low score (below `0.3`), or a
score outside the valid range `[0, 1]`.

### Deterministic checks

It checks that a query exists, that retrieval returned documents, that retrieved
documents contain text, that scores are present and within `[0, 1]`, and that chunks
are not near-duplicates. Checks that require chunks are skipped when no chunks exist.

### LLM metrics

If judging is enabled and both a query and documents are present, it judges
**context relevancy**. This check is performed by `eval_rag()` based on its own
eligibility conditions; it is not gated by the worker-only `--context-relevancy`
setting.

It also judges **context precision** and **context recall** only when an expected or
reference answer is available. That answer can be in the RAG record or, when absent,
the runner may find a suitable answer in the golden dataset. Without a reference
answer, those metrics are marked not assessable rather than scored.

If an answer is present, it creates an answer row containing the judge scores,
reasons, notes, and threshold-based pass values.

## 6. Telemetry evaluator

Function: [`eval_telemetry()`](./edrak_eval_agent_enhanced.py#L2300)

### What it evaluates

It reads trace spans, events, and explicit tool-call records. It creates rows for
trace events and worker spans, recording fields such as worker, task ID, attempt,
tool/event name, status, duration, errors, and flags.

### Deterministic checks

It checks that telemetry has recognizable events or tool calls, that errors are
recorded, and that worker spans include agent/task identity, successful process
status, and a valid non-negative duration. It flags trace rows with errors or
missing/incomplete worker information.

It also calls `eval_execution_trace()` on the telemetry payload to evaluate tool
execution details, then includes those checks and results.

## 7. Execution-trace quality evaluator

Function: [`eval_execution_trace()`](./edrak_eval_agent_enhanced.py#L1748)

This is a helper used for tool traces; it is not a separately registered file type.
The runner may attach its result to worker or RAG evaluations when a trace is
available, and `eval_telemetry()` also invokes it.

### What it evaluates

It normalizes recorded tool calls and checks up to six dimensions:

1. **Tool selection:** Are calls within declared/available tools or known worker
   policies?
2. **Tool arguments:** Do recorded arguments match available tool schemas?
3. **Tool-result use:** Does the final output use the recorded results appropriately?
4. **Workflow sequence:** Does the observed order include the expected steps?
5. **Error handling:** Were failures handled, retried, or explicitly surfaced?
6. **Efficiency:** Are there exact repeated calls with the same tool and arguments?

### Deterministic versus LLM assessment

Tool-set checks, schema validation, expected-sequence checks, recorded retry/fallback
fields, and duplicate-call detection are deterministic when the trace contains
enough information. A dimension can be `assessable`, `partial`, or
`not_assessable`; missing trace fields are not treated as proof of failure.

When the judge is enabled, it can use GEval for dimensions that require interpretation.
Some are conditional:

- Tool selection may use the LLM when deterministic policy information is insufficient.
- Tool argument value suitability and tool-result use require `--include-tool-payloads`.
- Efficiency judgment also requires payload/argument information.
- Workflow-sequence judgment requires an expected sequence.
- Error-handling judgment requires observed errors.

Tool payloads are redacted before optional judging. Without any structured calls, the
dimensions are marked not assessable and a warning check is returned.

## 8. Pipeline-wide checks

Function: [`pipeline_checks()`](./edrak_eval_agent_enhanced.py#L2397)

These checks are run after the individual stage evaluators. They connect the outputs
across stages rather than evaluating one file in isolation.

When the relevant stages exist, they check the traceability chain:

```text
decision recommendation -> cross-signal -> verified finding -> evidence
```

They also check that the verifier covers worker findings, that signals rest on
findings (and, when verification data exists, verified findings), and that
verification, cross-signal, and decision outputs use a consistent research-run ID.

For repeated worker or verification passes, they check whether finding IDs were
reused for changed statements and whether a later verification downgraded a finding
that was previously verified.

The checks contribute to the evaluator's final exit status. The current
`pipeline_report.json` stores aggregate counts (`total`, `passed`, `failed`, and
`warnings`), not the detailed pipeline-check entries.

## What is not an active evaluator

`eval_metrics()` appears as commented-out code in the Python file and is not
registered in `EVALUATORS`. The runner currently skips files classified as
`metrics`; those files do not get a standalone metrics-stage evaluation.

## Where results go

Per-stage results are written by `EvalAgent.write_all()` to files such as
`verification_report.json`, `cross_signal_report.json`, `decision_report.json`,
`orchestration_report.json`, `rag_report.json`, and `telemetry_report.json`.
Each report contains stage metrics, checks, and flagged units. Execution-quality
dimensions are included where applicable. The runner also writes
`pipeline_report.json` with aggregate pipeline-check counts.

## Quick comparison

| Evaluator | Main unit | Main purpose | LLM judging |
|---|---|---|---|
| `eval_verification()` | Verified finding | Check verifier labels against evidence and worker findings | Faithfulness, relevancy |
| `eval_cross_signal()` | Signal | Check synthesis against supporting findings | Faithfulness, relevancy; insight when extra metrics are enabled |
| `eval_decision()` | Recommendation; optional action and summary | Check decision support, grounding, linkage, and actionability | Faithfulness, relevancy; actionability when enabled; action checks with `--deep` |
| `eval_orchestration()` | Planned task | Compare plan, results, dependencies, and workflow status | No direct LLM call |
| `eval_rag()` | Retrieved chunk; optional answer | Check retrieval data and context quality | Context relevancy; precision/recall with reference answer |
| `eval_telemetry()` | Trace event/worker span | Check telemetry completeness and errors | Indirectly, through execution-trace evaluation |
| `eval_execution_trace()` | Tool-call trace | Assess tool use, arguments, sequence, failures, and repetition | Conditional GEval judgments |
| `pipeline_checks()` | Cross-stage links | Check decision-to-evidence traceability and rerun consistency | No direct LLM call |
