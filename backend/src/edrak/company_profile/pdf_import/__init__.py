"""Build a company profile draft from an uploaded PDF.

import_profile_from_pdf reads the pages (OCR for scans when Tesseract is installed), picks the most
useful ones when the document is long, and hands them to the extraction core. It saves nothing.
PyMuPDF is imported only when a PDF is read, so the server starts without it.
"""

from edrak.company_profile.pdf_import.errors import PdfRejected
from edrak.company_profile.pdf_import.importer import import_profile_from_pdf
from edrak.company_profile.pdf_import.ocr import log_ocr_status, ocr_status

__all__ = ["PdfRejected", "import_profile_from_pdf", "log_ocr_status", "ocr_status"]
