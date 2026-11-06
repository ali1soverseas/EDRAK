# Agent and verification report

The three agents were built in separate commits, then wired to one verification pass. The working tree still has uncommitted changes on top of commit `b7cb0ba`.

## Market agent

What's new since the pre-merge version (`d8565d8`):

- Evidence keeps the scraped page or API text the claim was summarized from.
- Tool arguments are the JSON object the model returns. They are passed straight to the scraper tool.
- Web requests wait 2 seconds. A GDELT 429 waits 30, 60, then 90 seconds, and a run sends at most two GDELT queries.
- The arguments used, and the reason a result was not useful, are printed and written to `artifacts/logs/market_intelligence.log`.

Committed history:

- `1125d43` moved the agent from `workers/market` into `agents/market_intelligence` and added the LangGraph loop: plan tasks, run one task, repeat, then write the private report. `tests/test_market_worker.py` was updated with that move.
- `d8565d8` made the agent return the shared `WorkerResult`. It added the `MarketIntelligence` class, `run()`, and `worker_result_from_state()`. Scrapers gained retry handling. Tests cover the planner-executor-output loop, the task router, invalid planner JSON, a skipped task marked `partial`, and a run that returns `WorkerResult`.

Uncommitted changes on top of that:

- Saved source text is stored on each evidence item. The claim is no longer copied into the evidence.
- Tool arguments are parsed as JSON and passed to the scraper. A reply that is not JSON falls back to a query built from the task description.
- Every web request waits 2 seconds. A GDELT 429 is retried after 30, 60, and 90 seconds, and a run sends at most two GDELT queries.
- Rejected arguments, the arguments that were used, and the reason a result was not useful are printed and appended to `artifacts/logs/market_intelligence.log`.

The graph is still `task_planner` to `task_executor`, looping until the task list is finished, then `output_node`. `run()` turns that private state into a `WorkerResult`.

## Internal agent

What's new since the pre-merge version (`8feacf3`):

- Each graph node writes a line to `artifacts/logs/internal_intelligence.log`.
- `claim_type`, `scope`, and `supporting_quote` are stored as limitations on `Finding`, so the shared contract accepts the synthesis output.

Committed history:

- `0dfd9f4` added the worker and `backend/tests/test_internal_agent.py`.
- `bfd6526` changed it to the shared `ResearchTask` and `WorkerResult` contracts and updated the internal, RAG, and contract tests.
- `234369e` added dynamic query planning, confidence scoring, and gap detection, plus `scripts/run_internal_agent.py`.
- `8feacf3` added LLM synthesis through `core/llm.py`, a rerank cutoff, and the rule that a claim must be grounded in the retrieved excerpt. `backend/tests/test_rag_text_utils.py` was added for fact extraction and grounding.

The graph is a straight line: `plan_queries` to `retrieve_evidence` to `analyze_synthesize` to `format_result`. Retrieval searches the indexed handbook and internal corpus. Synthesis writes findings. If the model reply does not fit `Finding`, the node falls back to a deterministic extraction from the retrieved text.

Uncommitted change: each of those four nodes appends a line to `artifacts/logs/internal_intelligence.log`. Extra model fields (`claim_type`, `scope`, `supporting_quote`) are stored as limitations so they fit the shared `Finding` contract.

## Competitor agent

What's new since the pre-merge version (`6745f75`):

- `python __main__.py` starts the package from `backend/src`, so the relative imports resolve.
- `.env` is loaded from the repo root, and the Brotli decoder accepts the argument `httpx2` passes, so OpenAI responses can be read.
- Each graph node writes a line to `artifacts/logs/competitor_intelligence.log`.

Committed history:

- `38a388b` added the agent: requirements, queries, search, synthesis, an internal verify step, a requirement check, and a second stage when requirements are still open, then a final report and comparison.
- `6745f75` connected that graph to `ResearchTask` and `WorkerResult`, added `__main__.py`, and saved a worker-result output.
- `62064dc` pointed environment loading at the repo-root `.env` and wrapped the Brotli decoder so OpenAI responses can be read. The graph itself did not change.

The graph is:

`identify_requirements` to `generate_queries`. If queries exist, `search` to `synthesize` to `verify` to `check_requirements`. If requirements remain, `prepare_next_stage` returns to `generate_queries`. Otherwise `final_report` to `comparison` and the graph ends. `run_research()` then builds the `WorkerResult`.

Uncommitted change: each node appends a line to `artifacts/logs/competitor_intelligence.log`. Search text was already stored on evidence as `excerpt`.

There is no dedicated competitor test file. The agent has been exercised by running `__main__.py` and the mock orchestrator.

## Tests

| Test file | What it checks |
| --- | --- |
| `tests/test_market_worker.py` | Market graph loop, router, JSON cleanup, planner fallback, private report, `WorkerResult`, partial status |
| `tests/test_market_schemas.py` | Task-plan schema |
| `backend/tests/test_internal_agent.py` | Partial coverage, full coverage, and a live knowledge-base query |
| `backend/tests/test_internal_flow.py` | End-to-end flow after retrieval |
| `backend/tests/test_rag.py` | Indexer and retriever |
| `backend/tests/test_rag_text_utils.py` | Fact extraction and grounding |
| `tests/test_verification.py` | Official finding verified, snippet marked insufficient, pricing conflict marked replan, invented number rejected, empty input cannot complete |
| `tests/test_contracts.py` and `backend/tests/test_contracts.py` | `BusinessRequest`, `ResearchTask`, `WorkerResult`, and verification decision contracts |

`tests/test_verification.py` stubs the verification model call so those tests stay on the rule path. `test_invented_number_is_rejected` feeds a fake model review that says `$19` was invented.

`backend/requirements.txt` now lists `langchain-openai` and `tavily-python`, which the competitor agent imports.

## Verification flow

Verification is a two-node graph in `backend/src/edrak/verification/graph.py`.

`VerificationInput` enters `run()`. The state starts with the payload and empty assessments. `assess_findings` runs, then `decide`, then the graph ends. `run()` validates the result as `VerificationResult` and prints it as `VERIFICATION OUTPUT STATE`.

`assess_findings` checks each finding from every worker:

1. Completeness is a rule. The finding needs supporting evidence whose text is at least 40 characters.
2. The model compares the claim with the saved source text. It returns source quality, contradictions, and any number, date, or price in the claim that is not in the source. If that call fails, quality and price conflicts fall back to the source-type rules.
3. Missing information stays a word check. If the claim says autonomy, enterprise, price, plan, availability, or task, and the source text does not, that gap is recorded. Worker limitations are copied onto the same list.
4. The finding is `verified` only when it is complete, has no contradiction, and the source quality is not low. Otherwise it is `insufficient`.

`decide` does not look at the sources again. It reads those verdicts and writes the orchestrator fields:

- Any contradiction sets `decision.status` to `replan_required`.
- Missing information with no contradiction sets `retry_required`.
- Every finding verified sets `verified`.
- No findings sets `cannot_complete`.
- `control_summary` lists the gaps, conflicts, worker errors, and one next-research line per insufficient finding.
- `targeted_actions` names the worker and the claim that still needs work.

`scripts/run_mock_orchestrator.py` runs internal, then market, then competitor, builds one `VerificationInput`, and calls `verification.graph.run` once. It prints the missing information, contradictions, and orchestrator actions. It does not start the agents again.
