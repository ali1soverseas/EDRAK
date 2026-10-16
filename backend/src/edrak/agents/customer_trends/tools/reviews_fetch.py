"""reviews_fetch: app store and Amazon reviews."""

import time

from pydantic import Field, field_validator

from edrak.agents.customer_trends.schemas.common import (
    CountryCode,
    RequestBase,
    ReviewStore,
    ToolResponse,
)
from edrak.agents.customer_trends.tools.base import (
    DEFAULT_RESULTS,
    ToolContext,
    ToolSpec,
    execute_collection,
    results_limit,
)

DESCRIPTION = """Read customer reviews of an app or product from a store.

Use it for real user ratings and complaints about a specific app (app_store, google_play) or
product (amazon): what people like, what breaks, why they leave one star. Give the store's own id
or a URL: the numeric app id for app_store, the package name for google_play (for example
com.spotify.music), an ASIN or product URL for amazon. Do NOT use it to find the id (use
web_search to find the store page first) and do NOT use it for social posts.

Each review keeps its star rating in the evidence store. The response holds a batch_id, how many
new reviews were stored, a preview of up to 5, coverage, gaps and warnings.

Example: {"store": "google_play", "target_id_or_url": "com.spotify.music",
"country": "US", "max_results": 50}"""


class ReviewsFetchInput(RequestBase):
    store: ReviewStore = Field(description="app_store, google_play or amazon.")
    target_id_or_url: str = Field(
        min_length=3, description="App id or package name, or a product ASIN or URL."
    )
    country: CountryCode | None = Field(
        default=None, description="Store country (ISO alpha-2); defaults to geo or the store's own."
    )

    @field_validator("target_id_or_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


async def reviews_fetch(ctx: ToolContext, inp: ReviewsFetchInput) -> ToolResponse:
    started = time.perf_counter()
    params = {
        "target": inp.target_id_or_url,
        "country": inp.country or inp.geo,
        "languages": inp.languages,
        "since": inp.since,
        "until": inp.until,
        "max_results": results_limit(inp.depth, inp.max_results, DEFAULT_RESULTS["reviews"]),
    }
    return await execute_collection(
        ctx, "reviews_fetch", f"reviews:{inp.store.value}", inp, params,
        languages=inp.languages, started=started,
    )  # fmt: skip


SPEC = ToolSpec("reviews_fetch", DESCRIPTION, ReviewsFetchInput, reviews_fetch)
