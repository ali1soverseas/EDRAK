from pydantic import BaseModel


class BusinessRequest(BaseModel):
    """Inbound research request accepted by the orchestrator."""

    request_id: str
    goal: str
    business_context: str = ""
    company: str | None = None
    use_case: str | None = None
