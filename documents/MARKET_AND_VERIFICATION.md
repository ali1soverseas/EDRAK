# Market Intelligence and Verification

These two agents sit on opposite sides of the same contract. The market worker **produces** claims with attached evidence. Verification **grades** those claims (and the other workers’) against the saved source text, then tells the orchestrator whether the run can finish.

```text
BusinessRequest
    → planner (one ResearchTask per worker)
        → MarketIntelligence.run(task)     → WorkerResult
        → CompetitorAgent.run(task)        → WorkerResult
        → …other workers…
    → VerificationInput(request + all WorkerResults)
        → assess_findings  (per claim)
        → decide           (run-level status)
    → finalize
```

The market graph never talks to verification. The supervisor never sees market’s private `task_list` or scraped pages. Only `WorkerResult` crosses the boundary.

---

## Market intelligence

**Job:** answer a *market* research task using public web evidence: size, demand, buyers, barriers, regulation, packaging/price *as market signals*. It should not own competitor monitoring; that belongs to the competitor worker. In practice the planner prompt still mentions Copilot/Q pricing as examples, so a brief that still lists GitHub/Azure as `targets` will pull competitor work into market.

**Entry:** `MarketIntelligence.run(task)` in `backend/src/edrak/agents/market_intelligence/graph.py`. It flattens `ResearchTask` into two strings (`goal` + `business_context`), invokes a LangGraph with `recursion_limit: 100`, then converts private state into a `WorkerResult`.

### Graph

```text
START
  → task_planner          # LLM: 4–7 sub-tasks
  → task_executor  ─┐     # one sub-task per visit
        │           │
        ├─ more pending/running → task_executor again
        ├─ leftover gaps, first time → fill_gaps → task_executor
        └─ done → output_node → END
```

`task_executor` looping is why the recursion limit is 100: 4–7 tasks × retries × one gap-fill pass can burn many graph steps.

### 1. `task_planner`

Deterministic first: `scope_from_brief(goal, context)` picks region (`gl`), language, recency from the brief (e.g. “market in Germany” → `gl=de`). Country names are meant to live in `gl`, not in the query string.

Then an LLM writes 4–7 sub-tasks, each with:

- `description`
- `tool_hint` (`tool_serper`, `tool_web_search`, `tool_newsapi`)
- query objects `{q, gl, language, recency, include_domains?}`

Code then **overrides** the hint: regulation/licensing/residency → Tavily (`tool_web_search`); news-ish + NewsAPI hint → `tool_newsapi`; everything else → Serper. If the model returns fewer than 4 tasks, it is asked once more. If parsing still fails, one fallback task is “Research: {goal}”.

Those sub-tasks are **internal**. The orchestrator already gave market one `ResearchTask`; this planner is market’s own to-do list.

### 2. `task_executor` (the research loop)

For the current sub-task it:

**Search, with retries and tool fallback**

- Attempt 1 uses planner queries.
- Later attempts ask the LLM for new keywords, skipping already-rejected ones.
- Query objects are **flattened to `List[str]`** before MCP, because the search tools only accept strings. Region/language/recency on the object are dropped at the tool call.
- Tools to try: primary, then fallback (`serper ↔ web_search`; `newsapi → serper`), up to 2 tools × 2 argument retries, plus connection retries.

**Rank hits, then decide if the result is useful**

Hits are scored by search rank + keyword overlap with the sub-task + geographic match + `DEFAULT_SOURCE_TIERS` (regulator/analyst up, reseller/aggregator down). Keep at most 5 candidates, later scrape 2.

A result is useful if:

- it produced URLs, **and**
- at least two ranked titles already name the task subject, **or**
- the snippet looks on-topic and (only if titles are weak) an LLM `Usefulness` check does not reject it.

Otherwise: log the queries as rejected and retry with different keywords.

**Scrape pages**

For each ranked URL: scrape, take the excerpt window that mentions the task terms (not the nav header), require the page to mention ≥2 task terms (and the place name if the task is geographic). Keep up to **2** relevant pages (`MAX_URLS_PER_TASK`).

**Extract claims**

The LLM must return 1–3 `{claim, quote}` pairs **from that source text only**. Code then rejects a claim unless:

- claim is ≥40 chars and not “Data retrieved for…”
- the quote is copied from the scraped text (≥40 chars)
- claim and quote share ≥2 content words

Confidence is a heuristic, not a model score: starts at 0.4, +0.25 if there is a digit, +0.1 for a long quote, +0.15 if a proper name in the claim appears in the quote; cap 0.9. Vague claims without numbers or names are capped at 0.5.

If search, scrape, or claims fail, the sub-task is **skipped** and a gap string is stored, e.g. `"No supported fact for: …"`.

### 3. `fill_gaps` (once)

If any sub-tasks were skipped, they are reset to `pending` with empty queries, `gap_fill_done=True`, and the executor runs them again. First-pass findings are kept. After that one extra pass, leftover gaps stay in the result.

### 4. `output_node` → `WorkerResult`

Private findings become shared contract objects:

| Internal | Contract |
|---|---|
| `claim` | `Finding.statement` |
| category | always `market_signal` |
| scraped URL + excerpt | `Evidence` (`web_page`), linked via `evidence_refs` |
| `confidence` | `Finding.confidence` |
| skipped tasks / empty claims | `WorkerResult.gaps` |

Status:

- findings, no gaps → `completed` (confidence ≈ mean of findings)
- findings + gaps/skips → `partial`
- no findings → `no_evidence` or `failed`

That `WorkerResult` is what verification sees. Scraped full pages, ranked hit lists, and the to-do list do **not** leave the worker.

---

## Verification

**Job:** not to research. It is a gate: for every finding from every worker, is the **saved** evidence enough? Then emit a compact control decision for the orchestrator.

**Entry:** after all dispatches finish, `verification_gate_node` builds:

```python
VerificationInput(
    request=state["request"],          # original BusinessRequest
    agent_outputs=state["results"],    # list[WorkerResult]
)
```

Graph is two nodes, no loop:

```text
START → assess_findings → decide → END
```

### `assess_findings` — one verdict per claim

For each finding it resolves linked evidence (deduped by URL+text), then runs **deterministic** checks first:

1. **URL quality** (`quality_from_url`), using the same market source tiers, not the worker’s `SourceType` enum. Aggregators/community paths → LOW; `.gov` / Gartner-class → HIGH; vendor docs/pricing paths → HIGH if they match known vendor hosts from the request (GitLab, GitHub, Azure, …).
2. **Quantities in the claim must appear in the excerpt.** If the claim says `$19` or `80%` or `2026` and the saved text does not, support is `no`.
3. **Word overlap** if there are no numbers: enough shared content terms → `yes`; some → `uncertain`; none → `no`.
4. **Price conflicts:** two different URLs each reporting a single different price (or one “free” and one priced) → contradiction. Two SKUs on one page are allowed.
5. **Worker confidence floor:** below **0.60** the finding cannot be verified, and the LLM is not called.

The LLM is only used when those checks are **ambiguous** (`uncertain` overlap, quality not LOW, confidence ≥ 0.60). Prompt: “is this claim in the saved source?” It may flag `invented_details` (numbers/dates/prices not in the text). Invented details are treated as **missing information**, not as a source conflict.

A finding is **verified** only if all of these hold:

- supporting evidence exists
- support strength is `yes`
- no contradictions
- no invented details
- quality is not LOW
- worker confidence ≥ 0.60

Otherwise it is **insufficient**, with a list of what is still needed.

This grading is independent per finding. A weak market claim does not poison a strong competitor claim.

### `decide` — run-level status

It also records:

- worker `gaps` (market’s “no supported fact for …”)
- worker errors (`internal_intelligence: no worker registered…`)
- **coverage gaps** only if the request goal looks like `Analyze N topics: a; b; c` — then it checks whether the owning worker’s statements mention those topics

Status:

| Situation | Status | Orchestrator |
|---|---|---|
| No findings at all | `cannot_complete` | finalize |
| Any finding with source **contradictions** | `replan_required` | `replan` → new plan → dispatch |
| Mix of verified + insufficient / gaps | `verified` | finalize |
| Every finding insufficient | `cannot_complete` | finalize |
| All findings clean | `verified` | finalize |

`retry_required` still exists on the contract, but verification **does not emit it**. Retry would route to `dispatch` without a `Send({"task": …})` payload and raise `KeyError: 'task'`. Weak findings are instead written into `control_summary.next_research_targets` and `missing_information`. `targeted_actions` stay empty on `verified` / `cannot_complete` because the contract forbids actions on those statuses.

What the orchestrator actually stores is only `result.decision` (status + summary + actions), not the full `FindingVerdict` list. The printed JSON / `verification.log` has the per-finding grades; synthesis is supposed to consume those, but the live pipeline currently finalizes on `WorkerResult`s plus that compact decision.

---

## How they interact on a real run

1. Market may return `status=completed` with confidence 0.66 and empty `gaps`. That only means *its* loop finished. Verification can still mark many of those findings insufficient (blog URL → LOW, number not in excerpt, confidence < 0.60).
2. Market’s own claim filter and verification’s quantity check are similar but not the same. Market requires a copied quote; verification also requires every price/percent/year in the **statement** to appear in the excerpt, and it scores the **URL class**.
3. A failed internal worker still appears in `agent_outputs` as a failed `WorkerResult`. Verification records that under `failures` and grades whatever findings exist from the other workers.
4. Because mixed quality now finishes as `verified`, a run can be “verified” while several market claims remain `insufficient`. That is a control-plane workaround, not a statement that every claim is good.

The intended split remains: market gathers and binds evidence; verification judges that evidence and only then should synthesis write the decision brief.
