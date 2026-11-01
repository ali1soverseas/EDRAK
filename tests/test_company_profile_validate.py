from edrak.company_profile.models import ProfileExtraction, ProfileFields, SourceRef, SourceType
from edrak.company_profile.validate import ALLOWED_INDUSTRIES, map_industry, validate_extraction
from edrak.contracts.request import CompanyProfile
from edrak.core.config import settings

SOURCES = [
    SourceRef(id="S1", type=SourceType.WEB_PAGE, ref="https://a.test/"),
    SourceRef(id="S2", type=SourceType.PDF_PAGE, ref="p.1"),
]


def extraction(profile, field_sources):
    return ProfileExtraction(profile=ProfileFields(**profile), field_sources=field_sources)


def test_shared_field_names_still_match_the_contract():
    assert {"name", "aliases", "notes", "products"} <= set(CompanyProfile.model_fields)
    assert set(ProfileFields.model_fields) == {"name", "aliases", "notes", "products", "industry", "markets", "goals"}


def test_unknown_citations_are_dropped_and_known_ones_are_kept_once():
    out = validate_extraction(
        extraction({"name": "Acme"}, {"name": ["S9", "[s1]", "S1", "S2", "nope"]}), SOURCES
    )
    assert out.profile.name == "Acme"
    assert out.field_sources["name"] == ["S1", "S2"]
    assert "name" not in out.gaps


def test_a_field_with_only_invalid_citations_becomes_a_gap():
    out = validate_extraction(
        extraction({"name": "Acme", "products": ["Pay"]}, {"name": ["S1"], "products": ["S7"]}), SOURCES
    )
    assert out.profile.products == []
    assert "products" not in out.field_sources
    assert "products" in out.gaps
    assert any("products" in w for w in out.warnings)


def test_a_value_with_no_citation_at_all_is_dropped_too():
    out = validate_extraction(extraction({"name": "Acme", "notes": "Sells things."}, {"name": ["S1"]}), SOURCES)
    assert out.profile.notes is None
    assert "notes" in out.gaps


def test_lists_dedupe_case_insensitively_and_across_arabic_spellings():
    out = validate_extraction(
        extraction(
            {
                "name": "Acme",
                "markets": ["Egypt", "egypt", " EGYPT "],
                # alef with hamza vs plain alef, taa marbuta vs haa, diacritics, tatweel
                "products": ["مدرسة الأمل", "مدرسه الامل", "مَدرسةـ الأمل"],
                "aliases": ["Acme", "acme", "أكمي", "اكمي"],
            },
            {"name": ["S1"], "markets": ["S1"], "products": ["S1"], "aliases": ["S1"]},
        ),
        SOURCES,
    )
    assert out.profile.markets == ["Egypt"]
    assert out.profile.products == ["مدرسة الأمل"]
    assert out.profile.aliases == ["أكمي"]  # the name itself is not its own alias


def test_industry_is_mapped_to_an_allowed_value():
    assert map_industry("Fintech") == "fintech"
    assert map_industry(" SaaS ") == "saas"
    assert map_industry("health") == "healthcare"
    assert map_industry("Health care") == "other"
    assert map_industry("Space tourism") == "other"
    assert map_industry("") is None and map_industry(None) is None
    assert set(ALLOWED_INDUSTRIES) == {
        "fintech", "saas", "retail", "technology", "healthcare", "manufacturing", "logistics", "education", "other",
    }
    out = validate_extraction(extraction({"name": "Acme", "industry": "Space tourism"}, {"name": ["S1"], "industry": ["S2"]}), SOURCES)
    assert out.profile.industry == "other"


def test_empty_name_is_a_gap_and_everything_empty_is_listed():
    out = validate_extraction(extraction({"name": "   "}, {}), SOURCES)
    assert out.profile.name is None
    assert out.gaps == ["name", "aliases", "notes", "products", "industry", "markets", "goals"]
    assert out.field_sources == {}


def test_whitespace_and_length_caps_come_from_config(monkeypatch):
    monkeypatch.setattr(settings, "PROFILE_IMPORT_MAX_LIST_ITEMS", 2)
    monkeypatch.setattr(settings, "PROFILE_IMPORT_MAX_ITEM_CHARS", 10)
    monkeypatch.setattr(settings, "PROFILE_IMPORT_MAX_NOTES_CHARS", 20)
    out = validate_extraction(
        extraction(
            {"name": "Acme", "products": ["  one  ", "two", "three"], "notes": "A very   long\n sentence about the firm."},
            {"name": ["S1"], "products": ["S1"], "notes": ["S1"]},
        ),
        SOURCES,
    )
    assert out.profile.products == ["one", "two"]
    assert len(out.profile.notes) == 20 and "  " not in out.profile.notes
    out = validate_extraction(extraction({"name": "A name well over ten chars"}, {"name": ["S1"]}), SOURCES)
    assert len(out.profile.name) == 10
