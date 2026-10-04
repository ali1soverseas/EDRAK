# Deviations from SPEC

## Batch 1

- Spec section 3 lists `graph/` and `prompts/` packages with one module per node and per prompt. The repository convention (AGENTS.md) is `graph.py`, `state.py`, `nodes.py` and `prompts.py` per worker, so those existing modules are kept and the packages are not created.
- Spec section 6 defines worker-owned `TaskBrief` and result schemas. AGENTS.md requires every worker to accept the shared `ResearchTask` and return the shared `WorkerResult`. The `schemas/` package stays for worker-internal models, and the mapping to the shared contracts is to be decided when the schemas batch starts.
