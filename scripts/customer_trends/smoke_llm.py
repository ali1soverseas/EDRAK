"""Check the configured LLM endpoint: completion, tool calling, structured output.

Run from backend/: uv run python ../scripts/customer_trends/smoke_llm.py
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from edrak.agents.customer_trends.llm.client import (
    LLMConfigError,
    get_chat_model,
    invoke_with_retry,
    structured_call,
)
from edrak.agents.customer_trends.settings import get_settings


class Capital(BaseModel):
    country: str
    capital: str


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def check_completion(llm: BaseChatModel) -> str:
    reply = await invoke_with_retry(llm.ainvoke, "Reply with the single word: ok")
    if not str(reply.content).strip():
        raise ValueError("empty completion")
    return f"{len(str(reply.content))} chars"


async def check_tool_call(llm: BaseChatModel) -> str:
    bound = llm.bind_tools([add])
    reply = await invoke_with_retry(bound.ainvoke, "What is 17 plus 25? Use the add tool.")
    if not isinstance(reply, AIMessage) or not reply.tool_calls:
        raise ValueError("no tool call emitted")
    call = reply.tool_calls[0]
    if call["name"] != "add":
        raise ValueError(f"unexpected tool {call['name']}")
    return f"add({call['args']})"


async def check_structured(llm: BaseChatModel) -> str:
    result = await structured_call(
        llm, Capital, [HumanMessage(content="What is the capital of France?")]
    )
    return f"{result.country} -> {result.capital}"


async def main() -> int:
    settings = get_settings()
    try:
        llm = get_chat_model("branch", settings=settings)
    except LLMConfigError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"model={llm.model} base_url={settings.ollama_base_url}")
    checks: list[tuple[str, Callable[[BaseChatModel], Awaitable[str]]]] = [
        ("completion", check_completion),
        ("tool calling", check_tool_call),
        ("structured output", check_structured),
    ]
    failed = 0
    for name, check in checks:
        try:
            detail = await check(llm)
            print(f"PASS {name}: {detail}")
        except Exception as exc:  # report any failure per check, then exit non-zero
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
