# EDRAK | إدراك

EDRAK is an Agentic Business Decision Intelligence Platform for evidence-backed business decision support.

## Current MVP Scope

The MVP focuses on three use cases:

1. Competitive Intelligence & Monitoring
2. Market Entry & Expansion Intelligence
3. Product Launch / New Offering Intelligence

The current implementation starts with **Use Case 1: Competitive Intelligence & Monitoring** using **GitLab** as the pilot company.

## Core Architecture

EDRAK uses a bounded Supervisor/Orchestrator with four domain-specialized LangGraph workers:

- Internal Intelligence
- Competitor Intelligence
- Market Intelligence
- Customer & Trends Intelligence

Worker outputs are collected into shared structured state, verified, and then passed to cross-signal synthesis. Humans remain responsible for final business decisions.

Shared tool capabilities are exposed through MCP. Internal knowledge retrieval uses RAG.

## Repository Areas

- `backend/` — FastAPI backend, LangGraph orchestration/workers, verification, synthesis, contracts, MCP client, RAG
- `mcp_servers/` — shared MCP tool servers
- `frontend/` — React/TypeScript UI
- `data/` — synthetic/internal data and generated vector-store persistence
- `artifacts/` — generated run outputs and reports
- `scripts/` — developer CLI entry points
- `docs/` — project context and architecture documentation

## Current First Vertical Slice

The first implementation target is:

```text
Business Request
    ↓
FastAPI / CLI
    ↓
Supervisor LangGraph
    ↓
Research Plan
    ↓
4 Worker LangGraphs
    ↓
Verification
    ↓
Cross-Signal Synthesis
    ↓
Competitive Intelligence Report
```

The first concrete scenario is a GitLab competitive-intelligence request comparing GitLab Duo with GitHub Copilot and Atlassian AI capabilities.
