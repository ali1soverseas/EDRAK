# Edrak comparison: Optimized vs GraphRAG

## Verdict: Optimized

GraphRAG has critical failures: GraphRAG: worker:market_intelligence has an error

| Overall score | Optimized | GraphRAG |
|---|---:|---:|
| Score | 0.508 | 0.469 |

## Worker and stage comparison

| Component | Optimized | GraphRAG | Difference (B - A) |
|---|---:|---:|---:|
| stage:cross_signal | 0.660 | 0.640 | -0.019 |
| stage:decision_analysis | 0.548 | 0.559 | 0.011 |
| stage:verification | 0.552 | 0.560 | 0.008 |
| worker:competitor_intelligence | 0.562 | 0.506 | -0.056 |
| worker:customer_trends | 0.184 | 0.159 | -0.024 |
| worker:internal_intelligence | 0.470 | 0.373 | -0.097 |
| worker:market_intelligence | 0.578 | 0.485 | -0.093 |

## Metric breakdown

### stage:cross_signal

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.700 | 0.600 |
| faithfulness | 0.900 | 0.800 |
| completeness | 0.500 | 0.500 |
| coverage | 0.364 | 0.636 |

### stage:decision_analysis

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.500 | 0.400 |
| faithfulness | 0.500 | 0.700 |
| completeness | 0.600 | 0.500 |
| coverage | 0.654 | 0.692 |

### stage:verification

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.600 | 0.600 |
| faithfulness | 0.700 | 0.600 |
| completeness | 0.300 | 0.500 |
| coverage | 0.583 | 0.500 |

### worker:competitor_intelligence

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.600 | 0.500 |
| faithfulness | 0.800 | 0.700 |
| completeness | 0.400 | 0.400 |
| coverage | 0.278 | 0.306 |

### worker:customer_trends

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.100 | 0.100 |
| faithfulness | — | — |
| completeness | 0.300 | 0.200 |
| coverage | 0.158 | 0.210 |

### worker:internal_intelligence

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.300 | 0.300 |
| faithfulness | 0.600 | 0.500 |
| completeness | 0.600 | 0.400 |
| coverage | 0.333 | 0.222 |

### worker:market_intelligence

| Metric | Optimized | GraphRAG |
|---|---:|---:|
| correctness | 0.600 | 0.300 |
| faithfulness | 0.700 | 0.900 |
| completeness | 0.500 | 0.300 |
| coverage | 0.417 | 0.333 |


## GraphRAG critical issues

- GraphRAG: worker:market_intelligence has an error

## Scoring

Correctness 30%, faithfulness 30%, completeness 25%, coverage 15%.
Available metric weights are renormalized when a metric is missing.
The overall score is the simple average across matching workers and stages.
This is a practical comparison, not a statistical significance test.
