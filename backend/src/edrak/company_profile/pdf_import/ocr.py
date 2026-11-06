"""Whether scanned pages can be read, and the OCR call itself.

OCR is PyMuPDF's wrapper around the Tesseract program. Tesseract is a system package, so it may be
missing, and a Tesseract without Arabic or English data cannot read these documents. That is checked
once and logged at startup; scanned pages are then skipped with a warning instead of failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from edrak.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrStatus:
    available: bool
    reason: str = ""  # why not, when it is not available


def _languages() -> list[str]:
    return [code for code in settings.PROFILE_IMPORT_OCR_LANGUAGE.split("+") if code]


@lru_cache(maxsize=1)
def ocr_status() -> OcrStatus:
    """Whether Tesseract and the language data for PROFILE_IMPORT_OCR_LANGUAGE are installed. Cached."""
    try:
        import pymupdf
    except ImportError:
        return OcrStatus(False, "PyMuPDF is not installed")
    try:
        tessdata = Path(pymupdf.get_tessdata())
    except Exception:  # PyMuPDF raises RuntimeError when it finds no Tesseract
        return OcrStatus(False, "the Tesseract program is not installed")
    missing = [code for code in _languages() if not (tessdata / f"{code}.traineddata").is_file()]
    if missing:
        return OcrStatus(False, f"Tesseract has no language data for: {', '.join(missing)}")
    return OcrStatus(True)


def log_ocr_status() -> OcrStatus:
    """Check once and say so in the log. Called when the server starts."""
    status = ocr_status()
    if status.available:
        logger.info("PDF import: OCR is available (%s)", settings.PROFILE_IMPORT_OCR_LANGUAGE)
    else:
        logger.warning("PDF import: OCR is not available (%s); scanned pages will be skipped", status.reason)
    return status


def ocr_page(page: Any) -> str:
    """The text OCR finds on `page`. Raises if Tesseract fails."""
    textpage = page.get_textpage_ocr(
        language=settings.PROFILE_IMPORT_OCR_LANGUAGE, dpi=settings.PROFILE_IMPORT_OCR_DPI, full=False
    )
    return str(page.get_text("text", textpage=textpage, sort=True))
