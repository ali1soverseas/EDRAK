# Contributing to EDRAK

This document defines how the EDRAK team should work on the repository.

---

# Branches

Main branches:

- `main` — stable/demo-ready
- `develop` — integration branch

Feature work should use branches such as:

- `feature/shared-contracts`
- `feature/orchestrator`
- `feature/internal-agent`
- `feature/competitor-agent`
- `feature/market-agent`
- `feature/customer-trends-agent`
- `feature/verification`
- `feature/synthesis`
- `feature/web-mcp`
- `feature/internal-data-mcp`
- `feature/rag`
- `feature/api`
- `feature/frontend`

Do not develop directly on `main`.

---

# Branch Naming

Use:

- `feature/...`
- `fix/...`
- `test/...`
- `docs/...`
- `refactor/...`
- `chore/...`

Examples:

feature/orchestrator
fix/competitor-empty-result
test/orchestrator-routing
docs/contracts

---

# Commit Messages

Use:

<type>(<scope>): <description>

Examples:

feat(orchestrator): add planning node

feat(competitor): add competitor research graph

fix(verification): handle missing evidence

test(contracts): validate worker result schema

docs(architecture): update MCP flow

Allowed types:

- feat
- fix
- test
- docs
- refactor
- chore

---

# Shared Contracts

Shared contracts must be agreed before workers depend on them.

Located under:

backend/src/edrak/contracts/

All workers must accept:

ResearchTask

and return:

WorkerResult

Agents must not invent incompatible input/output structures.

Changes to shared contracts should be reviewed by:

- orchestrator owner
- at least one worker owner

---

# Worker Convention

Every worker follows:

backend/src/edrak/agents/<worker>/
├── graph.py
├── state.py
├── nodes.py
└── prompts.py

graph.py:
LangGraph topology.

state.py:
private worker state.

nodes.py:
graph node implementations.

prompts.py:
LLM prompts.

Private worker state must not become part of the orchestrator contract.

---

# MCP Rules

Shared capabilities should be exposed through MCP.

Do not create one MCP server per worker.

Initial MCP servers:

- web_server.py
- internal_data_server.py

Workers should consume MCP tools through the EDRAK MCP integration layer.

Do not directly import another worker's tool implementation.

---

# Secrets

Never commit:

- API keys
- access tokens
- passwords
- `.env`

Use environment variables.

Update `.env.example` when introducing a new required setting.

---

# Pull Requests

Each PR should:

- address one clear concern
- pass tests
- preserve shared contracts
- contain no secrets
- explain what changed
- explain how it was tested

Worker PRs must demonstrate that output validates as `WorkerResult`.

---

# Definition of Done for a Worker

A worker is ready for integration when:

- it accepts `ResearchTask`
- it returns valid `WorkerResult`
- it has at least one test
- it handles no-evidence cases without crashing
- it handles tool failures reasonably
- its findings retain evidence provenance
- the orchestrator can invoke it without understanding private state

---

# Integration Strategy

Do not wait for all workers before building orchestration.

The orchestrator should initially run with mock workers.

Target:

BusinessRequest
-> Orchestrator
-> ResearchTasks
-> Mock Workers
-> WorkerResults
-> Verification
-> Synthesis

Then real workers replace mocks incrementally.

---

# Architecture Changes

Do not introduce new:

- agents
- frameworks
- databases
- queues
- services
- LLM calls
- infrastructure layers

unless they solve a concrete current requirement.

Keep the MVP architecture small.