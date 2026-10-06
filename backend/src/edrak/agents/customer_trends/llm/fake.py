"""Deterministic scripted chat model for tests and the keyless demo mode."""

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

Scripted = str | dict[str, Any] | BaseModel | AIMessage


class ScriptExhaustedError(RuntimeError):
    """The model was called more times than it has scripted responses."""


def tool_call_message(name: str, args: dict[str, Any], *, call_id: str = "call_0") -> AIMessage:
    """A scripted assistant turn that requests one tool call."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _payload(response: Scripted) -> Any:
    if isinstance(response, BaseModel):
        return response.model_dump(mode="json")
    if isinstance(response, str):
        return json.loads(response)
    if isinstance(response, AIMessage):
        return json.loads(str(response.content))
    return response


def _as_message(response: Scripted) -> AIMessage:
    if isinstance(response, AIMessage):
        return response
    if isinstance(response, str):
        return AIMessage(content=response)
    return AIMessage(content=json.dumps(_payload(response), ensure_ascii=False))


class ScriptedChatModel(BaseChatModel):
    """Plays back `responses` in order and records every call it receives.

    Strings and AIMessages are returned as is. Dicts and pydantic instances are JSON
    payloads: returned as JSON text from a plain call and as a validated schema instance
    from `with_structured_output`. Use `tool_call_message` to script a tool call.
    """

    responses: list[Scripted]
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted-chat"

    @property
    def remaining(self) -> int:
        return len(self.responses) - self._cursor

    def _next(self, messages: Sequence[BaseMessage]) -> Scripted:
        self.calls.append(list(messages))
        if self._cursor >= len(self.responses):
            raise ScriptExhaustedError(
                f"script exhausted: {len(self.responses)} response(s) scripted, "
                f"call {len(self.calls)} has none"
            )
        response = self.responses[self._cursor]
        self._cursor += 1
        return response

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = _as_message(self._next(messages))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.bound_tool_names.append([_tool_name(tool) for tool in tools])
        return self

    def with_structured_output(
        self,
        schema: Any,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, Any]:
        def run(value: LanguageModelInput) -> Any:
            messages = self._convert_input(value).to_messages()
            response = self._next(messages)
            raw = _as_message(response)
            try:
                payload = _payload(response)
                parsed = schema.model_validate(payload)
            except (ValidationError, ValueError) as exc:
                if include_raw:
                    return {"raw": raw, "parsed": None, "parsing_error": exc}
                raise OutputParserException(f"scripted output invalid: {exc}") from exc
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(run)


class FunctionChatModel(ScriptedChatModel):
    """A scripted model whose answer is computed from the messages it receives.

    `responder` gets the messages and returns what `ScriptedChatModel` accepts as a response.
    """

    responder: Callable[[list[BaseMessage]], Scripted]

    def _next(self, messages: Sequence[BaseMessage]) -> Scripted:
        self.calls.append(list(messages))
        return self.responder(list(messages))


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or tool.get("function", {}).get("name", ""))
    return str(getattr(tool, "name", getattr(tool, "__name__", "")))
