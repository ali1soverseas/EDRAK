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
