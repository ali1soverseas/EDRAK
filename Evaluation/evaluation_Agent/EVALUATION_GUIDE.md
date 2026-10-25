# EDRAK Evaluation Agent Guide

This guide covers the complete evaluation workflow implemented by
[`edrak_eval_agent_enhanced.py`](./edrak_eval_agent_enhanced.py): accepted
inputs, evaluation stages, deterministic checks, LLM-judged metrics, execution
traces, reports, options, and troubleshooting.

The evaluator audits saved artifacts from an EDRAK run. It does **not** rerun
the workers, retrieval, verification, synthesis, or make business decisions.
For deeper explanation of the evaluation process and dashboard behavior, see
[`EVALUATION_PROCESS.md`](../../EVALUATION_PROCESS.md). For the detailed
retrieval-specific reference-selection flow, see
[`RAG_EVALUATION.md`](./RAG_EVALUATION.md).

## Evaluation at a glance

```text
Saved run bundle or JSON inputs
        |
        v
Load and classify artifacts
        |
        v
Choose final attempts (run bundle default)
        |
        +--> Stage-specific deterministic checks
        +--> Optional DeepEval quality judgments
        +--> Execution-quality checks from structured tool traces
        |
        v
Per-agent JSON reports + pipeline report
        |
        +--> Optional separate golden comparison
```

Three kinds of results must be interpreted separately:

- **Integrity checks** test artifact structure, IDs, references, consistency,
  or workflow properties.
- **Quality judgments** are model-based scores, run only when enabled and
  required inputs are available.
- **Execution-quality checks** inspect recorded tool calls and traces.

A passed integrity check is not a high quality score. A missing score is not
zero; the report records why a metric could not be assessed.
### Artifact classification

Artifacts are classified from their JSON shape. Recognized evaluation kinds
include:

| Kind | Typical artifact |
|---|---|
| `worker` | Worker result containing findings and worker identity |
| `rag` | Query and retrieved-document/chunk record |
| `verification` | Findings with verification decisions |
| `cross_signal` | Signals with supporting finding references |
| `decision` | Recommendations and supporting signal references |
| `orchestration` | Plan and task results |
| `telemetry` | Spans, events, or structured tool traces |
| `metrics` | Metrics-stage artifact used as context, not evaluated as a separate agent |
| `input` | Initial business request/context |

If a file does not match a recognized shape, it may not be evaluated. Ensure
the input has the expected top-level JSON fields and is readable.

### Final attempts

For run bundles, the default is final-state evaluation. The evaluator keeps
the selected final worker attempts and latest verification/downstream
artifacts; RAG records with task IDs are filtered against final worker tasks.
Use `--all-attempts` to include earlier attempts and verification passes.
This option changes which artifacts are evaluated, so results from the two
modes are not directly equivalent.

The metrics stage is not a separate evaluation row, but its artifact may be
used as context by other checks.

## Evaluation kinds

### Workers

Worker evaluation is performed on individual findings, plus applicable
worker-specific units such as requirement answers and competitor profiles.
For each finding, the evaluator checks its cited evidence, rather than using
all run evidence indiscriminately.

Checks and diagnostics include:

- Evidence references resolve and provide source text.
- Numeric claims can be grounded in cited context.
- Confidence and source metadata are available for analysis.
- Evidence source diversity and first-/third-party properties.
- Synthetic-only support and whether limitations are stated.
- Non-supporting evidence relationships, absence claims, duplicates, and
  worker-specific structures.
- Competitor findings address named competitors and requirement links when
  the worker output contains those structures.

Worker integrity checks are deterministic. Faithfulness and Relevancy are the
default LLM judgments; additional configured metrics are available with
`--extra-metrics`. `--context-relevancy` additionally judges whether a
finding's evidence context relates to the research goal.

### RAG retrieval

RAG evaluation is per recorded retrieval: a query and its returned chunks. It
checks query presence, non-empty retrieval, chunk text, score availability,
score range, and near-duplicates. It reports chunk-level rows and flags such
as `empty_retrieved_chunk`, `low_retrieval_score`, and
`invalid_retrieval_score`.

When the judge is available, it may run:

- **Context relevancy:** whether retrieved context relates to the query.
- **Context precision:** whether retrieved context is relevant to the
  expected answer; needs a reference answer.
- **Context recall:** whether retrieved context supports the expected answer;
  also needs a reference answer.

The current golden fallback may infer a reference from internal-worker
findings using embeddings. Because retrieved chunks participate in selecting
the findings, this reference is not independent of the retrieval being scored.
See [`RAG_EVALUATION.md`](./RAG_EVALUATION.md) for its exact behavior and
limitations.

RAG retrieval records are not scored for Faithfulness or Answer Relevancy
unless an answer is separately evaluated through an answer-capable stage.

### Verification

Verification evaluation compares verifier findings with worker findings and
their evidence. It checks finding-ID coverage, cited-evidence resolution,
verification labels, evidence quality, confidence, contradictions, missing
information, rewrites, and near-duplicates.

When Faithfulness judgments are available, they can be compared with
verification labels to identify findings marked verified despite weak
grounding, or findings rejected despite a passing faithfulness judgment.
These are diagnostics for review, not replacements for human verification.

### Cross-signal synthesis

Cross-signal evaluation checks whether each signal's supporting finding IDs
resolve and whether its claims are supported by sufficiently diverse and
verified findings. Checks can flag unresolved support, convergence claims
based on a single worker, unverified support, unsupported entities/domains,
confidence exceeding its support, and mismatches with decision-ready context.

Faithfulness and Relevancy are default judgments. Insight is available with
`--extra-metrics`.

### Decision and recommendations

Decision evaluation follows recommendations to supporting signal IDs, then
through signals to findings and evidence. It checks recommendation coverage,
opportunities, risks, priorities, options, action prerequisites, success
criteria, human-approval markers, duplicate actions, and dangling references.

Faithfulness and Relevancy are default judgments. `--extra-metrics` enables
Actionability, which evaluates the full recommendation. `--deep` additionally
judges individual recommended actions. Deterministic checks can flag
unsupported numbers, past timelines, missing limitations or success
criteria, and invalid signal references.

The evaluator reports on recommendations; it does not approve or execute
them.

### Orchestration

Orchestration evaluation compares planned tasks with recorded results. It
checks missing/unexpected results, declared dependency references, failed
tasks, and workflow status. It reports planned, returned, and successful task
counts, task completion percentage, and workflow success percentage when
status permits.

A task with generated output can count as completed even if its status is
partial. A task with an explicit failure and no generated output is treated
as failed. Missing tasks reduce completion and are also flagged by the
integrity check.



The pipeline report performs cross-stage checks when the necessary artifacts
exist. Examples include:

- Decision → signal → finding → evidence traceability.
- Verifier coverage of worker findings.
- Shared research-run IDs across downstream stages.
- Finding-ID stability when statements do not change.
- Verification not downgrading previously verified unchanged findings.

Checks are conditional on available stages and inputs. An omitted check may
mean its prerequisite artifact was unavailable, not that the relationship
passed.

## Shared quality metrics

| Metric | Meaning | Typical requirements |
|---|---|---|
| Faithfulness | Whether an output is supported by its supplied evidence/context | Output and usable context |
| Relevancy | Whether an output addresses its question or task | Output and question/goal |
| Context relevancy | Whether evidence/retrieved context relates to the query or goal | Query/goal and context |
| Context precision | Whether supplied/retrieved context is relevant to the expected answer | Context and expected answer |
| Context recall | Whether context supports the expected answer | Context and expected answer |
| Specificity | Whether a statement is concrete and decision-useful | Enabled with `--extra-metrics` where configured |
| Insight | Whether synthesis adds a supported pattern, gap, tension, or consequence | Enabled with `--extra-metrics` where configured |
| Actionability | Whether recommendations are specific, measurable, and supported | Enabled with `--extra-metrics` where configured |

The evaluator can use different criteria for different agent types. A metric's
availability depends on the stage, configuration, and artifact contents.
Scores are judge outputs, not deterministic facts; inspect reasons and source
artifacts when a result is surprising.

## Execution-quality evaluation

Structured traces can support evaluation of tool use. The dimensions that may
be assessed include:

| Dimension | What is checked |
|---|---|
| Tool selection | Calls against declared allowed/expected tools or worker policy |
| Tool arguments | Recorded argument names/types and supplied schemas |
| Result use | Whether tool results are reflected in the generated output |
| Workflow sequence | Whether calls follow a declared expected order |
| Error handling | Whether observed failures are handled, retried, or surfaced |
| Efficiency | Whether equivalent tool work is repeated |

Dimensions lacking required trace data are marked not assessable. Redacted,
bounded tool-result payloads are recorded for the dashboard by default, but
raw argument/result payloads are not sent to the judge by default.
`--include-tool-payloads` permits redacted tool arguments/results for eligible
judgments and should be used only when permitted by data-handling policy.

RAG and worker traces are correlated to task ID, attempt, and retrieval query
when those values are available.
