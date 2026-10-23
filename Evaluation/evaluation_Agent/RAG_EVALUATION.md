# EDRAK RAG Evaluation

This guide describes how the enhanced evaluation agent evaluates retrieval
records: which inputs it reads, how it constructs a reference answer, what
checks and judge metrics it runs, how to run an evaluation, and how to
interpret the report.

The evaluator is implemented in
[`edrak_eval_agent_enhanced.py`](./edrak_eval_agent_enhanced.py). The RAG stage
evaluates retrievals (a query and retrieved chunks); it is not the same as
evaluating a worker's final answer.

## What is evaluated

For every selected RAG record, the evaluator combines:

1. **Deterministic retrieval-integrity checks** on the query, chunks, scores,
   and duplicate content.
2. **LLM-judged context quality**, when a judge is enabled and the record has
   the required inputs.
3. **Execution-trace quality**, when structured tool-call trace data is
   available for the corresponding internal RAG call.

These are separate signals. A valid retrieval score does not prove that a
chunk answers the query, and a low judge score does not mean the record is
malformed.

## Run RAG evaluation

From the repository root in PowerShell, evaluate only RAG records in a run
bundle:

```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id> `
  --agents rag
```

To run without the DeepEval judge:

```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id> `
  --agents rag `
  --dry-run
```

The normal LLM-judged command requires `OPENAI_API_KEY` and the `deepeval`
package. Query-specific references in the golden RAG files do not require an
embedding call. Unmatched queries may use the approximate embedding fallback;
see [Golden reference selection](#golden-reference-selection) for details.

To change the report directory, pass `--out`:

```powershell
.\.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<run-id> `
  --agents rag `
  --out artifacts\evaluation_runs\<run-id>\evaluation\rag_debug
```

For a run bundle, the default output directory is the bundle's `evaluation`
directory. `--agents rag` selects RAG evaluation records; it does not run the
internal worker or perform retrieval again.

## Input records

The input loader detects a record as RAG when it includes at least one
retrieval field (`retrieval_results`, `retrieved_documents`, or
`retrieved_chunks`) and at least one query field (`query`, `user_query`, or
`retrieval_query`).

The evaluator accepts these retrieval result fields, in order:

1. `retrieval_results`
2. `retrieved_documents`
3. `retrieved_chunks`

Each retrieved item is expected to be an object. Text is read from the first
available field among `text`, `content`, `page_content`, `excerpt`, and
`extracted_fact`. IDs are read from `chunk_id`, `document_id`, or `id`.
Retrieval scores can be provided directly as `score` or `relevance_score`, or
under `metadata` with `score`, `relevance_score`, or `confidence_score`.

The query is read from `query`, `user_query`, or `retrieval_query`.
An optional generated answer is read from `answer`, `response`,
`actual_output`, or `output`.

An optional reference answer may be supplied directly in the RAG record using
the first non-empty field among `expected_output`, `reference_answer`, and
`ground_truth`.

Example record shape:

```json
{
  "worker": "internal_intelligence",
  "task_id": "task-id",
  "attempt": 1,
  "query": "Which constraints could limit expansion?",
  "retrieved_chunks": [
    {
      "chunk_id": "chunk-001",
      "text": "The source text returned by retrieval.",
      "score": 0.78,
      "source_url": "internal://document-id"
    }
  ],
  "answer": "Optional generated answer"
}
```

For run bundles, records with a `task_id` are retained only when that task
belongs to a final worker result selected by the evaluator. Records without a
`task_id` are not filtered by this rule.

## Evaluation flow

For each selected RAG record, the evaluator performs the following steps:

1. Reads the query, retrieved chunks, IDs, text, and any available scores.
2. Produces one `retrieved_chunk` unit row per chunk.
3. Reads a generated answer, if present, and adds an answer unit row when one
   exists.
4. Uses an explicit reference answer from the RAG record if present.
5. If there is no explicit reference, looks for an exact normalized query in
   the matching golden dataset's `inputs/rag_internal_intelligence.json`.
   Unmatched internal-intelligence queries may use the approximate worker-
   finding fallback; details are in
   [Golden reference selection](#golden-reference-selection).
6. Runs deterministic checks and, when enabled and assessable, judge metrics.
7. Adds structured tool-execution checks when matching trace data is available.
8. Writes the RAG report and pipeline report to the selected output directory.

## Deterministic retrieval checks

The evaluator checks the following:

| Check | Meaning |
|---|---|
| Retrieval query is present | The query string is non-empty. |
| Retrieval returned at least one chunk | At least one retrieved item was parsed. |
| Retrieved chunks contain text | Every parsed chunk has non-empty text. |
| Retrieval scores are available for observability | Every parsed chunk has a numeric score. |
| Retrieval scores are within `[0, 1]` | Each provided score is finite and in the expected range. |
| Retrieved chunks are not near-duplicates | No pair of normalized chunk texts is considered near-duplicate by the evaluator. |

For each chunk, the evaluator also reports its ID, query, first 500 characters
of text, score, source, and flags. The current per-chunk flags include:

- `empty_retrieved_chunk`
- `low_retrieval_score` when a numeric score is below `0.3`
- `invalid_retrieval_score` when a provided score is non-finite or outside
  `[0, 1]`

The `0.3` low-score flag is an evaluation diagnostic. It is not the RAG
retriever's configured minimum score, and it does not independently establish
that a chunk is irrelevant.

## LLM-judged metrics

LLM metrics run only when the judge is enabled and both the query and retrieved
documents are present. Up to `MAX_CTX_ITEMS` (currently 8) retrieved chunk
texts are passed as context.

### Context relevancy

**Question:** Is the retrieved context relevant to the query?

- Uses the query and retrieved text.
- Does not require a golden answer.
- The call passes the recorded answer as `actual_output`, or the query itself
  when no answer is available.
- Does not establish factual correctness, completeness, or whether the
  retrieved context fully answers the query.

### Context precision

**Question:** Is the retrieved context relevant to the expected answer, rather
than containing irrelevant context?

- Requires an expected/reference answer.
- Uses the retrieved text and the query.
- Is not assessed when the reference answer is absent.

### Context recall

**Question:** Does the retrieved context contain information needed to support
the expected answer?

- Requires an expected/reference answer.
- Uses the retrieved text and the query.
- Is not assessed when the reference answer is absent.

The RAG retrieval evaluator does not calculate Faithfulness or Answer
Relevancy for retrieval-only records. Those metrics require a generated answer
and apply to worker or downstream answer evaluation.

When an input is missing, the score remains `null` and an explanatory error
note is emitted; the metric is not treated as a score of zero.

## Golden reference selection

An explicit non-empty `expected_output`, `reference_answer`, or `ground_truth`
on the RAG record takes precedence. Otherwise, the evaluator identifies the
golden dataset by the run's use case, then searches its
`inputs/rag_internal_intelligence.json` file for a query whose text matches
after Unicode NFKC normalization, case folding, and whitespace collapsing.
Each entry has this shape:

```json
{
  "worker": "internal_intelligence",
  "queries": [
    {
      "query": "Which constraints could limit expansion?",
      "expected_output": "The planning assumptions identify ...",
      "query_id": "optional-stable-id"
    }
  ]
}
```

The `query_id` field is optional metadata; current lookup uses the query text,
normalizing Unicode dash variants, case, and whitespace only.
Keep expected answers grounded in the dataset's cited material and clearly
label synthetic or provisional assumptions. An exact golden match is returned
without embedding. If no query-specific entry matches, the legacy embedding
fallback may select findings from the worker golden file as follows:

1. It only supports records whose `worker` is `internal_intelligence`.
2. It finds the run's single use case from the loaded `input` record.
3. It searches `golden/ground_truth/*/inputs/initial_input.json` for a dataset
   with the same use case.
4. It reads non-empty `statement` fields from that dataset's
   `inputs/worker_internal_intelligence.json`.
5. It embeds the RAG query, retrieved chunks, and golden statements using
   OpenAI's `text-embedding-3-small`.
6. For each finding, it computes cosine similarity to the query and the
   highest similarity to any retrieved chunk. Its ranking score is:

   ```text
   0.70 * query_similarity + 0.30 * best_chunk_similarity
   ```

   If there are no chunks, it uses query similarity alone.
7. It keeps findings scoring at least `0.35`, ranks them, and joins at most
   five statements into one expected-answer string.

If no query-specific match or usable fallback reference is available, context
precision and recall remain not assessable.

### Important limitations of the fallback

- **The legacy fallback depends on the retrieved chunks.** A chunk can
  influence which golden finding is selected, and that selected finding is
  then used to judge the same retrieval. This makes that fallback reference
  partly dependent on the result being evaluated and can bias precision/recall.
- **The matching is approximate.** Embedding similarity does not prove that a
  finding answers the query. The `0.35` cutoff and the `70/30` weighting are
  heuristics, not calibrated correctness criteria.
- **The embedding model differs from EDRAK's retrieval model.** Golden
  selection uses `text-embedding-3-small`; EDRAK's retrieval embeddings are
  selected through its own local embedding configuration. Their similarity
  rankings can differ.
- **The fallback sends internal text to an external API.** The query,
  retrieved chunks, and golden statements are sent to OpenAI's embeddings
  endpoint. Consider privacy, data-handling policy, and cost before using it
  with sensitive internal content.
- **Embedding calls are separate from judge calls.** The fallback is invoked
  before `eval_rag`; `--dry-run` disables the DeepEval judge but does not, by
  itself, guarantee that this fallback makes no embedding API call.
- **A selected group of findings is not a query-curated ground truth.** A
  worker finding may describe the broad research task rather than the
  particular retrieval query.

### Recommended ground-truth approach

For benchmark-quality context precision and recall, curate an expected
reference for each retrieval query, preferably using a stable query ID. Store
the reference on the RAG record or provide a deterministic lookup to a
query-specific golden entry. Do not use the retrieved chunks to select the
reference answer.

If no query-specific reference exists, leave context precision and recall
not assessable. Continue to report deterministic retrieval checks and context
relevancy, while treating those as diagnostics rather than a substitute for
reference-based recall.

## Thresholds and pass/fail interpretation

The default context-relevancy threshold is `0.5`. In the current evaluator,
context precision and recall also fall back to that threshold; the CLI does
not expose separate precision and recall threshold arguments.

Judge scores below their threshold add warning-severity checks. Deterministic
retrieval checks also have warning-severity checks where appropriate. Warning
failures are reported but do not, by themselves, make the command exit
non-zero. The evaluator exits non-zero when it encounters evaluator errors or
failed error-severity integrity checks.

An evaluation integrity `PASS` is not the same as high retrieval quality.
Review judge scores, warnings, and the underlying retrieval inputs together.

## Reports and artifacts

The output directory contains:

- `rag_report.json` — per-file metric summaries, thresholds, checks, execution
  quality summaries when available, and flagged units.
- `pipeline_report.json` — run-level pipeline check totals.

The evaluator's in-memory RAG result also includes per-chunk rows and, when an
answer is present, an answer row. The current JSON report writer exports only
flagged units from those rows, not every unflagged chunk row. Inspect the
original RAG input JSON when full chunk content is needed.

The report's per-file RAG metrics include:

- `context_relevancy_score` and its error/status note
- `context_precision_score` and its error/status note
- `context_recall_score` and its error/status note

Checks contain pass/fail status, severity, and details. Scores and checks are
independent: a score can be present while deterministic checks flag malformed
or suspicious retrieval data.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No RAG records detected | The input lacks a recognized retrieval field or query field. |
| Context relevancy is not assessed | Judge disabled, missing query, or no retrieved documents. |
| Precision/recall are not assessed | No explicit RAG reference and no usable golden fallback reference. |
| Golden fallback returns no reference | Worker is not `internal_intelligence`, use case is absent/ambiguous, golden files are missing, or no finding clears the similarity cutoff. |
| Golden lookup reports multiple datasets | More than one golden dataset declares the same use case; make the dataset selection unambiguous. |
| Embedding call fails | Check OpenAI credentials, network access, service availability, and whether sending the RAG text to that API is permitted. |
| A valid score is flagged as low | The evaluator's diagnostic flag uses a fixed `0.3` cutoff, separate from the retriever's configured cutoff. |
| Report has no unflagged chunk rows | The current report writer exports only flagged units; use the source RAG JSON for all chunks. |

## Related documentation

- [`EVALUATION_PROCESS.md`](../../EVALUATION_PROCESS.md) — broader evaluation
  process and metric definitions.
- [`edrak_eval_agent_enhanced.py`](./edrak_eval_agent_enhanced.py) — evaluator
  implementation and CLI.
