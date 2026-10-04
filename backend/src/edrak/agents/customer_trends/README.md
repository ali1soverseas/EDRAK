# Customer and Trends Worker

One of four parallel workers in the EDRAK multi-agent decision-intelligence platform. It collects and analyzes external customer and demand signals (social, reviews, news, search interest) for a business decision and writes structured findings and evidence to shared state. It reports evidence, confidence and gaps, never a go or no-go verdict.

## Quickstart

All commands run from `backend/`.

Install:

    make install

Configure:

    cp ../.env.example ../.env
    # fill in the keys you have

Run the tests and checks:

    make check

Launch the test UI (arrives in a later batch):

    make ui

## Configure the LLM

The worker talks to Ollama Cloud through the native chat route (`ChatOllama`, never the `/v1` shim). Settings come from `.env` at the repository root or the environment. The shared `LLM_*` keys are not used by this worker.

| Variable | Purpose | Default |
|---|---|---|
| `OLLAMA_API_KEY` | bearer token, required | none |
| `OLLAMA_MODEL` | model for every role | `gpt-oss:120b` |
| `OLLAMA_BASE_URL` | endpoint | `https://ollama.com` |
| `OLLAMA_MODEL_ANALYSIS` | optional model for the `analyst` role | falls back to `OLLAMA_MODEL` |
| `OLLAMA_TEMPERATURE`, `OLLAMA_TIMEOUT_S` | sampling and request timeout | `0.2`, `120` |

Check the endpoint with the smoke script (completion, tool calling, structured output):

    uv run python ../scripts/customer_trends/smoke_llm.py

It exits non-zero on any failure and never prints the key. Tests never touch the network: they use `ScriptedChatModel` from `llm/fake.py`.

## Documentation

- [docs/SPEC.md](docs/SPEC.md): implementation spec (kept local, not tracked)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/RUNBOOK.md](docs/RUNBOOK.md)
- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)
- [docs/DEVIATIONS.md](docs/DEVIATIONS.md)
