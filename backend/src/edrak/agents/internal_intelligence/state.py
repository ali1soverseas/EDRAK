"""State definition for Internal Intelligence Worker."""

import operator
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict

from edrak.contracts.evidence import Evidence
from edrak.contracts.result import Finding, WorkerResult
from edrak.contracts.task import ResearchTask


class InternalAgentState(TypedDict, total=False):
    """Private LangGraph state for the Internal Intelligence Worker.

    This state is strictly internal to this worker and is not exposed
    to the Orchestrator/Supervisor directly.
    """

    task: ResearchTask
    queries: List[str]
    retrieved_evidence: Annotated[List[Evidence], operator.add]
    findings: List[Finding]
    limitations_and_gaps: List[str]
    summary: str
    worker_result: Optional[WorkerResult]
    error: Optional[str]
