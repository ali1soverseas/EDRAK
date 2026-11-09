# EDRAK Evaluation Process

This guide explains what the EDRAK evaluation agent evaluates, how it produces scores, and where to find the results. The evaluator audits artifacts from an EDRAK run; it does not rerun workers or make business decisions.

## At a glance

```text
EDRAK run artifacts
        |
        v
Load and classify input JSON
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
Per-agent reports + pipeline checks
        |
        +--> Optional golden ground-truth comparison
        |
        v
Read-only evaluation API and dashboard
```

Backend runs save evaluator inputs and stage outputs under
`artifacts/evaluation_runs/<analysis-id>/`. The evaluator reads those JSON artifacts directly;
it does not rerun workers or collect runtime telemetry.

## 1. Inputs and scope

Backend runs save evaluator inputs under:

```text
artifacts/evaluation_runs/<run-id>/
├── inputs/       # workers, RAG retrievals, verification, synthesis, decisions
├── outputs/      # stage outputs such as metrics and human review
├── manifest.json # run status and timestamps
└── evaluation/   # generated evaluation reports
```

The evaluator recognizes artifacts by their JSON structure. It builds shared run context from inputs such as the research goal, worker findings, evidence, verified findings, and signals. That context lets checks compare stages and keeps each downstream evaluation grounded in what was available at that point in the run.

For run bundles, the default is to evaluate the final worker attempt and latest verification/downstream artifacts. Superseded worker and retrieval artifacts are skipped where they belong to an earlier attempt. `--all-attempts` opts into evaluating prior attempts and verification passes too.

The metrics stage is intentionally not evaluated as a separate agent. Its artifact may still be used as pipeline context.

## 2. Two kinds of evaluation

### Deterministic checks

These checks use the recorded JSON directly. They do not require an LLM and still run in `--dry-run` mode. Examples include:

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

Scores and thresholds are reported independently by metric. An unavailable score is not treated as zero.

## 3. What each agent evaluation does

Each agent type has its own evaluator. The evaluator uses the artifact's structure and relevant earlier-stage artifacts; it does not apply one generic checklist to every agent.

### Workers

Worker evaluation is unit-based: it checks individual findings and, where present, requirement answers and competitor profiles. A finding is judged against the evidence references attached to that finding, rather than against every document in the run. The evaluator also checks whether evidence IDs resolve and whether a finding's statements, numbers, source mix, confidence, limitations, and status are internally consistent.

| Worker type | Additional emphasis |
|---|---|
| Internal intelligence | Internal evidence and synthetic/adapted evidence are allowed, but synthetic-only findings need an explicit limitation. |
| Competitor intelligence | Competitor coverage, profiles, and requirement-to-finding links. |
| Market intelligence | Grounding, sources, claims and caveats for market, industry, economic, regulatory, or geographic findings. |
| Customer & trends intelligence | Grounding and source diversity for customer needs, sentiment, trends, and behavior claims. |

All worker types use Faithfulness and Relevancy by default. `--extra-metrics` enables the additional configured metrics by worker type (for example, specificity, insight, or actionability). `--context-relevancy` adds a separate judgment about whether a finding's evidence context is relevant to the research goal. Competitor profile overviews are checked against that competitor's findings.

Worker integrity checks cover evidence resolution and grounding, requirement links, claim quality, and competitor-specific structure. The report exports dashboard-rendered judge summaries and unit counts; other diagnostic aggregates are not calculated or exported.

Some measures are only meaningful when the input provides the corresponding field. Missing metadata is not automatically evidence of poor research; inspect each check's status and detail.

### RAG retrieval

RAG evaluation operates on each recorded retrieval: a query plus the chunks returned for that query. It has two layers:

1. **Retrieval integrity (deterministic):** checks query presence, whether any chunks were returned, empty chunk text, score coverage and score range `[0, 1]`, and duplicate/near-duplicate chunks.
2. **Retrieved-context quality (LLM, when inputs allow):** Context Relevancy assesses the retrieved text in relation to the query. Context Precision and Context Recall require a reference/expected answer. RAG retrieval records do not score Answer Relevancy or Faithfulness because they represent retrievals, not generated answers; those metrics remain available for worker and downstream outputs that include an answer. If required inputs for a metric are absent, it is not run.

For example, high retrieval scores do not prove that the chunks answer the query. Conversely, a low Context Relevancy score says the retrieved text did not relate well to the query, even if retrieval returned non-empty chunks and valid numeric scores. Read the deterministic retrieval checks and the LLM score together.

### Verification

Verification evaluation checks the verifier's labels and the provenance behind them. It compares verifier findings with worker findings by ID, resolves their cited evidence, and examines evidence quality, confidence, contradictions, missing information, rewrites, and near-duplicates. The report exports dashboard-rendered judge summaries and unit counts; diagnostic confidence and verification aggregates are not exported.

When Faithfulness judgments are available, the evaluator compares them with the verifier's `verified`/not-verified labels:

- **False verification:** verifier marked a finding verified, but the independent Faithfulness judgment failed.
- **Rejected but faithful:** verifier did not mark a finding verified, but the independent Faithfulness judgment passed.

These are diagnostic comparisons, not a replacement for human review. The evaluator also checks counts, coverage of worker findings, open contradictions/gaps on verified findings, confidence calibration, and whether a later verification pass downgrades previously verified findings.

### Cross-signal synthesis

Cross-signal evaluation checks whether each signal's supporting finding IDs resolve and whether the support is appropriately diverse and verified. Diagnostic support counts and confidence aggregates are not exported.

Checks flag signals with unresolved support, convergence claims based on one worker, unverified support, entities/domains not present in the cited support, confidence above the support, and mismatches between the signal list and decision-ready context. Faithfulness and Relevancy are the default quality judgments; Insight is available with `--extra-metrics`.

### Decision and recommendations

Decision evaluation follows recommendations back to their supporting signal IDs, then through those signals to findings and evidence. It checks recommendation coverage, opportunities, risks, priorities, options, action prerequisites, success criteria, human-approval markers, duplicate actions, and dangling cross-references.

Faithfulness and Relevancy are the default judgments. Actionability is available with `--extra-metrics` and evaluates the full recommendation, including actions, success criteria, and limitations. `--deep` additionally judges each recommended action. Deterministic checks also flag new unsupported numbers, past timelines, missing limitations/success criteria, and references to invalid signals.

Recommendations support human decision-making; the evaluator does not approve or execute them.

### Orchestration

Orchestration evaluation uses the plan and task results:

- **Task completion rate:** planned tasks with a successful result divided by planned tasks.
- **Workflow success rate:** task completion for successful workflow statuses, zero for failed/partial/cancelled statuses, and otherwise not assessable.

Partial results with generated worker output count as completed executions. Missing results reduce task completion and are separately flagged by the planned-task integrity check. Unrendered retry, dependency, failure-rate, and duration aggregates are not calculated or exported.

### Pipeline-wide checks

The pipeline report performs cross-stage checks such as decision → signal → finding → evidence traceability, verifier coverage, research-run ID consistency, and stability of finding IDs/statements across reruns. It records both summary counts and the individual check results. Checks only run when the relevant stages and evidence are present.

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

Dimensions with missing evidence are marked **not assessable** rather than inferred. Tool argument values can be fingerprinted locally for duplicate checks; by default, raw values are not sent to DeepEval.

## 5. Runtime and rate-limit safeguards

Each evaluation run sends eligible metrics to the judge; successful results are not persisted or reused between runs.

Current defaults are intentionally conservative:

- One judge request at a time (`--workers 1`).
- At most two attempts per metric (`--retries 2`).
- After repeated rate-limit responses for a metric, a run-scoped circuit breaker skips subsequent judge requests instead of spending the remainder of the run retrying.

Judge reporting distinguishes successful scores, failed metrics, and skipped requests. The circuit breaker limits wasted time during rate limiting; it cannot guarantee faster evaluation when the judge service is responding normally.

## 6. Reports and dashboard

The evaluator writes reports to `evaluation/` for a run bundle, or to the path supplied with `--out`.
A comparison requested with `--compare-to` is saved by default as
`reports/compare/<run-id>_<ground-truth-name>.json`. Typical evaluation outputs include:

- `<agent>_report.json` — dashboard-rendered per-file metrics, checks, flagged units, and execution-quality details.
- `pipeline_report.json` — summary counts and individual cross-stage check results used for run-level traceability.

Orchestration task completion counts a task as successful when its final result is successful or contains
generated worker output, even if it also records an error for skipped work. A failed/error result with no
generated output is a task failure; a planned task with no result is missing. The dashboard receives
planned/result/successful task counts, task completion, and workflow success; retry, dependency, failure-rate,
partial-task, and duration aggregates are not computed or exported.
For RAG context precision and recall, the evaluator uses an explicit reference answer on the RAG record
when present; otherwise, it matches the run's `business_context.use_case` to
`golden/ground_truth/*/inputs/initial_input.json` and looks for an exact normalized query in
`inputs/rag_internal_intelligence.json`. If no query-specific answer matches, the evaluator may use its
legacy embedding-based fallback over the golden worker file's expected output/findings. Precision and
recall remain not assessable when neither source provides a reference answer.
The Competitive Intelligence golden record includes an explicit scope-constraint reference for internal
RAG queries: no confidential NVIDIA data was supplied, so use only public, source-supported evidence.

The dashboard Overview shows run-level health and metadata from the selected reports. Include `initial_input.json` and `manifest.json` with the agent reports when available to populate the goal and run ID. Include `pipeline_report.json` to show cross-stage traceability checks. The Pipeline page displays these checks in a separate **Pipeline-wide checks** panel above the per-agent reports. Use the per-agent cards for stage metrics, integrity checks, execution quality, and flagged units. Failed warning-level checks are omitted from the dashboard; it shows passed checks, error-severity failures, and flagged units. If an evaluation input contains tool-call arguments or results, those are redacted in local reports and are sent to the judge only when `--include-tool-payloads` is explicitly enabled. Unflagged unit-level rows and metrics not rendered by the dashboard are not exported in agent reports. The comparison view is a separate ground-truth comparison and should not be confused with integrity checks or LLM-judged averages.
Execution-quality/tool-trace checks are included for tool-using workers and RAG stages. Verification,
cross-signal, decision, and orchestration are not treated as tool-execution stages and do not receive
the "no structured trace evidence" check.
When opened with `?api=http://localhost:8000`, the dashboard polls the backend for new comparison
reports and opens a new result automatically after the evaluator command completes.
For local reports, use **Upload run reports** to select one run's agent `*_report.json` files from
its `eval_results` folder. Include that run's `inputs/initial_input.json` to display its goal, and
optionally include `manifest.json`, `pipeline_report.json`, and
`compare_report.json` in the same selection. Each upload creates a saved run
page in the sidebar; the currently open page is marked **Opened**. Uploaded reports are stored in
the browser's IndexedDB and restored after refresh. They stay in that browser profile and are not
sent to the backend. Clearing site data removes saved pages.

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
```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id> `
  --compare-to nvidia_product_launch_inputs
```

Ground-truth comparisons always use the configured LLM judge, so `--compare-to` cannot be combined
with `--dry-run`. Use `--compare-out PATH` to choose the comparison report path. `--compare-to`
requires `--run-bundle`.

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
