"""Evidence models for EDRAK intelligence findings."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import uuid


class Evidence(BaseModel):
    """Represents a piece of verified ground-truth evidence gathered by a worker."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_uri: str = Field(
        ...,
        description="URI, filepath, or URL where this evidence originated.",
    )
    source_type: str = Field(
        ...,
        description="Type of source: 'internal_handbook', 'internal_doc', 'internal_rag', 'web_page', 'financial_report', etc.",
    )
    title: str = Field(
        default="",
        description="Title or heading of the document/page.",
    )
    content: str = Field(
        ...,
        description="Direct excerpt or relevant content supporting a finding.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this evidence was retrieved or created.",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score in the reliability/accuracy of the evidence source.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured metadata (e.g. author, department, section, tags).",
    )
