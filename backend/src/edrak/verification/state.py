from typing import Any, TypedDict

from edrak.contracts import FindingVerdict, VerificationInput


class VerificationState(TypedDict):
    payload: VerificationInput
    assessments: list[FindingVerdict]
    control: dict[str, Any]
    decision_status: str
    result: dict[str, Any]
