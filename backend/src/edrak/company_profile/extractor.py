"""One schema-constrained model call that turns packed source text into a ProfileExtraction."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from pydantic import ValidationError

from edrak.company_profile.models import ProfileExtraction
from edrak.company_profile.prompts import CONTEXT_INTRO, REPAIR_PROMPT, SYSTEM_PROMPT
from edrak.core.config import settings
from edrak.core.llm import get_chat_model, schema_instruction

logger = logging.getLogger(__name__)

# Takes chat messages, returns the model's reply text. Tests pass their own.
LLMCall = Callable[[list[dict[str, str]]], str]

_ERROR_CHARS = 1500
_REPLY_CHARS = 500


class ProfileImportError(Exception):
    """Base for failures while turning source text into a draft."""


class ProfileLLMError(ProfileImportError):
    """The model could not be reached or refused the call (no key, network, quota)."""


class ProfileExtractionError(ProfileImportError):
    """The model answered twice and neither reply validated against the profile schema."""

    def __init__(self, message: str, *, attempts: int, last_error: str, last_reply: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error
        self.last_reply = last_reply


def call_model(messages: list[dict[str, str]]) -> str:
    """Send `messages` to the shared model with the profile schema as the response format."""
    kwargs: dict[str, object] = {"format": ProfileExtraction.model_json_schema()}
    if settings.PROFILE_IMPORT_REASONING:
        kwargs["reasoning"] = settings.PROFILE_IMPORT_REASONING
    reply = get_chat_model(temperature=0).invoke(messages, **kwargs)
    return str(reply.content)


def _parse(reply: str) -> ProfileExtraction:
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return ProfileExtraction.model_validate_json(text)


def _ask(llm: LLMCall, messages: list[dict[str, str]]) -> str:
    try:
        return llm(messages)
    except ProfileImportError:
        raise
    except Exception as exc:  # the transport has many failure types; callers need one
        raise ProfileLLMError(f"The model call failed: {exc}") from exc


def extract_profile(context: str, llm: Optional[LLMCall] = None) -> ProfileExtraction:
    """Ask the model for the profile in `context`, validate it, and repair once if it does not validate.

    The repair turn shows the model its own reply and the validation error. If the second reply
    fails too, raises ProfileExtractionError.
    """
    call = llm or call_model
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": CONTEXT_INTRO + context},
        schema_instruction(ProfileExtraction),
    ]

    reply = _ask(call, messages)
    try:
        return _parse(reply)
    except ValidationError as first:
        error = str(first)[:_ERROR_CHARS]
        logger.warning("Profile extraction reply did not validate; retrying once: %s", error[:200])

    messages += [
        {"role": "assistant", "content": reply},
        {"role": "user", "content": REPAIR_PROMPT.format(error=error)},
    ]
    reply = _ask(call, messages)
    try:
        return _parse(reply)
    except ValidationError as second:
        raise ProfileExtractionError(
            "The model's reply did not match the profile schema after one repair attempt.",
            attempts=2,
            last_error=str(second)[:_ERROR_CHARS],
            last_reply=reply[:_REPLY_CHARS],
        ) from second
