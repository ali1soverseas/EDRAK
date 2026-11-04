# qwen3.8-27b, paced: plan and findings

Pilot: GitLab Duo vs GitHub Copilot (competitors GitHub, Atlassian, Microsoft
Azure; markets China, Europe, USA). Same form as run `21642c57`, launched
through the rerun endpoint.

Goal: find out whether qwen on Groq's free tier can *complete* a report when
the client paces itself, rather than failing on rate limits. Comparison only:
`gpt-oss:120b` stays the default.

## What Groq actually meters on the free tier

The console reports 8,000 tokens per minute per account. The 429 bodies show
that the 8k is split:

| bucket | limit | enforced against |
| --- | --- | --- |
| input (ITPM) | 7,000 / min | the request's prompt |
| output (OTPM) | 1,000 / min | the request's **expected reply** |
| requests | 1,000 / day | every call |

Two different failures follow from that split, and only one is fixed by
waiting:

- `Rate limit reached ... (ITPM): Limit 7000, Used X, Requested Y` - the
  prompt does not fit the rest of the input minute. A client-side token bucket
  prevents this.
- `Request too large ... on output tokens per minute (OTPM): Limit 1000,
  Requested 1327` - the request's *expected reply* is larger than the whole
  output minute, so it can never be admitted. Waiting does not help; the
  request ceiling itself has to come down.

A single markdown-heavy market analysis prompt does not exceed 7k input, so
the input bucket is sufficient there. The competitor synthesis prompt did
exceed it: `MAX_EVIDENCE_CHARS` was 60,000 characters per batch (~15k tokens),
sized for a large-context model. That is the deterministic 413 from run 2.

## Changes made for this run

All under `LLM_PROVIDER=groq`; the Ollama path is untouched.

1. **Per-credential token bucket** in `core/llm.py`, attached as a callback on
   the model that is actually invoked. Rolling 60 s window, 7,200 tokens
   (prompt estimate + 600 output reserve), 24 requests per minute, one bucket
   per credential variable. Callers sharing a credential serialise; agents
   with their own key never wait on each other.
2. **Competitor evidence batches**: 60k characters on Ollama, 12k on Groq
   (`evidence_budget_chars()`), and prior findings in the prompt drop from 40
   to 10. A 12k batch leaves room for the template, requirements and history
   under 7k tokens.
3. **Market reply ceilings**: `call_structured` takes `num_predict`, and the
   cheap calls state one - analysis 384, claim retry 192, query args 192,
   usefulness 96. The planner keeps the default because it legitimately
   returns ~1,300 tokens. The failures these answer were the permanent OTPM
   class (`Request too large ... Requested 1327`), where the request's
   expected reply exceeds the whole output minute. The ceiling is the lever
   the provider names, but a probe with a 16,384-token ceiling was admitted on
   a short prompt, so the estimate follows the expected reply rather than the
   ceiling alone; the ceilings are implemented and tested, not yet proven by a
   run.
4. **429 bodies are logged** by the pacer (`groq call failed on <credential>`),
   because the usage record keeps only the exception type.
5. **Metrics reply ceiling** (after the run): the same `num_predict` now goes
   through `get_structured`, and the metrics finder states 512. Metrics output
   measured ~67 tokens per call, so 512 is roomy; the point is that the
   request no longer asks the provider to reserve a large reply.
6. **A failed metrics call now logs its message at warning**, truncated to 300
   characters. Before, the finder's errors were swallowed at INFO and the run
   could not say which ceiling refused it.

No prompt was changed to accommodate the model. Temperature stays at each
call site's own value; qwen's invalid JSON at temperature 0 is recorded as a
finding, not patched around. A direct probe showed two shapes: the literal
`{}` and a reply opening with a code fence (`failed_generation: "{```"`),
and the same prompt succeeding on an immediate retry — so it is intermittent,
not deterministic.

## Runs

| run | what | calls | ok | outcome |
| --- | --- | --- | --- | --- |
| `21642c57` | qwen, spread keys, no pacing | 178 | 55 (31%) | 7 findings, 2 empty workers, 46 gaps |
| `6c9ec144` | qwen, paced, competitor chunked | 153 | 61 (40%) | brief delivered, 8 findings (8 verified), 347 sources, coverage 29%, evidence `partial`, 91 min, $0.1629 |
| (next) | qwen, paced + market and metrics reply ceilings | | | |

`6c9ec144` was launched after changes 1, 2 and 4. Market still lost 12 of 28
calls to OTPM; that is what change 3 answers. Metrics lost 76 of 113, all
429s, every one of them retried 60 s apart and every one refused again - the
signature of a request that cannot be admitted at all, not of a busy minute.
Change 5 and 6 are the response: cap the reply, and make the next run say
which ceiling it hit.

### Result of `6c9ec144`

- 153 calls, 61 successful (40%); 138,181 input and 13,088 output tokens;
  $0.1629 at published rates; 91 minutes wall clock; avg 0.93 s per call,
  P95 1.52 s.
- 413 gone entirely. Competitor 2/4 (both failures are qwen's `{}` at
  temperature 0), customer_trends 5/6, market 16/28, metrics 37/113,
  cross_signal 0/1, verification 0 LLM calls (8/8 verified by rule).
- Two workers produced nothing usable; all 8 findings are `market_signal`.
  The brief is honest about it: coverage 29%, evidence status `partial`.
- Cost per successful call $0.00267, per report $0.1629, per finding $0.0204
  (8 findings). MiniMax, for contrast: $0.00248 per call, $0.3899 per report,
  $0.0083 per finding (47 findings).

**Verdict.** Pacing turns qwen from "cannot finish" into "finishes shallow".
It is not competitive with gpt-oss on time (91 min vs 16 min) or with MiniMax
on depth (8 findings vs 47), and the binding constraint is per request, not
per minute: a prompt above 7k input, or an expected reply the provider puts
above 1k output, is refused no matter how empty the bucket is. With market and
metrics ceilings in place a second run would be worth doing only if the free
tier is the point - otherwise qwen stays failover-only.

**Known defect.** The pacer reserves a fixed 600 output tokens per request.
A long reply spends ~1,000 and exhausts the output minute; measured on the
next call: `Rate limit reached ... (OTPM): Limit 1000, Used 998, Requested
33`. The reserve should follow each call's ceiling instead. Not fixed, because
fixing it without a validating run would leave the number as unproven as the
reply ceilings are.

## Abort rules

- systematic 413 after chunking
- more than half of calls failing after 40 calls
- a 429 loop that does not clear when the minute rolls (this is what metrics
  did from 06:02 to 06:19; the loop was diagnosed after the run rather than
  aborted, because each individual retry looked like a fresh minute)
