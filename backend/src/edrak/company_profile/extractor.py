"""One schema-constrained model call that turns packed source text into a ProfileExtraction."""

from __future__ import annotations

import logging
from typing import Callable, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from edrak.company_profile.models import ProfileExtraction
from edrak.company_profile.prompts import CONTEXT_INTRO, REPAIR_PROMPT, SYSTEM_PROMPT
from edrak.core.config import settings
from edrak.core.llm import get_chat_model, schema_instruction

logger = logging.getLogger(__name__)

# Takes chat messages, returns the model's reply text. Tests pass their own.
LLMCall = Callable[[list[dict[str, str]]], str]

SchemaT = TypeVar("SchemaT", bound=BaseModel)

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


def schema_caller(schema: type[BaseModel]) -> LLMCall:
    """A model call that sends `schema` as the response format."""

    def call(messages: list[dict[str, str]]) -> str:
        kwargs: dict[str, object] = {"format": schema.model_json_schema()}
        if settings.PROFILE_IMPORT_REASONING:
            kwargs["reasoning"] = settings.PROFILE_IMPORT_REASONING
        reply = get_chat_model(temperature=0).invoke(messages, **kwargs)
        return str(reply.content)

    return call


def call_model(messages: list[dict[str, str]]) -> str:
    """Send `messages` to the shared model with the profile schema as the response format."""
    return schema_caller(ProfileExtraction)(messages)


def _parse(reply: str, schema: type[SchemaT] = ProfileExtraction) -> SchemaT:  # type: ignore[assignment]
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return schema.model_validate_json(text)


def _ask(llm: LLMCall, messages: list[dict[str, str]]) -> str:
    try:
        return llm(messages)
    except ProfileImportError:
        raise
    except Exception as exc:  # the transport has many failure types; callers need one
        raise ProfileLLMError(f"The model call failed: {exc}") from exc


def ask_structured(schema: type[SchemaT], messages: list[dict[str, str]], call: LLMCall, *, what: str) -> SchemaT:
    """Send `messages`, validate the reply as `schema`, and repair once if it does not validate.

    The repair turn shows the model its own reply and the validation error. If the second reply
    fails too, raises ProfileExtractionError; `what` names the schema in its message.
    """
    messages = list(messages)
    reply = _ask(call, messages)
    try:
        return _parse(reply, schema)
    except ValidationError as first:
        error = str(first)[:_ERROR_CHARS]
        logger.warning("The %s reply did not validate; retrying once: %s", what, error[:200])

    messages += [
        {"role": "assistant", "content": reply},
        {"role": "user", "content": REPAIR_PROMPT.format(error=error)},
    ]
    reply = _ask(call, messages)
    try:
        return _parse(reply, schema)
    except ValidationError as second:
        raise ProfileExtractionError(
            f"The model's reply did not match the {what} schema after one repair attempt.",
            attempts=2,
            last_error=str(second)[:_ERROR_CHARS],
            last_reply=reply[:_REPLY_CHARS],
        ) from second


def extract_profile(context: str, llm: Optional[LLMCall] = None) -> ProfileExtraction:
    """Ask the model for the profile in `context`, validate it, and repair once if it does not validate.

    The repair turn shows the model its own reply and the validation error. If the second reply
    fails too, raises ProfileExtractionError.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": CONTEXT_INTRO + context},
        schema_instruction(ProfileExtraction),
    ]
    return ask_structured(ProfileExtraction, messages, llm or call_model, what="profile")
