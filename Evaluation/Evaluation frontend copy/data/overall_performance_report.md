# Overall performance across ground-truth comparisons

Generated: 2026-10-22T04:08:53.034939+00:00

Method: compare.py llm judge (gpt-5.1)

Unweighted macro-average across the three use-case comparison reports.

## Overall averages

| Metric | Average |
|---|---:|
| Mean recall | 58.3% |
| Average pooled recall | 59.4% |
| Mean graded recall | 35.7% |
| Mean weighted recall | 35.7% |
| Must-item recall | 0.0% |
| Mean alignment | 58.7% |
| Mean F1 | 54.3% |
| Mean noise rate | 22.5% |
| Mean contradiction rate | 0.4% |

## Orchestration execution

- Successful tasks: 8 / 10 (80.0% pooled task completion).
- Mean per-run task completion: 75.0%.
- Mean workflow success: 75.0%.
- Partial worker outputs: 5; failed tasks: 2; incomplete tasks: 0.
- Retry/replan events: 2; successfully completed retried tasks: 4.

## Per-use-case comparisons

| Use case | Ground truth | Recall | Graded | Alignment | F1 | Noise | Task completion | Workflow success | Successful retry rate | Strongest stages | Weakest stages |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Competitive intelligence | partially_curated | 53.7% | 33.8% | 52.8% | 48.7% | 28.7% | 50.0% | 50.0% | 50.0% | decision/recommended_actions, decision/risks, decision/opportunities | decision/options, cross_signal, decision/executive_summary |
| Market entry | uncurated | 64.1% | 39.1% | 56.3% | 57.0% | 30.0% | 100.0% | 100.0% | 75.0% | workers/internal_intelligence, workers/competitor_intelligence, decision/opportunities | workers/customer_trends, decision/risks, decision/executive_summary |
| Product launch | partially_curated | 57.2% | 34.3% | 67.0% | 57.1% | 8.8% | 75.0% | 75.0% | 0.0% | decision/options, workers/customer_trends, cross_signal | workers/internal_intelligence, workers/competitor_intelligence, workers/market_intelligence |

## Interpretation

- Across 3 use cases, macro-average recall is 58.3%; mean graded recall is 35.7%.
- Market entry has the highest mean recall (64.1%); Competitive intelligence has the lowest (53.7%).
- Stages with zero recall in at least one comparison: decision/options.
- Competitive intelligence: strongest stages were decision/recommended_actions, decision/risks, decision/opportunities; weakest were decision/options, cross_signal, decision/executive_summary.
- Market entry: strongest stages were workers/internal_intelligence, workers/competitor_intelligence, decision/opportunities; weakest were workers/customer_trends, decision/risks, decision/executive_summary.
- Product launch: strongest stages were decision/options, workers/customer_trends, cross_signal; weakest were workers/internal_intelligence, workers/competitor_intelligence, workers/market_intelligence.
- Average noise rate is 22.5%; review off-reference findings for relevance rather than treating every extra as incorrect.
- Orchestration completed 8 of 10 planned tasks (80.0% pooled task completion); mean workflow-success status was 75.0%.
- 2 retry/replan event(s) were observed; 4 retried task(s) ultimately completed successfully.

## Caveats

- LLM-judged comparisons are model-based and may vary; treat semantic matches and scores as approximate.
- Ground-truth datasets are not fully curated; results are directional, not benchmark certification.
- Competitive intelligence ground truth is partially_curated.
- Market entry ground truth is uncurated.
- Product launch ground truth is partially_curated.
