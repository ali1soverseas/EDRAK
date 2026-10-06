"""The worker's tools as LangChain tools. Nodes get tools only from here (CONTRIBUTING 5)."""

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from edrak.agents.customer_trends.schemas.common import ProcessingResponse, ToolResponse
from edrak.agents.customer_trends.tools import (
    fetch_page,
    news_coverage,
    reviews_fetch,
    search_interest,
    social_search,
    web_search,
)
from edrak.agents.customer_trends.tools.base import ToolContext, ToolSpec, invoke_tool

COLLECTION_SPECS: tuple[ToolSpec, ...] = (
    web_search.SPEC,
    fetch_page.SPEC,
    social_search.SEARCH_SPEC,
    social_search.COMMENTS_SPEC,
    search_interest.SPEC,
    reviews_fetch.SPEC,
    news_coverage.SPEC,
)

# The tool subsets of the three collection branches (SPEC section 10).
BRANCH_TOOLS: dict[str, tuple[str, ...]] = {
    "social": ("social_search", "social_comments", "web_search"),
    "demand": ("search_interest", "news_coverage", "web_search"),
    "reviews": ("reviews_fetch", "web_search", "fetch_page"),
}

_INJECTED = ("run_id", "task_id")


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve $ref, drop titles and turn `X or null` into plain `X` (optional fields)."""
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _inline(
            {
                **defs[node["$ref"].rsplit("/", 1)[-1]],
                **{k: v for k, v in node.items() if k != "$ref"},
            },
            defs,
        )
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            return _inline({**options[0], **rest}, defs)
    return {
        k: _inline(v, defs)
        for k, v in node.items()
        if k not in ("title", "$defs") and not (k == "default" and v is None)
    }


def model_visible_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema of a tool input without `run_id` and `task_id`, with references inlined."""
    schema = model.model_json_schema()
    inlined = _inline(schema, schema.get("$defs", {}))
    properties = {k: v for k, v in inlined["properties"].items() if k not in _INJECTED}
    required = [name for name in inlined.get("required", []) if name not in _INJECTED]
    visible: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        visible["required"] = required
    return visible


def compact_json(response: ToolResponse | ProcessingResponse) -> str:
    """The response as the model sees it: empty fields left out, no bulk records."""
    data = response.model_dump(mode="json", exclude_none=True)
    kept = {k: v for k, v in data.items() if v or k in ("status", "count")}
    return json.dumps(kept, ensure_ascii=False, separators=(",", ":"))


def _structured_tool(ctx: ToolContext, spec: ToolSpec) -> StructuredTool:
    async def run(**arguments: Any) -> str:
        return compact_json(await invoke_tool(ctx, spec, arguments))

    return StructuredTool(
        name=spec.name,
        description=spec.description,
        args_schema=model_visible_schema(spec.input_model),
        coroutine=run,
    )


def build_tools(ctx: ToolContext) -> dict[str, StructuredTool]:
    """One LangChain tool per collection tool. Each injects the run and task ids from `ctx`
    and answers with a compact JSON string, so the model never handles bulk records."""
    return {spec.name: _structured_tool(ctx, spec) for spec in COLLECTION_SPECS}


def tools_for_branch(branch: str, ctx: ToolContext) -> list[StructuredTool]:
    """The tools a collection branch may use, with the branch recorded on its tool events."""
    if branch not in BRANCH_TOOLS:
        raise ValueError(f"unknown branch {branch!r}; choose one of {sorted(BRANCH_TOOLS)}")
    tools = build_tools(ctx.for_branch(branch))
    return [tools[name] for name in BRANCH_TOOLS[branch]]
