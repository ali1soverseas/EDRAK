import pytest

from edrak.agents.customer_trends.relevance.models import Comment
from edrak.agents.customer_trends.relevance.normalize import normalize
from edrak.agents.customer_trends.relevance.rules import (
    is_character_spam,
    stage_a,
    standalone_verdict,
    too_short,
    word_count,
)
from edrak.agents.customer_trends.relevance.terms import TermMatcher, match_terms
from tests.customer_trends.factories import load_brief

NEVER = TermMatcher([])


def comment(id_: str, text: str, platform: str = "instagram", **kw: object) -> Comment:
    return Comment(id=id_, text=text, platform=platform, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ normalization


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # alef forms become one alef
        ("أحمد إبراهيم آمن", "احمد ابراهيم امن"),
        # taa marbuta becomes haa, alef maqsura becomes yaa
        ("الشركة", "الشركه"),
        ("على مصطفى", "علي مصطفي"),
        # diacritics and tatweel are removed
        ("الشَّرِكَةُ", "الشركه"),
        ("جمـــيل", "جميل"),
        # letters repeated three or more times are written once (Egyptian and Gulf elongation)
        ("جداااااا", "جدا"),
        ("حلوووو", "حلو"),
        ("وايييد حلو", "وايد حلو"),
        ("sooooo good", "so good"),
        ("GitLaaaab", "gitlab"),
        # two repeats are ordinary spelling and stay
        ("good", "good"),
        ("الله", "الله"),
        # whitespace collapses and case is folded
        ("  GitLab \n  DUO  ", "gitlab duo"),
        # digits are not letters: prices keep their zeros
        ("بس 1000000 جنيه", "بس 1000000 جنيه"),
    ],
)
def test_normalization_samples(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_spellings_of_the_same_dialect_phrase_normalize_alike() -> None:
    egyptian = [("الكارت غالي اوي", "الكارت غالى أوي"), ("عايز اشترى", "عايز أشتري")]
    gulf = [("الخدمة وايد حلوة", "الخدمه وايييد حلوه"), ("شلونكم يالشركة", "شلونكم يالشركه")]
    for first, second in [*egyptian, *gulf]:
        assert normalize(first) == normalize(second)


def test_normalizing_does_not_change_the_original() -> None:
    original = "جداااا الشركة"
    c = comment("1", original)
    normalize(c.text)
    assert c.text == original


# ------------------------------------------------------------------ the drop rules of Stage A


@pytest.mark.parametrize("text", ["", "   ", "😂😂😂", "👍🏽", "...", "!!! ???", "—", "❤️ 🔥"])
def test_nothing_to_read_is_empty(text: str) -> None:
    verdict = standalone_verdict(text)
    assert verdict is not None and verdict.reason == "empty" and verdict.stage == "a"


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/page",
        "www.example.com",
        "bit.ly/3abc",
        "http://t.co/x https://x.com/y",
        "🔥 https://x.com/y",
    ],
)
def test_a_link_with_nothing_else_is_link_only(text: str) -> None:
    assert standalone_verdict(text).reason == "link_only"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "text", ["@ahmed", "@ahmed @sara.mo", "@أحمد_علي", "#gitlab #duo", "@ahmed 😂 #tag"]
)
def test_mentions_and_tags_alone_are_dropped(text: str) -> None:
    assert standalone_verdict(text).reason == "mentions_only"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "text",
    [
        "aaaaaaaaaa",
        "ههههههههههه",
        "lolololololol",
        "hahahahaha",
        "هههاهههاههه",
        "ااااااا",
        "xxxxxxxxxxxxx",
    ],
)
def test_repeated_character_spam_is_dropped(text: str) -> None:
    assert is_character_spam(text)
    verdict = standalone_verdict(text)
    assert verdict is not None and verdict.reason == "spam"


def test_only_letters_and_digits_count_towards_spam() -> None:
    assert not is_character_spam("wow!!!!!!!!!!!!")
    assert not is_character_spam(
        "!!!!!!1"
    )  # nothing to read, so the empty rule or the short rule decides
    assert not is_character_spam("aaaa")  # too short to judge
    assert not is_character_spam("the price is too high")


@pytest.mark.parametrize(
    "text",
    [
        "the price is too high",
        "الكارت غالي اوي",
        "see https://example.com for the price",
        "@gitlab when is the next release",
        "hahaha that is expensive",
        "wow!!!!!!!!!!!!",
        "ok",
        "غالي",
    ],
)
def test_comments_with_something_to_say_are_not_dropped_by_the_standalone_rules(text: str) -> None:
    assert standalone_verdict(text) is None


def test_word_count_ignores_links_mentions_and_tags() -> None:
    assert word_count("@ahmed see https://x.com/y #duo") == 1
    assert word_count("سعره كام") == 2
    assert word_count("") == 0


def test_short_comments_are_dropped_unless_they_hold_a_term() -> None:
    matcher = TermMatcher(["gitlab", "جيت لاب"])
    assert too_short("price?", matcher).reason == "too_short"  # type: ignore[union-attr]
    assert too_short("غالي", matcher).reason == "too_short"  # type: ignore[union-attr]
    assert too_short("GitLab", matcher) is None
    assert too_short("جيت لاب", matcher) is None  # the match term spares it, in Arabic too
    assert too_short("how much", matcher) is None  # two words
    assert too_short("سعره كام", matcher) is None


def test_language_never_decides_a_drop() -> None:
    comments = [
        comment("ar", "الكارت غالي اوي"),
        comment("en", "the card is expensive"),
        comment("mixed", "الكارت expensive اوي"),
    ]
    verdicts, _ = stage_a(comments, NEVER)
    assert verdicts == {}


# ------------------------------------------------------------------ duplicates


def test_the_first_of_a_set_of_duplicates_is_kept_and_counts_the_rest() -> None:
    comments = [
        comment("1", "الخدمة وايد حلوة"),
        comment("2", "الخدمة وايد حلوة"),
        comment("3", "الخدمه وايييد حلوه"),  # normalizes to the same text
        comment("4", "a different comment here"),
    ]
    verdicts, seen = stage_a(comments, NEVER)
    assert set(verdicts) == {"2", "3"}
    assert all(
        v.reason == "duplicate" and v.extra == {"duplicate_of": "1"} for v in verdicts.values()
    )
    assert dict(seen.repeats) == {"1": 2}


def test_duplicates_are_found_within_one_platform_only() -> None:
    comments = [
        comment("1", "same words on two sites", "instagram"),
        comment("2", "same words on two sites", "x"),
    ]
    verdicts, _ = stage_a(comments, NEVER)
    assert verdicts == {}


def test_a_comment_judged_in_an_earlier_pass_is_not_judged_again_but_still_catches_a_repeat() -> (
    None
):
    comments = [
        comment("old", "price is too high", already=True),
        comment("new", "Price is too HIGH"),
    ]
    verdicts, seen = stage_a(comments, NEVER)
    assert set(verdicts) == {"new"} and verdicts["new"].extra == {"duplicate_of": "old"}
    assert dict(seen.repeats) == {"old": 1}


def test_a_comment_dropped_by_a_standalone_rule_does_not_make_a_later_one_a_duplicate() -> None:
    comments = [comment("1", "ههههههههه"), comment("2", "ههههههههه")]
    verdicts, _ = stage_a(comments, NEVER)
    assert {k: v.reason for k, v in verdicts.items()} == {"1": "spam", "2": "spam"}


def test_stage_a_reports_each_reason_once_per_comment() -> None:
    comments = [
        comment("e", "😂"),
        comment("l", "https://x.com/a"),
        comment("m", "@ahmed"),
        comment("s", "aaaaaaaa"),
        comment("t", "price"),
        comment("ok", "the price is high"),
    ]
    verdicts, _ = stage_a(comments, NEVER)
    assert {k: v.reason for k, v in verdicts.items()} == {
        "e": "empty",
        "l": "link_only",
        "m": "mentions_only",
        "s": "spam",
        "t": "too_short",
    }


def test_the_short_comment_exception_leaves_a_term_match_for_stage_b() -> None:
    matcher = TermMatcher(["gitlab"])
    verdicts, _ = stage_a([comment("1", "GitLab"), comment("2", "price")], matcher)
    assert set(verdicts) == {"2"}


# ------------------------------------------------------------------ terms (Stage B)


def test_match_terms_come_from_the_company_products_competitors_and_focus() -> None:
    brief = load_brief().model_copy(
        update={
            "entity": "GitLab",
            "aliases": ["GitLab Inc.", "جيت لاب"],
            "products": ["Duo", "GitLab Ultimate"],
            "competitors": ["GitHub Copilot", "Atlassian"],
            "focus_terms": ["AI-assisted development", "x"],
        }
    )
    terms = match_terms(brief)
    assert terms == [
        "gitlab",
        "gitlab inc.",
        "جيت لاب",
        "duo",
        "gitlab ultimate",
        "github copilot",
        "atlassian",
        "ai-assisted development",
    ]  # the one-letter focus term is too short to be a term, and there are no repeats


def test_match_terms_are_normalized_like_the_text() -> None:
    brief = load_brief().model_copy(
        update={
            "entity": "شركة النيل",
            "aliases": ["الشّركةُ"],
            "products": [],
            "competitors": [],
            "focus_terms": [],
        }
    )
    assert match_terms(brief) == ["شركه النيل", "الشركه"]


def test_latin_terms_match_whole_words_only() -> None:
    matcher = TermMatcher(["duo", "gitlab"])
    assert matcher("GitLab Duo is great")
    assert matcher("is it Duo?")
    assert not matcher("I use Duolingo")
    assert not matcher("a duopoly")


def test_arabic_terms_match_inside_a_word_because_of_attached_prefixes() -> None:
    matcher = TermMatcher(["جيت لاب", "فوري"])
    assert matcher("بجيت لاب شغال")
    assert matcher("والفوري ده كويس")
    assert not matcher("خدمة عملاء")


def test_matching_ignores_spelling_variants_and_elongation() -> None:
    matcher = TermMatcher(["شركة النيل", "gitlab"])
    assert matcher("اشتريت من شركه النيل امس")
    assert matcher("شَركةُ النيل")
    assert matcher("gitlaaaab is nice")
    assert matcher("GITLAB")


def test_with_no_terms_nothing_matches() -> None:
    assert not TermMatcher([])("anything at all")
