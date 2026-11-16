"""Find, download and read the PDFs a company website offers, one document per page kept.

`read_company_pdfs` takes the result of `crawl_company_site`. Files are downloaded from the company's
domain or from hosts its own pages link to, read from temporary files that are deleted, and never
stored. No model is called.
"""

from edrak.company_research.pdfs.candidates import find_pdfs
from edrak.company_research.pdfs.models import (
    Outcome,
    PdfCandidate,
    PdfPageDocument,
    PdfReadResult,
    PdfReport,
    PdfStats,
    Via,
)
from edrak.company_research.pdfs.read import PdfLimits, read_company_pdfs, read_pdfs

__all__ = [
    "Outcome",
    "PdfCandidate",
    "PdfLimits",
    "PdfPageDocument",
    "PdfReadResult",
    "PdfReport",
    "PdfStats",
    "Via",
    "find_pdfs",
    "read_company_pdfs",
    "read_pdfs",
]
