import asyncio
import os
import tempfile

import pytest

from edrak.briefing.fill import build_cards
from edrak.briefing.propose import CardCopy, ProposedCopy, SignalCopy, apply_copy, render_text
from edrak.briefing.reconcile import reconcile
from edrak.contracts import Evidence, WorkerResult, WorkerStatus, WorkerType
from edrak.contracts.evidence import SourceType
from edrak.metrics.contracts import MetricObservation, MetricScope
from edrak.metrics.registry import get_registry
from tests.test_api_and_db import isolated  # noqa: F401


def _ev(eid, fact, url="https://blog.example.com/x"):
    return Evidence(evidence_id=eid, source_type=SourceType.WEB_PAGE, source_url=url, extracted_fact=fact, excerpt=fact)


def _obs(eid, text, value, entity="Fawry", scope=MetricScope.THIRD_PARTY, metric="price_per_tier", **kw):
    return MetricObservation(metric_id=metric, entity=entity, value_text=text, value=value, unit="USD", evidence_id=eid,
                             source_span=text, scope=scope, **kw)


def _result(evs, obs):
    return WorkerResult(task_id="t1", worker=WorkerType.COMPETITOR_INTELLIGENCE, status=WorkerStatus.COMPLETED, attempt=1,
                        evidence=evs, metadata={"metrics": [o.model_dump(mode="json") for o in obs]})


def test_conflicting_prices_show_a_range_with_both_sources():
    r = _result([_ev("e1", "Team plan is $19 per user", "https://a.com/p"), _ev("e2", "Team plan costs $23 per user", "https://b.com/p")],
                [_obs("e1", "$19", 19), _obs("e2", "$23", 23)])
    fixed, conflicts = reconcile([r])
    statuses = {m["evidence_id"]: m["status"] for m in fixed[0].metadata["metrics"]}
    assert statuses == {"e1": "conflict", "e2": "conflict"}
    assert "$19" in conflicts[0] and "$23" in conflicts[0]


def test_number_missing_from_its_source_is_unverified():
    r = _result([_ev("e1", "Team plan is $19 per user")], [_obs("e1", "$25", 25)])
    fixed, _ = reconcile([r])
    assert fixed[0].metadata["metrics"][0]["status"] == "unverified"


def test_official_source_is_verified_and_single_third_party_is_thin():
    r = _result([_ev("e1", "Pro is $30 per user"), _ev("e2", "Basic is $10 per user")],
                [_obs("e1", "$30", 30, scope=MetricScope.OFFICIAL), _obs("e2", "$10", 10, entity="XPay")])
    fixed, _ = reconcile([r])
    st = {m["entity"]: m["status"] for m in fixed[0].metadata["metrics"]}
    assert st == {"Fawry": "verified", "XPay": "thin"}


def test_number_lower_on_the_page_is_grounded_by_page_text():
    page = "Pricing overview. " + "Filler text. " * 400 + "Team plan is $19 per user per month."
    ev = Evidence(evidence_id="e1", source_type=SourceType.PRICING_PAGE, source_url="https://a.com/pricing",
                  extracted_fact="Pricing overview.", excerpt=page[:1500], page_text=page)
    fixed, _ = reconcile([_result([ev], [_obs("e1", "$19", 19, scope=MetricScope.OFFICIAL)])])
    assert fixed[0].metadata["metrics"][0]["status"] == "verified"


def test_page_text_is_capped():
    from edrak.contracts.evidence import PAGE_TEXT_LIMIT
    ev = Evidence(source_type=SourceType.WEB_PAGE, extracted_fact="x", page_text="a" * (PAGE_TEXT_LIMIT + 50))
    assert len(ev.page_text) == PAGE_TEXT_LIMIT


def _row(uid, metric, entity, value, status="verified", **kw):
    base = dict(uid=uid, metric_id=metric, entity=entity, value=value, value_text=f"${value}", unit="USD", period=None, label=None,
                status=status, scope="official", sample_size=None, derived=0, formula=None, source_span="", display_no=1,
                inputs=[], worker="competitor_intelligence", evidence_uid="x")
    base.update(kw)
    return base


def test_missing_required_metric_is_a_gap_card_never_a_blank():
    tpl = get_registry().template("competitive_intelligence")
    cards, gaps = build_cards([], [], [], [], tpl, "Acme")
    gap_ids = {c["metric_id"] for c in cards if c["component"] == "gap"}
    assert set(tpl.required) <= gap_ids
    assert all(c["slots"]["reason"] for c in cards)


def test_sentiment_below_minimum_sample_falls_back_to_a_table():
    tpl = get_registry().template("competitive_intelligence")
    rows = [_row("a" * 32, "sentiment_split", "Acme", 60.0, label="positive", sample_size=12, unit="%"),
            _row("b" * 32, "sentiment_split", "Acme", 40.0, label="negative", sample_size=12, unit="%")]
    cards, _ = build_cards(rows, [], [], [], tpl, "Acme")
    c = next(c for c in cards if c["metric_id"] == "sentiment_split")
    assert c["component"] == "table" and "fewer than 30" in c["caveat"]


def test_partial_share_shows_others_and_unknown_and_is_labelled_incomplete():
    tpl = get_registry().template("competitive_intelligence")
    rows = [_row("a" * 32, "share_of_voice", "Acme", 60.0, sample_size=100, unit="%"),
            _row("b" * 32, "share_of_voice", "Fawry", 20.0, sample_size=100, unit="%")]
    cards, _ = build_cards(rows, [], [], [], tpl, "Acme")
    c = next(c for c in cards if c["metric_id"] == "share_of_voice")
    assert c["component"] == "segmented" and c["slots"]["incomplete"] is True
    parts = {p["label"]: p["value"] for p in c["slots"]["parts"]}
    assert parts == {"Acme": 60.0, "Fawry": 20.0, "Others and unknown": 20.0}
    assert "Incomplete" in c["caveat"]


def test_parts_over_100_fall_back_to_a_table():
    tpl = get_registry().template("competitive_intelligence")
    rows = [_row("a" * 32, "share_of_voice", "Acme", 70.0, sample_size=100, unit="%"),
            _row("b" * 32, "share_of_voice", "Fawry", 50.0, sample_size=100, unit="%")]
    cards, _ = build_cards(rows, [], [], [], tpl, "Acme")
    assert next(c for c in cards if c["metric_id"] == "share_of_voice")["component"] == "table"


def test_a_single_found_share_is_a_kpi_card_not_a_pie():
    tpl = get_registry().template("competitive_intelligence")
    cards, _ = build_cards([_row("a" * 32, "share_of_voice", "Acme", 60.0, sample_size=100, unit="%")], [], [], [], tpl, "Acme")
    c = next(c for c in cards if c["metric_id"] == "share_of_voice")
    assert c["component"] == "kpi" and c["slots"]["value"]


def test_prices_for_different_periods_are_not_charted_together():
    tpl = get_registry().template("competitive_intelligence")
    rows = [_row("a" * 32, "price_per_tier", "Fawry", 19, period="2025"), _row("b" * 32, "price_per_tier", "XPay", 23, period="2026")]
    cards, _ = build_cards(rows, [], [], [], tpl, "Acme")
    c = next(c for c in cards if c["metric_id"] == "price_per_tier")
    assert c["component"] == "table" and "different periods" in c["caveat"]


def test_kpi_strip_changes_with_the_use_case():
    from edrak.briefing.layout import kpi_strip
    rows = [_row("a" * 32, "market_size", "Egypt", 5e9), _row("b" * 32, "price_per_tier", "Fawry", 19)]
    ci, me = get_registry().template("competitive_intelligence"), get_registry().template("market_entry_expansion")
    a = [k["metric_id"] for k in kpi_strip(build_cards(rows, [], [], [], ci, "Acme")[0], ci)]
    b = [k["metric_id"] for k in kpi_strip(build_cards(rows, [], [], [], me, "Acme")[0], me)]
    assert a != b and a[0] == "price_per_tier" and b[0] == "market_size"


def test_model_text_with_a_bare_number_is_rejected_and_tokens_render_from_the_table():
    uid = "c" * 32
    metrics = {uid: {"value": 19, "unit": "USD"}}
    assert render_text(f"Fawry charges {{m:{uid}}} per seat", metrics, ["Fawry"]) == "Fawry charges $19 per seat"
    assert render_text("Fawry charges $19 per seat", metrics, ["Fawry"]) is None
    assert render_text("Fawry charges {m:" + "d" * 32 + "}", metrics, ["Fawry"]) is None
    assert render_text("Prices are higher", metrics, ["Fawry"], need_entity=True) is None


def test_generic_why_it_matters_is_dropped():
    sig = [{"signal_id": "s1", "implications": []}]
    cards = []
    copy = ProposedCopy(headline="Fawry leads on price", cards=[], signals=[SignalCopy(signal_id="s1", why_it_matters="This is important.")])
    headline, _ = apply_copy(copy, cards, sig, {}, ["Fawry"])
    assert sig[0]["why_it_matters"] is None and headline == "Fawry leads on price"


def test_every_number_on_the_dashboard_resolves_to_evidence(isolated, monkeypatch):
    """Workers -> reconcile -> stored metrics -> typed brief: each figure traces to an evidence number."""
    from edrak.api.run_manager import RunManager
    from edrak.briefing.build import build_v3
    from edrak.contracts import BusinessContext, BusinessRequest, CompanyProfile, UseCase
    from edrak.db.repositories import results as results_repo
    from edrak.guardrails.output import check_brief
    from tests.test_api_and_db import _seed_run

    monkeypatch.setattr("edrak.briefing.build.propose_copy", lambda *a, **k: None)
    ws, request_id, task_id = _seed_run(isolated / "edrak.db")
    evs = [_ev("e1", "Team plan is $19 per user", "https://fawry.com/pricing"), _ev("e2", "Team plan costs $23 per user", "https://blog.example.com/p")]
    wr = WorkerResult(task_id=task_id, worker=WorkerType.COMPETITOR_INTELLIGENCE, status=WorkerStatus.COMPLETED, attempt=1, evidence=evs,
                      metadata={"metrics": [_obs("e1", "$19", 19, scope=MetricScope.OFFICIAL).model_dump(mode="json"),
                                            _obs("e2", "$23", 23).model_dump(mode="json")]})
    wr, conflicts = reconcile([wr])[0][0], reconcile([wr])[1]
    request = BusinessRequest(request_id=request_id, goal="Compare us with Fawry", company_profile=CompanyProfile(name="Acme"),
                              business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE, targets=["Fawry"]))

    async def go():
        from edrak.db import connection
        async with connection.get_db() as db:
            await results_repo.write_worker_result(db, request_id, wr)
            await RunManager._store_metrics(db, request_id, wr)
            await db.commit()
            numbers = await results_repo.evidence_numbers(db, request_id)
            from edrak.briefing.derive import derive, record_conflicts
            await derive(db, request_id, "Acme")
            await record_conflicts(db, request_id)
            return await build_v3(db, request, [wr], None, numbers)

    v3 = asyncio.run(go())
    ladder = v3["cards"]["price_per_tier"]
    row = ladder["slots"]["rows"][0]
    assert ladder["component"] == "ladder" and row["status"] == "conflict" and row["range"]["low"] == "$19" and row["range"]["high"] == "$23"
    assert v3["conflicts"] and "price_per_tier" in {c["metric_id"] for c in v3["conflicts"]}
    gap_ids = {c["metric_id"] for c in v3["cards"].values() if c["component"] == "gap"}
    assert {"paid_subscribers", "own_price"} <= gap_ids                       # required figures nobody found are gap cards
    assert v3["coverage"]["score"] is not None and v3["coverage"]["score"] < 1
    for uid, m in v3["metrics"].items():                                    # every shown figure traces to evidence
        assert m["display_no"] is not None, uid
    doc = check_brief({**v3, "evidence": [], "lenses": [], "options": []})
    assert not [c for c in doc["guardrails"]["checks"] if c["check"] in ("card_number_not_in_table", "uncited_card")]


def test_headline_claiming_a_dimension_with_no_figure_is_rejected():
    from edrak.briefing.propose import unmet_dimensions
    signals = [{"title": "Broadcom launches a low-power ASIC", "signal": "Broadcom ships an inference chip", "interpretation": ""}]
    unmet = unmet_dimensions(set(), ["price_per_tier", "sentiment_split", "paid_subscribers"], signals)
    assert unmet == ["price", "customer voice", "customer base"]
    copy = ProposedCopy(headline="Nvidia trails on price and hears mixed customer sentiment as Broadcom ships", cards=[], signals=[])
    headline, _ = apply_copy(copy, [], [], {}, ["Nvidia", "Broadcom"], unmet)
    assert headline is None
    ok = ProposedCopy(headline="Broadcom's low-power ASIC puts Nvidia's inference lead under pressure", cards=[], signals=[])
    assert apply_copy(ok, [], [], {}, ["Nvidia", "Broadcom"], unmet)[0]


def test_a_signal_that_mentions_the_dimension_grounds_it():
    from edrak.briefing.propose import unmet_dimensions
    signals = [{"title": "Fawry cuts its pricing for merchants", "signal": "", "interpretation": ""}]
    assert "price" not in unmet_dimensions(set(), ["price_per_tier"], signals)


def test_why_it_matters_that_repeats_the_implication_is_replaced():
    sig = {"signal_id": "s1", "title": "Intel launches a new GPU", "signal": "Intel's new GPU targets inference",
           "interpretation": "Inference buyers get a cheaper second supplier", "implications": ["Nvidia faces heightened competitive pressure from Intel"]}
    copy = ProposedCopy(headline="x", cards=[], signals=[SignalCopy(signal_id="s1", why_it_matters="Nvidia faces heightened competitive pressure from Intel")])
    apply_copy(copy, [], [sig], {}, ["Nvidia", "Intel"])
    assert sig["why_it_matters"] == "Inference buyers get a cheaper second supplier"


def test_signals_follow_use_case_emphasis_drop_duplicates_and_show_conflicts_first():
    from edrak.briefing.build import conflict_signals, rank_signals
    tpl = get_registry().template("competitive_intelligence")
    sigs = [{"signal_id": "m", "title": "Market demand is rising fast", "domains": ["market_intelligence"], "confidence": 0.9},
            {"signal_id": "c", "title": "Broadcom launches an inference chip", "domains": ["competitor_intelligence"], "confidence": 0.6},
            {"signal_id": "c2", "title": "Broadcom launches an inference chip now", "domains": ["competitor_intelligence"], "confidence": 0.5}]
    rows = {"a": {"uid": "a", "value": 19.0, "unit": "USD", "display_no": 3}, "b": {"uid": "b", "value": 23.0, "unit": "USD", "display_no": 7}}
    conf = conflict_signals([{"metric_id": "price_per_tier", "entity": "Fawry", "period": None, "low": 19.0, "high": 23.0, "metric_uids": ["a", "b"]}], rows)
    ranked = rank_signals([*conf, *sigs], tpl)
    assert [s["signal_id"] for s in ranked] == ["conflict:price_per_tier:fawry", "c", "m"]
    assert ranked[0]["refs"] == [3, 7] and len(ranked[0]["sides"]) == 2


def test_impact_and_feasibility_are_never_copied_from_confidence():
    from types import SimpleNamespace

    from edrak.DecisionAnalysis.nodes import _levels_for
    from edrak.contracts.DecisionAnalysis import AssessmentLevel
    sig = {"s1": {"signal_id": "s1", "confidence": 0.95, "urgency": "high", "decision_relevance": "risk_relevant"}}
    fit, impact, urgency, feas, likelihood = _levels_for(["s1"], sig, SimpleNamespace(confidence="high", impact="unknown", feasibility="unknown"))
    assert impact is feas is likelihood is AssessmentLevel.UNKNOWN and urgency is AssessmentLevel.HIGH
    _, impact, _, feas, _ = _levels_for(["s1"], sig, SimpleNamespace(impact="low", feasibility="high"))
    assert (impact, feas) == (AssessmentLevel.LOW, AssessmentLevel.HIGH)
