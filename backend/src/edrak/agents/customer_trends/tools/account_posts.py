"""account_posts: the recent posts of one official account (the company's or a competitor's).

Called by the worker itself for every account in the brief, not chosen by a model. The posts show
what each company says, and how much its audience engages; the replies under the most engaged
posts (read with social_comments) show what customers say back.
"""

import re
import time
from typing import Any

from pydantic import Field, field_validator

from edrak.agents.customer_trends.providers.base import ProviderResult
from edrak.agents.customer_trends.schemas.common import NonEmpty, Platform, RequestBase, ToolResponse
from edrak.agents.customer_trends.tools.base import (
    StoredBatch,
    ToolContext,
    ToolSpec,
    execute_collection,
    persist_evidence,
)

DESCRIPTION = """Read the recent posts of one official account on x, instagram or facebook.
Use it for the company's own accounts and the competitors' official accounts, to see what each
says and how its audience reacts. Pass the profile URL. Do NOT use it to search a topic (use
social_search)."""

ACCOUNT_PLATFORMS = (Platform.X, Platform.INSTAGRAM, Platform.FACEBOOK)
DEFAULT_POSTS = 15
_X_HANDLE = re.compile(r"(?:x|twitter)\.com/@?([A-Za-z0-9_]{1,15})", re.IGNORECASE)


class AccountPostsInput(RequestBase):
    platform: Platform = Field(description="x, instagram or facebook.")
    account_url: str = Field(description="The account's profile URL.")
    owner: NonEmpty = Field(description="Whose account it is: the company or a competitor.")

    @field_validator("platform")
    @classmethod
    def _supported(cls, value: Platform) -> Platform:
        if value not in ACCOUNT_PLATFORMS:
            raise ValueError("account_posts supports x, instagram and facebook")
        return value


def account_target(platform: Platform, url: str) -> str:
    """What the actor needs: the handle for X, the full profile URL otherwise."""
    if platform is Platform.X:
        match = _X_HANDLE.search(url)
        if not match:
            raise ValueError(f"not an X profile URL: {url}")
        return match.group(1)
    return url if url.startswith("http") else f"https://{url}"


def _tagged(owner: str, url: str) -> Any:
    """persist_evidence, after tagging every item with the account it came from."""

    def persist(ctx: ToolContext, tool: str, result: ProviderResult) -> StoredBatch:
        for item in result.items:
            metadata = getattr(item, "metadata", None)
            if isinstance(metadata, dict):
                metadata["account_owner"] = owner
                metadata["account_url"] = url
        return persist_evidence(ctx, tool, result)

    return persist


async def account_posts(ctx: ToolContext, inp: AccountPostsInput) -> ToolResponse:
    started = time.perf_counter()
    params = {
        "target": account_target(inp.platform, inp.account_url),
        "since": inp.since,
        "until": inp.until,
        "languages": inp.languages,
        "max_results": inp.max_results or DEFAULT_POSTS,
    }
    return await execute_collection(
        ctx, "account_posts", f"account_posts:{inp.platform.value}", inp, params,
        languages=inp.languages, persist=_tagged(inp.owner, inp.account_url), started=started,
    )  # fmt: skip


SPEC = ToolSpec("account_posts", DESCRIPTION, AccountPostsInput, account_posts)
