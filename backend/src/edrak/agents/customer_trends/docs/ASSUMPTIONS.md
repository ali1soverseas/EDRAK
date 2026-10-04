# Assumptions

## Batch 1

- The repository structure changed: the worker lives in `backend/src/edrak/agents/customer_trends/`, imported as `edrak.agents.customer_trends`. The standalone `agents/customer_trends/` project from the first attempt is gone.
- `backend/pyproject.toml` was an empty placeholder, so it now defines the shared `edrak` project (src layout, hatchling) with the worker's dependencies. Other components add their own dependencies there.
- `make` targets, lint and mypy are scoped to the worker's paths so other components are not checked or reformatted. Run them from `backend/`.
- Tests live in `backend/tests/customer_trends/`, evals in `backend/evals/customer_trends/`, scripts in `scripts/customer_trends/`, config YAML in the package's `config/`.
- The repository `.gitignore` ignores `docs/`. The worker docs directory is re-included, and only `docs/SPEC.md` is ignored, at the owner's request.
- The worker settings in `.env.example` are appended to the existing root file. `EDRAK_ENV` already exists there, so the spec's duplicate line is omitted.

## Batch 1

- `Settings` ignores empty values (`env_ignore_empty`), so `KEY=` lines in `.env.example` fall back to defaults or `None`.
- `EDRAK_ENV` is the shared key (`development` in the root `.env.example`); `prod` or `production` selects JSON logs, anything else console logs.
- `structured_call`, `invoke_with_retry` and the fake model are async, matching the async-first rule (SPEC section 4). The smoke script wraps them with `asyncio.run`.
- `structured_call` asks for `include_raw=True` so the repair attempt can show the model its own invalid output.
- `ScriptedChatModel.bind_tools` returns the same instance, so the script cursor and recorded calls are shared across bound copies.
- The worker's `tests/**` lint config also ignores S105 and S106 (fake secrets in assertions), in addition to S101.
- The lint paths in `backend/Makefile` include `../scripts/customer_trends`.
- Against the live endpoint (`gpt-oss:120b` on Ollama Cloud) the `format` schema constraint is not reliably honored, so `structured_call` also states the JSON schema in a closing user message. With that, the smoke script passes all three checks.

## Batch 2

- Evidence ids are unique per run, so `EvidenceStore.get_items` and `existing_ids` take a `run_id` (the spec sketch lists only ids).
- `add_batch` stamps `run_id`, `task_id` and `batch_id` on every item it stores, because providers build items before a batch exists. A batch always gets a row, even when empty, so trend series and summaries can hang off it. `batch_id` in a tool response is exposed only when something was stored.
- `Finding` applies only the store-free rules: distinct evidence ids, and fewer than two forces `low` plus the `single_source` caveat. It does not require non-empty `evidence_ids`; `submit_findings` (Batch 6) rejects that case with a reason, so one bad finding does not invalidate a whole model draft.
- `ToolResponse.preview` is coerced (first five items, snippets cut to 200 characters), not rejected, so a tool can never fail on it.
- `EvidenceItem.engagement_total` sums interaction counts and leaves out `views` and `rating`. It drives the `top` sample and `min_engagement`.
- `id` (16 hex) and `content_hash` (40 hex) are enforced on `EvidenceItem`. `run_id` must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` because it becomes a directory name in the sink.
- Datetimes are normalized to UTC; a naive datetime is taken as UTC. Date filters (`since`, `until`) use `published_at`, so undated items never match them.
- `TaskBrief.focus` defaults to empty: `intake` fills it from `use_cases.yaml`.
- Models added beyond the spec's list, each needed by the store: `MetricResult` (what `save_metric` stores), `EvidenceFilters`, `QueryResult`, `RunSummary`, `ReviewTarget`, `ReviewStore`, `Sentiment`.
- `ThemeAggregate` has no quotes field (SPEC 6.6), but `Theme` does. Batch 6 has to decide where `analyze_text` keeps representative quotes (for example as a second stored record).
- `find_numbers` skips digits glued to a Latin letter (Q3, B2B, GPT4) and treats years as ordinary numbers. Batch 6 can exempt years from the claim-versus-metrics rule if findings need them.
- `detect_language` returns `None` for texts with fewer than five letters (outside the Arabic script rule) and for short text where langdetect is under 90 percent sure. Arabic-dominant short text is `ar`.
- `langdetect` ships without type stubs: `backend/pyproject.toml` has a mypy override for it.
- Search for `text_contains` compares normalized, case-folded text, so Arabic diacritics and case do not matter.
