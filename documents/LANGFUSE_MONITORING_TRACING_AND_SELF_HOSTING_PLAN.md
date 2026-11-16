# EDRAK — Langfuse Observability, Token Costing, Prompt Versioning & Local Self-Hosting Master Plan

> **Target Subsystems**: `backend/src/edrak/core/llm.py`, `backend/src/edrak/core/config.py`, `backend/src/edrak/orchestration/`, and all worker agents (`agents/*/`)  
> **Applicable Stack**: Langfuse v2/v3, LangChain/LangGraph Callbacks, ChatOllama, Docker Compose  
> **Status**: Comprehensive Implementation Blueprint & Runbook  

---

## 1. Executive Summary & Value Proposition for EDRAK

As EDRAK evolves with multi-agent orchestration, Tri-Modal internal intelligence, and multi-turn verification loops, observability becomes mission-critical. Integrating **Langfuse** provides four foundational capabilities:

```
                            ┌──────────────────────────────────────────────────┐
                            │           EDRAK Agentic Platform                 │
                            │   (LangGraph Orchestrator + Multi-Agent Workers) │
                            └────────────────────────┬─────────────────────────┘
                                                     │
                                                     ▼
                            ┌──────────────────────────────────────────────────┐
                            │          Langfuse Unified Observability          │
                            └────────────────────────┬─────────────────────────┘
                                                     │
         ┌───────────────────┬───────────────────────┼───────────────────────┬───────────────────┐
         ▼                   ▼                       ▼                       ▼                   ▼
┌─────────────────┐ ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐ ┌─────────────────┐
│ Full Execution  │ │ Exact Token     │     │ Financial Cost  │ │ Prompt          │ │ Error Isolation │
│ Tracing (DAG)   │ │ Accounting      │     │ Calculation     │ │ Versioning      │ │ & Root Cause    │
│ (Nodes, Tools)  │ │ (Input/Output)  │     │ ($ per 1M tok)  │ │ (Prod / Staging)│ │ (Failed Spans)  │
└─────────────────┘ └─────────────────┘     └─────────────────┘     └─────────────────┘ └─────────────────┘
```

1. **End-to-End Tracing (DAG-Aware)**: Traces every LangGraph superstep, agent task dispatch, internal database SQL introspections, API calls, and verification gate decisions in a hierarchical tree.
2. **Token Accounting & Financial Costing**: Tracks exact prompt tokens, completion tokens, and dollar expenditures calculated against custom model pricing schemas (local Ollama models or hosted LLMs).
3. **Prompt Versioning & Management**: Decouples prompt strings from Python source code, allowing zero-deployment prompt tuning, A/B testing, and auditability of which exact prompt version produced a given finding.
4. **Instant Error Diagnosis**: Pinpoints failing nodes, malformed JSON outputs, tool execution timeouts, or schema validation failures with full input/output payload visibility.

---

## 2. Device Prerequisites & Local Self-Hosting Guide

Langfuse can be run 100% locally on your machine with zero external cloud dependencies using Docker Compose.

### 2.1 Hardware & System Prerequisites

| Resource | Minimum Requirement | Recommended for Production / Heavy Tracing |
| :--- | :--- | :--- |
| **Operating System** | Windows 10/11 with WSL 2 or Linux / macOS | Windows 11 with WSL 2 Ubuntu backend |
| **RAM** | 4 GB free RAM | 8 GB+ free RAM (ClickHouse + Postgres + Server) |
| **CPU** | 2 Cores | 4+ Cores |
| **Disk Space** | 10 GB free space | 25 GB+ SSD space (for trace logs and ClickHouse columnar storage) |
| **Software** | Docker Desktop (with Compose v2.x enabled) | Docker Desktop 4.25+ |

---

### 2.2 Local Docker Compose Architecture

Self-hosted Langfuse runs 5 core containers:
* **`langfuse-server`**: Web UI and REST/SDK ingestion API (`http://localhost:3000`).
* **`langfuse-worker`**: Background queue worker for batching traces, token calculation, and exports.
* **`postgres`**: Relational store for users, projects, API keys, and prompt versions.
* **`clickhouse`**: Columnar analytical store optimized for high-volume spans, traces, and metrics.
* **`redis`**: Low-latency cache and task queue broker.
* **`minio`**: S3-compatible local blob store for large prompt/completion payloads.

```
                                  Browser / User
                                        │ (Port 3000)
                                        ▼
                            ┌───────────────────────┐
                            │    langfuse-server    │
                            │ (Web UI + Ingest API) │
                            └───────────┬───────────┘
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
        ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
        │   PostgreSQL    │    │      Redis      │    │   ClickHouse    │
        │  (Auth, Prompts,│    │ (Task Queue &   │    │  (Fast Traces & │
        │   Project Keys) │    │  Rate Limiting) │    │  Token Events)  │
        └─────────────────┘    └────────┬────────┘    └─────────────────┘
                                        │
                                        ▼
                               ┌─────────────────┐
                               │ langfuse-worker │
                               │ (Batch Ingest & │
                               │ Cost Calculator)│
                               └─────────────────┘
```

---

### 2.3 Ready-to-Run `docker-compose.langfuse.yml`

Create this file in your root workspace: `EDRAK/docker-compose.langfuse.yml`:

```yaml
version: "3.9"

services:
  langfuse-server:
    image: ghcr.io/langfuse/langfuse:latest
    container_name: edrak-langfuse-server
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - NODE_ENV=production
      - DATABASE_URL=postgresql://langfuse:langfuse_password@postgres:5432/langfuse
      - CLICKHOUSE_URL=http://clickhouse:8123
      - CLICKHOUSE_USER=default
      - CLICKHOUSE_PASSWORD=clickhouse_password
      - REDIS_URL=redis://redis:6379
      - NEXTAUTH_SECRET=edrak_secret_key_change_me_in_production_1234567890
      - SALT=edrak_salt_key_1234567890
      - NEXTAUTH_URL=http://localhost:3000
      - TELEMETRY_ENABLED=false
      - LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES=true
    depends_on:
      - postgres
      - clickhouse
      - redis

  langfuse-worker:
    image: ghcr.io/langfuse/langfuse-worker:latest
    container_name: edrak-langfuse-worker
    restart: unless-stopped
    environment:
      - DATABASE_URL=postgresql://langfuse:langfuse_password@postgres:5432/langfuse
      - CLICKHOUSE_URL=http://clickhouse:8123
      - CLICKHOUSE_USER=default
      - CLICKHOUSE_PASSWORD=clickhouse_password
      - REDIS_URL=redis://redis:6379
      - SALT=edrak_salt_key_1234567890
      - TELEMETRY_ENABLED=false
    depends_on:
      - postgres
      - clickhouse
      - redis

  postgres:
    image: postgres:16-alpine
    container_name: edrak-langfuse-postgres
    restart: unless-stopped
    environment:
      - POSTGRES_USER=langfuse
      - POSTGRES_PASSWORD=langfuse_password
      - POSTGRES_DB=langfuse
    volumes:
      - langfuse_pgdata:/var/lib/postgresql/data
    ports:
      - "5433:5432"

  clickhouse:
    image: clickhouse/clickhouse-server:latest
    container_name: edrak-langfuse-clickhouse
    restart: unless-stopped
    environment:
      - CLICKHOUSE_DB=default
      - CLICKHOUSE_USER=default
      - CLICKHOUSE_PASSWORD=clickhouse_password
    volumes:
      - langfuse_chdata:/var/lib/clickhouse
    ports:
      - "8123:8123"

  redis:
    image: redis:7-alpine
    container_name: edrak-langfuse-redis
    restart: unless-stopped
    volumes:
      - langfuse_redisdata:/data

volumes:
  langfuse_pgdata:
  langfuse_chdata:
  langfuse_redisdata:
```

---

### 2.4 Self-Hosting Step-by-Step Setup Runbook

#### Step 1: Start the Local Stack
Open PowerShell in the EDRAK directory and run:
```powershell
docker compose -f docker-compose.langfuse.yml up -d
```

#### Step 2: Access the Langfuse Web Console
1. Open your browser and navigate to: **`http://localhost:3000`**
2. Click **Sign Up** to create your local admin account (e.g., `admin@edrak.local`).
3. Create a project named **`EDRAK`**.

#### Step 3: Generate API Credentials
1. In the Langfuse dashboard, navigate to **Settings** $\to$ **API Keys**.
2. Click **Create new API keys**.
3. Copy the three values:
   * **`LANGFUSE_PUBLIC_KEY`**: `pk-lf-...`
   * **`LANGFUSE_SECRET_KEY`**: `sk-lf-...`
   * **`LANGFUSE_HOST`**: `http://localhost:3000`

---

## 3. Configuration & Environment Integration in EDRAK

### 3.1 Dependencies
Add `langfuse` to `requirements.txt`:
```text
langfuse>=2.36.0
```

Install the dependency:
```powershell
.venv\Scripts\pip install langfuse
```

### 3.2 Update `backend/src/edrak/core/config.py`
Add native Langfuse settings to EDRAK's Pydantic settings:

```python
# In backend/src/edrak/core/config.py:

    # Langfuse Observability & Tracing Settings
    LANGFUSE_ENABLED: bool = Field(default=False, validation_alias=AliasChoices("LANGFUSE_ENABLED"))
    LANGFUSE_PUBLIC_KEY: Optional[str] = Field(default=None, validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY"))
    LANGFUSE_SECRET_KEY: Optional[str] = Field(default=None, validation_alias=AliasChoices("LANGFUSE_SECRET_KEY"))
    LANGFUSE_HOST: str = Field(default="http://localhost:3000", validation_alias=AliasChoices("LANGFUSE_HOST"))
    LANGFUSE_RELEASE: str = Field(default="edrak-2.0.0", validation_alias=AliasChoices("LANGFUSE_RELEASE"))
```

### 3.3 Add to `.env`
```dotenv
# Langfuse Monitoring & Cost Tracing
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-your-public-key
LANGFUSE_SECRET_KEY=sk-lf-your-secret-key
LANGFUSE_HOST=http://localhost:3000
```

---

## 4. Tracing, Error Diagnosis & LangGraph Integration

Langfuse provides native integration with LangChain and LangGraph via `CallbackHandler`.

### 4.1 Global Langfuse Handler Factory
Create `backend/src/edrak/core/observability.py`:

```python
"""Langfuse Observability & Tracing Factory for EDRAK."""

import logging
from typing import Optional
from langfuse import Langfuse
from langfuse.callback import CallbackHandler
from edrak.core.config import settings

logger = logging.getLogger(__name__)

_langfuse_client: Optional[Langfuse] = None

def get_langfuse_client() -> Optional[Langfuse]:
    """Returns singleton Langfuse client if enabled."""
    global _langfuse_client
    if not settings.LANGFUSE_ENABLED:
        return None
    if _langfuse_client is None:
        try:
            _langfuse_client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_HOST,
                release=settings.LANGFUSE_RELEASE,
            )
            logger.info("Langfuse client initialized successfully at %s", settings.LANGFUSE_HOST)
        except Exception as e:
            logger.warning("Failed to initialize Langfuse client: %s", e)
            return None
    return _langfuse_client

def get_langfuse_callback_handler(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    trace_name: str = "edrak_execution",
) -> Optional[CallbackHandler]:
    """Returns a Langfuse CallbackHandler for LangGraph and ChatOllama invocations."""
    if not settings.LANGFUSE_ENABLED:
        return None
    try:
        handler = CallbackHandler(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
            session_id=session_id,
            user_id=user_id,
            tags=tags or ["edrak", settings.EDRAK_ENV],
            trace_name=trace_name,
        )
        return handler
    except Exception as e:
        logger.warning("Failed to build Langfuse CallbackHandler: %s", e)
        return None
```

### 4.2 Auto-Attaching Callbacks in `backend/src/edrak/core/llm.py`

Update `get_chat_model()` and `get_json_object()` in [`backend/src/edrak/core/llm.py`](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/backend/src/edrak/core/llm.py) to automatically receive callbacks:

```python
# In backend/src/edrak/core/llm.py:

def get_chat_model(
    model: Optional[str] = None,
    *,
    agent: Optional[str] = None,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
    num_predict: Optional[int] = None,
    callbacks: Optional[list] = None,
) -> ChatOllama:
    from edrak.core.observability import get_langfuse_callback_handler
    
    cb_list = list(callbacks or [])
    default_handler = get_langfuse_callback_handler(
        tags=["agent:" + (agent or "default")],
        trace_name=f"llm_{agent or 'default'}",
    )
    if default_handler:
        cb_list.append(default_handler)

    return ChatOllama(
        model=model or settings.OLLAMA_MODEL,
        base_url=base_url or NATIVE_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
        num_predict=num_predict or DEFAULT_NUM_PREDICT,
        callbacks=cb_list,
        client_kwargs={"headers": {"Authorization": f"Bearer {ollama_key(agent)}"}},
    )
```

### 4.3 Attaching Tracing to LangGraph Orchestrator

When running the orchestrator in [`backend/src/edrak/api/analysis_service.py`](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/backend/src/edrak/api/analysis_service.py):

```python
# Pass Langfuse handler to graph.invoke or graph.astream
handler = get_langfuse_callback_handler(
    session_id=analysis_id,
    user_id=workspace_id,
    trace_name="orchestrator_analysis_run",
    tags=["pipeline:full", company_name],
)

config = {
    "configurable": {"thread_id": analysis_id},
    "callbacks": [handler] if handler else [],
}

final_state = await graph.ainvoke(initial_state, config=config)
```

**What This Gives You in Langfuse UI:**
* A full interactive waterfall chart of every node: `plan` $\to$ `dispatch` (parallel workers) $\to$ `verify` $\to$ `metrics` $\to$ `cross_signal` $\to$ `finalize`.
* Exact latency of each step in milliseconds.
* Direct visibility into which worker threw an exception or returned empty evidence.

---

## 5. Token Counting & Financial Costing Engine

### 5.1 How Langfuse Calculates Tokens
When `ChatOllama` or OpenAI models execute, LangChain extracts token usage from the model's `usage_metadata`:
* `input_tokens` (Prompt tokens)
* `output_tokens` (Completion tokens)
* `total_tokens`

The Langfuse `CallbackHandler` intercepts this metadata and streams it to ClickHouse.

### 5.2 Configuring Model Pricing in Langfuse
In self-hosted Langfuse, model pricing is managed under **Settings** $\to$ **Models**:

1. Click **Add Model Definition**.
2. Fill in the pricing for your model:
   * **Model Name / Match Pattern**: `gpt-oss:120b` (or `llama3.1`, `qwen2.5`)
   * **Input Price**: e.g., `$0.15` per 1,000,000 tokens
   * **Output Price**: e.g., `$0.60` per 1,000,000 tokens
3. Save the model.

Every subsequent run will automatically display:
* Total tokens used per agent.
* Exact USD cost per research brief.
* Aggregate cost per company profile or analysis session.

---

## 6. Prompt Versioning & Management

Currently, prompts in EDRAK are hardcoded strings in `prompts.py` across different workers. Langfuse allows storing and versioning them dynamically while maintaining a deterministic local fallback.

### 6.1 Architecture for Resilient Prompt Fetching

```
             ┌──────────────────────────────────────────────┐
             │ Request Prompt (e.g. 'internal_synthesize')  │
             └──────────────────────┬───────────────────────┘
                                    │
                         Is Langfuse reachable?
                         ┌──────────┴──────────┐
                     YES │                     │ NO (or disabled)
                         ▼                     ▼
             ┌──────────────────────┐ ┌──────────────────────┐
             │ Fetch from Langfuse  │ │ Use Hardcoded Python │
             │ (Versioned / Labeled)│ │ Fallback String      │
             └──────────┬───────────┘ └──────────┬───────────┘
                        │                        │
                        └───────────┬────────────┘
                                    ▼
                         Return Compiled Prompt
                       (Linked to Trace in UI)
```

### 6.2 Prompt Fetcher Implementation

In `backend/src/edrak/core/observability.py`:

```python
def get_versioned_prompt(prompt_name: str, fallback_template: str, label: str = "production") -> str:
    """Fetches a versioned prompt from Langfuse with deterministic offline fallback."""
    client = get_langfuse_client()
    if not client:
        return fallback_template

    try:
        prompt_obj = client.get_prompt(prompt_name, label=label)
        # Langfuse prompt template supports mustache or f-string variables
        return prompt_obj.prompt
    except Exception as e:
        logger.debug("Could not fetch prompt '%s' from Langfuse (%s). Using fallback.", prompt_name, e)
        return fallback_template
```

### 6.3 Example Usage in an Agent Worker
In [`backend/src/edrak/agents/internal_intelligence/nodes.py`](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/backend/src/edrak/agents/internal_intelligence/nodes.py):

```python
from edrak.core.observability import get_versioned_prompt
from edrak.agents.internal_intelligence.prompts import INTERNAL_SYNTHESIS_SYSTEM_PROMPT

# Dynamically resolves production version from Langfuse, falls back to Python constant
system_prompt = get_versioned_prompt(
    prompt_name="internal_intelligence_synthesis",
    fallback_template=INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
    label="production",
)
```

**Benefits of Prompt Management:**
* Edit prompts in the Langfuse UI without restarting EDRAK or creating git commits.
* Rollback instantly if a prompt tweak causes JSON parsing regressions.
* Tag prompt versions with labels: `production`, `staging`, `experimental`.

---

## 7. Phased Implementation Roadmap

```mermaid
flowchart TD
    A["Phase 1: Local Docker Setup"] --> B["Phase 2: Core Observability Module"]
    B --> C["Phase 3: LLM & Graph Callback Hooks"]
    C --> D["Phase 4: Token & Cost Definition"]
    D --> E["Phase 5: Prompt Versioning Migration"]
    E --> F["Phase 6: End-to-End Verification Suite"]
```

### Phase 1: Local Environment & Docker (1-2 Hours)
* Deploy `docker-compose.langfuse.yml`.
* Sign up and create the `EDRAK` project.
* Generate and store API keys in `.env`.

### Phase 2: Core Observability Factory (2 Hours)
* Create `backend/src/edrak/core/observability.py`.
* Expose `get_langfuse_callback_handler()` and `get_versioned_prompt()`.
* Add settings to `config.py`.

### Phase 3: LangGraph & LLM Hooking (2-3 Hours)
* Update `backend/src/edrak/core/llm.py` to auto-inject handlers.
* Update `backend/src/edrak/api/analysis_service.py` to pass session tags and user IDs.
* Verify error stack traces appear on failing nodes in the Langfuse UI.

### Phase 4: Token Pricing & Cost Configuration (1 Hour)
* Set up model cost schemas in the Langfuse UI for `gpt-oss:120b`, `llama3.1`, etc.
* Confirm that each analysis run calculates token totals and total dollar costs.

### Phase 5: Prompt Versioning Migration (2 Hours)
* Register core agent prompts in Langfuse.
* Update agent nodes to use `get_versioned_prompt` with offline fallbacks.

### Phase 6: Automated Verification Script (1 Hour)
* Write `scripts/verify_langfuse_integration.py` to assert that traces, prompts, and tokens are flowing properly.

---

## 8. Summary Checklist Before Starting

- [ ] Docker Desktop installed and running on Windows with WSL 2.
- [ ] Port `3000` (Langfuse UI), `8123` (ClickHouse), and `5433` (Postgres) available.
- [ ] Minimum 4 GB RAM allocated to Docker.
- [ ] `langfuse` Python package installed in `.venv`.
- [ ] `.env` updated with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST`.

---

## 9. Implemented Operational Runbook & Production Reference

### 9.1 Commands Quick Reference

| Action | Command | Purpose |
| :--- | :--- | :--- |
| **Start Langfuse Stack** | `docker compose -f docker-compose.langfuse.yml up -d` | Launches all 6 local containers (Web, Worker, Postgres, ClickHouse, MinIO, Redis) |
| **Stop Langfuse Stack** | `docker compose -f docker-compose.langfuse.yml down` | Gracefully stops all Langfuse local containers |
| **Seed All 12 Prompts** | `.venv\Scripts\python scripts/seed_langfuse_prompts.py` | Uploads and versions all agent prompts in the Langfuse Prompts catalog |
| **Run Observability Tests** | `.venv\Scripts\pytest tests/test_observability.py -v` | Executes the 7 automated tests verifying config, callbacks, and prompt fallbacks |

---

### 9.2 Complete Prompt Catalog in Langfuse (12 System & Agent Prompts)

All 12 agent prompts across EDRAK are cataloged and versioned:

| Prompt Name in Langfuse | Owning Agent | Tags | Purpose |
| :--- | :--- | :--- | :--- |
| `edrak-planner-prompt` | **Orchestration** | `orchestration`, `planner` | Control-plane task decomposition into bounded worker assignments |
| `edrak-request-builder-prompt` | **Orchestration** | `orchestration`, `parser` | Translates raw business queries into structured research requests |
| `edrak-internal-query-planner` | **Internal Intelligence** | `internal_intelligence`, `retrieval` | Formulates targeted queries for handbook, SQLite DB, and REST API |
| `edrak-internal-synthesis` | **Internal Intelligence** | `internal_intelligence`, `synthesis` | Strict factual evidence synthesis with quote attribution |
| `edrak-cross-signal-synthesis` | **Cross-Signal** | `cross_signal`, `synthesis` | Reconciles findings and detects cross-domain contradictions & momentum |
| `edrak-cross-signal-summary` | **Cross-Signal** | `cross_signal`, `summary` | Executive cross-signal synthesis summary |
| `edrak-decision-analysis` | **Decision Analysis** | `decision_analysis`, `strategy` | Evaluates strategic business opportunities, trade-offs, and decisions |
| `edrak-metrics-finder` | **Metrics Extraction** | `metrics`, `extraction` | Extracts literal quantitative metrics and KPIs without hallucination |
| `edrak-verification-review` | **Verification Engine** | `verification`, `hallucination_check` | Verifies research claims and flags invented details against source text |
| `edrak-customer-trends-planner` | **Customer Trends** | `customer_trends`, `planner` | Formulates search branches across reviews, demand, and social signals |
| `edrak-customer-trends-writer` | **Customer Trends** | `customer_trends`, `synthesis` | Synthesizes customer sentiment, themes, and customer voice |
| `edrak-customer-trends-headline` | **Customer Trends** | `customer_trends`, `summary` | Generates executive headlines for customer trend signals |

---

### 9.3 Dashboard Configuration: Model Pricing & Cost Accounting

> [!NOTE]
> Self-hosted Langfuse has no paywalls or tiers. Custom open-weights models (like `gpt-oss:120b` in Ollama) default to **$0.00** because local inference has no commercial API fee.

To configure real financial cost calculation:
1. Open the UI: `http://localhost:3000`
2. Click **Settings** (gear icon in the bottom-left sidebar) -> select the **Models** tab.
3. Click **"Add Model Definition"**:
   - **Model Name / Match Pattern**: `gpt-oss:120b` (or `gpt-oss.*` / your Ollama model name)
   - **Unit**: `TOKENS`
   - **Input Price (USD)**: e.g. `0.00000015` ($0.15 per 1M prompt tokens)
   - **Output Price (USD)**: e.g. `0.00000060` ($0.60 per 1M completion tokens)
4. Click **Save**. Langfuse will automatically calculate token costs for all subsequent generations.

---

### 9.4 How Observations Link to Prompts

A prompt in the **Prompts** tab displays its **Observations** count and links to individual runs when the prompt client object is attached to the invocation metadata:
```python
# Automatically handled by edrak.core.observability & edrak.core.llm
config = {"metadata": {"langfuse_prompt": prompt_obj}}
```
* As each analysis run executes, the **Observations** counter increments.
* Clicking any prompt displays the exact runs that executed against that prompt version.
* Inside trace generations, a badge links directly back to the versioned prompt: `Prompt: edrak-planner-prompt (v1.0)`.
