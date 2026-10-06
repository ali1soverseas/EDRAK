"""Builders for evidence and results used across the worker's tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.findings import (
    ControlSummary,
    CustomerTrendsResult,
    EvidenceRef,
)
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.utils.text import content_hash, evidence_id

FIXTURES = Path(__file__).parent / "fixtures"
BRIEFS = Path(__file__).resolve().parents[2] / "evals" / "customer_trends" / "briefs"
RUN_ID = "run-test-001"
TASK_ID = "task-test-001"
BASE_DAY = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class FakeClock:
    """A clock whose `sleep` moves time forward instead of waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def call_params(**overrides: Any) -> dict[str, Any]:
    """Provider call params for the default test run."""
    return {"run_id": RUN_ID, "task_id": TASK_ID, **overrides}


def fixture_json(*parts: str) -> Any:
    """A recorded payload from fixtures/: fixture_json("providers", "apify", "x_post.json")."""
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


def load_brief(use_case: str = "competitive_intelligence") -> TaskBrief:
    return TaskBrief.model_validate_json((BRIEFS / f"{use_case}.json").read_text(encoding="utf-8"))


def make_evidence(
    text: str,
    *,
    platform: Platform | None = Platform.REDDIT,
    source_type: SourceType = SourceType.SOCIAL_POST,
    language: str | None = "en",
    published_at: datetime | None = BASE_DAY,
    engagement: dict[str, int] | None = None,
    snippet_only: bool = False,
    provider: str = "fixture",
    url: str | None = None,
    metadata: dict[str, Any] | None = None,
    run_id: str = RUN_ID,
    task_id: str = TASK_ID,
    batch_id: str = "b-placeholder",
) -> EvidenceItem:
    key = f"{platform.value if platform else 'none'}|{content_hash(text)}"
    return EvidenceItem(
        id=evidence_id(key),
        run_id=run_id,
        task_id=task_id,
        batch_id=batch_id,
        source_type=source_type,
        platform=platform,
        url=url or f"https://example.test/{evidence_id(key)}",
        author="user_" + evidence_id(key)[:4],
        text=text,
        language=language,
        published_at=published_at,
        collected_at=BASE_DAY + timedelta(days=90),
        engagement=engagement or {},
        snippet_only=snippet_only,
        provider=provider,
        content_hash=content_hash(text),
        metadata=metadata or {},
    )


ARABIC_PHRASES = [
    "الدعم الفني بطيء جدا والأسعار مرتفعة",
    "التطبيق ممتاز لكن ينقصه التصدير إلى إكسل",
    "أحتاج ميزة تتبع الدخل غير المنتظم",
    "الخدمة سريعة وسهلة الاستخدام",
    "المنافس أرخص لكن جودته أقل",
]
ENGLISH_PHRASES = [
    "The support team is slow to reply and pricing keeps going up",
    "Great product but exporting to Excel is missing",
    "I need irregular income tracking for freelance work",
    "Fast, simple and easy to use",
    "The competitor is cheaper but the quality is worse",
]

# (platform, source_type, language, count): 40 items in total.
SYNTHETIC_PLAN: list[tuple[Platform | None, SourceType, str, int]] = [
    (Platform.REDDIT, SourceType.SOCIAL_POST, "en", 6),
    (Platform.REDDIT, SourceType.SOCIAL_COMMENT, "en", 4),
    (Platform.X, SourceType.SOCIAL_POST, "en", 4),
    (Platform.X, SourceType.SOCIAL_POST, "ar", 4),
    (Platform.YOUTUBE, SourceType.SOCIAL_COMMENT, "en", 3),
    (Platform.YOUTUBE, SourceType.SOCIAL_COMMENT, "ar", 3),
    (Platform.TIKTOK, SourceType.SOCIAL_POST, "ar", 3),
    (Platform.INSTAGRAM, SourceType.SOCIAL_POST, "en", 2),
    (Platform.FACEBOOK, SourceType.SOCIAL_POST, "ar", 3),
    (None, SourceType.NEWS, "en", 3),
    (None, SourceType.NEWS, "ar", 2),
    (None, SourceType.REVIEW, "en", 3),
]


def synthetic_evidence(run_id: str = RUN_ID, task_id: str = TASK_ID) -> list[EvidenceItem]:
    """Forty distinct items over platforms and languages, dated one day apart from BASE_DAY."""
    items: list[EvidenceItem] = []
    for platform, source_type, language, count in SYNTHETIC_PLAN:
        phrases = ARABIC_PHRASES if language == "ar" else ENGLISH_PHRASES
        for _ in range(count):
            n = len(items)
            engagement = {"likes": (n * 7) % 50, "replies": n % 5, "views": n * 100}
            if source_type is SourceType.REVIEW:
                engagement = {"rating": 1 + n % 5}
            items.append(
                make_evidence(
                    f"{phrases[n % len(phrases)]} #{n}",
                    platform=platform,
                    source_type=source_type,
                    language=language,
                    published_at=BASE_DAY + timedelta(days=n),
                    engagement=engagement,
                    snippet_only=source_type is SourceType.NEWS,
                    provider="serper" if source_type is SourceType.NEWS else "fixture",
                    run_id=run_id,
                    task_id=task_id,
                )
            )
    return items


def make_result(
    brief: TaskBrief | None = None, *, headline: str = "Evidence collected.", **overrides: Any
) -> CustomerTrendsResult:
    brief = brief or load_brief()
    fields: dict[str, Any] = {
        "task_id": brief.task_id,
        "run_id": brief.run_id,
        "brief": brief,
        "evidence_ref": EvidenceRef(store_path="data/evidence.db", run_id=brief.run_id, count=0),
        "control_summary": ControlSummary(
            status="partial",
            headline=headline,
            overall_confidence="low",
            findings_count=0,
            evidence_count=0,
        ),
        "created_at": BASE_DAY,
    }
    fields.update(overrides)
    return CustomerTrendsResult(**fields)
