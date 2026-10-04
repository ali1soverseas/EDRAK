from pydantic import BaseModel, Field


class Evidence(BaseModel):
    type: str
    source: str
    excerpt: str | None = None


class Finding(BaseModel):
    claim: str
    evidence: list[Evidence] = Field(default_factory=list)
    task: str | None = None
