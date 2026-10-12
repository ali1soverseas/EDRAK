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


def test_segments_that_do_not_sum_to_100_fall_back():
    tpl = get_registry().template("competitive_intelligence")
    rows = [_row("a" * 32, "share_of_voice", "Acme", 60.0, sample_size=100, unit="%"),
            _row("b" * 32, "share_of_voice", "Fawry", 20.0, sample_size=100, unit="%")]
    cards, _ = build_cards(rows, [], [], [], tpl, "Acme")
    assert next(c for c in cards if c["metric_id"] == "share_of_voice")["component"] == "table"


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
