# Internal Intelligence Worker Agent (`edrak.agents.internal_intelligence`)

The **Internal Intelligence Worker** is a stateful LangGraph agent that investigates the pilot company's (**GitLab**) internal posture, capabilities, telemetry, OKRs, architecture, and pricing strategy.

---

## 1. Package Structure

```text
backend/src/edrak/agents/internal_intelligence/
├── __init__.py       # Package exports (run_internal_intelligence, graph)
├── graph.py          # StateGraph definition and entrypoint runner
├── nodes.py          # Pure dynamic workflow node functions
├── prompts.py        # System prompt templates
├── state.py          # Typed InternalAgentState definition
└── README.md         # Worker-specific guide
```

---

## 2. Dynamic Workflow Lifecycle

The worker executes a 4-stage linear LangGraph state machine:

1. **`plan_queries`**:
   - Dynamically parses `task.goal`, `task.focus` clauses, `task.company_profile.name`, and `task.business_context` (`targets`, `focus_areas`).
   - Produces targeted semantic search queries across product features, packaging, privacy architecture, telemetry, and competitive battlecards.

2. **`retrieve_evidence`**:
   - Queries the internal RAG knowledge base via `InternalDataToolClient`.
   - Searches across both authentic handbook chunks (`data/handbook/chunked/`) and private synthetic internal markdown files (`data/internal/`).
   - Deduplicates evidence and records query hit counts.

3. **`analyze_and_synthesize`**:
   - Synthesizes factual findings grounded in retrieved evidence.
   - Categorizes each finding dynamically into `PRODUCT_FEATURE`, `PRICING_PACKAGING`, `POSITIONING`, `MARKET_SIGNAL`, or `OTHER`.
   - Capping rule: Synthetic data is capped at $\le 0.60$ confidence and annotated with limitations.
   - Dynamic Gaps: Detects if any requested focus topic from the task was not covered by retrieved evidence.

4. **`format_worker_result`**:
   - Evaluates focus area coverage to derive `WorkerStatus` (`COMPLETED`, `PARTIAL`, `NO_EVIDENCE`).
   - Populates accurate UTC `started_at` and `completed_at` ISO timestamps.
   - Returns a valid, strongly-typed `WorkerResult` contract adhering to `extra="forbid"`.

---

## 3. Usage Examples

### Programmatic Python Invocation
```python
from edrak.contracts.request import BusinessContext, CompanyProfile, UseCase
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.core.profiles import get_gitlab_profile
from edrak.agents.internal_intelligence.graph import run_internal_intelligence

task = ResearchTask(
    parent_request_id="req_001",
    worker=WorkerType.INTERNAL_INTELLIGENCE,
    goal="Assess GitLab Duo product architecture and packaging vs GitHub Copilot",
    focus="GitLab Duo Agent Platform, AI Gateway, zero retention data privacy, GitLab Credits, and self-hosted readiness",
    company_profile=get_gitlab_profile(),
    business_context=BusinessContext(
        use_case=UseCase.COMPETITIVE_INTELLIGENCE,
        targets=["GitHub Copilot"],
        focus_areas=["AI capabilities", "pricing & packaging"],
    ),
)

worker_result = run_internal_intelligence(task)
print(f"Status: {worker_result.status.value}")
print(f"Findings: {len(worker_result.findings)}")
print(f"Confidence: {worker_result.confidence}")
```

### CLI Execution
```powershell
# Run and display formatted logs
python scripts/run_internal_agent.py

# Run and output pure JSON
python scripts/run_internal_agent.py --json-output

# Run and persist results to file
python scripts/run_internal_agent.py --save-to artifacts/reports/internal_worker_result.json
```
