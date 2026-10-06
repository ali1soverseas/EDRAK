from pathlib import Path
from typing import Any

import pytest

from edrak.agents.customer_trends.schemas.common import (
    Confidence,
    Platform,
    SourceType,
    ToolStatus,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools import compute_metrics, submit_findings
from edrak.agents.customer_trends.tools.base import ToolContext, invoke_tool
from edrak.agents.customer_trends.tools.submit_findings import (
    DOWNGRADE_CAVEAT,
    claim_numbers,
    load_verdict_phrases,
    matches,
    verdict_phrases_in,
)
from tests.customer_trends.factories import RUN_ID, TASK_ID, make_evidence
from tests.customer_trends.tool_helpers import processing_context


def finding(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "f1",
        "type": "pain_point",
        "claim": "Slow support is raised in 34.5% of analyzed posts.",
        "confidence": "medium",
        "evidence_ids": [],
        "metrics": {"share_pct": 34.5},
    }
    return {**base, **overrides}


async def submit(ctx: ToolContext, *findings: dict[str, Any]) -> Any:
    return await invoke_tool(ctx, submit_findings.SPEC, {"findings": list(findings)})


def ids_of(store: EvidenceStore, **filters: Any) -> list[str]:
    found = store.query(RUN_ID, EvidenceFilters(**filters), limit=100)
    return [item.id for item in found.items]


@pytest.fixture
def ctx(loaded_store: EvidenceStore) -> ToolContext:
    return processing_context(loaded_store)


@pytest.fixture
def two_ids(loaded_store: EvidenceStore) -> list[str]:
    return ids_of(loaded_store)[:2]


async def test_a_sound_finding_is_accepted_and_stored(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    response = await submit(ctx, finding(evidence_ids=two_ids, caveats=["Reddit only"]))
    assert response.status is ToolStatus.OK and response.count == 1
    assert (response.accepted, response.rejected, response.adjusted) == (["f1"], [], [])
    [stored] = loaded_store.get_findings(RUN_ID)
    assert stored.claim.startswith("Slow support") and stored.confidence is Confidence.MEDIUM
    assert stored.evidence_ids == two_ids and stored.caveats == ["Reddit only"]


async def test_evidence_that_is_not_stored_rejects_the_finding(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    ghosts = [f"{n:016x}" for n in range(1, 8)]
    response = await submit(ctx, finding(evidence_ids=[two_ids[0], *ghosts]))
    assert response.status is ToolStatus.PARTIAL and response.accepted == []
    [rejected] = response.rejected
    assert rejected.id == "f1"
    assert rejected.reasons == [
        "evidence ids not found in this run: "
        + ", ".join(ghosts[:5])
        + " (and 2 more); use ids returned by evidence_query"
    ]
    assert loaded_store.get_findings(RUN_ID) == []


async def test_evidence_of_another_run_does_not_count(
    ctx: ToolContext, loaded_store: EvidenceStore
) -> None:
    loaded_store.create_run("other-run", "t")
    other = make_evidence("only in another run", run_id="other-run")
    loaded_store.add_batch("other-run", "t", "test", [other])
    response = await submit(ctx, finding(evidence_ids=[other.id]))
    assert "not found in this run" in response.rejected[0].reasons[0]


async def test_a_finding_without_evidence_is_rejected(ctx: ToolContext) -> None:
    response = await submit(ctx, finding())
    assert response.rejected[0].reasons == [
        "evidence_ids is empty: cite the stored items that support the claim"
    ]


async def test_a_number_that_is_not_in_the_metrics_is_rejected_with_a_readable_reason(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    response = await submit(
        ctx,
        finding(
            claim="Slow support is raised in 45% of the 120 analyzed posts.",
            evidence_ids=two_ids,
            metrics={"share_pct": 34.5, "posts": 120},
        ),
    )
    assert response.accepted == []
    assert response.rejected[0].reasons == [
        "numbers in the claim are not in metrics: 45; copy each into metrics as written "
        "(45% is 45), or cite a compute_metrics result as metric_id"
    ]
    assert loaded_store.get_findings(RUN_ID) == []


async def test_a_claim_with_numbers_and_no_metrics_at_all_is_rejected(
    ctx: ToolContext, two_ids: list[str]
) -> None:
    response = await submit(
        ctx, finding(claim="About 12 posts and 3.5 percent.", evidence_ids=two_ids, metrics={})
    )
    assert "not in metrics: 12, 3.5" in response.rejected[0].reasons[0]


async def test_a_claim_without_numbers_needs_no_metrics(
    ctx: ToolContext, two_ids: list[str]
) -> None:
    response = await submit(
        ctx, finding(claim="Users complain about slow replies.", evidence_ids=two_ids, metrics={})
    )
    assert response.accepted == ["f1"]


@pytest.mark.parametrize(
    ("claim", "known", "ok"),
    [
        (12, 12.4, True),
        (12, 12.6, False),
        (1000, 1009, True),
        (1000, 1011, False),
        (0.3, 0.7, False),
        (0.35, 0.3512, True),
        (0.35, 0.37, False),
        (34.5, 34.52, True),
        (34.5, 34.7, True),
        (34.5, 35.0, False),
        (5, 5.5, True),
        (-20, -20, True),
        (-20, 20, False),
        (0, 0.4, True),
        (0, 3, False),
    ],
)
def test_numbers_match_within_one_percent_or_the_rounding_the_claim_shows(
    claim: float, known: float, ok: bool
) -> None:
    assert matches(float(claim), known) is ok


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("In 2026 the share was 34.5%", [34.5]),
        ("Since 1999 and until 2100", []),
        ("Interest rose 1,200 times in 1800", [1200.0, 1800.0]),
        ("2,500 mentions", [2500.0]),
        ("٣٤ posts in Q3 and B2B", [34.0]),
        ("down 20% and up 3.5x", [20.0, 3.5]),
    ],
)
def test_claim_numbers_skip_years_and_identifiers(claim: str, expected: list[float]) -> None:
    assert claim_numbers(claim) == expected


async def test_numbers_may_be_written_in_text_values_and_arabic_digits(
    ctx: ToolContext, two_ids: list[str]
) -> None:
    response = await submit(
        ctx,
        finding(
            id="f1",
            claim="Between 10 and 20 posts raise it.",
            evidence_ids=two_ids,
            metrics={"range": "10 to 20"},
        ),
        finding(
            id="f2",
            claim="٣٤ posts raise it.",
            evidence_ids=two_ids,
            metrics={"posts": 34},
        ),
    )
    assert response.accepted == ["f1", "f2"]


async def test_numbers_may_come_from_a_stored_metric(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    metric = await invoke_tool(ctx, compute_metrics.SPEC, {"metric": "platform_mix"})
    claim = "Reddit holds 25% of the 40 items and news 12.5%."
    ok = await submit(
        ctx, finding(claim=claim, evidence_ids=two_ids, metrics={"metric_id": metric.metric_id})
    )
    assert ok.accepted == ["f1"]
    wrong = await submit(
        ctx,
        finding(
            id="f2",
            claim="Reddit holds 26% of the items.",
            evidence_ids=two_ids,
            metrics={"metric_id": metric.metric_id},
        ),
    )
    assert "not in metrics: 26" in wrong.rejected[0].reasons[0]
    other_key = await submit(
        ctx,
        finding(
            id="f3",
            claim="Reddit holds 25% of the items.",
            evidence_ids=two_ids,
            metrics={"platforms_metric_id": metric.metric_id},
        ),
    )
    assert other_key.accepted == ["f3"]


async def test_the_digits_of_a_metric_id_are_not_numbers_a_claim_can_use(
    ctx: ToolContext, two_ids: list[str]
) -> None:
    metric = await invoke_tool(ctx, compute_metrics.SPEC, {"metric": "platform_mix"})
    digits = "".join(c for c in metric.metric_id if c.isdigit())
    assert digits
    response = await submit(
        ctx,
        finding(
            claim=f"There were {int(digits)} posts.",
            evidence_ids=two_ids,
            metrics={"metric_id": metric.metric_id},
        ),
    )
    assert "not in metrics" in response.rejected[0].reasons[0]


async def test_a_metric_id_that_does_not_exist_is_rejected(
    ctx: ToolContext, two_ids: list[str]
) -> None:
    response = await submit(
        ctx,
        finding(
            claim="Support is a common complaint.",
            evidence_ids=two_ids,
            metrics={"metric_id": "m_000000000000"},
        ),
    )
    assert response.rejected[0].reasons == ["metric_id 'm_000000000000' does not exist in this run"]


@pytest.mark.parametrize(
    "claim",
    [
        "GitLab should enter the Egyptian market.",
        "GITLAB SHOULD ENTER THE EGYPTIAN MARKET.",
        "Given this, do not launch the feature yet.",
        "We recommend launching a pilot.",
        "نوصي بإطلاق المنتج",
        "نوصي باطلاق المنتج",
    ],
)
async def test_verdict_language_is_rejected(
    ctx: ToolContext, two_ids: list[str], claim: str
) -> None:
    response = await submit(ctx, finding(claim=claim, evidence_ids=two_ids, metrics={}))
    [reason] = response.rejected[0].reasons
    assert reason.startswith("the claim contains verdict language (")
    assert reason.endswith("state what the evidence shows and leave the decision to the reader")


@pytest.mark.parametrize(
    "claim",
    [
        "Users recommend the support team to colleagues.",
        "The competitor entered the market in the spring.",
        "Several posts say the launch was delayed.",
        "Some users said they would not launch it without SSO.",
    ],
)
async def test_descriptive_claims_that_sound_similar_pass(
    ctx: ToolContext, two_ids: list[str], claim: str
) -> None:
    response = await submit(ctx, finding(claim=claim, evidence_ids=two_ids, metrics={}))
    assert response.accepted == ["f1"]


async def test_the_phrase_list_is_a_config_file(
    ctx: ToolContext,
    two_ids: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = tmp_path / "phrases.yaml"
    custom.write_text("phrases:\n  - must buy\n", encoding="utf-8")
    monkeypatch.setattr(submit_findings, "VERDICT_PHRASES_PATH", custom)
    blocked = await submit(
        ctx, finding(claim="Buyers must buy now.", evidence_ids=two_ids, metrics={})
    )
    assert (
        blocked.rejected[0]
        .reasons[0]
        .startswith("the claim contains verdict language ('must buy')")
    )
    allowed = await submit(
        ctx, finding(id="f2", claim="GitLab should enter.", evidence_ids=two_ids, metrics={})
    )
    assert allowed.accepted == ["f2"]


def test_the_shipped_phrase_list_has_english_and_arabic_entries() -> None:
    phrases = load_verdict_phrases()
    assert len(phrases) == len(set(phrases)) and all(p.strip() for p in phrases)
    assert {"should enter", "do not launch"} <= set(phrases)
    assert any(any("؀" <= c <= "ۿ" for c in p) for p in phrases)
    assert not any(chr(0x2014) in p for p in phrases)
    assert verdict_phrases_in("Do NOT launch it", phrases) == ["do not launch"]


async def test_every_problem_of_a_finding_is_listed(ctx: ToolContext, two_ids: list[str]) -> None:
    response = await submit(
        ctx,
        finding(
            claim="GitLab should enter, as 77% of posts agree.",
            evidence_ids=[two_ids[0], "f" * 16],
            metrics={},
        ),
    )
    reasons = response.rejected[0].reasons
    assert len(reasons) == 3
    assert reasons[0].startswith("evidence ids not found")
    assert reasons[1].startswith("numbers in the claim are not in metrics: 77")
    assert reasons[2].startswith("the claim contains verdict language")


def high_evidence(
    count: int,
    platform: Platform | None = Platform.REDDIT,
    source_type: SourceType = SourceType.SOCIAL_POST,
) -> list[EvidenceItem]:
    return [
        make_evidence(
            f"evidence number {n} about support", platform=platform, source_type=source_type
        )
        for n in range(count)
    ]


async def test_high_confidence_needs_ten_ids_and_two_platforms_or_source_types(
    store: EvidenceStore,
) -> None:
    store.create_run(RUN_ID, TASK_ID)
    spread = [
        *high_evidence(6),
        *[make_evidence(f"x post {n}", platform=Platform.X) for n in range(6)],
    ]
    one_platform = high_evidence(12)
    two_types = [
        *high_evidence(5),
        *[make_evidence(f"comment {n}", source_type=SourceType.SOCIAL_COMMENT) for n in range(5)],
    ]
    for items in (spread, one_platform, two_types):
        store.add_batch(RUN_ID, TASK_ID, "test", items)
    ctx = processing_context(store)

    def high(name: str, items: list[EvidenceItem]) -> dict[str, Any]:
        return finding(
            id=name,
            claim="Support is a common complaint.",
            confidence="high",
            evidence_ids=[i.id for i in items],
            metrics={},
        )

    response = await submit(
        ctx,
        high("spread", spread),
        high("one_platform", one_platform),
        high("two_types", two_types),
        high("nine", spread[:9]),
    )
    assert response.accepted == ["spread", "one_platform", "two_types", "nine"]
    stored = {f.id: f for f in store.get_findings(RUN_ID)}
    assert stored["spread"].confidence is Confidence.HIGH
    assert stored["two_types"].confidence is Confidence.HIGH
    assert stored["one_platform"].confidence is Confidence.MEDIUM
    assert stored["nine"].confidence is Confidence.MEDIUM
    assert stored["one_platform"].caveats == [DOWNGRADE_CAVEAT]
    assert [(a.id, a.confidence, a.reasons) for a in response.adjusted] == [
        ("one_platform", Confidence.MEDIUM, [DOWNGRADE_CAVEAT]),
        ("nine", Confidence.MEDIUM, [DOWNGRADE_CAVEAT]),
    ]


async def test_a_single_evidence_id_means_low_confidence_and_a_caveat(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    response = await submit(
        ctx,
        finding(confidence="high", evidence_ids=[two_ids[0], two_ids[0]], metrics={})
        | {"claim": "Support is a common complaint."},
    )
    assert response.accepted == ["f1"]
    [stored] = loaded_store.get_findings(RUN_ID)
    assert stored.confidence is Confidence.LOW and "single_source" in stored.caveats
    assert stored.evidence_ids == [two_ids[0]]
    [note] = response.adjusted
    assert note.confidence is Confidence.LOW and "single_source" in note.reasons[0]


async def test_some_findings_can_be_accepted_while_others_are_not(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    response = await submit(
        ctx,
        finding(id="good", evidence_ids=two_ids),
        finding(id="bad", claim="Support is 99% bad.", evidence_ids=two_ids),
        finding(id="good", claim="A repeated id.", evidence_ids=two_ids, metrics={}),
    )
    assert response.status is ToolStatus.PARTIAL and response.count == 1
    assert response.accepted == ["good"]
    assert [r.id for r in response.rejected] == ["bad", "good"]
    assert response.rejected[1].reasons == [
        "duplicate id in this submission; every finding needs its own id"
    ]
    assert [f.id for f in loaded_store.get_findings(RUN_ID)] == ["good"]


async def test_sending_a_finding_again_replaces_it(
    ctx: ToolContext, loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    await submit(ctx, finding(evidence_ids=two_ids))
    await submit(
        ctx,
        finding(claim="Slow support is a recurring complaint.", metrics={}, evidence_ids=two_ids),
    )
    [stored] = loaded_store.get_findings(RUN_ID)
    assert stored.claim == "Slow support is a recurring complaint."


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"findings": []}, "findings"),
        ({"findings": [finding(type="rumor")]}, "findings.0.type"),
        ({"findings": [finding(confidence="certain")]}, "findings.0.confidence"),
        ({"findings": [finding(claim="")]}, "findings.0.claim"),
        ({"findings": [finding(extra_field=1)]}, "findings.0.extra_field"),
        ({"findings": [finding(id=str(n)) for n in range(21)]}, "findings"),
    ],
)
async def test_malformed_findings_are_a_readable_error(
    ctx: ToolContext, arguments: dict[str, Any], problem: str
) -> None:
    response = await invoke_tool(ctx, submit_findings.SPEC, arguments)
    assert response.status is ToolStatus.ERROR
    assert response.error_code == "invalid_input" and problem in response.gaps[0]


async def test_the_call_is_reported_as_an_event(
    loaded_store: EvidenceStore, two_ids: list[str]
) -> None:
    events: list[dict[str, Any]] = []
    ctx = processing_context(loaded_store, emit=events.append)
    await submit(ctx, finding(evidence_ids=two_ids), finding(id="f2", evidence_ids=[]))
    [event] = events
    assert event["tool"] == "submit_findings" and event["status"] == "partial"
    assert event["count"] == 1
