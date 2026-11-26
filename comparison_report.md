# Edrak comparison: Optimized vs GraphRAG

- Optimized: `2aaf080a301c155b6e6c1bd159ecd8ad`
- GraphRAG: `9e373b46748a80c11cc0949128b4e228`

## Verdict: GraphRAG

GraphRAG has the higher overall score (0.584 vs 0.582) across 6 workers and stages.

| | Optimized | GraphRAG |
|---|---:|---:|
| **Overall score** | 0.582 | 0.584 |

## Best version per metric

| Metric | Optimized | GraphRAG | Best |
|---|---:|---:|---|
| correctness | 0.650 | 0.567 | Optimized |
| faithfulness | 0.583 | 0.700 | GraphRAG |
| completeness | 0.550 | 0.500 | Optimized |
| coverage | 0.497 | 0.530 | GraphRAG |

## Worker and stage scores

| Component | Optimized | GraphRAG | Difference (B - A) |
|---|---:|---:|---:|
| worker:competitor_intelligence | 0.463 | 0.535 | 0.072 |
| worker:customer_trends (skipped: no findings) | 0.175 | 0.189 | 0.014 |
| worker:internal_intelligence | 0.606 | 0.564 | -0.041 |
| worker:market_intelligence | 0.498 | 0.623 | 0.125 |
| stage:cross_signal | 0.664 | 0.701 | 0.037 |
| stage:decision_analysis | 0.651 | 0.566 | -0.085 |
| stage:verification | 0.610 | 0.517 | -0.093 |

## Metric breakdown

### worker:competitor_intelligence

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.500 | 0.500 |
| faithfulness | 0.500 | 0.700 |
| completeness | 0.400 | 0.400 |
| coverage | 0.423 | 0.500 |

### worker:customer_trends

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.100 | 0.100 |
| faithfulness | — | — |
| completeness | 0.100 | 0.200 |
| coverage | 0.450 | 0.350 |

### worker:internal_intelligence

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.700 | 0.500 |
| faithfulness | 0.600 | 0.600 |
| completeness | 0.600 | 0.600 |
| coverage | 0.438 | 0.562 |

### worker:market_intelligence

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.500 | 0.600 |
| faithfulness | 0.600 | 0.900 |
| completeness | 0.400 | 0.500 |
| coverage | 0.455 | 0.318 |

### stage:cross_signal

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.800 | 0.700 |
| faithfulness | 0.700 | 0.900 |
| completeness | 0.600 | 0.500 |
| coverage | 0.429 | 0.643 |

### stage:decision_analysis

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.700 | 0.500 |
| faithfulness | 0.600 | 0.600 |
| completeness | 0.700 | 0.600 |
| coverage | 0.571 | 0.571 |

### stage:verification

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.700 | 0.600 |
| faithfulness | 0.500 | 0.500 |
| completeness | 0.600 | 0.400 |
| coverage | 0.667 | 0.583 |

## Left out of the overall score (no findings)

- worker:customer_trends

## Scoring

Correctness 30%, faithfulness 30%, completeness 25%, coverage 15%; missing metrics are skipped and the weights rescaled.
Overall score = simple average of workers and stages; the higher score wins, and only identical overall scores are a tie.
Individual metric comparisons within 0.01 are labeled ties.
This is a practical comparison, not a statistical test.
