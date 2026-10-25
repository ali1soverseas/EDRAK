# EDRAK Evaluation Process and Comparison

## Purpose

EDRAK evaluation examines the quality, integrity, and execution of a completed analysis. It helps the team identify strengths, gaps, and areas to improve; it does not rerun the analysis or make business decisions.

The process has two complementary views:

1. **Run evaluation** checks the artifacts and recorded execution of one EDRAK run.
2. **Reference comparison** compares the run's outputs with a selected golden reference dataset.

These views answer different questions and should be presented separately.

## Evaluation questions

The process is designed to answer four practical questions:

1. **Did the workflow execute as planned?** Check planned tasks, task results, dependencies, retries, terminal status, and recorded execution traces.
2. **Are the outputs structurally sound and traceable?** Check evidence references, verification coverage, and links from recommendations back to signals and source findings.
3. **Are the outputs useful and grounded?** Review quality judgments and the evidence behind flagged units.
4. **How does the output compare with the reference?** Check stage-level matches, missing reference items, contradictions, and useful novel output.

No single score answers all four. A technically successful run may have weak content coverage, and a strong match score does not prove that the workflow executed reliably.

## End-to-end process

```text
Business request
      ↓
EDRAK executes its planned research and decision workflow
      ↓
Run artifacts and telemetry are saved
      ↓
Run evaluation checks integrity, quality, and execution
      ↓
Optional comparison matches outputs to a golden reference
      ↓
Reports are reviewed in the evaluation dashboard
      ↓
Findings inform human review and system improvement
```

### 1. Run EDRAK and capture the evidence

An evaluation uses a completed run bundle, normally saved under `artifacts\evaluation_runs\<run-id>`. The evaluator needs the request and plan, worker outputs, verification and downstream artifacts, run status, and available telemetry. It uses these saved artifacts; it does not replay workers or repeat retrievals.

Telemetry records workflow and tool-call information, such as task identity, status, duration, and result counts. It supports execution and traceability checks without requiring the evaluator to repeat tool calls.

The default run-bundle behavior selects final worker attempts and the latest relevant downstream artifacts. This prevents superseded results from being counted as if they were part of the final answer. Use the all-attempts option only when the goal is to diagnose retries or changes between attempts.

### 2. Evaluate the run

The evaluation agent produces several kinds of findings:

| Evaluation area | What it checks |
|---|---|
| Deterministic integrity | Structure and references, evidence resolution, required coverage, declared dependencies, and other checks that can be verified directly from saved artifacts. |
| LLM-judged quality | Where the necessary context is available, whether outputs are faithful to evidence and relevant to the request. Optional metrics can assess qualities such as specificity, insight, and actionability. |
| Retrieval (RAG) | Whether retrieval records are structurally sound and whether retrieved context is relevant when judge inputs are available. Latency comes from recorded telemetry. |
| Verification and synthesis | Whether verification labels and support are coherent, and whether cross-domain signals resolve to appropriate findings and evidence. |
| Decisions and recommendations | Whether recommendations and related options, risks, and opportunities trace back to supporting signals and evidence. |
| Orchestration and telemetry | Planned-task completion, failures, retries, dependencies, workflow status, duration, and the quality of the recorded execution trace. |
| Pipeline traceability | Whether references connect the decision back through signals and findings to evidence. |

Deterministic checks and quality judgments are reported separately. A passed integrity check does not by itself mean an output is high quality; likewise, a missing or unavailable score is not a zero.

#### What is reviewed at each stage

| Stage | Main review focus |
|---|---|
| Research workers | Finding-level evidence support, resolvable evidence IDs, source coverage, confidence and limitations. Domain-specific checks apply to internal, competitor, market, and customer/trends work. |
| Retrieval (RAG) | Query and returned chunks, empty or duplicate content, score validity, source coverage, and context relevance where assessable. |
| Verification | Whether worker findings are covered, labels and evidence are coherent, contradictions or gaps remain visible, and later passes do not silently weaken support. |
| Cross-signal synthesis | Whether supporting findings resolve, support is diverse and verified where required, and confidence is appropriate to that support. |
| Decision and recommendations | Whether risks, opportunities, options, and actions trace to signals and evidence, and whether assumptions, prerequisites, limitations, and success criteria are clear. |
| Orchestration | Whether planned tasks have results, dependencies are consistent, failures and partial results are visible, and retries/replans are recorded. |
| Telemetry and pipeline | Whether trace data is recognizable and useful, and whether references connect decisions to signals, findings, and evidence. |

#### Quality judgments and assessability

Faithfulness asks whether a statement is supported by the supplied evidence. Relevancy asks whether it addresses the request. Optional judgments can assess specificity, insight, actionability, or evidence-context relevance. These judgments are applied to selected output units with the relevant goal, evidence, or retrieval context.

Some metrics require inputs that a run may not contain. For example, retrieval context precision/recall needs an expected answer, and retrieval latency needs a matching telemetry span with duration. In these cases the report should say unavailable or not assessable and give a reason. Do not interpret missing judge results as zero or as a pass.

Deterministic checks may report errors, warnings, informational observations, or not-assessable results. An error identifies a concrete integrity issue; a warning calls for review and is not always proof that the content is wrong. Review the check detail and affected unit before deciding on severity.

### 3. Compare with a golden reference

The comparison aligns the run's outputs with expected reference items for the relevant task. It covers research-worker findings, verified findings, cross-domain signals, and decision outputs such as summaries, opportunities, risks, actions, and options.

The comparison uses an LLM judge to assess semantic equivalence rather than requiring identical wording. Unmatched reference items receive a rescue pass: OpenAI text embeddings rank likely candidates by cosine similarity, then the LLM judge rechecks those candidates. Optional settings can test consistency across ordering or split long outputs into smaller claims.

The comparison is organized by stage so the team can see where expected coverage is strong or weak, rather than relying only on one overall score.

The comparison also checks whether the run is comparable to the reference at the request level, including the business goal and relevant company, targets, or markets. Its orchestration comparison checks expected versus actual worker coverage and task counts. These checks provide context: a content score is harder to interpret if the evaluated request or planned scope differs.

For each reference item, the judge records a verdict:

| Verdict | Meaning |
|---|---|
| Full | The run covers the same claim, even if wording or examples differ. |
| Partial | The run addresses the theme but covers only part of the expected claim or a different angle. |
| None | No run output addresses the reference item. |
| Contradicts | A run output states the opposite of the reference claim. |

One reference item may be supported by multiple run items. The report retains match explanations and linked outputs so reviewers can inspect the basis for a verdict. An optional consistency mode compares a second judgment with the output ordering reversed; it reports agreement and applies the selected merge policy.

#### Golden dataset

The golden dataset is a separately maintained reference for evaluation. It is used to support consistent comparisons across runs and use cases. Its contents are intentionally not described in detail in this guide.

Reference coverage and curation can differ. Check the dataset's curation status before drawing conclusions: comparisons against incomplete or provisional references are directional, not benchmark certification. Keep the reference independent of the run being evaluated; do not modify it simply to make a run score better.

### 4. Read the comparison metrics

| Metric | How to interpret it |
|---|---|
| Recall | Proportion of reference items receiving a full or partial match. It measures coverage, not correctness of every extra output. |
| Full recall | Proportion of reference items receiving a full match only. |
| Graded recall | Credits full matches as 1, partial matches as 0.5, and none/contradictions as 0, then averages over reference items. |
| Weighted recall | Like graded recall, but reference items marked must/should/nice receive weights of 3/2/1. |
| Must recall | Coverage of items explicitly designated must; it is unavailable when no must-items are present. |
| Alignment (strict precision) | Share of run items that were used to support at least one reference item. |
| Adjusted precision | Alignment that additionally credits extras classified as valid, relevant novel findings. |
| Noise rate | Share of run items judged as noise among the stage's outputs. Useful novel findings are not counted as noise. |
| Contradiction rate | Proportion of reference items with a contradictory verdict. |
| F1 / adjusted F1 | Harmonic mean combining recall with strict alignment or adjusted precision, respectively. |
| One-to-one recall | Coverage when each run item can be assigned to at most one reference item; helps reveal when a single broad output is credited for many expected items. |
| Duplicate rate | Share of run items identified as highly overlapping with an earlier item. |

For example, recall may be high while alignment is low if most reference items are covered but the run also contains many unrelated outputs. Conversely, alignment may be high with low recall if the run is concise and relevant but misses important expected content. Graded and weighted metrics add nuance; inspect the underlying verdicts to understand the score.

Scores are calculated per stage and then summarized. The overall comparison report includes an unweighted macro-average across use-case reports, while pooled metrics weight results by item counts. These aggregation methods answer different questions: macro averages give each case equal influence, while pooled values give more influence to cases with more scored items. State which summary is being shown.

### 5. Aggregate and review

Per-run reports can be summarized by use case and across use cases. The aggregate view can include macro-average comparison metrics, per-stage strengths and weaknesses, and orchestration completion, workflow success, and retry information.

The evaluation dashboard presents:

- **Overview:** run identity, evaluation health, and notable issues.
- **Pipeline:** per-agent checks, quality metrics, domain-specific measures, and flagged units.
- **Ground truth:** stage-by-stage comparison with the selected reference.
- **Overall performance:** aggregate results across supported use cases.

Use detailed reports and flagged units to understand why a score changed. Treat aggregate scores as a navigation aid for review, not as a substitute for inspecting evidence and context.
