# Market agent: published evaluation, optimized, this run, and original run

All four results use the same NVIDIA golden brief, dataset `1.0.0-goal-derived`, snapshot 2026-10-29. Goal: “What is the market momentum for AI computing accelerators in the data center sector across Europe and the United States?” Company: NVIDIA. Markets: Europe and the United States. Competitors: Broadcom and Intel. Customers: cloud providers and AI developers.

| | Published evaluation | optimized | this run | original run |
|---|---|---|---|---|
| What it is | Earlier scored market output | `feat/optm-market` before the call cut | `feat/optm-market` after the call cut | `develop` at `44f1153` |
| Report | `market_intelligence_evaluation.json` | `artifacts/reports/market_optm_golden_2026-11-08_16-12.json` | `artifacts/reports/market_optm_golden_2026-11-08_17-40.json` | `artifacts/reports/market_develop_golden_2026-11-08_16-59.json` |
| When | The published file | 2026-11-08, 14:03–14:12 UTC | 2026-11-08, 15:32–15:40 UTC | 2026-11-08, 14:57–14:59 UTC |
| How it was scored | DeepEval G-Eval | Same 12 findings, read again | Same 12 findings, read again | Same 12 findings, read again |

The optimized, this run, and original run scores use the golden thresholds: covered ≥ 0.70, partial ≥ 0.40, partial credit 0.5. Coverage is the mean of covered = 1, partial = 0.5, missing = 0. Strict coverage counts covered findings only. Worker quality is the unweighted mean of relevancy, correctness, faithfulness, and completeness. These readings follow that rubric. They are not a second pass of the DeepEval judge. The published scores stay the judge’s own numbers, including two findings the judge marked partial below 0.40 (Europe 0.20, momentum 0.25).

## Changes in the optimized run

The optimized run is `artifacts/reports/market_optm_golden_2026-11-08_16-12.json`.

- **Ask the model if a source fits the task.** The agent sends the text to the model and asks if it is about the subject and the place. The model answers useful or not useful, and gives a short reason.
- **Skip empty and broken text.** Very short text, a credit error with no link, a page that failed to load, or an empty search result is dropped without asking the model.
- **Check the search result, each page, and the pages together.** The same question is asked about the search snippet, about each page that was opened, and about those kept pages pasted together before a claim is written.
- **Count short place names and early headlines.** US, U.S., and EU count as the place. A headline about the subject can be useful before it states a price or a percent.

## Changes in this run

This run is `artifacts/reports/market_optm_golden_2026-11-08_17-40.json`. It keeps the optimized run’s other behavior and changes only how sources are checked.

- **Open a search result when it has page links.** A search result that contains links and is not an empty or broken message is scraped. The model is not asked about the snippet first. Very short text, a credit error with no link, a failed page, or an empty result is still dropped on its own.
- **Judge a task’s pages in one call.** After the pages for a task are opened, one model call reads them together and says which ones are about the subject and the place. US, U.S., and EU still count, and a page can still count before it states a price. Up to two useful pages are kept.
- **Write the claim from the kept pages.** The pages already accepted are pasted together and sent to the claim step. There is no extra model question about that combined text.
- **Build a retry search from keywords.** When a search has to be tried again, the next queries are built from the task words. That retry does not ask the model for new queries.

## What the code does

Search limits are `MAX_RETRIES=2`, `MAX_TOOL_FALLBACK=2`, `MAX_URLS_PER_TASK=2`, and `MAX_URL_CANDIDATES=5`. Tools are Serper, Tavily (`tool_web_search`), and NewsAPI, then a page fetch. The model is `gpt-oss:120b` through Ollama. Token totals are not recorded: `llm.py` does not log prompt tokens, completion tokens, or Ollama usage.

### Original run (`develop`)

The graph is `task_planner` → `task_executor` loop → `fill_gaps` once → `output_node`. There is no `conclude` step, so the worker does not write a comparison, a momentum link, or a company implication.

A search preview is relevant when `source_is_relevant` finds at least two task words and the place phrase in the text. A title shortcut can accept the hit with no model call. The usefulness model runs only when that word gate already passed and the title shortcut did not accept the hit. Each scraped page and the joined source text are checked with the same word gate, not with a model call. A price or percent claim still has to be supported by its quote. One skipped task is retried once. This run planned one size task for “the United States and Europe” and searched it with `gl=us`, including the Europe query.

### optimized (`feat/optm-market`)

The graph adds `conclude` before `output_node`. On top of the original run, optimized does the following.

1. A wider-market number can be kept, with a warning that names the task words the quote does not contain.
2. Every named place is stamped onto size, growth, and regulation tasks. Regions such as Europe are searched with an empty country code so the place name stays in the query. Country tasks keep their country code.
3. A failed named-topic search retries once with a recency-of-month query built from capitalized names plus content terms (adopt, competitor, rival, technolog, announc).
4. A price or percent claim must carry every year from its quote, and forecast wording (forecast, projection, CAGR) must appear in the quote when the claim uses it.
5. `conclude` writes a comparison from accepted claims. A sentence that mixes a dollar figure and a percent is dropped unless it says the figures are different. If no pair can be compared, the finding is “The named markets cannot be compared.”
6. An adoption task whose quote never says announced, preview, available, deployed, or launched records the gap “Source does not say whether this is an announcement or actual use.”
7. Momentum is linked to adoption or technology only when the claims support both sides. Otherwise the sentence is “A link between market size and adoption or technology cannot be made.” Momentum is stored with limitation `projection`.
8. A company implication is written from the brief’s Company line, with no new price, percent, or year, and limitation `analysis`.
9. The planner is told to make a size or growth task per named place, and to add competitor, customer, rule, or technology tasks only when the brief already asks.
10. Source relevance is `judge_relevance`. A body is rejected locally when it is shorter than 80 characters, when it says “not enough credits” and has no link, when it is an HTTP error with no link, or when a short body says there were no results. Anything else is one structured `Usefulness` call on the search preview, on each scraped page, and on the joined source text. The prompt accepts US, U.S., and EU, and it accepts a headline that does not yet contain a price or percent.

This run keeps items 1–9 and replaces item 10 with the changes listed under “Changes in this run.”

The published evaluation is an earlier output of this same brief. Its file does not name a git revision. Its failures were an unlabeled US AI data-center figure ($42.39 billion by 2031) used as accelerator TAM, a CAGR of 30.56% where the excerpt said 29.77%, a GDPR claim that overstated an EU-only storage rule, no Europe size, no comparison, no NVIDIA implication, and 3 of 5 tasks skipped.

## Results

| | Published evaluation | optimized | this run | original run |
|---|---:|---:|---:|---:|
| Tasks | 2 done, 3 skipped of 5 | 8 done, 1 skipped of 9 | 7 done, 2 skipped of 9 | 5 done, 1 skipped of 6 |
| Findings | 3 | 23 | 21 | 12 |
| Evidence | 2 | 20 | 18 | 12 |
| Gaps | 3 | 3 | 2 | 1 |
| Status | completed, skips labeled | partial | partial | partial |
| Coverage | 0.375 | 0.625 | 0.542 | 0.250 |
| Strict coverage | 0.083 | 0.250 | 0.167 | 0.000 |
| Worker quality | 0.30 fail | 0.59 partial | 0.52 partial | 0.41 partial |
| Relevancy | 0.40 partial | 0.65 | 0.55 | 0.42 |
| Correctness | 0.20 fail | 0.55 | 0.50 | 0.40 |
| Faithfulness | 0.40 partial | 0.60 | 0.58 | 0.55 |
| Completeness | 0.20 fail | 0.55 | 0.45 | 0.28 |

| Requirement | Published | | optimized | | this run | | original run | |
|---|---:|---|---:|---|---:|---|---:|---|
| TAM | 0.45 | partial | 0.60 | partial | 0.58 | partial | 0.25 | missing |
| United States | 0.40 | partial | 0.65 | partial | 0.55 | partial | 0.22 | missing |
| Europe | 0.20 | partial | 0.64 | partial | 0.60 | partial | 0.15 | missing |
| US–Europe comparison | 0.075 | missing | 0.78 | covered | 0.76 | covered | 0.08 | missing |
| Adoption | 0.60 | partial | 0.51 | partial | 0.32 | missing | 0.42 | partial |
| Competitors | 0.675 | partial | 0.61 | partial | 0.48 | partial | 0.62 | partial |
| Technology | 0.20 | missing | 0.59 | partial | 0.62 | partial | 0.48 | partial |
| Momentum | 0.25 | partial | 0.53 | partial | 0.48 | partial | 0.12 | missing |
| Regulation | 0.12 | missing | 0.44 | partial | 0.50 | partial | 0.52 | partial |
| Sources | 0.40 | partial | 0.53 | partial | 0.52 | partial | 0.46 | partial |
| Gaps | 0.90 | covered | 0.81 | covered | 0.74 | covered | 0.66 | partial |
| NVIDIA implication | 0.10 | missing | 0.70 | covered | 0.58 | partial | 0.20 | missing |

Coverage is 0.625 for optimized, 0.542 for this run, 0.375 for the published evaluation, and 0.250 for the original run. Strict coverage is 0.250, 0.167, 0.083, and 0.000. Worker quality is 0.59 partial, 0.52 partial, 0.30 fail, and 0.41 partial. optimized covers comparison and the NVIDIA implication. This run covers comparison and gap honesty. The original run covers none of the 12 requirements. Its six partials are adoption, competitors, technology, regulation, sources, and gaps.

### Published evaluation

Three findings: a US AI data-center size used as accelerator TAM, a CAGR that did not match its excerpt, and a GDPR claim that overstated an EU-only storage rule. Two of five tasks finished. There was no Europe size, no comparison, and no NVIDIA implication. Gap honesty was the only covered requirement (0.90).

### optimized

Twenty sourced statements, including North America data-center accelerators at USD 58.59 billion in 2025, the United States at USD 51.85 billion in 2025 (the warning says the quote never says accelerators), US AI accelerators at $25 billion in 2030, $7.9 billion in 2025 with CAGR 26%, hardware at 87.3% of 2025 AI data-center value, NVIDIA Q4 FY2026 data-center revenue up 75% year over year, the EU Cloud and AI Development Act, Intel’s GPU D-Series at 1.3 PFLOPS FP8, Hopper at about 90% data-center AI accelerator share in 2024, and Microsoft’s Azure Maia rollout with production deployments in 2027. The comparison sentence is: “The United States generated USD 51.85 billion in 2025, while no market size figure is available for Europe, so the named markets cannot be compared.” The implication, marked analysis, says NVIDIA’s data-center revenue growth and share position it for cloud providers and AI developers.

Remaining gaps: no relevant source for the Europe market-size task, one adoption quote that does not say whether the event is an announcement or actual use, and no supported market estimate for Europe. Stand-in warnings still name ordinary task verbs. Conflicting size figures sit side by side. One size task is still described as “United States (Europe)” because scope stamping adds the other region onto a task that already names a place. Broadcom, AWS, and Google stay thin. Adoption (0.51) and competitors (0.61) are a bit below the published reading (0.60 and 0.675).

### this run

Seven of nine tasks finished. Kept figures include the United States at USD 51.85 billion in 2025 and the North America data-center accelerator market at USD 58.59 billion in 2025. The warning on the US figure says the quote does not say accelerators. A Canada projection of USD 42.98 billion by 2035 sits in the same size task. The dedicated United States and Europe size tasks were both skipped. The comparison sentence says the United States figure cannot be set beside Europe because no Europe size was found. Momentum, marked as a projection, ties the US figure to data-center systems spending expected to grow 55.8% in 2026. The implication, marked analysis, says this shows demand for NVIDIA accelerators from cloud providers and AI developers.

Adoption findings are cloud and data-center spending plus an NREL facility, so adoption scores 0.32 and is missing. Broadcom’s XPU customers, about 30 gigawatts, and about $12 billion per gigawatt are present; Intel is thin, so competitors score 0.48. Technology names Rubin Ultra, a Kyber rack, and that Broadcom capacity. Regulation includes an EU data-residency option, a plan to triple EU data-centre capacity, and US export limits. Gaps are the two skipped size tasks. Stand-in warnings still name ordinary task verbs, and one size task is still described as “United States (Europe).”

### original run

The size task finished with one definition and no dollar figure: the market “includes physical devices, software stacks, middleware, integration services, and power and cooling infrastructure.” That quote is from a LinkedIn post about the United States data-center accelerator market. It does not give a size, a year, or a Europe figure, and it mixes accelerator hardware with power and cooling. TAM 0.25, United States 0.22, and Europe 0.15 are missing. There is no comparison sentence (0.08) and no momentum synthesis (0.12).

Adoption is one Microsoft Maia 300 plan from The Register (eight HBM4 stacks, up to 18 TB/s, rack-scale systems of up to 96 accelerators) plus Nebius joining the PyTorch Foundation. AWS and Google are absent, and the Maia lines are not labeled as an announcement rather than deployed use. Score 0.42.

The competitor task was skipped twice. The only gap is “No page stated a relevant fact” for Broadcom and Intel versus NVIDIA. That names both competitors and does not present the skip as a finding, which is why competitors score 0.62, above optimized’s 0.61 and this run’s 0.48, with no product, share, or date attached.

Technology findings are NVIDIA page lines: Rubin, Vera Rubin across seven chips, Spectrum-X, Blackwell, and MGX. They name products and cite nvidia.com. They do not carry dates or a demand implication. Intel and Broadcom pages were fetched and dropped. Score 0.48.

Regulation is stronger than the published run. The findings are US export controls, a BIS TPP cap on ACIC exports, and two Data Center Validated End User types, from The Regulatory Review and Freshfields. They do not repeat the GDPR overclaim. They also do not explain GDPR or the EU AI Act. Score 0.52, above optimized’s 0.44 and this run’s 0.50, and well above the published 0.12.

The NVIDIA implication is missing (0.20): product lines are present, and nothing connects them to cloud providers and AI developers as labeled analysis. Gap honesty is partial (0.66) because the competitor skip is labeled, while the missing Europe size, AWS, Google, and Intel technology are not reported as gaps. The size task is marked done.

Faithfulness is 0.55. The Maia, Nebius, and export-control claims sit in their excerpts. Several NVIDIA claims are marketing lines whose stored excerpt is a different span of the same product page.

## Performance

The published file records scores, findings, evidence, and “2 done, 3 skipped of 5.” It does not record wall-clock time, token usage, or per-tool calls. optimized, this run, and the original run were counted from their logs in the same way: one “Fetching … for N queries” line is N HTTP calls, and one `Scraping:` line is one page GET.

Token totals are unknown. Model-call counts are the proxy. optimized sends a preview, each page, and the joined text into separate usefulness calls. This run sends up to five page excerpts in one page judgment, then sends the kept pages again for analysis. The original run sends page text only into analysis.

### Latency

| | optimized | this run | original run |
|---|---:|---:|---:|
| Process wall clock | 553.4 s (9 min 13 s) | 516.3 s (8 min 36 s) | 144.7 s (2 min 25 s) |
| Agent clock (started_at to completed_at) | 8 min 58 s | 8 min 25 s | 2 min 12 s |

### Model calls

| Call | optimized | this run | original run |
|---|---:|---:|---:|
| Planner | 1 | 1 | 1 |
| Query arguments | 5 | 0 | 2 |
| Usefulness on a search preview | 19 | 0 | 0–7 |
| Usefulness on scraped pages | 45 | 11 | 0 |
| Usefulness on joined source text | 10 | 0 | 0 |
| Claim analysis | 10 | 8 | 5 |
| Conclude | 1 | 1 | 0 |
| **Total** | **91** | **21** | **8–15** |

optimized asks once per preview, once per page, and once on the joined text. This run’s 11 page judgments are one call per task that returned links, including the two size tasks searched again after they were skipped. The original run’s usefulness range is 0 to 7 because that log does not say whether a useful preview was accepted by the title shortcut or by the model.

### API calls by tool

| Tool | optimized | this run | original run |
|---|---:|---:|---:|
| Serper | 21 | 18 | 13 |
| Tavily | 8 | 4 | 2 |
| NewsAPI | 8 | 0 | 5 |
| Page fetch | 46 | 55 | 28 |
| **Search HTTP calls** | **37** | **22** | **20** |
| **All HTTP calls (search + pages)** | **83** | **77** | **48** |

optimized used 11 Serper batches, 4 Tavily batches, and 4 NewsAPI batches, with 14 first attempts and 5 second attempts. Sixteen of 46 page fetches were kept.

This run used 9 Serper batches (18 queries) and 2 Tavily batches (4 queries), with 11 first attempts and no second attempt. Thirteen of 55 page fetches were kept.

The original run’s search batches were 5 Serper (13 queries), 1 Tavily (2 queries), and 2 NewsAPI (5 queries). There were 7 first attempts and 1 second attempt. Eight of 28 page fetches were kept.

## Tradeoff

optimized has the highest coverage and worker quality of the four: 0.625 and 0.59, against 0.542 and 0.52 for this run, 0.375 and 0.30 for the published evaluation, and 0.250 and 0.41 for the original run. Strict coverage is 0.250, 0.167, 0.083, and 0.000.

This run is the cheaper measured version of the same agent: 8 min 36 s and 21 model calls, against 9 min 13 s and 91 model calls for optimized. Page fetches rose from 46 to 55 because a search result with links is opened before the model sees it. The United States and Europe size tasks were both skipped, and the adoption findings are spending figures, so coverage is lower than optimized.

The original run is the shortest clock, 2 min 25 s, and 8–15 model calls. Its regulation score (0.52) is above optimized (0.44) and matches this run. Its competitor score (0.62) is the highest of the three executed runs. It has no market size, no Europe figure, no comparison, and no NVIDIA implication. The published run’s time and token cost remain unknown.
