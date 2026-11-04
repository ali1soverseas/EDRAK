# MiniMax M3.1 Flash â€” full pipeline run (plan + context)

Status: executing
Started: 2026-10-22
Goal: run the full EDRAK competitive-intelligence pipeline on
`minimax-m3.1-flash` (Ollama Cloud, free for a limited time), price every call,
and produce two HTML deliverables: a run report and a three-model comparison.

This file exists so the context survives a session restart. It is a working
plan, not a contract.

---

## 1. Why this run

The qwen3.8-27b trial (Groq) was rejected as a host: 73% call failure, a
structural HTTP 413 wall in the competitor stage, no completed report, and a
higher cost per successful call than gpt-oss:120b. MiniMax M3.1 Flash is the
next candidate: available on the same Ollama Cloud transport we already use
for `gpt-oss:120b`, so no new client, no new auth path, no new adapter.

Compared with the qwen trial, this run removes the two confounders that made
qwen's numbers hard to read:

- no per-account 8k-token request ceiling (Ollama Cloud, not Groq free tier);
- the incumbent transport is reused, so any failure is the model's, not the
  plumbing's.

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Parameters | mirror analysis `21642c571768453d0811d0f93c606989` (GitLab Duo vs GitHub Copilot, 3 competitors: GitHub / Atlassian / Microsoft Azure) via the `rerun` endpoint, which clones the stored `form` verbatim |
| 2 | Comparison deliverable | new page `comparison-gpt-oss-minimax-qwen.html`; keep `comparison-gpt-oss-vs-qwen-2026-10-22.html` as history |
| 3 | Cost basis | published MiniMax paid rates ($0.15 / $0.60 / $0.015 cached per 1M); note that actual billing during the promo is $0 |
| 4 | Execution | unattended to completion; abort only on the thresholds in آ§7 |

## 3. Model facts

| Item | Value |
|---|---|
| Ollama tag | `minimax-m3.1-flash` (verified in `https://ollama.com/api/tags`) |
| Context | 1M tokens (vendor claim) |
| Price, input | $0.15 / 1M |
| Price, output | $0.60 / 1M |
| Price, cached input | $0.015 / 1M |
| Sources | MiniMax blog (minimax.io/blog/minimax-m31f), Artificial Analysis, GMI Cloud, llm-stats â€” all four agree |

Note: these are the same input/output rates as `gpt-oss:120b` in `pricing.yaml`,
so a straight cost comparison is apples-to-apples; only the cached rate differs
($0.015 vs $0.014).

## 4. How the provider switch works (verified in code)

- `backend/src/edrak/core/llm.py` â€” `NATIVE_BASE_URL = "https://ollama.com"`;
  `ChatOllama(..., base_url=base_url or NATIVE_BASE_URL, client_kwargs={headers:
  Authorization Bearer <ollama_key(agent)>})`. Per-agent keys already exist.
- `backend/src/edrak/core/config.py` â€” `OLLAMA_BASE_URL` defaults to
  `https://ollama.com/v1`; `OLLAMA_MODEL` is the single model switch.
- `LLM_PROVIDER` empty/`ollama` â†’ native Ollama path; `groq` â†’ ChatOpenAI path.
  The provider recorded on every ledger row is `LLM_PROVIDER` or `ollama`.
- `backend/src/edrak/usage/pricing.py` â€” catalogue keyed by
  `(provider, model)`; unknown model â‡’ unknown cost (never silent zero).

## 5. Preflight changes

1. Back up `.env` â†’ `.env.bak-minimax`.
2. Remove both `LLM_PROVIDER=groq` lines (88 and 106 â€” duplicate from earlier
   rewrites), leaving provider default `ollama`.
3. Set `OLLAMA_MODEL=minimax-m3.1-flash`.
4. Add to `backend/src/edrak/usage/pricing.yaml` under `providers.ollama.models`:

   ```yaml
   "minimax-m3.1-flash":
     input_per_million: 0.15
     output_per_million: 0.60
     cached_input_per_million: 0.015
   ```

5. Smoke test: one `get_structured()` call with a real EDRAK schema. If the
   model returns `{}` or unparseable JSON at temperature 0, STOP and record it
   as a finding. Do not prompt-hack around it (same rule as the qwen trial).
6. Restart backend on 127.0.0.1:8000.

## 6. Launch

1. Mint a short-lived session row for user `16c701ce46a7110d2cdfb440a7777d7e`
   (sha256(token) in `sessions`, 30-day expiry, cookie `session_token`).
2. `POST /api/analyses/21642c57â€¦/rerun` â†’ new analysis, identical form, fresh plan.
3. `POST /api/analyses/{new_id}/approve` â†’ `run_manager.start_run`.
4. `run_id` is set at the workflow boundary via `usage/context.py`, so every
   model call lands in `usage/logs/llm_calls.jsonl` tagged with the new run.

## 7. Monitoring thresholds

Poll run status and the ledger every ~2 minutes.

| Signal | Action |
|---|---|
| smoke test fails | stop, report, no full run |
| one stage fails every call (systematic) | stop at that stage, report |
| overall failure rate > 60% after 40 calls | stop, report |
| 429 storm (rate limit) | observe one retry cycle, then stop |
| normal slow pace | let it run |

Record per stage: calls ok/failed, input/output tokens, latency, error classes.

## 8. Deliverables

1. `backend/src/edrak/usage/logs/run-<run_id>.html` via
   `python scripts/llm_usage_report.py run <run_id> --html`.
2. Refresh `index.html` + `system.html` (`system --html`).
3. New `backend/src/edrak/usage/logs/comparison-gpt-oss-minimax-qwen.html`:
   - run-by-run table (MiniMax, gpt-oss:120b, qwen3.8-27b);
   - per-stage ok/fail matrix;
   - failure mechanics per model;
   - economics: $/successful call, $/finding, tokens per finding;
   - projection for a full clean run;
   - verdict.
   Same visual language as `comparison-gpt-oss-vs-qwen-2026-10-22.html`.
4. âک… link from `index.html` to the new comparison page.

## 9. Rollback

- `.env.bak-minimax` restores the exact pre-run environment.
- Revert the `pricing.yaml` block if the model entry is rejected.
- No database migration, no contract change, no code change is required for
  this run. If code does change, it is additive and logged here.

### Incident log

**Run `d003c807â€¦` aborted at 19:11 (infrastructure, not model).**
`customer_trends/llm/client.py` called `provider_key(...)` after an earlier
uncommitted edit replaced its local import with `from edrak.core import llm as
core_llm`. Under `LLM_PROVIDER=groq` the function returned before that line, so
the qwen trial never hit it; on the Ollama path it raised
`NameError: name 'provider_key' is not defined` and killed the demand/social
branches. One-line fix: `core_llm.provider_key(...)`. Run relaunched afterwards.

**First MiniMax quirk observed (tracked, not fixed).** One market structured
call returned empty content (`Invalid json output: ''`); the worker fell back to
deterministic extraction. Recorded as a per-call failure in the comparison.


## 10. What counts as success

- run reaches `done` (or `partial` for a documented, model-attributable reason);
- every call priced (no `unknown` cost except genuine provider failures);
- per-stage numbers comparable with the gpt-oss and qwen runs;
- two HTML deliverables exist and are linked from `index.html`.

## 11. Result (2026-10-22)

Run `d6f29438047a70fa9bb56e63916087ae` finished at 20:26 with status
`partial` (evidence coverage: 45 of 47 findings verified; 2 insufficient),
stage `done`, brief generated.

| metric | value |
|---|---|
| calls | 160 (157 ok, 3 fail â€” all transient `ResponseError`) |
| input tokens | 559,375 |
| output tokens | 509,928 |
| total tokens | 1,069,303 |
| estimated cost | $0.3899 at published MiniMax rates ($0 actual during promo) |
| wall clock | 70m 46s (19:15 â†’ 20:26) |
| avg latency / p95 | 30.4 s / 144.0 s |
| findings | 47 (45 evidence-backed), 32 gaps, 354 sources |
| brief | delivered, `evidence_status: partial` |

Per stage (ok/total): supervisor 1/1, internal 2/2, market 16/17,
customer_trends 7/7, competitor 12/14, verification 5/5, cross_signal 1/1,
metrics 113/113. Metrics alone cost $0.2006 â€” 51% of the run.

Delivered:

- `logs/run-competitive-intelligence-minimax-m3.1-flash-d6f29438.html`
- `logs/comparison-gpt-oss-minimax-qwen.html` (new âک… page)
- refreshed `logs/index.html`, `logs/system.html`, all run pages
- corrected the "gpt-oss is local" claim on `comparison-gpt-oss-vs-qwen-2026-10-22.html`
  (it is Ollama Cloud)

Verdict: MiniMax is the most reliable model tried (98% success, zero
structural failures, richest output) but costs 2.8أ— gpt-oss per report and is
~10أ— slower. Keep gpt-oss as default host; use MiniMax as the depth/escalation
option. qwen stays failover-only.

Environment after the run: `.env` has `LLM_PROVIDER` removed and
`OLLAMA_MODEL=minimax-m3.1-flash`; `.env.bak-minimax` restores the prior state.
Backend left running on 127.0.0.1:8000.

