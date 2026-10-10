# EDRAK handoff: integration branch state

Written so a second pair of hands can pick up `feat/integration` and merge
`origin/feat/full_pipeline` without inheriting assumptions that turned out to
be wrong. Everything below is measured, not intended.

Last verified against `4feaaae` on the `feat/integration` branch, pushed and
identical to `origin/feat/integration`.

## Where things stand

| | |
|---|---|
| Branch | `feat/integration` at `4feaaae`, pushed, no unpushed commits |
| Tests | 33 passed |
| Pipeline | live run exits 0 |
| Health check | `python scripts/pipeline_health_check.py` exits 0 |
| Safety tag | `pre-merge-4feaaae` marks the pre-merge point |
| Local-only | `.env` holds every credential and is gitignored; a merge cannot disturb it |

### The other branch

`origin/feat/full_pipeline` (`378bf6f`) is **15 commits ahead and 55 behind**,
merging from `0dad297` ("feat(frontend): make the layout responsive"). It is
not a fast-forward in either direction.

Four remote branches were deleted upstream at the same time:
`feat/market-agent`, `feat/verification-agent`,
`refactor/integration-market-verification`,
`refactor/merge-agents-for-verification-test`. Their work appears to have been
consolidated into `feat/full_pipeline`.

## Per-agent change report

What this branch changed, by component. Commit subjects are the source of truth.

### core (shared LLM access) — `backend/src/edrak/core/`

- **`get_structured` default changed** `function_calling` → `json_schema`.
- **New `schema_instruction(schema)` helper.** Restates the schema in the
  prompt as JSON text. Shared by every component so there is one wording.
- **`DEFAULT_NUM_PREDICT` raised to 16384.** At 2048 internal synthesis was cut
  off mid-string; real answers run 3234–3706 tokens.
- **Credential lookup docstring corrected.** It claimed backup-then-primary; the
  code has always been primary-then-backup. Anyone trusting the comment would
  have reordered the candidates and let a dead backup beat a live primary.
- Five previously undocumented settings now documented in `.env.example`:
  `MAX_REPLANS`, `MAX_TASK_ATTEMPTS`, `SCRAPE_TEXT_CHARS`, `RETRIEVAL_MIN_SCORE`,
  `EMBEDDING_ALLOW_FALLBACK`.

### The structured-output rule

**`json_schema` bound on the model, plus the schema stated in the prompt. Both
halves are required.** Measured on `gpt-oss:120b`, first attempt, repair off:

| Schema | `function_calling` | `json_schema` + instruction |
|---|---|---|
| CT `HeadlineDraft` (flat) | 2–5/10 | 10/10 |
| CT `FindingsDraft` (nested) | 0/10 | 9–10/10 |
| CT `PlanDraft` (nested) | 0/10 | 10/10 |
| CT `ChunkAnalysis` (nested) | 0/10 | 10/10 |
| competitor `ResearchRequirements` | **0/5** | **4/5** |
| cross-signal `SignalDetectionResult` | 0/5 as shipped | **5/5** |
| **CT total, 3 runs** | **11/120** | **119/120** |

Two findings worth keeping:

1. `function_calling` returns **nothing at all** for a schema whose top-level
   field is a list of objects. Not "worse" — empty.
2. `json_schema` alone is **not** sufficient. With no instruction the model
   answers in prose, or picks its own shape: cross-signal returned the bare
   `signals` array instead of the object wrapping it, and pydantic rejected the
   type. No method flag fixes that; only stating the shape does.

The harness is checked in at
`backend/src/edrak/agents/customer_trends/llm/ab_structured_output.py`. Re-run
it after any change to model, schemas, or `langchain-ollama`. It spends 80 real
model calls.

### market_intelligence — 7 commits

- Model temperature pinned to `0`; it was inheriting `OLLAMA_TEMPERATURE=1.0`.
- Four schemas bound and returned as instances, not JSON text: `Usefulness`,
  `TaskPlan`, `AnalysisReply`, `QueryArgs`.
- Hand-rolled JSON parsing deleted (`call_llm`, `parse_model`, `clean_json`).
- **Still uses `method="json_mode"` at 6 call sites.** `json_mode` parses but
  does not constrain, so a malformed reply becomes a silent gap rather than an
  error. This is the largest remaining inconsistency with the rule above.

### competitor_intelligence — 1 commit

- `function_calling` → `json_schema` plus the schema instruction.
- **This was a total failure before the fix**: requirement identification
  returned `None` and the worker raised, contributing zero findings. After: 39
  findings over 128 sources. No test had exercised this path against a real
  model, which is why it survived.

### CrossSignal — 1 commit

- Method made explicit rather than relying on the library default, plus the
  schema instruction. Went from 0/5 to 5/5.
- Without the instruction the model returned the bare `signals` array; that
  specific parse failure is the reason the instruction is load-bearing.

### verification — 2 commits

- `_llm_review` now passes `schema_instruction`. Measured 2/3 without, 3/3
  with, so roughly a third of reviews were silently returning `None` and the
  deterministic result stood in.
- Default method change inherited from core.

### orchestration — 5 commits

- **Targeted replan.** Previously a replan re-planned the whole request and
  cleared every result, so one contradictory finding re-ran all four workers.
  Observed: 71 findings assessed, replan, 104 assessed, replan, finalize — nine
  worker executions to resolve one pricing discrepancy, and it could not
  converge because the replan ignored the ControlSummary that had already named
  the contradiction.
- Now a replan re-tasks **only** the workers owning a finding with
  contradictions, keeping the original `task_id` so `merge_by_task_id` replaces
  just those results. No LLM call.
- New `replan_tasks` state key carries the narrowed dispatch set. **The plan
  itself is left intact on purpose**: `OrchestrationResult` rejects a result
  whose `task_id` is absent from the plan, so narrowing `plan.tasks` would
  orphan untouched workers' results and fail finalize.
- `MAX_REPLANS` 2 → 1, in `core/config.py` **and** `.env`, because `.env` wins.

### customer_trends — 6 commits

- Startup report of which providers registered, which are keyless, which were
  skipped.
- End-of-run provider health and quota logging.
- Provenance report scoping fix.
- Structured-output A/B and documentation.

### internal_intelligence — no direct edits

Benefited from the `core` token-ceiling fix. Previously produced 8 findings with
no deterministic fallback; now 10–11.

### Tests

- 4 stale expectations settled after proving at `2f781e6` that all four failed
  before this branch's work.
- New: `tests/test_orchestration_targeted_replan.py` (9 tests, including an
  end-to-end graph test proving only one worker re-dispatches),
  `tests/test_pipeline_health_check.py` (8 tests).

## New operational tooling

`scripts/pipeline_health_check.py` exists because **exit code 0 is close to
worthless as evidence that the pipeline works.**

`finalize_node` derives the run status from whether a worker *failed* or an
error was raised. A worker that collected nothing returns `partial`, not
`failed`, with `error=None`, so the run still reports `completed`. That is how
customer-trends returned zero findings across an entire run while the pipeline
exited 0, unnoticed across several runs.

The checker asserts what the exit code cannot: every worker present, every
worker produced a finding, every evidence reference resolves, cross-signal
completed. It exits non-zero on any failure, so it can gate a run.

Severity is deliberate: a worker reporting `partial` **with** findings is being
honest about gaps it could not close, so that is `WARN` and does not fail the
check. Scoring it as a failure would train everyone to ignore the output.

```bash
python scripts/pipeline_health_check.py            # newest artifact
python scripts/pipeline_health_check.py run.json   # specific run
python scripts/pipeline_health_check.py --no-strict  # always exit 0
```

## How each agent is configured now

- Model: `gpt-oss:120b` via native `ChatOllama` against Ollama Cloud. No OpenAI
  dependency and no `ChatOpenAI` anywhere; `langchain-openai` was removed.
- Auth goes in `client_kwargs={"headers": {"Authorization": "Bearer ..."}}`.
  `ChatOllama` has no `api_key` field; passing one is silently discarded as a
  pydantic extra and the request goes out unauthenticated as a 401.
- Per-agent credentials: `orchestrator`, `competitor`, `market`, `internal`,
  `cross_signal`, `customer_trends`. Resolution is own key → own `_BACKUP` →
  shared, and an agent never reads another agent's scoped variable.
- Shared providers are scoped: Tavily for competitor and market, Serper for
  market and customer-trends.

## Local-only: documentation is gitignored

`.gitignore` line 18 excludes `docs/` as "private, not published yet". That has
been true since the first commit, and **zero files under `docs/` are tracked**.
Anything written there stays on this machine: it does not reach a collaborator,
does not survive a clean checkout, and is invisible in a review.

This was left as-is rather than lifted, because reversing a deliberate repo
decision, and republishing roughly 86KB of customer-trends documentation its
author marked private, is not a call to make quietly.

Corrected locally, **not visible to anyone else**:

- `docs/INTEGRATION_NOTES.md` — removed four false `OPENAI_API_KEY` /`ChatOpenAI`
  claims that no longer exist in the source, documented the per-agent key scheme
  and the new `MAX_REPLANS` default, and corrected the security note.
- `docs/ORCHESTRATOR.md` — §13.1 said the retry/replan loop was inert because
  `verify` was a stub. It is live now, and the replan semantics described in the
  node table were rewritten.
- `docs/CONTRACTS.md` — added the note explaining why a targeted replan cannot
  narrow `plan.tasks`, tied to the `task_id` membership invariant, plus how
  `replan_required` is actually raised.

If these should be shared, lift the ignore for `docs/` in a separate, explicit
change and say so in the PR. Do not assume they propagated.

## Merge result: `origin/feat/full_pipeline` merged 2026-10-10

Merged into `feat/integration` with `--no-ff`. 34 conflicts, all resolved; the
merge commit is on this branch and the pre-merge point remains tagged
`pre-merge-4feaaae`.

### How each conflict was decided, and why

The rule was evidence, not preference: keep whichever side is correct for the
code that actually ships now.

| Area | Kept | Reason |
|---|---|---|
| `orchestration/nodes.py` `replan_node` | **ours** | Theirs is byte-identical to the version we replaced: full replan, `results: RESET` |
| `orchestration/nodes.py` `finalize_node` | **theirs** | Wires `decision_analysis` into the run result |
| `orchestration/state.py`, `graph.py` | **merged** | Both: `replan_tasks` and `decision_analysis` |
| `verification/nodes.py` | **ours** | Theirs calls `get_llm_client()`, which no longer exists |
| `market_intelligence/nodes.py` | **ours** | 13 hunks, two referencing `get_llm_client()` |
| `CrossSignal/*`, `contracts/CrossSignal.py` | **ours** | Theirs hardcodes `gpt-4o-mini` and imports `ChatOpenAI` |
| `contracts/result.py` | **theirs** | Adds `decision_analysis`; ours already had no change there |
| `tests/*` | **ours** | Test our code, not the discarded version |
| `.pyc`, `outputs/`, `logs/` | **deleted** | Run products; 47 files untracked, none on our side before |

### The finding that decided several of those

**`feat/full_pipeline` still calls an API this branch deleted.**
`get_llm_client` and its `LLMClient` shim were removed during the Ollama
migration, but that branch's `verification/nodes.py`, `market_intelligence/nodes.py`
and `scripts/run_pipeline.py` still import it. Taking their side of those files
would have produced `ImportError` at runtime, not a test failure — nothing in
the suite exercises those imports.

That branch also still carries OpenAI in four files: `CrossSignal/graph.py`
(`from langchain_openai import ChatOpenAI`, `OPENAI_API_KEY`),
`CrossSignal/state.py` (`model = "gpt-4o-mini"`),
`competitor_intelligence/nodes.py`, and `DecisionAnalysis/nodes.py`. Ours wins
in the first three. The fourth is the new agent, so it was ported rather than
discarded — see below.

Worth knowing: `langchain-openai` is **not in `requirements.txt`** but *is*
present in `backend/.venv`, left over from before the migration. So
`import langchain_openai` still succeeds locally and would fail on a clean
install. Anything depending on that is passing for the wrong reason.

### DecisionAnalysis ported to Ollama

Their new stage arrived wired to OpenAI. Its own error message said
"replace ChatOpenAI with EDRAK's configured LLM client", so this was a known
loose end, not a design choice. Two changes:

1. `get_chat_model(agent="orchestrator", temperature=0)` instead of
   `ChatOpenAI(**model_kwargs)` built from `OPENAI_API_KEY`. The model is
   `gpt-oss:120b` and it uses the per-agent credential scope.
2. `json_schema` **plus `schema_instruction`**. The first live run failed
   because `json_schema` alone let the model return the bare recommendations
   array with no `{"question_recommendations": [...]}` wrapper, and pydantic
   refused it. This is the same failure shape as cross-signal, now observed
   twice. It parses correctly after the fix.

### Their reporting overhaul was not taken

`scripts/run_pipeline.py` gained ~165 lines of rendering on their side,
restructuring `_render_human`. Their version also reintroduces the
`get_llm_client` call. Taking it wholesale would have lost our renderer's
working shape for no gain in this merge, so `run_pipeline.py` was restored from
`pre-merge-4feaaae` and only the `decision_analysis` output block was added
back. Their reporting improvements can be ported deliberately later.

### An unreferenced duplicate contracts tree came in

Their branch adds a top-level `contracts/` package: 9 files, 1297 lines,
duplicating `backend/src/edrak/contracts/`. **Nothing in the repo imports it** —
no `from contracts.` or `import contracts` anywhere. The two copies already
disagree: root `result.py` is 12,337 characters against the backend's 12,508.

Kept for now so the merge stays reviewable, but it should not be merged to
`develop` as-is. Either delete it or make it a re-export shim of the backend
package. Flagging rather than deciding, because it is their structure and the
intent is not recoverable from the code.

### Verified after the merge

- 35 passed (was 33; their 2 DecisionAnalysis tests added).
- All 17 conflicted modules compile and import.
- Live run exits 1 with all four workers producing findings, 118/118 evidence
  references resolving.
- Targeted replan confirmed working in the merged pipeline: **5 dispatches
  instead of 12**, re-running only `competitor_intelligence` for its one
  contradiction.

The health check reports `FAIL` on two counts, both expected on this run:
status `partial`, and cross-signal absent because verification returned
`replan_required`, which routes to replan rather than cross-signal. When
verification passes, cross-signal and decision-analysis both run. This is
pre-existing routing, not merge damage.

## Known issues, ranked

1. **customer-trends is intermittent.** Two consecutive runs of the same query
   gave 5 findings and 0 findings. The 0-finding run still reported `completed`.
   Causes: the `demand` branch hit a provider-side `500` from Ollama, the
   `social` branch times out at ~83s, and `news:gdelt` is rate-limited or
   unavailable upstream.
2. **customer-trends collection is time-starved.** Branches get
   `min(branch_timeout_s=100, time_left)` inside a 210s collection window
   (300 × (1 − 0.3) reserve). They receive ~70–85s. Raising the brief's
   `max_seconds` to 600 roughly doubles CT wall time; undecided.
3. **A lost CT branch is absorbed silently.** A branch failure becomes a gap,
   which verification records and never retries. `_worker_error` reads only
   `result.error`, so it never registers as a failure.
4. **Branch error text is only partly logged.** `ct/nodes.py` logs
   `type(exc).__name__` alone; the full message survives in the artifact gaps.
5. **Confidence is degenerate in two workers.** internal emits every finding at
   exactly 0.75, customer-trends at exactly 0.30 — 14 of 67 findings carry a
   constant, so the number carries no information there. Competitor (7 distinct
   of 39) and market (6 of 14) are healthy.
6. **All customer-trends evidence is typed `other`**, so downstream cannot
   distinguish a forum thread from a news article.
7. **`json_mode` remains at 6 market call sites**, contradicting the rule above.
8. **`GOOGLE_TRENDS_API_KEY` is unset**, so that capability is keyless by design
   and reports as skipped.

## Corrections log

Recorded deliberately, because these were wrong and would mislead anyone who
reads the earlier reasoning.

- **Apify is not missing.** It was reported twice as `APIFY_API_KEY` unset. The
  worker reads `APIFY_TOKEN`, which is set along with 3 fallback tokens; the
  variable checked did not exist. The real failures are account-level
  (`ActorLimitReached`, `ProviderBadResponse`), not credential-level.
- **The replan trigger is not the confidence threshold.** `decide()` only sets
  `REPLAN_REQUIRED` when findings *conflict*. Insufficient findings and worker
  gaps deliberately finalize with graded findings. The actual trigger was one
  pricing-conflict finding, "Sources report different pricing or packaging for
  the same claim".
- **A CT branch timeout does not re-run the pipeline.** It becomes a gap, which
  does not replan. The pipeline absorbs it.
- **The `ResponseError` was not context overflow.** It is a provider-side HTTP
  500 from Ollama.
- **Customer-trends `json_schema` is 119/120, not 40/40.** An early run showed
  39/40 on arm A alone; the 40/40 claim was corrected after a re-run came back
  39/40.

## Merge playbook

`origin/feat/full_pipeline` diverged 15/55 from merge base `0dad297`, with **53
files changed on both sides**.

### Where a straight merge will fight

Their `replan_node` in `orchestration/nodes.py` is **identical to the pre-fix
version** — full replan, `results: RESET`. Taking their side silently reverts
targeted replan. Ours must win that function.

`verification/nodes.py` is the hardest file: about 680 lines changed on their
side against roughly 15 on ours, so expect a real conflict rather than a
textual one. Their `quality.py` adds roughly 400 lines and still defines
`MIN_CONFIDENCE = 0.60`, so the confidence behaviour analysed above survives the
merge.

Also both-sided: `orchestration/{graph,routing,state}.py`, `market_intelligence`
(8 files), `competitor_intelligence/nodes.py`, `CrossSignal/*`,
`contracts/{__init__,result}.py`, `mcp_servers/web/scrapers.py`,
`scripts/{run_pipeline,run_market_worker,run_verification}.py`, and three test
files.

### What should not be merged

Their branch commits run products and build output that the repository already
ignores:

- `backend/src/edrak/verification/__pycache__/*.pyc` — 5 compiled files, plus
  more under `market_intelligence/__pycache__/`
- `backend/src/edrak/agents/competitor_intelligence/outputs/*.json` — 16 files
- `artifacts/logs/competitor_intelligence.log`, `artifacts/logs/market_intelligence.log`

`.gitignore` already covers all three patterns (`__pycache__/` at line 4,
`agents/*/outputs/` and `artifacts/logs/`). Ignoring does not untrack files that
are already committed, which is how they got in. `feat/integration` tracks **zero**
of them, so this is entirely on their side: drop them from the merge and untrack
them there rather than carrying them forward.

### Suggested order

1. Merge with ours as the base; do not rebase published history.
2. Resolve `orchestration/nodes.py` in favour of targeted replan, then run the
   tests. `tests/test_orchestration_targeted_replan.py` is the arbiter.
3. Resolve `verification/nodes.py` by hand, keeping the `schema_instruction`
   call in `_llm_review`.
4. Untrack the `.pyc`, `outputs/` and `logs/` files. The ignore rules already
   exist; the files only need `git rm --cached` on their branch.
5. Re-run: `pytest`, then a live `scripts/run_pipeline.py`, then
   `scripts/pipeline_health_check.py`. Treat the health check, not the exit code,
   as the signal.
6. Expect customer-trends to be intermittent regardless. It is upstream and
   budget, not code.

## Local-only credentials

`.env` is gitignored and never committed; only `.env.example` is tracked and it
carries no live values. Nothing in the 18 commits contains a key-shaped string,
verified by scanning the diff. A merge cannot disturb local credentials.