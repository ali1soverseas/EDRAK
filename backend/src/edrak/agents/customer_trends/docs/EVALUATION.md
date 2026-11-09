# Evaluation against the golden dataset

How the worker is scored with the evaluation suite (`Evaluation/`, imported from `feat/eval`), what
was changed to raise the score, and what the numbers do and do not show.

## How to run it

```bash
uv pip install --python .venv/bin/python deepeval        # once, the judge package
.venv/bin/python scripts/customer_trends/eval_golden.py --label mytry --runs 3
```

- Each run is a **live** run of `backend/evals/customer_trends/briefs/ai_accelerators.json` under
  a new run id (it spends provider quota), with `EVAL_TRACE_ENABLED=true`.
- `Evaluation/generic/evaluator.py` scores the trace against
  `Evaluation/golden/customer_trends_ground_truth_single_case.json` with an Ollama judge
  (`OLLAMA_MODEL`, `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`). Variables set in the shell win over `.env`.
- Reports go to `reports/customer_trends/<label>/run<N>/` (not committed); the mean over the runs
  is in `reports/customer_trends/<label>/summary.json`.
- `--rescore` scores the traces already in a label's folder again, without new runs.
- The second pass of the evaluator is off by default (it needs `sentence-transformers`, which pulls
  in torch); every number below was produced the same way.

## What the score is

The golden case is **one recorded run**: 13 mandatory findings such as "the 34-item sample contains
24 positive (70.6%) ...", "Broadcom had 9 mentions", "the social branch did not finish within 100
seconds". A judge model decides for each whether the report states it (covered 1, partial 0.5,
missing 0). Coverage is the mean. Consequences:

- Some criteria describe **that run's failures and counts**. A different live run has different
  counts, so the judge marks them missing even when the report states the same kind of fact with
  its own correct figures (the judge reasons say exactly this: "reports 80 dated items with a peak
  on 2026-09-14, whereas the expected output requires 33 of 34 ... 2024-06-03"). The agent was not
  tuned to reproduce those figures. CT02 and CT07 to CT11 are mostly of this kind.
- The data providers were **degraded** during these runs (Socialcrawl over quota, Apify runs not
  finishing, GDELT rate-limited, Google Trends not configured, Serper rejecting some queries), so
  most runs were `partial`. The original recorded run was degraded in the same way.
- The judge is noisy. The same code gave faithfulness between 0.3 and 0.9 in separate runs. Compare
  means over several runs, never single runs.

## What was changed

| Change | Where | Targets |
|---|---|---|
| An **evidence profile**: findings written by code from the stored metrics, with stable ids `p_scope`, `p_composition`, `p_sentiment`, `p_temporal`, `p_voice`, `p_comparability`, `p_engagement`, `p_language`, `p_gaps`. Each cites a stored metric and real evidence, passes `check_finding`, and states the limit of the number (sample not population, mentions not market share, zero engagement not zero real engagement). | `profile.py`, `nodes.write_findings` | CT01, CT02, CT08 to CT13 |
| The overall sentiment split of the analysed items is stored as the `sentiment_overview` metric (it was returned by the tool and then discarded). | `profile.store_sentiment_overview`, `nodes.analyze` | CT02 |
| The profile findings do **not** count toward `overall_confidence`: they describe the evidence and make no claim of their own, so they cannot lift a degraded run's confidence. | `assembly.overall_confidence` | CT13 |
| A failed collection branch names the providers and causes (`socialcrawl quota exceeded, serper not configured`), not only the error codes. | `nodes.BranchCapture.problems` | CT05, CT06 |
| The writer is told to write per-company and per-segment findings, to say when a segment or region has no evidence, and to mark one-example adoption claims as low confidence. | `prompts.WRITE_FINDINGS` | CT03, CT04, CT12 |
| `focus_terms` (segments, regions) reach the planner, branches and writer, and the scope finding reports which of them no item mentions. | `prompts._TASK_BLOCK`, `profile._scope` | CT01 |
| The evaluation trace (`eval_trace.json`) is written per run on request. It lists the computed metrics as evidence and adds the metrics a finding cites to that finding's evidence ids **in the trace copy only**; without this a judge reads a correct figure as unsupported. | `tracing.py` | measurement |

The model's findings are capped so that they and the profile together stay within the 20 findings
`submit_findings` accepts.

## Results (3 live runs per series, same judge)

| Series | Coverage (mean, each run) | Strict coverage | Relevancy | Correctness | Faithfulness | Completeness |
|---|---|---|---|---|---|---|
| Baseline (`feature/customer-agent`) | 0.04 (0.04, 0.04, 0.04) | 0.00 | 0.33 | 0.07 | 0.63 | 0.20 |
| Profile findings only (first version) | 0.13 (0.12, 0.15, 0.12) | 0.08 | 0.33 | 0.10 | 0.50 | 0.23 |
| All changes | 0.12 (0.19, 0.08, 0.08) | 0.10 | 0.23 | 0.13 | 0.50 | 0.23 |

Mean score per golden finding (baseline, profile only, all changes):

| | CT01 | CT02 | CT03 | CT04 | CT05 | CT06 | CT07 | CT08 | CT09 | CT10 | CT11 | CT12 | CT13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Baseline | 0.27 | 0.00 | 0.00 | 0.00 | 0.30 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.30 | 0.03 |
| Profile only | 0.40 | 0.03 | 0.00 | 0.03 | 0.30 | 0.40 | 0.00 | 0.07 | 0.00 | 0.17 | 0.03 | 0.70 | 0.07 |
| All changes | 0.43 | 0.00 | 0.00 | 0.00 | 0.53 | 0.07 | 0.00 | 0.13 | 0.03 | 0.10 | 0.03 | 0.63 | 0.13 |

The "profile findings only" series ran before the profile counts were stored as metrics and before the later
changes; "all changes" is the final code.

How to read it:

- **Coverage roughly tripled** (0.04 to about 0.12) and strict coverage went from none to about a
  tenth. The gain is in scope (CT01), vendor comparability (CT12) and the search-interest gap
  (CT05).
- **The provider-cause, prompt and `focus_terms` changes did not add a measurable gain** on top of
  the profile findings: the two later series differ by less than the run-to-run spread.
- **Faithfulness did not improve** (0.63 to 0.50). With values from 0.3 to 0.9 on identical code it
  cannot separate the series, so no claim is made about it either way.
- **Correctness stays near 0.1** and is out of reach for the same reason as the specific criteria:
  it compares against the recorded run's content.
- The baseline was re-scored after the trace fix above, so all three series are on the same footing.
  The first baseline scoring, before that fix, gave coverage 0.04, faithfulness 0.63.

## What would move the score further

- A golden dataset that states what a report must contain (the *kind* of fact, with its caveat),
  not one run's figures, or a frozen evidence snapshot replayed into the analysis and writing
  stages so the counts are fixed.
- Healthy providers. With social collection and search interest working, the findings that
  describe them (CT03, CT04, CT05, CT09) would have data to report.
- More runs per series, to bring the judge noise below the size of the effect being measured.
