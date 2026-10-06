"""fetch_page: the main text of one public web page."""

import time

from pydantic import Field, field_validator

from edrak.agents.customer_trends.schemas.common import NonEmpty, RunId, StrictModel, ToolResponse
from edrak.agents.customer_trends.tools.base import (
    ToolContext,
    ToolSpec,
    execute_collection,
)
from edrak.agents.customer_trends.tools.urls import host_of, non_public_reason, social_platform_of

DESCRIPTION = """Download one public web page and read its main text.

Use it on a URL found by web_search when the snippet is not enough: an article, a review, a
comparison, a forum thread, a pricing or product page. It respects robots.txt and reads only
the main text, not menus or comments. Do NOT use it on social platform pages (x, reddit,
tiktok, instagram, facebook, youtube): use social_search to find posts and social_comments to
read the comments under one post. It cannot reach pages behind a login or paywall.

The text is stored in full in the evidence store. The response holds a batch_id, a count of 1,
a preview (title and the start of the text), and warnings when the page was refused, empty or
not a text page.

Example: {"url": "https://about.gitlab.com/blog/example-post/", "max_chars": 20000}"""


class FetchPageInput(StrictModel):
    run_id: RunId
    task_id: NonEmpty
    url: str = Field(description="Full http or https URL of the page to read.")
    max_chars: int = Field(
        default=20000, ge=500, le=50000, description="Most characters of text to keep."
    )

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        host = host_of(value)
        if host is None:
            raise ValueError("url must be a full http or https URL")
        platform = social_platform_of(host)
        if platform is not None:
            raise ValueError(
                f"{platform.value} pages cannot be fetched: use social_search to find posts "
                "and social_comments to read the comments under a post"
            )
        if reason := non_public_reason(host):
            raise ValueError(f"url cannot be fetched because {reason}")
        return value


async def fetch_page(ctx: ToolContext, inp: FetchPageInput) -> ToolResponse:
    started = time.perf_counter()
    params = {"url": inp.url, "max_chars": inp.max_chars, "max_results": 1}
    return await execute_collection(ctx, "fetch_page", "fetch_page", inp, params, started=started)


SPEC = ToolSpec("fetch_page", DESCRIPTION, FetchPageInput, fetch_page)
