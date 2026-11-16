"""Turn crawled pages and PDF pages into verified, deduplicated facts in the company_facts shape.

`extract_company_facts` calls the model in batches and checks everything it returns with plain code.
`apply_extraction` stores the result. Facts a person approved or edited are never overwritten.
"""

from edrak.company_research.extraction.extract import ExtractionLimits, extract_company_facts
from edrak.company_research.extraction.models import ExtractionResult, ExtractionStats, NewKey
from edrak.company_research.extraction.sources import SourceDoc, documents_from_crawl, documents_from_pdfs
from edrak.company_research.extraction.store import apply_extraction

__all__ = [
    "ExtractionLimits",
    "ExtractionResult",
    "ExtractionStats",
    "NewKey",
    "SourceDoc",
    "apply_extraction",
    "documents_from_crawl",
    "documents_from_pdfs",
    "extract_company_facts",
]
