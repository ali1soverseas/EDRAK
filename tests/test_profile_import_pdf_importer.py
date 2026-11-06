import asyncio
import json
import os

import pytest

import pdf_fixtures as fx
from edrak.company_profile.pdf_import import PdfRejected, import_profile_from_pdf
from edrak.company_profile.pdf_import import importer as importer_module
from edrak.company_profile.pdf_import.ocr import OcrStatus
from edrak.core.config import settings


class FakeLLM:
    def __init__(self, profile=None, sources=None):
        self.calls = []
        self.profile = profile or {"name": "Acme Pay", "products": ["Acme Terminal"], "notes": "Acme Pay builds card payment terminals.", "markets": ["Egypt"]}
        self.sources = sources or {"name": ["S1"], "products": ["S2"], "notes": ["S1"], "markets": ["S1"]}

    def __call__(self, messages):
        self.calls.append(messages)
        return json.dumps({"profile": self.profile, "field_sources": self.sources})


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    from edrak.company_profile.pdf_import import extract

    monkeypatch.setattr(extract.ocr_module, "ocr_status", lambda: OcrStatus(False, "the Tesseract program is not installed"))


def run(content, llm=None):
    return asyncio.run(import_profile_from_pdf(content, llm=llm or FakeLLM()))


def test_a_pdf_becomes_a_draft_in_the_same_shape_as_the_url_importer(tmp_path):
    llm = FakeLLM()
    draft = run(fx.english_single(tmp_path / "a.pdf").read_bytes(), llm)

    assert set(draft.model_dump()) == {"profile", "links", "field_sources", "gaps", "warnings", "sources"}
    assert [(s.id, s.type.value, s.ref, s.title) for s in draft.sources] == [
        ("S1", "pdf_page", "p.1", "About Acme Pay"),
        ("S2", "pdf_page", "p.2", "Products"),
    ]
    assert draft.profile.name == "Acme Pay" and draft.profile.products == ["Acme Terminal"]
    assert draft.field_sources["products"] == ["S2"]
    context = llm.calls[0][1]["content"]
    assert "[S1] type=pdf_page ref=p.1 title=About Acme Pay" in context and "countertop card reader" in context
    assert [(l.platform.value, l.url) for l in draft.links] == [
        ("website", "https://www.acmepay.example"),
        ("x", "https://x.com/acmepay"),
    ]


def test_scan_and_selection_warnings_come_before_the_cores(tmp_path):
    draft = run(fx.image_only(tmp_path / "s.pdf", text_pages=2).read_bytes())
    assert draft.warnings[0].startswith("Page(s) 3 are mostly scanned images")
    assert [s.ref for s in draft.sources] == ["p.1", "p.2"]


def test_a_long_pdf_is_cut_to_the_budget_with_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROFILE_IMPORT_CONTEXT_CHARS", 9_000)
    llm = FakeLLM()
    draft = run(fx.long_document(tmp_path / "long.pdf", 30, {14: "Who we are"}).read_bytes(), llm)
    refs = [s.ref for s in draft.sources]
    assert refs[:3] == ["p.1", "p.2", "p.3"] and "p.14" in refs and len(refs) < 30
    assert any(w.startswith("The document is longer than the size limit") for w in draft.warnings)
    assert len(llm.calls[0][1]["content"]) <= 9_000 + len("Sources:\n\n")


def test_arabic_text_reaches_the_model_in_logical_order(tmp_path):
    llm = FakeLLM({"name": "فوري تك"}, {"name": ["S1"]})
    draft = run(fx.arabic(tmp_path / "ar.pdf").read_bytes(), llm)
    assert fx.ARABIC_SAMPLE in llm.calls[0][1]["content"]
    assert draft.profile.name == "فوري تك"


def test_a_pdf_with_no_readable_text_makes_no_model_call(tmp_path):
    llm = FakeLLM()
    draft = run(fx.image_only(tmp_path / "s.pdf", text_pages=0).read_bytes(), llm)
    assert llm.calls == []
    assert draft.sources == [] and draft.profile.name is None
    assert any("No readable text was found" in w for w in draft.warnings)
    assert any("scanned images" in w for w in draft.warnings)


@pytest.mark.parametrize(
    "content, code",
    [(b"", "empty_file"), (b"PK\x03\x04 not a pdf", "not_a_pdf"), (b"%PDF-1.7\n" + b"junk\n" * 30, "corrupt")],
)
def test_rejected_files_never_reach_the_model(content, code):
    llm = FakeLLM()
    with pytest.raises(PdfRejected) as caught:
        run(content, llm)
    assert caught.value.code == code and llm.calls == []


def test_an_encrypted_pdf_is_rejected(tmp_path):
    with pytest.raises(PdfRejected) as caught:
        run(fx.encrypted(tmp_path / "x.pdf").read_bytes())
    assert caught.value.code == "encrypted"


def test_the_temporary_copy_is_deleted_after_success_and_after_failure(tmp_path, monkeypatch):
    made = []
    real = importer_module.tempfile.mkstemp

    def tracking(*args, **kwargs):
        fd, path = real(*args, **kwargs)
        made.append(path)
        return fd, path

    monkeypatch.setattr(importer_module.tempfile, "mkstemp", tracking)
    run(fx.english_single(tmp_path / "a.pdf").read_bytes())
    with pytest.raises(PdfRejected):
        run(fx.encrypted(tmp_path / "x.pdf").read_bytes())
    assert len(made) == 2 and not any(os.path.exists(p) for p in made)


def test_nothing_is_written_to_the_uploads_folder(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(settings, "UPLOADS_PATH", uploads)
    run(fx.english_single(tmp_path / "a.pdf").read_bytes())
    assert not uploads.exists()
