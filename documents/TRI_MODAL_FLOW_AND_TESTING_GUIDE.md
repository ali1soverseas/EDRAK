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
│   ├── run_nvidia_tri_modal_demo.py           # Interactive end-to-end CLI demonstration script
│   └── run_tri_modal_agent.py                 # Parameterized CLI runner with preflight checks
└── tests/
    ├── test_tri_modal_internal.py             # Automated pytest suite covering all 3 modalities (5 tests)
    └── test_guardrail_penetration_and_validation.py # Full penetration & meaningfulness validation suite (93 tests)
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
Seeds `mock_services/nvidia/nvidia_enterprise.db` with realistic synthetic data across 7 relational tables:
```powershell
.venv\Scripts\python mock_services/nvidia/seed_nvidia_db.py
```
**Database Schema Created (7 Tables):**
1. `gpu_products`: 7 models (GB200 NVL72 rack $2.9M ASP, B200 $32.5k ASP, H200 $27k ASP, H100 $19.5k ASP, Spectrum-X switch $250k, GB300 preview $45k, DGX H200 system $300k) with TDP wattage and HBM capacity.
2. `enterprise_deals`: 14 customer contracts across Hyperscalers, Sovereign AI, and Tier 2 Clouds (Microsoft Azure $297.5M, AWS $414.4M, CoreWeave $173.5M, Meta Platforms $690.6M, Google Cloud $552.5M, Tencent Cloud $190.1M, Mistral AI $73.3M, Saudi SDAIA $102.1M, UAE G42 $121.5M, Softbank $77.1M, Lambda Labs $61.4M).
3. `software_arr`: 5 recurring offerings ($3.5B+ total ARR): NVIDIA AI Enterprise ($1.44B ARR, 320k active licenses, +42% YoY), NIM microservices ($832.2M ARR, 95k licenses, +68% YoY), DGX Cloud ($821.4M ARR, 1,850 nodes, +55% YoY), Omniverse Enterprise ($66M ARR), Base Command Platform ($384M ARR).
4. `supply_chain_constraints`: Critical component allocations, lead times in weeks, and packaging constraints (TSMC CoWoS-L 22-week lead time / 88.5% allocation, Vertiv liquid cooling manifolds 18-week lead time, SK Hynix HBM3e stacking 10-week lead time).
5. `competitor_benchmarks`: Verified MLPerf v4.0 / v4.1 LLM training & inference performance benchmarks (H100/H200/B200 vs AMD MI300X/MI325X and Google TPUv5e with measured percentage advantages up to +92.2%).
6. `sales_pipeline_forecast`: Weighted forward opportunity pipeline (Apple Private AI $325M, Alibaba Cloud $292.3M, Saudi Aramco $71.6M, JP Morgan $33.1M, ByteDance $260M) with stage probabilities and assigned Account Executives.
7. `export_control_flags`: US BIS / OFAC regulatory compliance matrices (Restricted for China BIS EAR 742.6, Prohibited for Russia/Iran/North Korea, Conditional for UAE G42 and Saudi SDAIA, General Permitted for India).

---

### Command 3: Run the Automated Testing Suites

#### A. Tri-Modal Baseline Suite (5 Tests)
Runs baseline unit and integration tests verifying introspection, API discovery, and evidence serialization:
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

#### B. Guardrail Penetration & Query Meaningfulness Validation Suite (93 Tests)
Runs comprehensive penetration testing covering SQL injection attacks, query meaningfulness validation, API method enforcement, write-endpoint blocking, and data assertions:
```powershell
.venv\Scripts\pytest tests/test_guardrail_penetration_and_validation.py -v
```
**Test Coverage Breakdown (10 Categories):**
* **Section A (19 tests)**: SQL Guardrail Penetration Attacks (DDL/DML mutations, query stacking, SLEEP/time-based SQLi, xp_cmdshell, VERSION/USER disclosure, OR 1=1 tautologies, LIMIT bounds).
* **Section B (10 tests)**: SQL Meaningfulness Validation (Rejection of `SELECT 1`, `SELECT NULL`, `LIMIT 0`, always-false WHERE `1=0`, missing FROM clause).
* **Section C (6 tests)**: SQL Valid Business Queries (Complex aggregates, JOINs, subqueries).
* **Section D (9 tests)**: API Method Enforcement (Rejection of `POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS` with `PermissionError`).
* **Section E (7 tests)**: API Discoverer Read-Only Indexing (Zero write routes leaked into index; only `GET` indexed).
* **Section F (7 tests)**: API Endpoint Penetration (Path traversal `../../etc/passwd`, SQLi/script injection in path params, long params).
* **Section G (7 tests)**: DB Introspector Safety & Table ACLs (`allowed_tables` enforcement, limit bounds).
* **Section H (5 tests)**: End-to-End Evidence Contract Integrity (Serialization into typed `Evidence` objects).
* **Section I (8 tests)**: Rich Database Data Assertions (Querying all 7 tables, weighted pipeline calculations).
* **Section J (15 tests)**: Rich API Endpoint Assertions (Validating all GET endpoints, 404 handling, write endpoint rejection).

**Expected Output:**
```text
============================= 93 passed in 14.21s =============================
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
* **Read-Only Endpoints (Permitted for Agent)**:
  * `GET /v1/telemetry/gpu-clusters`: Live cluster telemetry, uptime, and utilization.
  * `GET /v1/pricing/discount-matrix`: Volume discount tiers, approval thresholds, and margin floor.
  * `GET /v1/supply-chain/lead-times`: Shipment lead times and CoWoS packaging allocation.
  * `GET /v1/products`: Full GPU hardware catalogue with target ASP and margins.
  * `GET /v1/products/{product_name}/margin`: Single product margin and negotiation headroom.
  * `GET /v1/competitive/benchmark-summary`: MLPerf training and inference comparisons vs AMD and TPU.
  * `GET /v1/finance/arr-summary`: Software ARR breakdown by product offering.
  * `GET /v1/export-control/{country}`: US BIS / OFAC regulatory compliance status.
* **Write Endpoints (Strictly Blocked by Agent Guardrail)**:
  * `POST /v1/deals/create`: Simulated deal creation. *(Agent execution raises `PermissionError`)*
  * `POST /v1/supply-chain/emergency-order`: Simulated emergency hardware order. *(Agent execution raises `PermissionError`)*
  * `DELETE /v1/pipeline/{opportunity_id}`: Pipeline opportunity deletion. *(Agent execution raises `PermissionError`)*
  * `PATCH /v1/pricing/override`: Margin floor override. *(Agent execution raises `PermissionError`)*

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

## 5. Security & Query Meaningfulness Guardrails

The Tri-Modal integration implements a multi-layered defense-in-depth model across both Relational SQL Databases and REST APIs:

```
                  Incoming Query / Agent Generated Action
                                    │
          ┌─────────────────────────┴─────────────────────────┐
          ▼                                                   ▼
┌─────────────────────────────────┐                 ┌─────────────────────────────────┐
│     SQL Guardrail Pipeline      │                 │    REST API Security Pipeline   │
├─────────────────────────────────┤                 ├─────────────────────────────────┤
│ 1. AST Security Analysis        │                 │ 1. Dynamic OpenAPI Indexing     │
│    • Rejects Non-SELECT DDL/DML │                 │    • Indexes ONLY GET operations│
│    • Blocks query chaining (;)  │                 │    • Omits POST/PUT/DELETE/PATCH│
│    • Detects blind SQLi (SLEEP) │                 │                                 │
│    • Blocks OS commands/files   │                 │ 2. Method Enforcement           │
│    • Catches tautologies (OR 1=1│                 │    • Pre-flight check rejects   │
│                                 │                 │      any non-GET with           │
│ 2. Query Meaningfulness Layer   │                 │      PermissionError            │
│    • Rejects LIMIT 0            │                 │                                 │
│    • Rejects bare constants     │                 │ 3. Parameter Sanitization       │
│      (SELECT 1, SELECT NULL)    │                 │    • Path traversal blocked     │
│    • Rejects always-false WHERE │                 │    • URL encoding validated     │
│      (1=0, NULL=NULL, 'a'='b')  │                 │    • Script/SQL injection safe  │
│                                 │                 │                                 │
│ 3. Row Limit Enforcer           │                 │ 4. Secret Isolation             │
│    • Caps LIMIT <= 100          │                 │    • Tokens stored in backend   │
│    • Injects LIMIT if absent    │                 │      vault / config only        │
└────────────────┬────────────────┘                 └────────────────┬────────────────┘
                 │                                                   │
                 └─────────────────────────┬─────────────────────────┘
                                           ▼
                                Safe Evidence Produced
```

### 5.1 Guardrail Matrix

| Layer | Threat Prevented | Enforcement Mechanism | Failure Action |
| :--- | :--- | :--- | :--- |
| **Read-Only DB Role** | Accidental data deletion or schema modification. | Database-level read-only user / SQLite read-only connection URI. | DB rejects query. |
| **AST Security Check** | SQL mutation (`DROP`, `DELETE`, `INSERT`, `UPDATE`, `ALTER`, `TRUNCATE`, `MERGE`, `CREATE`). | `sqlglot` walks syntax tree; verifies root is `exp.Select` with zero forbidden AST nodes. | Raises `SQLGuardrailViolation` (subclass of `PermissionError`). |
| **Query Chaining Prevention** | Multi-statement injection via `;` stacking (`SELECT 1; DROP TABLE`). | `sqlglot.parse()` ensures `len(statements) == 1`. | Raises `SQLGuardrailViolation`. |
| **Injection Pattern Scanner** | Tautology authentication bypass (`OR 1=1`, `OR '1'='1'`) and dangerous functions (`SLEEP`, `PG_SLEEP`, `XP_CMDSHELL`, `LOAD_FILE`, `VERSION()`). | Pre-execution regex & token scanning on comment-stripped query. | Raises `SQLGuardrailViolation`. |
| **Query Meaningfulness Layer** | Unproductive queries that consume agent resources without returning business facts (`SELECT 1`, `SELECT NULL`, `LIMIT 0`, `WHERE 1=0`, `WHERE 'a'='b'`). | Checks AST for missing `FROM` clauses, trivial projection columns, and provably-false WHERE conditions. | Raises `SQLMeaninglessQuery` (subclass of `ValueError`). |
| **Hard Row Bounds** | Unbounded Cartesian product queries causing memory DoS. | Injects `LIMIT 100` if missing; caps any higher limit to default. | Rewrites AST before execution. |
| **OpenAPI Method Whitelist** | Calling state-modifying endpoints (`POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS`). | `DynamicAPIDiscoverer` indexes only `GET`; `SecureAPIExecutor` validates method before dispatch. | Raises `PermissionError` before HTTP request is sent. |
| **Path Traversal Guard** | Directory traversal probes in REST API parameters (`../../etc/passwd`). | Normalized URL path building and ASGI routing validation. | Returns HTTP 404/422 without accessing filesystem. |
| **Secret Vault Isolation** | Database passwords or API bearer tokens leaking into LLM prompt context. | Connection URIs and tokens reside exclusively in backend configuration; LLM receives only symbolic identifiers. | Secrets never enter prompt context. |

---

### 5.2 Query Meaningfulness Verification Helper

In test suites and custom retrieval pipelines, developers can test queries non-destructively using `assess_query_usefulness`:

```python
from edrak.connectors.sql_guardrail import assess_query_usefulness

# Useful business query -> (True, "Query passed all guardrail and meaningfulness checks.")
useful, msg = assess_query_usefulness("SELECT product_name, target_asp_usd FROM gpu_products")

# Meaningless query -> (False, "Meaningless query: LIMIT 0 detected: query will always return 0 rows...")
useful, msg = assess_query_usefulness("SELECT * FROM gpu_products LIMIT 0")

# Security violation -> (False, "Security violation: Prohibited SQL expression 'Drop' detected.")
useful, msg = assess_query_usefulness("DROP TABLE gpu_products")
```
