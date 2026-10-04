from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .base import ContractModel, NonBlankStr
from .task import WorkerType


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    RETRY_REQUIRED = "retry_required"
    REPLAN_REQUIRED = "replan_required"
    CANNOT_COMPLETE = "cannot_complete"


class TargetedAction(ContractModel):
    worker: WorkerType = Field(description="Which worker to act on.")
    reason: NonBlankStr = Field(description="What action to take in plain language.")


class VerificationDecision(ContractModel):
    status: VerificationStatus = Field(description="Verification outcome.")
    targeted_actions: list[TargetedAction] = Field(
        default_factory=list,
        description="Concrete actions to take if action is required.",
    )
    summary: NonBlankStr = Field(description="One-line explanation of the decision.")

    @model_validator(mode="after")
    def _actions_match_status(self) -> VerificationDecision:
        status = self.status
        action_count = len(self.targeted_actions)

        if status in (VerificationStatus.RETRY_REQUIRED, VerificationStatus.REPLAN_REQUIRED):
            if action_count < 1:
                raise ValueError(f"{status.value} requires at least one targeted_action")
            return self

        if action_count > 0:
            raise ValueError(
                f"{status.value} requires no targeted_actions, but got {action_count}"
            )
        return self
