"""Figures mean what they say: ranges, labels, our own price, who a figure belongs to, and headlines."""

from __future__ import annotations

from edrak.briefing.extract import Candidate, to_observation
from edrak.briefing.propose import render_text
from edrak.contracts import Evidence
from edrak.contracts.evidence import SourceType
from edrak.metrics.numbers import parse_numbers


def _ev(text: str, url: str = "https://blog.example.com/x", st: SourceType = SourceType.WEB_PAGE, title: str = "") -> Evidence:
    return Evidence(evidence_id="e1", source_type=st, source_url=url, source_title=title, extracted_fact="x", page_text=text)


def test_a_range_scale_applies_to_both_ends():
    assert parse_numbers("AMD grew to ~$7–8 billion in AI GPU revenue")[0].value == 7e9
    assert parse_numbers("margin of 70-73%")[0].unit == "%"


def test_two_products_from_one_price_list_get_their_own_labels():
    text = "| H200 GPU | Enterprise / Cloud | $25,000 - $30,000 | 70-73% |"
    ev = _ev(text, url="internal://doc.md", st=SourceType.INTERNAL_DOCUMENT)
    obs = to_observation(Candidate(metric_id="own_price", entity="NVIDIA", evidence_id="e1", source_span=text), ev, allowed={"own_price"})
    assert obs is not None and obs.label == "H200" and obs.value == 25000


def test_our_price_never_comes_from_a_third_party_page():
    text = "The price for a single H100 80GB GPU typically ranges from $30,000 to over $40,000."
    c = Candidate(metric_id="own_price", entity="NVIDIA", evidence_id="e1", source_span=text)
    assert to_observation(c, _ev(text), allowed={"own_price"}) is None


def test_a_table_cell_that_does_not_name_the_competitor_is_not_its_figure():
    text = "GYGO colocation TCO* $0.85–1.20 $1.10–1.60 $0.65–0.95"
    c = Candidate(metric_id="unit_price", entity="AMD", evidence_id="e1", source_span=text)
    assert to_observation(c, _ev(text, title="H100 vs MI300X vs GB200: 2026 Price and Benchmark Tables"), allowed={"unit_price"}) is None
    named = "AMD lists the MI300X at $15,000 per unit."
    assert to_observation(Candidate(metric_id="unit_price", entity="AMD", evidence_id="e1", source_span=named),
                          _ev(named), allowed={"unit_price"}) is not None


def test_a_win_rate_keeps_its_deal_count():
    text = "AMD | 42 | 71% | Price per GPU, memory capacity"
    ev = _ev(text, url="internal://doc.md", st=SourceType.INTERNAL_DOCUMENT)
    obs = to_observation(Candidate(metric_id="win_loss", entity="NVIDIA", evidence_id="e1", source_span=text, label="vs AMD",
                                   sample_size=42), ev, allowed={"win_loss"})
    assert obs is not None and obs.sample_size == 42 and obs.label == "vs AMD" and obs.value == 71


def test_headlines_reject_status_words_and_figures_credited_to_the_wrong_company():
    metrics = {"a" * 32: {"entity": "AMD", "value": 0.65, "unit": "USD"}}
    names = ["NVIDIA", "AMD", "Intel"]
    assert render_text("NVIDIA's unit price {m:" + "a" * 32 + "} leads", metrics, names) is None
    assert render_text("AMD's figure stays thin {m:" + "a" * 32 + "}", metrics, names) is None
    assert render_text("AMD prices below NVIDIA at {m:" + "a" * 32 + "}", metrics, names) is not None
