"""analyze_text: map-reduce theme and sentiment analysis over stored items.

The map step asks the analyst model to label chunks of at most 25 items. The reduce step is
plain code: labels are normalized and merged, then counted into `ThemeAggregate` records.
The model never counts, never writes a quote and never sees more than one chunk at a time.
"""

import json
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from edrak.agents.customer_trends.llm.client import (
    LLMConfigError,
    StructuredOutputError,
    get_chat_model,
    is_transient,
    structured_call,
)
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.prompts import ANALYZE_THEMES
from edrak.agents.customer_trends.schemas.analysis import (
    MAX_QUOTE_CHARS,
    MAX_QUOTES,
    Sentiment,
    ThemeAggregate,
)
from edrak.agents.customer_trends.schemas.common import (
    NonEmpty,
    ProcessingResponse,
    RunScope,
    StrictModel,
    ToolResponse,
    ToolStatus,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.tools.base import (
    ToolContext,
    ToolSpec,
    error_response,
    finish_processing,
    unknown_batch_response,
)
from edrak.agents.customer_trends.tools.selection import group_key, load_items
from edrak.agents.customer_trends.utils.labels import jaccard, normalize_label
from edrak.agents.customer_trends.utils.text import truncate

log = get_logger(__name__)

CHUNK_SIZE = 25
DEFAULT_MAX_ITEMS = 300
MAX_ITEMS_LIMIT = 1000
ITEM_TEXT_CHARS = 600
MERGE_THRESHOLD = 0.6
TOP_THEMES = 15
MAX_THEMES_PER_ITEM = 3
MAX_TAXONOMY = 40
MIN_DATED_ITEMS = 6
MIN_GROWTH_COUNT = 3
LATEST_FRACTION = 1 / 3
MIN_QUOTE_CHARS = 20
DESCRIPTION_CHARS = 160

Task = Literal["sentiment", "themes", "language"]
ALL_TASKS: tuple[Task, ...] = ("sentiment", "themes", "language")
_LANGUAGE_CODE = re.compile(r"^[A-Za-z]{2}$")

DESCRIPTION = """Find what people talk about in the collected evidence, and how they feel about it.

Pass the batch_ids from your collection calls. The items are read in chunks by a model and
labelled with a sentiment, a language and up to 3 short English theme labels each (Arabic and
English items are read as written, nothing is translated first). Code then merges similar labels
and counts them, so every count and share in the answer is exact. Themes are stored for the run;
read the items behind one with evidence_query(filters={"theme": label}).

Call it once with all the batches you want analyzed: a later call replaces the themes stored by
an earlier one. It reads at most `max_items` items, the most engaged first, so on a large
collection raise max_items (up to 1000) or analyze fewer batches. `tasks` picks what to produce
(default all three); `taxonomy` is an optional list of theme labels to prefer, not a closed set.

The answer lists the 15 biggest themes with count, share of analyzed items, sentiment mix and
recent_growth (the change in the theme's share of items, latest third of the dates against the
earlier two thirds: 0.5 is 50 percent more, null when there are too few dated items), the
sentiment overview and warnings. Do NOT use it to count platforms or time (use compute_metrics).

Example: {"batch_ids": ["b_1a2b3c4d5e6f"], "tasks": ["themes", "sentiment"], "max_items": 200}"""


class AnalyzeTextInput(RunScope):
    batch_ids: list[str] = Field(
        min_length=1, description="The batch_id values of the collection calls to analyze."
    )
    tasks: list[Task] = Field(
        default_factory=lambda: list(ALL_TASKS),
        min_length=1,
        description="What to produce: sentiment, themes, language.",
    )
    taxonomy: list[NonEmpty] | None = Field(
        default=None,
        max_length=MAX_TAXONOMY,
        description="Theme labels to prefer when they fit; new labels are still allowed.",
    )
    max_items: int = Field(
        default=DEFAULT_MAX_ITEMS,
        ge=1,
        le=MAX_ITEMS_LIMIT,
        description="Most items to analyze, the most engaged first.",
    )


class ItemLabel(BaseModel):
    """What the model says about one item. Unknown keys are ignored, a bad value is an error."""

    model_config = ConfigDict(extra="ignore")

    id: NonEmpty
    sentiment: Sentiment
    themes: list[NonEmpty] = Field(default_factory=list)
    language: str | None = None

    @field_validator("sentiment", mode="before")
    @classmethod
    def _clean_sentiment(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("themes", mode="before")
    @classmethod
    def _clean_themes(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        cleaned = [theme.strip() for theme in value if isinstance(theme, str) and theme.strip()]
        return cleaned[:MAX_THEMES_PER_ITEM]

    @field_validator("language", mode="before")
    @classmethod
    def _clean_language(cls, value: Any) -> str | None:
        if isinstance(value, str) and _LANGUAGE_CODE.match(value.strip()):
            return value.strip().lower()
        return None


class CandidateTheme(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: NonEmpty
    description: str = ""


class ChunkAnalysis(BaseModel):
    """The strict JSON answer for one chunk."""

    model_config = ConfigDict(extra="ignore")

    items: list[ItemLabel]
    candidate_themes: list[CandidateTheme] = Field(default_factory=list)


class ThemeSummary(StrictModel):
    label: str
    count: int
    share: float
    sentiment_mix: dict[str, float] = Field(default_factory=dict)
    recent_growth: float | None = None
    description: str = ""


class AnalyzeTextResponse(ProcessingResponse):
    analyzed: int
    total_items: int
    themes_total: int
    themes: list[ThemeSummary]
    sentiment: dict[str, Any] = Field(default_factory=dict)
    languages: dict[str, int] = Field(default_factory=dict)


@dataclass
class MapOutcome:
    labels: dict[str, ItemLabel] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    chunks_skipped: int = 0


@dataclass
class Cluster:
    head: str
    tokens: set[str]
    members: set[str] = field(default_factory=set)
    surfaces: Counter[str] = field(default_factory=Counter)
    keys: set[str] = field(default_factory=set)


def _messages(
    chunk: Sequence[EvidenceItem], taxonomy: Sequence[str] | None, tasks: Sequence[str]
) -> list[BaseMessage]:
    items = [
        {"id": item.id, "platform": group_key(item), "text": truncate(item.text, ITEM_TEXT_CHARS)}
        for item in chunk
    ]
    human = (
        f"Tasks wanted: {', '.join(tasks)}\n"
        f"Taxonomy: {', '.join(taxonomy) if taxonomy else 'none'}\n"
        f"Items:\n{json.dumps(items, ensure_ascii=False)}"
    )
    return [SystemMessage(content=ANALYZE_THEMES), HumanMessage(content=human)]


async def label_chunks(
    llm: BaseChatModel,
    ranked: Sequence[EvidenceItem],
    taxonomy: Sequence[str] | None,
    tasks: Sequence[str],
) -> MapOutcome:
    """The map step. A chunk is tried, repaired once by `structured_call`, and skipped if the
    model still cannot answer; the rest of the analysis goes on without it."""
    outcome = MapOutcome()
    chunks = [ranked[start : start + CHUNK_SIZE] for start in range(0, len(ranked), CHUNK_SIZE)]
    invented = unlabeled = 0
    for number, chunk in enumerate(chunks, start=1):
        try:
            answer = await structured_call(llm, ChunkAnalysis, _messages(chunk, taxonomy, tasks))
        except StructuredOutputError:
            reason = "no valid answer"
        except Exception as exc:
            if not is_transient(exc):
                raise
            reason = f"the model call failed ({type(exc).__name__})"
        else:
            wanted = {item.id for item in chunk}
            for label in answer.items:
                if label.id not in wanted:
                    invented += 1
                elif label.id not in outcome.labels:
                    outcome.labels[label.id] = label
            unlabeled += sum(1 for item in chunk if item.id not in outcome.labels)
            for candidate in answer.candidate_themes:
                outcome.descriptions.setdefault(
                    normalize_label(candidate.label), candidate.description.strip()
                )
            continue
        outcome.chunks_skipped += 1
        outcome.warnings.append(
            f"chunk {number} of {len(chunks)} skipped ({reason}): {len(chunk)} items not analyzed"
        )
        log.warning("analyze_chunk_skipped", chunk=number, of=len(chunks), items=len(chunk))
    if invented:
        outcome.warnings.append(f"{invented} labels named ids that were not in the input; ignored")
    if unlabeled:
        outcome.warnings.append(f"{unlabeled} items got no label from the model")
    return outcome


def build_clusters(labels: dict[str, ItemLabel]) -> list[Cluster]:
    """Group theme labels whose normalized words overlap by at least `MERGE_THRESHOLD`.

    The most used label of each group is its head and later labels join the head they overlap
    most, so a group never drifts through a chain of loose matches.
    """
    members: dict[str, set[str]] = {}
    surfaces: dict[str, Counter[str]] = {}
    for item_id, label in labels.items():
        for theme in label.themes:
            key = normalize_label(theme)
            if key:
                members.setdefault(key, set()).add(item_id)
                surfaces.setdefault(key, Counter())[theme] += 1
    clusters: list[Cluster] = []
    for key in sorted(members, key=lambda k: (-len(members[k]), k)):
        tokens = set(key.split())
        best = max(clusters, key=lambda c: jaccard(tokens, c.tokens), default=None)
        if best is not None and jaccard(tokens, best.tokens) >= MERGE_THRESHOLD:
            target = best
        else:
            target = Cluster(key, tokens)
            clusters.append(target)
        target.members |= members[key]
        target.surfaces.update(surfaces[key])
        target.keys.add(key)
    return clusters


def cluster_label(cluster: Cluster, taxonomy: Sequence[str] | None) -> str:
    """A taxonomy label when the group matches one, else the most used wording (shortest, then
    alphabetical, on a tie)."""
    by_key = {normalize_label(label): label for label in taxonomy or []}
    for key in sorted(cluster.keys):
        if key in by_key:
            return by_key[key]
    return min(cluster.surfaces, key=lambda text: (-cluster.surfaces[text], len(text), text))


def clip_quote(text: str) -> str:
    """The text itself if it fits, else its start cut at a word, so a quote is always a verbatim
    part of the stored item."""
    text = text.strip()
    if len(text) <= MAX_QUOTE_CHARS:
        return text
    cut = text[:MAX_QUOTE_CHARS]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    return (cut[:boundary] if boundary > MAX_QUOTE_CHARS // 2 else cut).rstrip()


def pick_quotes(ranked_members: Sequence[EvidenceItem]) -> list[str]:
    """Up to three distinct quotes from the most engaged items, longer than a few words first."""
    quotes = [clip_quote(item.text) for item in ranked_members]
    distinct = list(dict.fromkeys(quote for quote in quotes if quote))
    substantial = [quote for quote in distinct if len(quote) >= MIN_QUOTE_CHARS]
    rest = [quote for quote in distinct if len(quote) < MIN_QUOTE_CHARS]
    return [*substantial, *rest][:MAX_QUOTES]


def split_by_date(items: Sequence[EvidenceItem]) -> tuple[set[str], set[str]] | None:
    """Ids of the items in the earlier two thirds and in the latest third of the date range, or
    None when too few items have a date to compare."""
    dated = [(item.id, item.published_at.timestamp()) for item in items if item.published_at]
    if len(dated) < MIN_DATED_ITEMS:
        return None
    first = min(when for _, when in dated)
    last = max(when for _, when in dated)
    if first == last:
        return None
    cutoff = last - (last - first) * LATEST_FRACTION
    return (
        {item_id for item_id, when in dated if when <= cutoff},
        {item_id for item_id, when in dated if when > cutoff},
    )


def recent_growth(members: set[str], split: tuple[set[str], set[str]] | None) -> float | None:
    """Relative change of the theme's share of items from the earlier period to the latest."""
    if split is None or len(members) < MIN_GROWTH_COUNT:
        return None
    earlier, latest = split
    share_earlier = len(members & earlier) / len(earlier)
    if share_earlier == 0:
        return None
    return round(len(members & latest) / len(latest) / share_earlier - 1, 3)


def _fractions(counter: Counter[str], total: int) -> dict[str, float]:
    return {key: round(n / total, 4) for key, n in sorted(counter.items())}


def aggregate_themes(
    clusters: Sequence[Cluster],
    items: Sequence[EvidenceItem],
    outcome: MapOutcome,
    taxonomy: Sequence[str] | None,
    tasks: Sequence[str],
) -> tuple[list[ThemeAggregate], dict[str, str]]:
    """Counts, shares, mixes, growth and quotes per merged theme, biggest first."""
    labels = outcome.labels
    rank = {item.id: position for position, item in enumerate(items)}
    by_id = {item.id: item for item in items if item.id in labels}
    split = split_by_date(list(by_id.values()))
    analyzed = len(labels)
    aggregates: list[ThemeAggregate] = []
    descriptions: dict[str, str] = {}
    for cluster in clusters:
        members = sorted((by_id[i] for i in cluster.members), key=lambda item: rank[item.id])
        label = cluster_label(cluster, taxonomy)
        sentiments = Counter(labels[item.id].sentiment.value for item in members)
        languages = Counter(
            item.language or labels[item.id].language or "unknown" for item in members
        )
        aggregates.append(
            ThemeAggregate(
                theme_label=label,
                count=len(members),
                share=round(len(members) / analyzed, 4),
                sentiment_mix=_fractions(sentiments, len(members)) if "sentiment" in tasks else {},
                by_platform=dict(sorted(Counter(group_key(item) for item in members).items())),
                by_language=dict(sorted(languages.items())),
                recent_growth=recent_growth(cluster.members, split),
                evidence_ids=[item.id for item in members],
                representative_quotes=pick_quotes(members),
            )
        )
        descriptions[label] = next(
            (outcome.descriptions[k] for k in sorted(cluster.keys) if outcome.descriptions.get(k)),
            "",
        )
    aggregates.sort(key=lambda a: (-a.count, a.theme_label))
    return aggregates, descriptions


def _overview(counter: Counter[str], total: int) -> dict[str, Any]:
    ordered = dict(sorted(counter.items(), key=lambda pair: (-pair[1], pair[0])))
    return {
        "counts": ordered,
        "percent": {key: round(100 * n / total, 1) for key, n in ordered.items()},
    }


async def _analyze(ctx: ToolContext, inp: AnalyzeTextInput) -> ToolResponse | ProcessingResponse:
    known = set(ctx.store.run_summary(ctx.run_id).batch_ids)
    unknown = [batch for batch in inp.batch_ids if batch not in known]
    if unknown:
        return unknown_batch_response(unknown)
    items, total = load_items(
        ctx.store,
        ctx.run_id,
        EvidenceFilters(batch_ids=inp.batch_ids),
        limit=inp.max_items,
        order="top",
    )
    if not items:
        return error_response("no_data", "the given batches hold no items to analyze")
    try:
        llm = ctx.analyst or get_chat_model("analyst", settings=ctx.settings)
    except LLMConfigError as exc:
        return error_response("llm_unavailable", str(exc))

    outcome = await label_chunks(llm, items, inp.taxonomy, inp.tasks)
    labels = outcome.labels
    warnings = list(outcome.warnings)
    if not labels:
        return error_response(
            "analysis_failed", "the model could not label any chunk; " + "; ".join(warnings)
        )
    if total > len(items):
        warnings.append(
            f"analyzed the {len(items)} most engaged of {total} items; raise max_items to read more"
        )

    aggregates: list[ThemeAggregate] = []
    descriptions: dict[str, str] = {}
    if "themes" in inp.tasks:
        aggregates, descriptions = aggregate_themes(
            build_clusters(labels), items, outcome, inp.taxonomy, inp.tasks
        )
        if not aggregates:
            warnings.append("the model found no themes; the stored themes were left as they were")
        else:
            earlier = len(ctx.store.get_aggregates(ctx.run_id))
            if earlier:
                warnings.append(f"replaced {earlier} theme(s) stored by an earlier analysis")
            ctx.store.save_aggregates(ctx.run_id, aggregates)

    analyzed = len(labels)
    sentiment: dict[str, Any] = {}
    if "sentiment" in inp.tasks:
        sentiment = _overview(Counter(label.sentiment.value for label in labels.values()), analyzed)
    languages: dict[str, int] = {}
    if "language" in inp.tasks:
        by_id = {item.id: item for item in items}
        languages = dict(
            Counter(
                by_id[item_id].language or label.language or "unknown"
                for item_id, label in labels.items()
            ).most_common()
        )
    return AnalyzeTextResponse(
        status=ToolStatus.PARTIAL if outcome.chunks_skipped else ToolStatus.OK,
        count=analyzed,
        warnings=warnings,
        analyzed=analyzed,
        total_items=total,
        themes_total=len(aggregates),
        themes=[
            ThemeSummary(
                label=a.theme_label,
                count=a.count,
                share=round(a.share, 3),
                sentiment_mix=a.sentiment_mix,
                recent_growth=a.recent_growth,
                description=truncate(descriptions.get(a.theme_label, ""), DESCRIPTION_CHARS),
            )
            for a in aggregates[:TOP_THEMES]
        ],
        sentiment=sentiment,
        languages=languages,
    )


async def analyze_text(
    ctx: ToolContext, inp: AnalyzeTextInput
) -> ToolResponse | ProcessingResponse:
    started = time.perf_counter()
    return finish_processing(ctx, "analyze_text", inp, await _analyze(ctx, inp), started)


SPEC = ToolSpec("analyze_text", DESCRIPTION, AnalyzeTextInput, analyze_text)
