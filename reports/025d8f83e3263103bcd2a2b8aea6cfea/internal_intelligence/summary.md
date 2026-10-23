# EDRAK evaluation summary

Generated 2026-10-17T01:09:09+00:00 | judge model: gpt-4.1-mini

**Agents present in this run:** internal_intelligence

## Scores per file

| Agent | File | Execution quality | Integrity checks | Warnings | Units | Flagged | faithfulness avg | relevancy avg | specificity avg | insight avg | actionability avg |
|---|---|---|---|---|---|---|---|---|---|---|---|
| internal_intelligence | worker_internal_intelligence_attempt_1_69deee2f-c40.json | partial | 8/9 | 2 | 5 | 3 | 1.0 | 0.9166 | 0.2774 | 0.2319 | 0.342 |

## RAG / orchestration / observability

- RAG inputs are checked for retrieval score coverage and range, empty/duplicate chunks; source-domain coverage is recorded. DeepEval scores contextual relevance and, when an answer is supplied, answer relevancy and faithfulness unless `--dry-run` is selected.
- Orchestration inputs are evaluated for planned-task completion, dependency integrity, failed tasks, retries, and observed tool calls.
- Worker and downstream-agent quality metrics use DeepEval unless `--dry-run` is selected; `--deep` also judges every recommendation and comparison-matrix cell.
- Telemetry checks worker-run spans for agent/task identity and successful status/duration, and correlates tool spans with their worker when trace parentage is present.
- Execution quality is reported separately for tool selection, argument validity/suitability, result use, workflow sequence, failure/retry handling, and efficiency. Checks are marked not assessable when the trace lacks required evidence.
- Tool argument values and result summaries are excluded from DeepEval by default. Use `--include-tool-payloads` to send redacted payloads for argument and result-use judgments.
- `observability.json` records judge calls, cache hits, judge failures, metric-level failures, evaluation latency, and per-file tool traces (provider, argument names/types/values, latency, results). Per-file metrics and failed checks are in each agent report.
- The backend exports OpenTelemetry spans as evaluator-readable `spans` JSON. Set `EDRAK_TELEMETRY_EXPORT_PATH` to choose the file; set `OTEL_EXPORTER_OTLP_ENDPOINT` to additionally export to an OTLP collector.
- Request, orchestration, RAG retrieval, document indexing, and customer-trends tool spans export timing/status metadata, plus safe tool names, providers, and argument names/types/values (secret-named keys redacted and long values truncated; set `EDRAK_TELEMETRY_ARGUMENT_VALUES=0` to omit values); document text and credentials are not exported.


## Failed integrity checks (errors)

- **internal_intelligence** / worker_internal_intelligence_attempt_1_69deee2f-c40.json: no discarded evidence is cited (941656fd-1f0c-4b49-9ae6-b873d1cac199, 99d386bd-160f-4ccb-9e52-a9014d1359b7, e16dc34e-aa41-4cef-aa74-7f0bce786342, e58ff12b-ca97-4ffd-b054-9a11c31e8bd0, eb49a9e0-9454-4798-a72e-eeeaff9795c8)

## Quality warnings

- **internal_intelligence** / worker_internal_intelligence_attempt_1_69deee2f-c40.json: workflow sequence is assessable (no expected_sequence/workflow_sequence supplied)
- **internal_intelligence** / worker_internal_intelligence_attempt_1_69deee2f-c40.json: trace records tool failure handling (no errors observed, but trace completeness is not declared)

## Pipeline-level checks

- PASS: every finding traces to evidence (0 untraceable)
- PASS: every signal traces to a finding (0 untraceable)
- PASS (warn): signals only rest on verified findings (0 signals)
- PASS: every recommendation traces to signal -> finding (6/6)
- PASS: verifier covers all worker findings (0 missing)
- PASS: verification, cross-signal and decision share one research_run_id (025d8f83e3263103bcd2a2b8aea6cfea)

## Flagged units

- [internal_intelligence] worker_internal_intelligence_attempt_1_69deee2f-c40.json finding 585e5dee-f357-4c7f-812b-076a2c8f3975: no_added_insight, not_actionable — # 04 — Competitor Intelligence.pdf ## Page 1 COMPETITOR INTELLIGENCE — REGIONAL AI INFRASTRUCTURE Reporting pe
- [internal_intelligence] worker_internal_intelligence_attempt_1_69deee2f-c40.json finding 8ff582d0-15fe-403d-b740-d79de3b7bbd6: generic_statement, no_added_insight, not_actionable — # 03 — Market Opportunity Assessment.pdf ## Page 2 Potential demand drivers: - Regional cloud availability and
- [internal_intelligence] worker_internal_intelligence_attempt_1_69deee2f-c40.json finding 9966838c-b3b4-4b6d-a74f-b549bf75f5dd: generic_statement, no_added_insight, not_actionable — # 08 — Management Decision Memo.pdf ## Page 2 Required evidence: A consistent qualification framework and expl
