# EDRAK — Tri-Modal Internal Intelligence Integration & Testing Guide
## Complete Flow, Architecture, and Runbook for Vector RAG, Dynamic SQL DB, and OpenAPI REST API

> **Status**: Verified & Operational  
> **Target Subsystem**: `backend/src/edrak/agents/internal_intelligence/` & `backend/src/edrak/connectors/`  
> **Mock Services**: `mock_services/nvidia/`  
> **Applies to**: EDRAK Evidence-First Multi-Agent Architecture

---

## 1. Executive Summary & Architecture Flow

In enterprise settings, relying solely on **Unstructured Vector RAG** (vector search over markdown docs and PDFs) leaves two critical operational blind spots:
1. **Aggregations & Exact Figures**: Vector embeddings cannot compute mathematical sums or exact numbers (e.g., total ARR across tiers, exact order values, average discounts).
2. **Real-Time Operational State**: Operational databases and REST microservices change by the second, whereas vector stores require ingestion pipelines and experience indexing latency.

To solve this, the **Internal Intelligence Worker** operates as a **Tri-Modal Grounding Agent**:

```
                       ┌────────────────────────────────────────────────────────┐
                       │      Internal Intelligence Worker (LangGraph)          │
                       └──────────────────────────┬─────────────────────────────┘
                                                  │
                 ┌────────────────────────────────┼────────────────────────────────┐
                 ▼                                ▼                                ▼
       ┌───────────────────┐            ┌───────────────────┐            ┌───────────────────┐
       │  1. Vector RAG    │            │ 2. Relational DB  │            │  3. REST API      │
       │  (Unstructured)   │            │   (Text-to-SQL)   │            │  (OpenAPI 3.x)    │
       └─────────┬─────────┘            └─────────┬─────────┘            └─────────┬─────────┘
                 │                                │                                │
         ChromaDB Vector Store          Two-Tier Introspector           Dynamic APIDiscoverer
         NVIDIA Playbooks & RFCs        sqlglot AST Guardrails          Secure API Executor
                 │                                │                                │
                 └────────────────────────────────┼────────────────────────────────┘
                                                  ▼
                                     ┌────────────────────────┐
                                     │ Universal Evidence     │
                                     │ Contract (Typed Fact,  │
                                     │ Excerpt, Metadata)     │
                                     └────────────┬───────────┘
                                                  ▼
                                    Verification Gate & Synthesis
```

Because all three channels serialize their outputs into EDRAK's universal **`Evidence` contract**, downstream stages (**Verification Gate**, **Metrics Agent**, **CrossSignal Synthesis**, and **Executive Briefing**) consume tabular SQL rows and API payloads seamlessly with **zero downstream code changes**.

---

## 2. Directory & Module Structure

```text
EDRAK/
├── backend/
│   └── src/
│       └── edrak/
│           ├── agents/
│           │   └── internal_intelligence/
│           │       └── nodes.py               # Concurrent Tri-Modal evidence retrieval & LLM synthesis
│           ├── connectors/
│           │   ├── __init__.py                # Package exports
│           │   ├── sql_guardrail.py           # AST query parser & safety guardrail (sqlglot)
│           │   ├── db_introspector.py         # Two-Tier schema introspection (SQLAlchemy)
│           │   ├── api_discoverer.py          # Dynamic OpenAPI 3.x parser & endpoint matcher
│           │   ├── api_executor.py            # Secure HTTP executor with in-memory TestClient & httpx
│           │   └── tri_modal_retriever.py     # High-level pipeline coordinating RAG + DB + REST API
│           └── contracts/
│               ├── request.py                 # DatabaseConnectionConfig & RESTAPIConfig on CompanyProfile
│               └── evidence.py                # Universal Evidence contract (SourceType, Fact, Excerpt)
├── mock_services/
│   └── nvidia/
│       ├── seed_nvidia_db.py                  # Database seeder for SQLite
│       ├── nvidia_enterprise.db               # Realistic NVIDIA relational database
│       ├── mock_nvidia_api.py                 # FastAPI microservice with live cluster & pricing endpoints
│       └── nvidia_openapi_spec.json           # Exported OpenAPI 3.1 specification
├── scripts/
│   └── run_nvidia_tri_modal_demo.py           # Interactive end-to-end CLI demonstration script
└── tests/
    └── test_tri_modal_internal.py             # Automated pytest suite covering all 3 modalities
```

---

## 3. All Commands to Run

### Command 1: Install New Dependencies
Ensure `sqlalchemy` and `sqlglot` are installed:
```powershell
.venv\Scripts\pip install -r requirements.txt
```
*(Added to `requirements.txt`: `sqlalchemy` and `sqlglot`)*

---

### Command 2: Seed the Mock NVIDIA Relational Database
Seeds `mock_services/nvidia/nvidia_enterprise.db` with realistic synthetic data:
```powershell
.venv\Scripts\python mock_services/nvidia/seed_nvidia_db.py
```
**Database Schema Created:**
* `gpu_products`: B200 ($32,500 ASP, 73.5% margin), GB200 NVL72 rack ($2.9M ASP, 75% margin), H200, H100, Spectrum-X switch.
* `enterprise_deals`: 7 large customer contracts (Microsoft Azure $297.5M, AWS $414.4M, CoreWeave $173.5M, Meta Platforms $690.6M, Mistral AI $73.3M, Saudi SDAIA $102.1M, Lambda Labs $61.4M).
* `software_arr`: NVIDIA AI Enterprise ($1.44B ARR, 320k active licenses), NIM microservices ($832.2M ARR, 95k licenses), DGX Cloud ($821.4M ARR, 1,850 nodes).

---

### Command 3: Run the Automated Pytest Suite
Runs 5 unit and integration tests verifying guardrails, introspection, API discovery, and evidence mapping:
```powershell
.venv\Scripts\pytest tests/test_tri_modal_internal.py -v
```
**Expected Output:**
```text
tests/test_tri_modal_internal.py::test_sql_guardrail_safety PASSED                 [ 20%]
tests/test_tri_modal_internal.py::test_database_introspector PASSED                [ 40%]
tests/test_tri_modal_internal.py::test_openapi_discoverer PASSED                   [ 60%]
tests/test_tri_modal_internal.py::test_secure_api_executor_with_mock_app PASSED   [ 80%]
tests/test_tri_modal_internal.py::test_sql_and_api_evidence_mapping PASSED        [100%]

============================== 5 passed in 5.60s ==============================
```

---

---

### Command 4: Parameterized CLI Runner (`scripts/run_tri_modal_agent.py`)

You can pass any custom database URI, OpenAPI spec, company name, or research goal directly from the command line using `scripts/run_tri_modal_agent.py`.

#### A. Supported CLI Parameters Table

| Parameter | Alias | Description | Example |
| :--- | :--- | :--- | :--- |
| `--db-uri` | `--db-url` | Database connection URI (PostgreSQL, MySQL, SQLite, etc.) | `postgresql://user:pass@host:5432/dbname` |
| `--allowed-tables` | | Comma-separated list of tables to restrict introspection to | `deals,contracts,subscriptions` |
| `--db-id` | | Symbolic identifier for the database | `acme_db` |
| `--api-base-url` | | Base URL of the operational REST microservice | `https://api.acme.com` |
| `--openapi-spec` | | URL or local file path to OpenAPI 3.x / Swagger JSON spec | `https://api.acme.com/openapi.json` |
| `--api-token` | | Bearer token (automatically added as `Authorization: Bearer <token>`) | `secret_token_123` |
| `--auth-header` | | Custom header in `Header-Name: Value` format | `X-API-Key: custom_key` |
| `--company` | | Company name under analysis | `Acme Corp` (Default: `NVIDIA`) |
| `--workspace-id` | | Workspace ID for internal vector document search | `default` |
| `--goal` | | Targeted research goal for the agent | `"Assess customer churn and ARR"` |
| `--focus` | | Specific focus areas for the task | `"Compare contract sizes and discounts"` |
| `--preflight-only` | | Fast check: tests DB and API connection without running LLM synthesis | *(Flag, no value)* |

---

#### B. Database Drivers Reference

| Target Database | Required Driver | Installation Command | Connection URI Format |
| :--- | :--- | :--- | :--- |
| **PostgreSQL** | `psycopg2-binary` | `pip install psycopg2-binary` | `postgresql://user:pass@host:5432/dbname` |
| **MySQL / MariaDB** | `pymysql` | `pip install pymysql` | `mysql+pymysql://user:pass@host:3306/dbname` |
| **Snowflake** | `snowflake-sqlalchemy` | `pip install snowflake-sqlalchemy` | `snowflake://user:pass@account/dbname/schema` |
| **SQLite** | Built-in | *(None needed)* | `sqlite:///path/to/database.db` |

---

#### C. Concrete Copy-Pasteable Command Examples

##### Example 1: Fast Pre-Flight Check (Tests DB & API in < 2 seconds without LLM)
Verifies database connection, dialect detection, table columns, and OpenAPI endpoint discovery without running synthesis:
```powershell
.venv\Scripts\python scripts/run_tri_modal_agent.py `
    --db-uri "postgresql://readonly_user:password@localhost:5432/analytics" `
    --api-base-url "https://api.acme.com" `
    --openapi-spec "https://api.acme.com/openapi.json" `
    --preflight-only
```

##### Example 2: Full Agent Run with PostgreSQL & Live REST API
Runs the LangGraph internal agent with planning, concurrent tri-modal retrieval, AST guardrails, and executive synthesis:
```powershell
.venv\Scripts\python scripts/run_tri_modal_agent.py `
    --company "Acme Corp" `
    --db-uri "postgresql://readonly_user:password@localhost:5432/analytics" `
    --allowed-tables "contracts,subscriptions,billing" `
    --api-base-url "https://api.acme.com" `
    --openapi-spec "https://api.acme.com/openapi.json" `
    --api-token "acme_live_secret_token" `
    --goal "Assess active subscription ARR, customer contract sizes, and operational cluster health."
```

##### Example 3: MySQL / MariaDB Database Run
```powershell
.venv\Scripts\python scripts/run_tri_modal_agent.py `
    --company "CloudTech" `
    --db-uri "mysql+pymysql://agent_ro:Secret123@10.0.0.5:3306/sales_db" `
    --allowed-tables "enterprise_orders,licenses" `
    --api-base-url "https://api.cloudtech.com" `
    --openapi-spec "https://api.cloudtech.com/v2/swagger.json" `
    --goal "Analyze quarterly revenue aggregations and license renewals."
```

##### Example 4: Custom SQLite Database + Local OpenAPI Spec File
```powershell
.venv\Scripts\python scripts/run_tri_modal_agent.py `
    --company "Mistral" `
    --db-uri "sqlite:///mock_services/nvidia/nvidia_enterprise.db" `
    --allowed-tables "gpu_products,enterprise_deals" `
    --api-base-url "http://127.0.0.1:8088" `
    --openapi-spec "mock_services/nvidia/nvidia_openapi_spec.json" `
    --goal "Assess GPU hardware pricing ASP and enterprise deal volumes."
```

##### Example 5: Default Run (Zero Arguments - uses seeded NVIDIA mock DB & API)
```powershell
.venv\Scripts\python scripts/run_tri_modal_agent.py
```

##### Example 6: View Help & All Options
```powershell
.venv\Scripts\python scripts/run_tri_modal_agent.py --help
```

---

### Command 5 (Optional): Start the Live Mock REST API Server
If you want to run the NVIDIA mock REST API as an external standalone HTTP server:
```powershell
.venv\Scripts\python -m uvicorn mock_services.nvidia.mock_nvidia_api:app --host 127.0.0.1 --port 8088
```
* **Interactive OpenAPI Swagger Docs**: Navigate to `http://127.0.0.1:8088/docs` in your browser.
* **Raw OpenAPI Spec**: `http://127.0.0.1:8088/openapi.json`.
* **Telemetry Endpoint**: `http://127.0.0.1:8088/v1/telemetry/gpu-clusters`.
* **Discount Matrix Endpoint**: `http://127.0.0.1:8088/v1/pricing/discount-matrix`.

---

## 4. Production & Frontend Architecture Flow

In production, analysts configure database and API connections through the EDRAK Web UI.

### Step 1: Frontend User Configuration
In the frontend ([frontend/src/pages/CompanyProfile.tsx](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/frontend/src/pages/CompanyProfile.tsx) or [Sources.tsx](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/frontend/src/pages/Sources.tsx)), the user configures their company's data sources:

```tsx
// Connection configuration fields
const dbConfig = {
  db_id: "customer_analytics_db",
  connection_uri: "postgresql://readonly_user:vault_token@db.customer.com:5432/corp_data",
  allowed_tables: ["enterprise_deals", "gpu_products", "software_arr"]
};

const apiConfig = {
  api_id: "customer_ops_api",
  base_url: "https://ops.customer.com",
  openapi_spec_url: "https://ops.customer.com/openapi.json",
  auth_headers: { "Authorization": "Bearer <TOKEN_FROM_VAULT>" }
};
```

### Step 2: Request Payload to Backend
When the user triggers a new analysis, the frontend sends `POST /api/analyses` with `db_config` and `api_config`:

```json
{
  "goal": "Assess our Blackwell enterprise deal performance and fleet utilization.",
  "use_case": "competitive_intelligence",
  "competitors": ["AMD", "Intel"],
  "focus": ["pricing", "enterprise deals", "cluster telemetry", "software ARR"],
  "db_config": {
    "db_id": "nvidia_enterprise_db",
    "connection_uri": "sqlite:///mock_services/nvidia/nvidia_enterprise.db",
    "allowed_tables": ["enterprise_deals", "gpu_products", "software_arr"]
  },
  "api_config": {
    "api_id": "nvidia_ops_api",
    "base_url": "http://127.0.0.1:8088",
    "openapi_spec_url": "http://127.0.0.1:8088/openapi.json"
  }
}
```

### Step 3: Backend Dispatch & Execution
1. [analysis_service.py](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/backend/src/edrak/api/analysis_service.py) attaches `db_config` and `api_config` to `CompanyProfile`.
2. The orchestrator dispatches `ResearchTask` to the `Internal Intelligence Worker`.
3. [retrieve_evidence_node](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/backend/src/edrak/agents/internal_intelligence/nodes.py) runs the three pipelines concurrently:
   * **Vector RAG**: Finds strategic playbooks in ChromaDB.
   * **Direct Database**: Introspects table schema, validates SQL with `sqlglot`, and queries records.
   * **REST API**: Introspects OpenAPI spec, matches routes, and executes GET operations.
4. All rows and responses are serialized into `Evidence` objects.
5. [analyze_and_synthesize_node](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/backend/src/edrak/agents/internal_intelligence/nodes.py) synthesizes grounded findings with evidence references.

### Step 4: UI Evidence Card Badges
In the final Briefing and Sources view ([frontend/src/pages/Report.tsx](file:///d:/Courses/NTI%20-%20Agentic%20AI/Technical/Final%20project/Final/EDRAK/frontend/src/pages/Report.tsx)), each evidence item is rendered with its originating channel:
* `[Internal Document (RAG)]`: Displays document title, excerpt, and relevance score.
* `[SQL Database]`: Displays table name (`enterprise_deals`), row count, and the exact SQL query executed.
* `[REST API]`: Displays endpoint path (`/v1/telemetry/gpu-clusters`) and structured JSON payload.

---

## 5. Security Guardrails Summary

| Layer | Threat Prevented | Enforcement |
| :--- | :--- | :--- |
| **Read-Only DB Role** | Accidental data deletion or modification (`DROP`, `DELETE`). | Read-only connection user or SQLite read-only mode. |
| **AST Query Guardrail** | SQL injection and non-SELECT queries. | `sqlglot` parses AST; rejects any mutating expression. |
| **Hard Query Bounds** | Long-running Cartesian joins and DoS. | Automatic injection of `LIMIT 100` and statement timeout. |
| **OpenAPI Safe Methods** | Accidental state modification via REST API. | Filters OpenAPI routes strictly for `GET`/`HEAD` read operations. |
| **Secret Vault Isolation** | Password/token leakage into prompt context. | URIs and tokens are held in server configuration; model receives symbolic IDs. |
