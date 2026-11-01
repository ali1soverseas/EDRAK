"""Stage C: batched model classification of the comments the free stages could not decide.

Comments are grouped under their parent post, and each batch shows a post once and then its
comments, so a short reaction ("how much?") is read in the context it was written in. Only the
ids of the relevant comments come back. The stage fails open: a batch the model cannot answer, or
that is cut off by the time limit, is kept whole and the failure is reported.
"""

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from edrak.agents.customer_trends.llm.client import structured_call
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.prompts import RELEVANCE_FILTER, render
from edrak.agents.customer_trends.relevance.models import (
    KEPT_ON_ERROR,
    LLM_IRRELEVANT,
    LLM_RELEVANT,
    Comment,
    Verdict,
)
from edrak.agents.customer_trends.schemas.common import NonEmpty, StrictModel
from edrak.agents.customer_trends.utils.text import truncate

log = get_logger(__name__)

NO_PARENT_TEXT = "(the post was not collected)"
_WHITESPACE = re.compile(r"\s+")

ModelFor = Callable[[], BaseChatModel]


class RelevanceVerdict(StrictModel):
    """What the model returns: the ids of the relevant comments."""

    relevant: list[NonEmpty] = Field(default_factory=list)


@dataclass
class Batch:
    """Up to a batch size of comments, grouped under their posts. Ids are local: c1, c2, ..."""

    groups: list[tuple[Comment, list[Comment]]] = field(
        default_factory=list
    )  # (first, all) per post

    @property
    def comments(self) -> list[Comment]:
        return [c for _, group in self.groups for c in group]


def group_by_parent(comments: Sequence[Comment]) -> list[list[Comment]]:
    """The comments grouped by their parent post, in the order the posts first appear."""
    groups: dict[str, list[Comment]] = {}
    for comment in comments:
        groups.setdefault(comment.parent_key, []).append(comment)
    return list(groups.values())


def make_batches(comments: Sequence[Comment], size: int) -> list[Batch]:
    """Whole posts are kept together while they fit. A post with more comments than a batch holds is
    split, and its text is sent again with each part."""
    batches: list[Batch] = []
    current = Batch()
    room = size
    for group in group_by_parent(comments):
        rest = list(group)
        while rest:
            if room == 0:
                batches.append(current)
                current, room = Batch(), size
            part, rest = rest[:room], rest[room:]
            current.groups.append((part[0], part))
            room -= len(part)
    if current.groups:
        batches.append(current)
    return batches


def _line(text: str, limit: int) -> str:
    return truncate(_WHITESPACE.sub(" ", text).strip(), limit)


def render_batch(batch: Batch, parent_chars: int, comment_chars: int) -> tuple[str, dict[str, str]]:
    """The batch as the model reads it, and the map from its local ids to the comment ids. Only the
    copy sent to the model is cut; the stored text is not."""
    lines: list[str] = []
    ids: dict[str, str] = {}
    for number, (first, group) in enumerate(batch.groups, start=1):
        post = _line(first.parent_text, parent_chars) or NO_PARENT_TEXT
        lines.append(f"POST P{number}: {post}")
        for comment in group:
            local = f"c{len(ids) + 1}"
            ids[local] = comment.id
            lines.append(f"{local}: {_line(comment.text, comment_chars)}")
        lines.append("")
    return "\n".join(lines).rstrip(), ids


@dataclass
class ClassifyOutcome:
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    sent: int = 0  # comments that went to the model, answered or not
    calls: int = 0


async def _classify_batch(
    batch: Batch, system: str, model: BaseChatModel, parent_chars: int, comment_chars: int
) -> dict[str, Verdict]:
    text, ids = render_batch(batch, parent_chars, comment_chars)
    answer = await structured_call(
        model, RelevanceVerdict, [SystemMessage(content=system), HumanMessage(content=text)]
    )
    relevant = {ids[i] for i in answer.relevant if i in ids}  # an id that was never sent is ignored
    return {
        comment_id: Verdict("relevant", LLM_RELEVANT, "c")
        if comment_id in relevant
        else Verdict("irrelevant", LLM_IRRELEVANT, "c")
        for comment_id in ids.values()
    }


def kept_on_error(comments: Sequence[Comment]) -> dict[str, Verdict]:
    return {c.id: Verdict("relevant", KEPT_ON_ERROR, "c") for c in comments}


async def classify(
    comments: Sequence[Comment],
    *,
    model_for: ModelFor,
    prompt_values: dict[str, Any],
    batch_size: int,
    parent_chars: int,
    comment_chars: int,
    concurrency: int,
    timeout_s: float,
) -> ClassifyOutcome:
    """Classify `comments` in batches, `concurrency` at a time, within `timeout_s` seconds overall.

    Never raises for a model problem: a batch that fails, or does not finish in time, is kept
    (`kept_on_error`) and a warning says so.
    """
    outcome = ClassifyOutcome()
    if not comments:
        return outcome
    batches = make_batches(comments, batch_size)
    outcome.sent = len(comments)
    outcome.calls = len(batches)
    try:
        model = model_for()
    except Exception as exc:
        log.warning("relevance_model_unavailable", error=type(exc).__name__)
        outcome.verdicts = kept_on_error(comments)
        outcome.warnings.append(
            f"the relevance filter could not build its model ({type(exc).__name__}); "
            f"{len(comments)} comment(s) were kept"
        )
        return outcome

    system = render(RELEVANCE_FILTER, prompt_values)
    gate = asyncio.Semaphore(concurrency)

    async def run(batch: Batch) -> dict[str, Verdict]:
        async with gate:
            return await _classify_batch(batch, system, model, parent_chars, comment_chars)

    tasks = {asyncio.create_task(run(batch)): batch for batch in batches}
    done, pending = await asyncio.wait(tasks, timeout=timeout_s)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    failed = timed_out = 0
    for task, batch in tasks.items():
        if task in pending:
            timed_out += len(batch.comments)
            outcome.verdicts.update(kept_on_error(batch.comments))
        elif task.exception() is not None:
            exc = task.exception()
            log.warning("relevance_batch_failed", error=type(exc).__name__)
            failed += len(batch.comments)
            outcome.verdicts.update(kept_on_error(batch.comments))
        else:
            outcome.verdicts.update(task.result())
    if failed:
        outcome.warnings.append(
            f"the relevance filter could not classify {failed} comment(s) (a model error); "
            "they were kept"
        )
    if timed_out:
        outcome.warnings.append(
            f"the relevance filter ran out of time ({timeout_s:g} s) for {timed_out} comment(s); "
            "they were kept"
        )
    return outcome
