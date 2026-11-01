"""PDFs built in memory for the PDF import tests. Nothing here is a checked-in binary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("pymupdf4llm")

A4 = pymupdf.Rect(0, 0, 595, 842)

ARABIC_SAMPLE = "نقدم حلول الدفع الإلكتروني للتجار في مصر والسعودية"
ARABIC_HEADING = "من نحن"

# Fonts with real Arabic glyphs, by platform. The tests skip when none is installed.
_ARABIC_FONTS = (
    "/System/Library/Fonts/Supplemental/Baghdad.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def arabic_font() -> str:
    for path in _ARABIC_FONTS:
        if os.path.exists(path):
            return path
    pytest.skip("no font with Arabic glyphs is installed")


def _save(doc, path: Path, **kwargs) -> Path:
    try:
        doc.subset_fonts()  # an embedded system font can be tens of MB; keep only the glyphs used
    except Exception:
        pass
    doc.save(str(path), garbage=4, deflate=True, **kwargs)
    doc.close()
    return path


def html_page(doc, html: str, rect=None, **kwargs):
    page = doc.new_page()
    page.insert_htmlbox(rect or pymupdf.Rect(50, 40, 545, 800), html, **kwargs)
    return page


def english_single(path: Path) -> Path:
    doc = pymupdf.open()
    html_page(
        doc,
        "<h1>About Acme Pay</h1><p>Acme Pay builds card payment terminals for merchants in Egypt and Saudi Arabia.</p>"
        "<p>Visit https://www.acmepay.example for more, or follow https://twitter.com/acmepay.</p>",
    )
    html_page(
        doc,
        "<h2>Products</h2><p>Acme Terminal is a countertop card reader. Acme Link sends payment links by message.</p>",
    )
    return _save(doc, path)


def two_column(path: Path) -> Path:
    left = "Left column sentence about terminals. " * 8
    right = "Right column sentence about settlement. " * 8
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(pymupdf.Rect(50, 40, 545, 90), "<h1>Company overview</h1>")
    page.insert_htmlbox(pymupdf.Rect(50, 110, 290, 600), f"<p>{left}</p>")
    page.insert_htmlbox(pymupdf.Rect(310, 110, 545, 600), f"<p>{right}</p>")
    return _save(doc, path)


def arabic(path: Path) -> Path:
    font = arabic_font()
    css = f'@font-face {{font-family: ar; src: url("{os.path.basename(font)}");}} * {{font-family: ar;}}'
    archive = pymupdf.Archive(os.path.dirname(font))
    doc = pymupdf.open()
    html_page(
        doc,
        f'<h1 dir="rtl">{ARABIC_HEADING}</h1><p dir="rtl">{ARABIC_SAMPLE}</p>',
        css=css,
        archive=archive,
    )
    return _save(doc, path)


def deck(path: Path, slides: int = 6) -> Path:
    titles = ["Acme Pay", "The problem", "Our products", "Markets", "Customers", "Contact"]
    doc = pymupdf.open()
    for i in range(slides):
        page = doc.new_page(width=960, height=540)
        page.insert_htmlbox(pymupdf.Rect(60, 60, 900, 480), f"<h1>{titles[i % len(titles)]}</h1><p>Short line {i + 1}.</p>")
    return _save(doc, path)


def image_only(path: Path, text_pages: int = 1) -> Path:
    """`text_pages` text pages, then one page that is only a picture of text (the shape of a scan)."""
    source = pymupdf.open()
    sp = source.new_page()
    sp.insert_htmlbox(pymupdf.Rect(50, 50, 545, 300), "<h1>Scanned brochure</h1><p>Acme Pay builds payment terminals in Cairo.</p>")
    pixmap = sp.get_pixmap(dpi=100)
    doc = pymupdf.open()
    for i in range(text_pages):
        html_page(doc, f"<h1>Text page {i + 1}</h1><p>This page has a real text layer with enough words on it to count as text.</p>")
    page = doc.new_page(width=pixmap.width, height=pixmap.height)
    page.insert_image(page.rect, pixmap=pixmap)
    return _save(doc, path)


def encrypted(path: Path) -> Path:
    doc = pymupdf.open()
    html_page(doc, "<h1>Secret</h1><p>Locked content.</p>")
    return _save(doc, path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="open-sesame", owner_pw="owner-pw")


def long_document(path: Path, pages: int, keyword_pages: dict[int, str]) -> Path:
    """`pages` pages of filler; `keyword_pages` maps a page number to a heading that names a topic."""
    filler = "General filler sentence that says very little about anything in particular. " * 10
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        heading = keyword_pages.get(number, f"Section {number}")
        html_page(doc, f"<h1>{heading}</h1><p>{filler}</p>")
    return _save(doc, path)
