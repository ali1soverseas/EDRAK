"""web_search: Google web or news results through Serper."""

import time
from typing import Literal

from pydantic import Field

from edrak.agents.customer_trends.schemas.common import RequestBase, ToolResponse
from edrak.agents.customer_trends.tools.base import (
    DEFAULT_RESULTS,
    ToolContext,
    ToolSpec,
    execute_collection,
    results_limit,
)

DESCRIPTION = """Search Google for web pages or news articles about a topic.

Use it to find articles, blog posts, forum threads, press coverage and official pages, and to
discover URLs worth reading with fetch_page. Set search_type to "news" for recent news coverage.
Do NOT use it for posts or comments on social platforms (use social_search and social_comments)
or for search interest over time (use search_interest).

Results are search snippets only (title and a short excerpt), not full pages. The response holds
a batch_id, how many new items were stored, a preview of up to 5, coverage by language, gaps and
warnings. The full records stay in the evidence store.

Example: {"query": "GitLab Duo vs GitHub Copilot review", "search_type": "web", "num": 10}"""


class WebSearchInput(RequestBase):
    query: str = Field(min_length=2, description="What to search for. Quote exact phrases.")
    search_type: Literal["web", "news"] = Field(
        default="web", description="web for pages, news for recent news articles."
    )
    num: int = Field(default=10, ge=1, le=100, description="How many results to fetch.")


async def web_search(ctx: ToolContext, inp: WebSearchInput) -> ToolResponse:
    started = time.perf_counter()
    limit = results_limit(inp.depth, inp.max_results or inp.num, DEFAULT_RESULTS["web"])
    capability = "news:google_news" if inp.search_type == "news" else "web_search"
    params = {
        "query": inp.query,
        "languages": inp.languages,
        "geo": inp.geo,
        "since": inp.since,
        "until": inp.until,
        "max_results": limit,
    }
    return await execute_collection(
        ctx, "web_search", capability, inp, params, languages=inp.languages, started=started
    )


SPEC = ToolSpec("web_search", DESCRIPTION, WebSearchInput, web_search)
