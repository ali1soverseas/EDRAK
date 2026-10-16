from edrak.metrics.numbers import appears_in, parse_value, span_in_source
from edrak.metrics.registry import get_registry


def test_parse_numbers():
    assert parse_value("$19 per user").value == 19 and parse_value("$19 per user").unit == "USD"
    assert parse_value("1,250,000 customers").value == 1_250_000
    assert parse_value("raised $4.5M").value == 4_500_000
    assert parse_value("growing 12.5%").unit == "%"
    assert parse_value("٢٣ دولار").value == 23


def test_appears_in_and_span():
    assert appears_in("$19", "Team plan costs $19.00 per month")
    assert not appears_in("$23", "Team plan costs $19 per month")
    assert span_in_source("costs $19  per month", "Team plan Costs $19 per month.")


def test_registry_and_templates():
    reg = get_registry()
    assert reg.match("pricing").id == "price_per_tier"
    ci, me = reg.template("competitive_intelligence"), reg.template("market_entry_expansion")
    assert ci.kpi_strip != me.kpi_strip
    assert len(ci.kpi_strip) == 12


def _task(reqs):
    from edrak.contracts import BusinessContext, CompanyProfile, ResearchTask, WorkerType
    return ResearchTask(parent_request_id="r", worker=WorkerType.COMPETITOR_INTELLIGENCE, goal="g", focus="f",
                        company_profile=CompanyProfile(name="Acme"), business_context=BusinessContext(use_case="competitive_intelligence"), requirements=reqs)


def _result(task, obs, evidence=()):
    from edrak.contracts import WorkerResult, WorkerStatus
    return WorkerResult(task_id=task.task_id, worker=task.worker, status=WorkerStatus.PARTIAL, attempt=1,
                        metadata={"metrics": [o.model_dump(mode="json") for o in obs]})


def test_requirements_planned_per_worker():
    from edrak.metrics.planning import requirements_for
    reqs = requirements_for("competitor_intelligence", "competitive_intelligence", "Acme", ["Fawry", "XPay"], [])
    assert reqs and all(r.entity in ("Fawry", "XPay") for r in reqs)
    assert reqs[0].required  # required metrics come first


def test_third_party_price_does_not_fulfil_an_official_only_metric():
    from edrak.metrics.contracts import MetricObservation, MetricRequirement, MetricScope, OutcomeStatus
    from edrak.metrics.requirements import check
    req = MetricRequirement(metric_id="price_per_tier", entity="Fawry")
    task = _task([req])
    obs = MetricObservation(metric_id="price_per_tier", entity="Fawry", value_text="$19", value=19, evidence_id="e1",
                            source_span="$19", scope=MetricScope.THIRD_PARTY)
    out = check(task, _result(task, [obs]))[0]
    assert out.status is OutcomeStatus.RETRY and out.reason.value == "only_third_party_source"
    obs2 = obs.model_copy(update={"scope": MetricScope.OFFICIAL})
    assert check(task, _result(task, [obs2]))[0].status is OutcomeStatus.FOUND
    assert check(task, _result(task, []))[0].reason.value == "not_found"
