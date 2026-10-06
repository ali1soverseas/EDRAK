"""social_search and social_comments: posts and comments from public social platforms."""

import time
from typing import Literal

from pydantic import Field, field_validator, model_validator

from edrak.agents.customer_trends.schemas.common import (
    NonEmpty,
    Platform,
    RequestBase,
    ToolResponse,
)
from edrak.agents.customer_trends.tools.base import (
    DEFAULT_RESULTS,
    ToolContext,
    ToolSpec,
    execute_collection,
    results_limit,
)
from edrak.agents.customer_trends.tools.urls import PLATFORM_DOMAINS, host_of, on_domain

SEARCH_DESCRIPTION = """Search public posts on one social platform for a topic.

Use it to find what customers and users say about a product, brand or problem: complaints,
praise, questions, comparisons. Choose the platform that fits (reddit and x for discussion,
youtube for reviews and demos, tiktok and instagram for consumer reaction, facebook for page and
group posts). Give hashtags only when they are real tags people use. Call it once per platform.
Do NOT use it for comments under one specific post (use social_comments), for articles (use
web_search), or for search interest over time (use search_interest).

The response holds a batch_id, how many new posts were stored, a preview of up to 5, coverage by
language, gaps (for example when only snippets could be collected) and warnings. The full
records, with likes, replies, shares and views, stay in the evidence store.

Example: {"platform": "reddit", "query": "GitLab Duo vs Copilot", "sort": "top",
"max_results": 30}"""

COMMENTS_DESCRIPTION = """Read the comments or replies under one specific public post.

Use it after social_search, on a post that already looks relevant (many replies or comments), to
see how people react in detail. Pass the post's own URL, on the same platform. Do NOT use it to
discover posts (use social_search) and do NOT pass a profile, hashtag or search URL.

The response holds a batch_id, how many new comments were stored, a preview of up to 5, coverage
by language, gaps and warnings. A post without comments returns zero items and a gap.

Example: {"platform": "youtube", "max_results": 40,
"post_url": "https://www.youtube.com/watch?v=abc123xyz00"}"""


class SocialSearchInput(RequestBase):
    platform: Platform = Field(description="x, reddit, tiktok, instagram, facebook or youtube.")
    query: str = Field(min_length=2, description="Search words or a phrase.")
    hashtags: list[NonEmpty] = Field(
        default_factory=list, max_length=5, description="Optional real hashtags."
    )
    sort: Literal["recent", "top"] = Field(
        default="recent", description="recent or top (most engaged)."
    )


class SocialCommentsInput(RequestBase):
    platform: Platform = Field(description="The platform the post is on.")
    post_url: str = Field(description="Full URL of the post whose comments to read.")
    sort: Literal["recent", "top"] = Field(default="top", description="top (most liked) or recent.")

    @field_validator("post_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if host_of(value) is None:
            raise ValueError("post_url must be a full http or https URL")
        return value.strip()

    @model_validator(mode="after")
    def _url_matches_platform(self) -> "SocialCommentsInput":
        host = host_of(self.post_url) or ""
        if not on_domain(host, PLATFORM_DOMAINS[self.platform]):
            raise ValueError(f"post_url is not a {self.platform.value} URL (host {host})")
        return self


async def social_search(ctx: ToolContext, inp: SocialSearchInput) -> ToolResponse:
    started = time.perf_counter()
    params = {
        "query": inp.query,
        "hashtags": inp.hashtags,
        "sort": inp.sort,
        "languages": inp.languages,
        "geo": inp.geo,
        "since": inp.since,
        "until": inp.until,
        "max_results": results_limit(inp.depth, inp.max_results, DEFAULT_RESULTS["social"]),
    }
    return await execute_collection(
        ctx, "social_search", f"social_search:{inp.platform.value}", inp, params,
        languages=inp.languages, started=started,
    )  # fmt: skip


async def social_comments(ctx: ToolContext, inp: SocialCommentsInput) -> ToolResponse:
    started = time.perf_counter()
    params = {
        "post_url": inp.post_url,
        "sort": inp.sort,
        "languages": inp.languages,
        "since": inp.since,
        "until": inp.until,
        "max_results": results_limit(inp.depth, inp.max_results, DEFAULT_RESULTS["social"]),
    }
    return await execute_collection(
        ctx, "social_comments", f"social_comments:{inp.platform.value}", inp, params,
        languages=inp.languages, started=started,
    )  # fmt: skip


SEARCH_SPEC = ToolSpec("social_search", SEARCH_DESCRIPTION, SocialSearchInput, social_search)
COMMENTS_SPEC = ToolSpec(
    "social_comments", COMMENTS_DESCRIPTION, SocialCommentsInput, social_comments
)
