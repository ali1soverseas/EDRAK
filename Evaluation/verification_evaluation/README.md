# Edrak Verification evaluation

## Files
- `verification_golden.json`: 19 findings from the supplied trace, with original evidence, original Verification status, and independently reviewed expected status.
- `baseline_metrics.json`: deterministic comparison of the original trace against the reviewed labels.
- `run_verification_eval.py`: deterministic metrics plus DeepEval LLM-judge metrics.

## Important evaluation rule
The expected labels are human-reviewed references. Do not pass `golden_reference` to the Verification Agent when running it. It is only for evaluation after the agent has produced its result.

## Run
```bash
pip install deepeval scikit-learn
export OPENAI_API_KEY="..."
export DEEPEVAL_MODEL="gpt-4o-mini"  # optional
python run_verification_eval.py --golden verification_golden.json --predictions actual_verification_trace.json --output evaluation_report.json
```

If `--predictions` is omitted, the script evaluates the original trace statuses saved in the golden file as a baseline.

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
