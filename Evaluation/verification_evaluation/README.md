# Edrak Verification evaluation

## Files
- `verification_golden.json`: 19 findings from the supplied trace, with original evidence, original Verification status, and independently reviewed expected status.
- `baseline_metrics.json`: deterministic comparison of the original trace against the reviewed labels.
- `run_verification_eval.py`: deterministic metrics plus DeepEval LLM-judge metrics.
- `../golden/combined_golden_dataset.json`: consolidated worker, verification, Cross-Signal, and Decision Analysis evaluation rubrics.

The combined dataset rewrites the output-derived competitor and trace-specific customer-trends expectations as reusable evidence-based rubrics. Its Cross-Signal and Decision Analysis sections contain criteria, not run-output examples. Use its `golden_reference` only for evaluation, never as agent input.

## Important evaluation rule
The expected labels are human-reviewed references. Do not pass `golden_reference` to the Verification Agent when running it. It is only for evaluation after the agent has produced its result.

## Run
```bash
pip install deepeval scikit-learn
# Add OPENAI_API_KEY to the repository .env file.
# Optional: set OPENAI_MODEL there (defaults to gpt-4o-mini).
python run_verification_eval.py --golden verification_golden.json --predictions actual_verification_trace.json --output evaluation_report.json
```

If `--predictions` is omitted, the script evaluates the original trace statuses saved in the golden file as a baseline.

## Combined evaluation pipeline

The generic evaluator can run worker evaluations and the verification evaluation in one invocation:

```bash
python ../generic/evaluator.py --all-workers --repo-root ../.. --out-dir ../pipeline_output
```

To supply the consolidated worker benchmarks explicitly:

```powershell
.\env\Scripts\python.exe Evaluation\generic\evaluator.py --all-workers --repo-root . --ground-truth Evaluation\golden\combined_golden_dataset.json --out-dir Evaluation\pipeline_output
```

The pipeline searches for the verification golden file and, when available, the trace referenced by its `trace_id` or the saved `NewReport/verify_report.json`. Pass `--verification-predictions path/to/actual_verification_trace.json` to select a predictions file explicitly. It writes the verification report and includes its metrics alongside worker results in `all_worker_summary.json`.

To evaluate a specific run's worker artifacts and the Verification, Cross-Signal, and Decision Analysis stage artifacts:

```powershell
$requestId = "your-request-id"
.\env\Scripts\python.exe Evaluation\generic\evaluator.py --run-dir "artifacts\runs\$requestId" --ground-truth Evaluation\golden\combined_golden_dataset.json --out-dir "Evaluation\run_evaluations\$requestId"
```

This mode requires the combined golden dataset and evaluates each worker and all three stages against its rubric. LLM judging is mandatory; second-pass retrieval and review is mandatory for findings initially judged missing or partial. The 19-case Verification golden remains a trace-specific benchmark; run artifacts use the separate general Verification-stage rubric. Judge failures or incomplete required second-pass reviews are reported as evaluation errors.

To continue an interrupted run without repeating completed workers, reuse the same output directory and choose the first unfinished pipeline step. For example, `--resume-from market_intelligence` requires valid completed evaluations for the three earlier workers, then reruns Market Intelligence and all three stages:

```powershell
.\env\Scripts\python.exe Evaluation\generic\evaluator.py --run-dir "artifacts\runs\$requestId" --ground-truth Evaluation\golden\combined_golden_dataset.json --out-dir "Evaluation\run_evaluations\$requestId" --resume-from market_intelligence
```

The evaluator sends trace/artifact content to OpenAI for judging; second-pass retrieval embeddings use local sentence-transformers by default. Confirm OpenAI is approved for the data before running. The judge loads `OPENAI_API_KEY` from the repository `.env`; optional settings are `OPENAI_MODEL`, `OPENAI_BASE_URL`, and `OPENAI_TIMEOUT_S`. The default model is `gpt-4o-mini`. There are no `--no-judge`, `--no-second-pass`, or `--judge` switches; the quality and benchmark evaluation cannot be silently skipped.

## Baseline from this trace
- Findings reviewed: 19
- Expected verified: 11
- Expected insufficient: 8
- Original agent verified: 18
- Original agent insufficient: 1
- Accuracy: 0.632
- Precision (verified): 0.611
- Recall (verified): 1.000
- F1 (verified): 0.759
- False-verification rate among agent-verified findings: 0.389

## DeepEval metrics
- `GEval` — checks whether the predicted verification decision matches the expected label and whether the complete claim is supported by evidence.
- `FaithfulnessMetric` — checks whether the assessment output is grounded in the attached evidence.
- `ContextualRelevancyMetric` — checks whether the attached evidence context is relevant to the finding/assessment.

DeepEval scores are LLM-judge scores, not replacements for deterministic status comparison. They require a configured model/API key. The script records a `not_run` status rather than inventing scores if DeepEval cannot execute.

## Review caveats
Some labels are intentionally scope-sensitive:
- Internal context findings are verified only as statements about the supplied EDRAK context.
- Market-size findings refer to the broader US AI data-center market, not the AI-accelerator market alone.
- Old 2024 claims are marked insufficient when they are worded as current 2026 claims without dates.
- The Broadcom financing announcement does not prove a direct paying-customer relationship for accelerator products.
