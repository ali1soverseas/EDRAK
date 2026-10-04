"""Business request model representing user decision requirements."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class BusinessRequest(BaseModel):
    """The high-level user decision request fed into the Supervisor/Orchestrator."""

    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the business request.",
    )
    title: str = Field(
        ...,
        description="Short title or headline for the business request.",
    )
    user_goal: str = Field(
        ...,
        description="The primary strategic question, decision objective, or problem statement.",
    )
    target_company: str = Field(
        default="GitLab",
        description="Primary company under analysis (e.g., GitLab).",
    )
    focus_domain: str = Field(
        default="Competitive Intelligence & Monitoring",
        description="Business focus domain / MVP use case.",
    )
    constraints: List[str] = Field(
        default_factory=list,
        description="Explicit constraints, boundaries, or criteria.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional additional context or parameters.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the request was initiated.",
    )
