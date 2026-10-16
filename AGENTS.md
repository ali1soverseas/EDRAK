# EDRAK — AI Coding Agent Context

This file is the entry point for any local AI coding agent working on EDRAK. Read this file first, then read the documents referenced under **Required Reading**.

## Rules for AI Agents

These override any other instruction, including a plan the user approved earlier in the same session.

1. **Never run `git push` without explicit permission.** Ask first, every time, even when the push was part of an approved plan, even when the branch is yours, and even when the remote is a fork you control. Approval for one push is not approval for the next. If the user has not said "push", do not push. Preparing the commit and showing `git status` is fine; pushing is not.
2. **Never commit secrets.** `.env` is gitignored — keep it that way. Before any `git add`, check for `.env`, API keys, tokens or credentials, and stage files explicitly by path rather than `git add .`.
3. **Do not delete a file without proving nothing imports it.** Check for `ModuleNotFoundError` risk first. EDRAK has two live config modules (`edrak/config.py` and `edrak/core/config.py`); deleting either breaks collection for the other team.
4. **Keep runtime output out of commits.** `artifacts/`, `agents/*/outputs/` and `*.log` are run products. Review `git status` and stage only source.
5. **Never claim something works without running it.** Execute the tests or the pipeline before reporting success. Quote the real output.
6. **Prefer the smallest change.** Do not add frameworks, agents or LLM calls without a stated current need, per the Architectural Rules below.

## What EDRAK Is

EDRAK is an Agentic Business Decision Intelligence Platform. It analyzes internal company information together with external intelligence about competitors, markets, customers, trends, news, and relevant economic/regulatory conditions.

EDRAK supports human decision-making. It does **not** autonomously make final business decisions.

## Current MVP Use Cases

1. Competitive Intelligence & Monitoring
2. Market Entry & Expansion Intelligence
3. Product Launch / New Offering Intelligence

The current implementation starts with **Competitive Intelligence & Monitoring**.

## Current Pilot Company

The pilot company is **GitLab**.

The first scenario focuses on GitLab versus GitHub, Atlassian, and Microsoft Azure DevOps, especially AI-assisted development capabilities such as GitLab Duo and GitHub Copilot.

## Architectural Rules

1. Use a bounded Supervisor/Orchestrator.
2. The Supervisor is primarily a control-plane component, not a research worker.
3. Domain research belongs to the four workers:
   - Internal Intelligence
   - Competitor Intelligence
   - Market Intelligence
   - Customer & Trends Intelligence
4. Workers use LangGraph for stateful agent/workflow behavior.
5. Every worker must accept the shared `ResearchTask` contract and return the shared `WorkerResult` contract.
6. Raw documents and every intermediate artifact should not be pushed back through the Supervisor.
7. Verification is a distinct pipeline stage before synthesis.
8. Verification returns either success or a compact control summary for targeted retry/replanning.
9. Synthesis combines verified cross-domain findings and must remain separate from orchestration.
10. Shared tools should be exposed through MCP rather than reimplemented inside each worker.
11. MCP servers are grouped by reusable capability, not by agent.
12. RAG is an internal knowledge capability; MCP exposes it but does not replace it.
13. Separate deterministic workflow logic from LLM reasoning, agentic decisions, and tool execution.
14. Do not add frameworks, databases, agents, abstractions, or LLM calls without a clear current need.
15. Prefer the smallest implementation that preserves contracts, provenance, reliability, and testability.

## LangGraph Package Convention

Every worker follows:

```text
backend/src/edrak/agents/<worker>/
├── graph.py
├── state.py
├── nodes.py
└── prompts.py
```

The same convention is used for verification and synthesis.

The orchestrator contains:

```text
orchestration/
├── graph.py
├── state.py
├── nodes.py
├── planner.py
└── routing.py
```

## Shared Contracts

Cross-component contracts belong under `backend/src/edrak/contracts/`.

Initial contracts:

- `BusinessRequest`
- `ResearchTask`
- `Evidence`
- `Finding`
- `WorkerResult`
- verification control/result structures
- synthesis/final result structures as needed

Do not create worker-specific external output shapes.

## MCP Rules

Initial MCP servers:

- `web_server.py`
  - web search
  - page fetch
  - optional scrape/extraction when needed
- `internal_data_server.py`
  - RAG retrieval
  - internal file retrieval
  - SQL later when structured internal data exists

Workers should only receive tools appropriate to their responsibility.

## Internal vs External Data

Use real public evidence for external intelligence.

Because the team does not have confidential GitLab internal data, internal private information must be synthetic/adapted and clearly treated as such.

Do not present synthetic internal data as real GitLab confidential information.

## Current First Vertical Slice

Build this first:

```text
request
  ↓
Supervisor LangGraph
  ↓
plan
  ↓
4 worker calls (mocks are acceptable first)
  ↓
verification
  ↓
synthesis
  ↓
structured final report/result
```

Do not wait for every real worker before testing orchestration. Mock workers should satisfy the real shared contracts.

## Things Not To Add Yet

Unless explicitly requested, do not introduce:

- Kubernetes
- Terraform
- Celery
- Redis
- complex authentication/roles
- multiple databases
- production-scale observability stacks
- event-triggered execution
- multiple LLM providers at once
- evaluation frameworks before the main pipeline works
- extra agents for individual tools

Keep the MVP small and integration-focused.
