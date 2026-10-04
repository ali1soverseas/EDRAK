"""Research task model dispatched from Orchestrator/Supervisor to domain workers."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class WorkerRole(str, Enum):
    """Supported specialized intelligence worker domains."""

    INTERNAL_INTELLIGENCE = "internal_intelligence"
    COMPETITOR_INTELLIGENCE = "competitor_intelligence"
    MARKET_INTELLIGENCE = "market_intelligence"
    CUSTOMER_TRENDS = "customer_trends"


class ResearchTask(BaseModel):
    """The formal contract representing a sub-objective assigned to a worker."""

    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the research task.",
    )
    request_id: str = Field(
        ...,
        description="Reference to the parent BusinessRequest ID.",
    )
    worker_role: WorkerRole = Field(
        ...,
        description="Target worker domain responsible for fulfilling this task.",
    )
    objective: str = Field(
        ...,
        description="Clear, actionable goal for the worker.",
    )
    scope: str = Field(
        ...,
        description="Boundaries, specific entities, products, or departments to investigate.",
    )
    key_questions: List[str] = Field(
        default_factory=list,
        description="Key targeted questions the worker must answer.",
    )
    constraints: List[str] = Field(
        default_factory=list,
        description="Specific constraints or instructions for research.",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Shared contextual parameters passed from the planner.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the task was dispatched.",
    )
