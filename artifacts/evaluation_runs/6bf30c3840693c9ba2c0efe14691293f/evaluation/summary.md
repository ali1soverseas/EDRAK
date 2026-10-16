# EDRAK evaluation summary

Generated 2026-10-16T17:43:20+00:00 | judge model: gpt-5

**Agents present in this run:** competitor_intelligence, cross_signal, customer_trends, decision, internal_intelligence, market_intelligence, metrics, orchestration, rag, telemetry, verification

## Scores per file

| Agent | File | Execution quality | Integrity checks | Warnings | Units | Flagged | faithfulness avg | relevancy avg | context_relevancy avg | specificity avg | insight avg | actionability avg |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| competitor_intelligence | worker_competitor_intelligence_attempt_1_011e2c88-57a.json | not_assessable | 7/8 | 5 | 38 | 9 | - | - | n/a | - | - | - |
| internal_intelligence | worker_internal_intelligence_attempt_1_0c587331-2e7.json | not_assessable | 6/6 | 1 | 6 | 0 | - | - | n/a | - | - | - |
| market_intelligence | worker_market_intelligence_attempt_1_87029b67-a73.json | not_assessable | 5/5 | 2 | 12 | 0 | - | - | n/a | - | - | - |
| customer_trends | worker_customer_trends_attempt_1_2efa19bd-4df.json | not_assessable | 6/6 | 2 | 5 | 1 | - | - | n/a | - | - | - |
| metrics | metrics_007.json | not_assessable | 4/5 | 0 | None | None | n/a | n/a | n/a | n/a | n/a | n/a |
| verification | verification_pass_001.json | not_assessable | 10/10 | 3 | 37 | 1 | - | - | n/a | n/a | n/a | n/a |
| cross_signal | cross_signal.json | not_assessable | 6/7 | 1 | 6 | 0 | - | - | n/a | n/a | - | n/a |
| decision | decision_analysis.json | not_assessable | 13/13 | 2 | 7 | 4 | - | - | n/a | n/a | n/a | - |
| orchestration | orchestration_result.json | not_assessable | 2/2 | 2 | None | None | n/a | n/a | n/a | n/a | n/a | n/a |
| rag | rag_internal_intelligence_attempt_1_0c587331-2e7_001.json | not_assessable | 1/1 | 1 | None | None | n/a | n/a | - | n/a | n/a | n/a |
| rag | rag_internal_intelligence_attempt_1_0c587331-2e7_002.json | not_assessable | 1/1 | 1 | None | None | n/a | n/a | - | n/a | n/a | n/a |
| rag | rag_internal_intelligence_attempt_1_0c587331-2e7_003.json | not_assessable | 1/1 | 1 | None | None | n/a | n/a | - | n/a | n/a | n/a |
| rag | rag_internal_intelligence_attempt_1_0c587331-2e7_004.json | not_assessable | 1/1 | 1 | None | None | n/a | n/a | - | n/a | n/a | n/a |
| telemetry | telemetry.json | partial | 1/2 | 2 | 54 | 0 | n/a | n/a | n/a | n/a | n/a | n/a |

## RAG / orchestration / observability

- RAG inputs are checked for retrieval score coverage and range, empty/duplicate chunks; source-domain coverage is recorded. DeepEval scores contextual relevance and, when an answer is supplied, answer relevancy and faithfulness unless `--dry-run` is selected.
- Orchestration inputs are evaluated for planned-task completion, dependency integrity, failed tasks, retries, and observed tool calls.
- Worker and downstream-agent quality metrics use DeepEval unless `--dry-run` is selected; `--deep` also judges every recommendation and comparison-matrix cell.
- Telemetry checks worker-run spans for agent/task identity and successful status/duration, and correlates tool spans with their worker when trace parentage is present.
- Execution quality is reported separately for tool selection, argument validity/suitability, result use, workflow sequence, failure/retry handling, and efficiency. Checks are marked not assessable when the trace lacks required evidence.
- Tool argument values and result summaries are excluded from DeepEval by default. Use `--include-tool-payloads` to send redacted payloads for argument and result-use judgments.
- `observability.json` records judge calls, cache hits, judge failures, metric-level failures, evaluation latency, and per-file metrics.
- The backend exports OpenTelemetry spans as evaluator-readable `spans` JSON. Set `EDRAK_TELEMETRY_EXPORT_PATH` to choose the file; set `OTEL_EXPORTER_OTLP_ENDPOINT` to additionally export to an OTLP collector.
- Request, orchestration, RAG retrieval, document indexing, and customer-trends tool spans export timing/status metadata, plus safe tool names and argument names/types; argument values, query text, document text, and credentials are not exported.


## Failed integrity checks (errors)

- **competitor_intelligence** / worker_competitor_intelligence_attempt_1_011e2c88-57a.json: every fulfilled requirement maps to at least one finding (3 unlinked)
- **metrics** / metrics_007.json: verified metric observations are grounded in cited sources (23 unsupported or malformed verified/thin/conflicting observation(s))
- **cross_signal** / cross_signal.json: pipeline reported no unresolved support references (1)
- **telemetry** / telemetry.json: tool arguments satisfy supplied schemas (call 1: invalid type for origin; call 3: invalid type for origin; call 5: invalid type for origin; call 7: invalid type for origin; call 9: invalid type for origin; call 11: invalid type for origin)

## Quality warnings

- **competitor_intelligence** / worker_competitor_intelligence_attempt_1_011e2c88-57a.json: fulfilled requirements: every claimed 'N grounded findings' is backed (4 mismatches)
- **competitor_intelligence** / worker_competitor_intelligence_attempt_1_011e2c88-57a.json: no requirement is 'missing' while a grounded finding exists
- **competitor_intelligence** / worker_competitor_intelligence_attempt_1_011e2c88-57a.json: no unit states an absence of evidence as a fact ('not mentioned') (2 units)
- **competitor_intelligence** / worker_competitor_intelligence_attempt_1_011e2c88-57a.json: run completed (not partial/failed) (partial)
- **competitor_intelligence** / worker_competitor_intelligence_attempt_1_011e2c88-57a.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **internal_intelligence** / worker_internal_intelligence_attempt_1_0c587331-2e7.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **market_intelligence** / worker_market_intelligence_attempt_1_87029b67-a73.json: run reported no error (none)
- **market_intelligence** / worker_market_intelligence_attempt_1_87029b67-a73.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **customer_trends** / worker_customer_trends_attempt_1_2efa19bd-4df.json: run completed (not partial/failed) (partial)
- **customer_trends** / worker_customer_trends_attempt_1_2efa19bd-4df.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **verification** / verification_pass_001.json: no finding is verified on low-quality evidence
- **verification** / verification_pass_001.json: confidence follows evidence quality (high >= medium >= low) ({'high': 0.94, 'medium': 0.7605, 'low': 0.8})
- **verification** / verification_pass_001.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **cross_signal** / cross_signal.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **decision** / decision_analysis.json: no recommendation has numbers absent from its signals (4 recommendations)
- **decision** / decision_analysis.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **orchestration** / orchestration_result.json: orchestration reports no failed tasks (1 failed)
- **orchestration** / orchestration_result.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **rag** / rag_internal_intelligence_attempt_1_0c587331-2e7_001.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **rag** / rag_internal_intelligence_attempt_1_0c587331-2e7_002.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **rag** / rag_internal_intelligence_attempt_1_0c587331-2e7_003.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **rag** / rag_internal_intelligence_attempt_1_0c587331-2e7_004.json: execution quality has structured trace evidence (tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable)
- **telemetry** / telemetry.json: workflow sequence is assessable (no expected_sequence/workflow_sequence supplied)
- **telemetry** / telemetry.json: trace records tool failure handling (no errors observed, but trace completeness is not declared)

## Pipeline-level checks

- PASS: every finding traces to evidence (0 untraceable)
- PASS: every signal traces to a finding (0 untraceable)
- PASS (warn): signals only rest on verified findings (0 signals)
- PASS: every recommendation traces to signal -> finding (6/6)
- PASS: verifier covers all worker findings (0 missing)
- PASS: verification, cross-signal and decision share one research_run_id (6bf30c3840693c9ba2c0efe14691293f)

## Which phase each downstream file was evaluated against

- metrics_007.json (metrics): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- verification_pass_001.json (verification): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal -
- cross_signal.json (cross_signal): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- decision_analysis.json (decision): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- orchestration_result.json (orchestration): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- rag_internal_intelligence_attempt_1_0c587331-2e7_001.json (rag): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- rag_internal_intelligence_attempt_1_0c587331-2e7_002.json (rag): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- rag_internal_intelligence_attempt_1_0c587331-2e7_003.json (rag): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json
- rag_internal_intelligence_attempt_1_0c587331-2e7_004.json (rag): worker attempts {'competitor_intelligence': 1, 'customer_trends': 1, 'internal_intelligence': 1, 'market_intelligence': 1}, verification verification_pass_001.json, cross-signal cross_signal.json

## Flagged units

- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R1: missing_despite_grounded_finding — The information was not established by the verified evidence.
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R3: fulfilled_without_findings, grounded_count_mismatch — Intel provides AI computing hardware for cloud service providers and AI developers through its Xeon processor 
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R4: missing_despite_grounded_finding — The information was not established by the verified evidence.
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R6: fulfilled_without_findings, grounded_count_mismatch — In the United Arab Emirates, Intel positions its AI platform as a scalable, secure, and high‑performance solut
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R7: grounded_count_mismatch, ungrounded_numbers — Broadcom provides AI computing products for cloud service providers and AI developers, including custom AI acc
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R8: missing_despite_grounded_finding — The information was not established by the verified evidence.
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json requirement R10: fulfilled_without_findings, grounded_count_mismatch — Broadcom positions its AI offerings around high performance with low power consumption, citing up to 80 W powe
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json matrix_cell Recent changes | Broadcom: absence_claim — No recent change information is documented for Broadcom in the provided evidence.
- [competitor_intelligence] worker_competitor_intelligence_attempt_1_011e2c88-57a.json matrix_cell Recent changes | Intel Corp: absence_claim — No recent change information is documented for Intel in the provided evidence.
- [customer_trends] worker_customer_trends_attempt_1_2efa19bd-4df.json finding 2efa19bd-4dfa-4607-baec-f1b3e9e22d34:f3: single_domain_support — Users report consistent login failures and poor app stability for NVIDIA‑branded consumer applications, indica
- [metrics] metrics_007.json metric_observation internal_intelligence:own_price:NIVIDIA Corporation:ec589e5b-6114-44c2-aace-838e7eb191b5: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation internal_intelligence:own_price:NIVIDIA Corporation:ec589e5b-6114-44c2-aace-838e7eb191b5: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation internal_intelligence:own_price:NIVIDIA Corporation:ec589e5b-6114-44c2-aace-838e7eb191b5: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation internal_intelligence:own_price:NIVIDIA Corporation:ec589e5b-6114-44c2-aace-838e7eb191b5: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:market_size:United Arab Emirates:50700a60-c592-470f-a674-463053022ffa: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:market_size:United Arab Emirates:50700a60-c592-470f-a674-463053022ffa: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:market_size:United Arab Emirates:50700a60-c592-470f-a674-463053022ffa: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:market_size:United Arab Emirates:6459c54a-a8c8-4327-b48c-15e669b7af18: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:market_size:United Arab Emirates:6459c54a-a8c8-4327-b48c-15e669b7af18: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:market_cagr:United Arab Emirates:2bcc0c0a-7f3e-4179-8b76-99391792486c: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:8757adf4-7f5b-4df7-807f-1a58f710f2ea: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:8757adf4-7f5b-4df7-807f-1a58f710f2ea: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:8757adf4-7f5b-4df7-807f-1a58f710f2ea: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:477162cb-61ba-40e6-a3a2-0705d7e7ed87: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:477162cb-61ba-40e6-a3a2-0705d7e7ed87: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:477162cb-61ba-40e6-a3a2-0705d7e7ed87: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:477162cb-61ba-40e6-a3a2-0705d7e7ed87: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:477162cb-61ba-40e6-a3a2-0705d7e7ed87: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:477162cb-61ba-40e6-a3a2-0705d7e7ed87: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:8757adf4-7f5b-4df7-807f-1a58f710f2ea: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:01914bf2-fe2f-4bac-930b-c725df1dc878: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:87d20df5-3579-4920-961b-424733d13251: metric_value_not_in_evidence — 
- [metrics] metrics_007.json metric_observation market_intelligence:regulation_events:United Arab Emirates:56889f75-b5bb-420c-8174-cd18aafe566d: metric_value_not_in_evidence — 
- [verification] verification_pass_001.json finding 2efa19bd-4dfa-4607-baec-f1b3e9e22d34:f5: verified_on_low_quality_evidence — UAE AI leader G42 has secured NVIDIA’s most advanced AI chips, indicating strong demand for NVIDIA hardware am
- [decision] decision_analysis.json recommendation q1: new_numbers_not_in_signals — Accelerate the UAE launch by at least 3‑4 months and front‑load partner onboarding to match competitor timelin
- [decision] decision_analysis.json recommendation q2: new_numbers_not_in_signals — Synchronize NVIDIA’s deployment schedule with utility‑backed power provisioning projects to enable rapid GPU c
- [decision] decision_analysis.json recommendation q4: new_numbers_not_in_signals — Invest in power‑and‑cooling infrastructure partnerships and secure at least three certified local systems‑inte
- [decision] decision_analysis.json recommendation q5: new_numbers_not_in_signals — Prioritize securing a reliable supply pipeline and implement competitive, region‑specific pricing to retain an
- [orchestration] orchestration_result.json orchestration_task market_intelligence: task_failed — Which geographic markets beyond the United Arab Emirates show strong demand, favorable regulation, and growth 
