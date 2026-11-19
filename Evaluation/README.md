# Edrak Optimized vs GraphRAG comparison engine

A small, dependency-free Python CLI that compares the `competitor_intelligence`
worker in two Edrak combined evaluation-run JSON files.

## What it does

- Extracts the existing Edrak evaluation fields:
  - `evaluation.coverage_score`
  - `evaluation.worker_result_quality.metrics.correctness`
  - `...faithfulness`
  - `...completeness`
  - `evaluation.deterministic_checks.evidence_reference_rate`
  - `...reference_resolution_rate`
  - `...findings_with_unsupported_numbers`
  - `evaluation.scope_compliance.scope_policy_pass`
- Produces a weighted score with these starting weights:
  - Correctness 25%
  - Faithfulness / evidence integrity 25%
  - Completeness 20%
  - Mandatory coverage 20%
  - Numeric grounding 10%
- Does not treat unavailable metrics as zero. It excludes them and renormalizes the
  available weights.
- Checks that the two runs use the same worker, task goal, ground-truth dataset,
  snapshot date, and mandatory requirement IDs.
- Refuses to declare a winner if the reports are not comparable.
- Writes both JSON and Markdown comparison reports.

## Run

Python 3.10+; no third-party packages required.

```bash
python -m version_comparison.compare \
  --optimized path/to/optimized/evaluation_run.json \
  --graphrag path/to/graphrag/evaluation_run.json \
  --worker competitor_intelligence \
  --out-dir reports/version_comparison
```

If the package is run from the project root, use the paths to the two evaluation
run JSON files. The output files are:

- `reports/version_comparison/comparison.json`
- `reports/version_comparison/comparison.md`

## Important experiment rules

1. Use the exact same user task, target company, competitors, regions, time range,
   research requirements, golden dataset, and evaluation configuration.
2. Keep the LLM model, temperature, token limits, web/search tools, and tool-call
   budgets identical where possible. Document any unavoidable difference.
3. Run each version at least 3 times; 5 is preferable. Compare the mean and
   standard deviation, not just one run.
4. Do not compare reports generated from different task goals or different
   benchmark versions. This tool blocks winner selection in that situation.
5. Keep GraphRAG-specific metrics as a separate evaluation extension:
   entity/relationship extraction precision and recall, relationship retrieval
   relevance, multi-hop reasoning accuracy, graph-grounded faithfulness, and
   incremental value over vector RAG. Do not reward graph size or number of
   relationships by itself.

## Score interpretation

The overall score is a weighted benchmark score, not a probability that the
agent's output is true. Evidence reference rate and reference resolution rate
only confirm that citations exist and resolve; they do not establish that the
source actually supports the associated claim. Review unsupported numbers and
high-impact claims manually or with a claim-to-evidence verification evaluator.

## Current limitations

- The script compares one run per version. Repeated-run aggregation (mean,
  standard deviation, paired confidence intervals) should be added once the
  per-run outputs are stable.
- Runtime, token usage, and monetary cost are not scored unless those values are
  added to the report schema.
- GraphRAG-specific metrics are not inferred from the report. Add explicit
  evaluator output before including them in the score.
