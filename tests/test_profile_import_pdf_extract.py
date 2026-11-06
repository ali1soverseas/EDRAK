import pytest

import pdf_fixtures as fx
from edrak.company_profile.pdf_import import extract as extract_module
from edrak.company_profile.pdf_import.arabic import letters_reversed, words_reversed
from edrak.company_profile.pdf_import.errors import PdfRejected
from edrak.company_profile.pdf_import.extract import extract_document, first_heading, open_pdf, page_ranges
from edrak.company_profile.pdf_import.importer import check_upload
from edrak.company_profile.pdf_import.ocr import OcrStatus, ocr_status
from edrak.core.config import settings

NO_OCR = OcrStatus(False, "the Tesseract program is not installed")
OCR = OcrStatus(True)

GOOD_AR = "نقدم حلول الدفع الإلكتروني للتجار في مصر والسعودية. تأسست الشركة في القاهرة وتخدم آلاف المتاجر في جميع المحافظات، والفريق يدعم العملاء من خلال المركز الرئيسي"


# ------------------------------------------------------------------ text extraction


def test_single_column_english_keeps_headings_per_page(tmp_path):
    out = extract_document(str(fx.english_single(tmp_path / "a.pdf")), ocr=NO_OCR)
    assert [(p.number, p.title) for p in out.pages] == [(1, "About Acme Pay"), (2, "Products")]
    assert "card payment terminals" in out.pages[0].text and "# " in out.pages[0].text
    assert out.warnings == []


def test_two_columns_are_read_left_then_right(tmp_path):
    out = extract_document(str(fx.two_column(tmp_path / "b.pdf")), ocr=NO_OCR)
    text = out.pages[0].text
    assert text.rindex("Left column") < text.index("Right column")  # no interleaving of the columns
    assert out.pages[0].title == "Company overview"


def test_arabic_comes_out_in_logical_order(tmp_path):
    out = extract_document(str(fx.arabic(tmp_path / "c.pdf")), ocr=NO_OCR)
    text = out.pages[0].text
    assert fx.ARABIC_SAMPLE in text  # the sentence, not a mirrored one
    assert text.index(fx.ARABIC_HEADING) < text.index("نقدم")
    assert not letters_reversed(text)
    assert out.warnings == []


def test_a_deck_gives_one_short_page_per_slide(tmp_path):
    out = extract_document(str(fx.deck(tmp_path / "d.pdf")), ocr=NO_OCR)
    assert [p.title for p in out.pages] == ["Acme Pay", "The problem", "Our products", "Markets", "Customers", "Contact"]
    assert all(len(p.text) < 60 for p in out.pages)
    assert not any(p.scanned for p in out.pages)  # short text and no image is a slide, not a scan


def test_hyperlinks_on_a_page_are_collected(tmp_path):
    import pymupdf

    path = fx.english_single(tmp_path / "e.pdf")
    doc = pymupdf.open(str(path))
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(50, 50, 200, 80), "uri": "https://acmepay.example/contact"})
    doc.saveIncr()
    doc.close()
    assert extract_document(str(path), ocr=NO_OCR).pages[0].links == ["https://acmepay.example/contact"]


def test_formatting_failure_falls_back_to_plain_text(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_module, "_markdown_by_page", lambda doc: None)
    out = extract_document(str(fx.english_single(tmp_path / "f.pdf")), ocr=NO_OCR)
    assert "card payment terminals" in out.pages[0].text
    assert any("plain text was used" in w for w in out.warnings)


# ------------------------------------------------------------------ scans and OCR


def test_an_image_only_page_is_a_scan_and_without_ocr_it_is_named_in_a_warning(tmp_path):
    out = extract_document(str(fx.image_only(tmp_path / "s.pdf", text_pages=2)), ocr=NO_OCR)
    assert [p.number for p in out.pages] == [1, 2]  # the scan has no text to give
    assert out.warnings == [
        "Page(s) 3 are mostly scanned images and OCR is not available on this server, so the text in those images was not read."
    ]


def test_scanned_pages_are_read_with_ocr_when_it_is_available(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(extract_module.ocr_module, "ocr_page", lambda page: seen.append(page.number) or "Acme Pay builds payment terminals in Cairo.")
    out = extract_document(str(fx.image_only(tmp_path / "s.pdf", text_pages=0)), ocr=OCR)
    assert seen == [0]  # PyMuPDF numbers pages from 0
    page = out.pages[0]
    assert page.scanned and page.ocr and "Acme Pay builds payment terminals" in page.text
    assert out.warnings == []


def test_scan_threshold_and_ocr_failures_are_reported(tmp_path, monkeypatch):
    def boom(page):
        raise RuntimeError("tesseract crashed")

    monkeypatch.setattr(extract_module.ocr_module, "ocr_page", boom)
    out = extract_document(str(fx.image_only(tmp_path / "s.pdf", text_pages=1)), ocr=OCR)
    assert [p.number for p in out.pages] == [1]
    assert out.warnings == ["No text could be read from the scanned page(s) 2."]


def test_the_scan_threshold_comes_from_config(tmp_path, monkeypatch):
    path = fx.image_only(tmp_path / "s.pdf", text_pages=1)
    monkeypatch.setattr(settings, "PROFILE_IMPORT_SCANNED_MIN_CHARS", 0)  # nothing counts as a scan
    assert extract_document(str(path), ocr=NO_OCR).warnings == []


def test_ocr_status_reports_a_missing_tesseract_and_missing_language_data(monkeypatch, tmp_path):
    import pymupdf

    from edrak.company_profile.pdf_import import ocr

    ocr.ocr_status.cache_clear()
    monkeypatch.setattr(pymupdf, "get_tessdata", lambda: (_ for _ in ()).throw(RuntimeError("no tesseract")))
    assert ocr.ocr_status() == OcrStatus(False, "the Tesseract program is not installed")

    ocr.ocr_status.cache_clear()
    (tmp_path / "eng.traineddata").write_bytes(b"x")
    monkeypatch.setattr(pymupdf, "get_tessdata", lambda: str(tmp_path))
    assert ocr.ocr_status() == OcrStatus(False, "Tesseract has no language data for: ara")

    ocr.ocr_status.cache_clear()
    (tmp_path / "ara.traineddata").write_bytes(b"x")
    assert ocr.ocr_status().available
    ocr.ocr_status.cache_clear()


def test_startup_logs_the_ocr_result_once(monkeypatch, caplog):
    import logging

    from edrak.company_profile.pdf_import import ocr

    monkeypatch.setattr(ocr, "ocr_status", lambda: OcrStatus(False, "the Tesseract program is not installed"))
    with caplog.at_level(logging.INFO, logger=ocr.logger.name):
        ocr.log_ocr_status()
    assert "OCR is not available" in caplog.text and "Tesseract program is not installed" in caplog.text


@pytest.mark.skipif(not ocr_status().available, reason="Tesseract with ara+eng data is not installed")
def test_real_ocr_reads_a_scanned_page(tmp_path):
    out = extract_document(str(fx.image_only(tmp_path / "s.pdf", text_pages=0)))
    assert "Acme" in out.pages[0].text


# ------------------------------------------------------------------ rejected files


def test_an_encrypted_pdf_is_rejected(tmp_path):
    with pytest.raises(PdfRejected) as caught:
        extract_document(str(fx.encrypted(tmp_path / "x.pdf")), ocr=NO_OCR)
    assert caught.value.code == "encrypted" and "password" in str(caught.value)


def test_a_file_that_is_not_a_pdf_is_rejected_by_its_first_bytes():
    for content in (b"PK\x03\x04 a zip renamed to .pdf", b"<html>not a pdf</html>", b"MZ\x90\x00 executable"):
        with pytest.raises(PdfRejected) as caught:
            check_upload(content)
        assert caught.value.code == "not_a_pdf"


def test_empty_and_oversized_uploads_are_rejected(monkeypatch):
    with pytest.raises(PdfRejected) as caught:
        check_upload(b"")
    assert caught.value.code == "empty_file"
    monkeypatch.setattr(settings, "PROFILE_IMPORT_PDF_MAX_MB", 1)
    with pytest.raises(PdfRejected) as caught:
        check_upload(b"%PDF-1.7\n" + b"0" * (1024 * 1024))
    assert caught.value.code == "too_large" and "1 MB" in str(caught.value)


def test_a_damaged_pdf_is_rejected_even_with_the_right_magic_bytes(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"this is not a real pdf body\n" * 20)
    with pytest.raises(PdfRejected) as caught:
        extract_document(str(path), ocr=NO_OCR)
    assert caught.value.code == "corrupt"


def test_a_pdf_over_the_page_limit_is_rejected(tmp_path, monkeypatch):
    path = fx.deck(tmp_path / "many.pdf", slides=6)
    monkeypatch.setattr(settings, "PROFILE_IMPORT_PDF_MAX_PAGES", 5)
    with pytest.raises(PdfRejected) as caught:
        open_pdf(str(path))
    assert caught.value.code == "too_many_pages" and "6 pages" in str(caught.value) and "limit is 5" in str(caught.value)


# ------------------------------------------------------------------ Arabic order


def test_mirrored_words_are_detected_and_good_text_is_not():
    mirrored = "\n".join(line[::-1] for line in GOOD_AR.split("."))
    assert letters_reversed(mirrored) and not letters_reversed(GOOD_AR)
    assert not letters_reversed("plain English text with no Arabic")
    assert not letters_reversed("مرحبا")  # too short to judge


def test_reversed_word_order_is_detected_against_a_second_extraction():
    reference = "نقدم حلول الدفع الإلكتروني للتجار\nتأسست الشركة في القاهرة وتخدم آلاف"
    flipped = "\n".join(" ".join(reversed(line.split())) for line in reference.splitlines())
    assert words_reversed(flipped, reference)
    assert not words_reversed(reference, reference)
    assert not words_reversed("two words", reference)


class _Page:
    def __init__(self, plain):
        self.plain = plain

    def get_text(self, *args, **kwargs):
        return self.plain


def test_a_reversed_page_is_retried_with_plain_text(monkeypatch):
    mirrored = "\n".join(line[::-1] for line in GOOD_AR.split("."))
    warnings = []
    assert extract_module._arabic_in_order(mirrored, _Page(GOOD_AR), 4, warnings) == GOOD_AR
    assert warnings == []


def test_a_page_that_is_still_reversed_is_kept_with_a_warning():
    mirrored = "\n".join(line[::-1] for line in GOOD_AR.split("."))
    warnings = []
    assert extract_module._arabic_in_order(mirrored, _Page(mirrored), 7, warnings) == mirrored
    assert warnings == ["The Arabic text on page 7 may be in reverse order."]


def test_good_arabic_and_other_languages_are_left_alone():
    warnings = []
    assert extract_module._arabic_in_order(GOOD_AR, _Page("anything"), 1, warnings) == GOOD_AR
    assert extract_module._arabic_in_order("English only text here", _Page("x"), 1, warnings) == "English only text here"
    assert warnings == []


def test_heading_and_page_range_helpers():
    assert first_heading("intro\n## **Our Products**\nmore") == "Our Products"
    assert first_heading("no heading") == ""
    assert page_ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"
    assert page_ranges([5]) == "5"
