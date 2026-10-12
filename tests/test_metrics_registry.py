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
