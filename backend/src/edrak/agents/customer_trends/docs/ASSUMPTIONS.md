# Assumptions

## Batch 1

- The repository structure changed: the worker lives in `backend/src/edrak/agents/customer_trends/`, imported as `edrak.agents.customer_trends`. The standalone `agents/customer_trends/` project from the first attempt is gone.
- `backend/pyproject.toml` was an empty placeholder, so it now defines the shared `edrak` project (src layout, hatchling) with the worker's dependencies. Other components add their own dependencies there.
- `make` targets, lint and mypy are scoped to the worker's paths so other components are not checked or reformatted. Run them from `backend/`.
- Tests live in `backend/tests/customer_trends/`, evals in `backend/evals/customer_trends/`, scripts in `scripts/customer_trends/`, config YAML in the package's `config/`.
- The repository `.gitignore` ignores `docs/`. The worker docs directory is re-included, and only `docs/SPEC.md` is ignored, at the owner's request.
- The worker settings in `.env.example` are appended to the existing root file. `EDRAK_ENV` already exists there, so the spec's duplicate line is omitted.
