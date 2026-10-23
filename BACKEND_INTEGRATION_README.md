# EDRAK — Backend & Frontend Integration Guide

This guide describes the persistence layer, API endpoints, live streaming, multi-file RAG ingestion, and frontend client integration implemented for EDRAK.

---

## Architecture Overview

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Frontend (React / Vite)                         │
│   Analyses Dashboard  │  Company Setup  │  Live Run  │  Brief View     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / SSE
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend Layer                           │
│   /api/auth/*     /api/company/*     /api/analyses/*     /api/briefs/* │
└─────────┬─────────────────────────┬───────────────────────────┬────────┘
          │                         │                           │
          ▼                         ▼                           ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│   SQLite DB      │      │  Run Manager &   │      │ Document Parser  │
│   (aiosqlite)    │      │  SSE Streaming   │      │   & ChromaDB     │
│   data/edrak.db  │      │  LangGraph Exec  │      │  (PDF, MD, JSON) │
└──────────────────┘      └──────────────────┘      └──────────────────┘
```

---

## 1. What Was Built

### A. Database Layer (`edrak.db`)
Using async SQLite via `aiosqlite` with zero external heavy dependencies:
- **`workspaces`**: Multi-tenant workspace records.
- **`users` & `sessions`**: Session-based auth with demo credentials bootstrapped on startup.
- **`company_profiles`**: Workspace company profile metadata and fields.
- **`company_documents`**: Uploaded internal files with status (`indexed`, `indexing`, `failed`) and size metrics.
- **`analyses`**: Full lifecycle of analyses (`draft`, `awaiting_approval`, `running`, `completed`, `partial`, `failed`, `cancelled`).
- **`runs`**: Real-time run state, stages, timestamps, and serialized `OrchestrationResult`.
- **`briefs`**: Final synthesized executive briefs with citations, options, and lenses.

### B. REST API Endpoints (`edrak.api.routes`)

| Category | Endpoint | Method | Purpose |
|---|---|---|---|
| **Auth** | `/api/auth/session` | `POST` | Get active session / workspace |
| | `/api/auth/sign-in` | `POST` | Authenticate user |
| | `/api/auth/create-account` | `POST` | Register new user & workspace |
| | `/api/auth/sign-out` | `POST` | Invalidate session |
| **Company** | `/api/company` | `GET` | Retrieve active company profile |
| | `/api/company` | `PUT` | Save company profile & sync to disk |
| | `/api/company/documents` | `POST` | Upload and auto-index files (PDF, MD, JSON, etc.) |
| | `/api/company/documents` | `GET` | List company documents |
| | `/api/company/documents/{id}` | `DELETE` | Delete file from DB, disk, and vector store |
| **Analyses** | `/api/analyses` | `GET` | List analyses summary |
| | `/api/analyses/{id}` | `GET` | Get analysis detail, request, and plan |
| | `/api/analyses/draft` | `POST` | Draft research plan with LLM planner |
| | `/api/analyses/{id}/approve` | `POST` | Approve plan and start run |
| | `/api/analyses/{id}/reject` | `POST` | Reject plan with feedback |
| | `/api/analyses/{id}/rerun` | `POST` | Clone request and draft fresh plan |
| **Live Run** | `/api/analyses/{id}/run` | `GET` | Snapshot of run state & tasks |
| | `/api/analyses/{id}/run/stream`| `GET` | **SSE stream** for real-time progress |
| | `/api/analyses/{id}/cancel` | `POST` | Cancel active orchestration run |
| **Brief** | `/api/briefs/{id}` | `GET` | Retrieve final synthesized Brief |

### C. Company Profile & Document Ingestion
- **JSON Profile Auto-Detection**: When a company profile `.json` (such as `gitlab_profile.json`) is uploaded, the full structured JSON is saved to `data/profiles/active_profile.json` (preserving all deep fields: financials, TAM, differentiators, strategic priorities). Form fields in SQLite are updated automatically so the UI form populates immediately.
- **PDF & Multi-File RAG Ingestion**:
  - Accepts multiple files simultaneously (`.pdf`, `.md`, `.txt`, `.docx`, `.json`).
  - Text from PDFs is extracted page-by-page via `pypdf`.
  - Extracted text is automatically chunked and upserted into **ChromaDB** (`edrak_internal_knowledge`) so the **Internal Intelligence worker** can cite it.
  - Complete removal via `DELETE /api/company/documents/{id}` purges the document from disk and removes its vectors from ChromaDB.

### D. Frontend Client Integration (`frontend/src/services`)
- **`httpApi.ts`**: Replaces mock with full `EdrakApi` implementation consuming the FastAPI backend.
- **`sse.ts`**: Real-time `EventSource` listener subscribing to stage changes, task progression, and verification events.
- **`Company.tsx`**: Updated with `.json` acceptance, live document removal (`x` button), auto-refresh on profile import, and a collapsible/minimizable files section.
- **`vite.config.ts`**: Configured `/api` proxy forwarding to `http://127.0.0.1:8000`.

---

## 2. How to Run & Work With the Full System

### Prerequisites
- Python 3.11 with `.venv` activated
- Node.js 18+ and `npm`

### Step 1: Start the Backend Server
From the project root:
```powershell
$env:PYTHONPATH="backend/src"
.venv\Scripts\python.exe -m uvicorn edrak.main:app --host 127.0.0.1 --port 8000 --reload
```
*The backend automatically initializes the database tables at `data/edrak.db` on startup.*

- API Base: `http://127.0.0.1:8000`
- Swagger Interactive Docs: `http://127.0.0.1:8000/docs`

### Step 2: Start the Frontend Server
In a separate terminal, navigate to `frontend/`:
```bash
npm run dev
```
- Web Application: `http://localhost:5173/`

---

## 3. End-to-End Walkthrough

1. **Open the Dashboard**: Navigate to `http://localhost:5173/`. You are logged in with the demo account (`Strategy Lead / Default Workspace`).
2. **Company Setup**: Click **Company** in the sidebar.
   - Edit the company details or drag & drop a profile JSON (e.g. `data/profiles/gitlab_profile.json`). Notice the form auto-populates.
   - Upload internal PDF or Markdown files in the files area. They are immediately indexed into ChromaDB.
   - Test minimizing the files card with the chevron toggle.
   - Test removing files with the `x` button.
3. **Draft a New Analysis**:
   - Click **New Analysis**, select **Competitive Intelligence**, enter a question (e.g. `Compare GitLab Duo vs GitHub Copilot on enterprise AI`), select competitors, and click **Review plan**.
   - The backend drafts the contract `ResearchPlan` across workers (`competitor_intelligence`, `market_intelligence`, `customer_trends`, `internal_intelligence`).
4. **Approve & Live Run**:
   - Review the tasks and click **Approve plan**.
   - The app navigates to `/analyses/{id}/run`.
   - The backend launches the orchestration graph in the background and streams live events via **SSE** (`dispatch` → `workers` → `verify` → `synthesize` → `brief`).
5. **Review the Brief**:
   - Once completed, the final Brief is loaded with evidence chips (E-numbers), worker lenses, cross-signal hypotheses, and strategic options (A, B, C).

## 4. Evaluation Artifacts and Tracing

Each backend run writes evaluator inputs and stage history under
`artifacts/evaluation_runs/<analysis_id>/`. Internal retrievals are recorded as RAG inputs;
worker, verification, metrics, synthesis, and decision outputs are retained as their stages
complete. The run-bundle evaluator reads metrics-stage artifacts as pipeline context, but does
not emit a separate evaluation report for the metrics stage. Worker and tool spans are exported
to the run bundle's `inputs/telemetry.json`.

Evaluate a completed or paused run locally without calling a model:

```powershell
.venv\Scripts\python.exe Evaluation\evaluation_Agent\edrak_eval_agent_enhanced.py `
  --run-bundle artifacts\evaluation_runs\<analysis_id> --dry-run
```

For model-judged quality scores, configure `OPENAI_API_KEY` and install `deepeval`, then omit
`--dry-run`. The default judge set is faithfulness and relevancy; use `--extra-metrics` to also
run the slower specificity, insight, and actionability judges. Deterministic evidence metrics
and integrity checks still run in either mode. The judge model defaults to `gpt-4.1-mini`
(`--model` or `OPENAI_MODEL` to change it). Judge requests run sequentially by default
(`--workers 1`) to avoid rate-limit bursts; `--retries 2` bounds retries for a metric.
After repeated rate-limit responses, the evaluator opens a run-scoped circuit breaker and
skips further judge requests instead of spending the remaining run retrying. Successful
judge results are cached in `--cache`.

To compare the run against a golden dataset immediately after evaluation, add
`--compare-to <name>` using the folder name under `golden\ground_truth`. The comparison report
is written under `reports\compare` for the evaluation API and dashboard. `--dry-run` also makes
the comparison use word overlap without a model; use `--compare-no-llm` to force this fallback
during a model-judged evaluation. Keep the Evaluation frontend open at
`http://localhost:5500/?api=http://localhost:8000` (and signed in to the backend); it checks for
new comparison results every three seconds and opens the newest result automatically.
Execution-quality reports correlate telemetry spans to matching task IDs and attempts for
worker and retrieval artifacts. The API exposes the six execution dimensions per evaluated
agent; dimensions without their required trace evidence remain not assessable. Tool argument
values are fingerprinted locally for duplicate-call checks and are not sent to DeepEval unless
`--include-tool-payloads` is explicitly enabled.

**Evaluation dashboard.** The backend serves read-only results at `/api/evaluation/runs`,
`/api/evaluation/runs/{run_id}`, `/api/evaluation/comparisons` and `/api/evaluation/comparisons/{id}`
(sign-in required; reports are read from `reports/`, override with `EDRAK_EVAL_REPORTS_DIR`).
To view them, start the backend, sign in, then serve the dashboard:
`cd "Evaluation frontend"; python -m http.server 5500` and open `http://localhost:5500/?api=http://localhost:8000`.
Telemetry can also be sent to an OTLP collector by setting
`OTEL_EXPORTER_OTLP_ENDPOINT`; `EDRAK_TELEMETRY_EXPORT_PATH` selects the local span export file.

---

## 5. How to Run Automated Tests

### Backend Tests (Pytest)
```powershell
.venv\Scripts\pytest.exe tests/test_api_and_db.py
.venv\Scripts\pytest.exe tests/test_contracts.py
```

### Frontend Tests (Vitest & TypeScript Typecheck)
In `frontend/`:
```bash
npm test
npm run build
```
All unit tests, contract checks, and production bundles compile with zero errors.
