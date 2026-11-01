"""The free stages together: what A and B decide, and what is left for the model."""

from collections.abc import Sequence

from edrak.agents.customer_trends.relevance.models import TERM_MATCH, Comment, Verdict
from edrak.agents.customer_trends.relevance.rules import Duplicates, stage_a
from edrak.agents.customer_trends.relevance.terms import TermMatcher


def free_stages(
    comments: Sequence[Comment], matcher: TermMatcher
) -> tuple[dict[str, Verdict], list[Comment], Duplicates]:
    """Verdicts of stages A and B, the comments neither could decide, and the duplicate counts.

    A comment judged in an earlier pass (`already`) only takes part in duplicate detection.
    """
    verdicts, duplicates = stage_a(comments, matcher)
    pending: list[Comment] = []
    for comment in comments:
        if comment.already or comment.id in verdicts:
            continue
        if matcher(comment.text):
            verdicts[comment.id] = Verdict("relevant", TERM_MATCH, "b")
        else:
            pending.append(comment)
    return verdicts, pending, duplicates
