from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, StringConstraints


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    """Base for every shared contract.

    ``extra="forbid"`` is deliberate. Unknown fields are rejected rather than
    ignored so a worker that assumes a field the contract does not guarantee
    fails loudly at the boundary instead of silently producing an empty value.
    """

    model_config = ConfigDict(extra="forbid")