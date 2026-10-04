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

## Documentation

- [docs/SPEC.md](docs/SPEC.md): implementation spec (kept local, not tracked)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/RUNBOOK.md](docs/RUNBOOK.md)
- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)
- [docs/DEVIATIONS.md](docs/DEVIATIONS.md)
