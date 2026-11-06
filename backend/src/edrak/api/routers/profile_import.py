"""Draft a company profile from outside sources. A draft is returned, never saved: the person reviews
it in the company setup screen and saves it with PUT /company."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from edrak.api.deps import get_current_session
from edrak.api.schemas import SessionOut
from edrak.company_profile import ProfileDraft, ProfileExtractionError, ProfileLLMError
from edrak.company_profile.pdf_import import PdfRejected, import_profile_from_pdf
from edrak.company_profile.url_import import SiteUnreachable, UrlBlocked, import_profile_from_url
from edrak.core.config import settings

router = APIRouter(tags=["company"])


class ImportUrlIn(BaseModel):
    url: str


def _model_failure(exc: Exception) -> HTTPException:
    """The 502 for a failed model step, shared by every importer."""
    if isinstance(exc, ProfileLLMError):
        return HTTPException(
            status_code=502,
            detail={"code": "model_unavailable", "message": "The language model could not be reached. Try again shortly."},
        )
    return HTTPException(
        status_code=502,
        detail={"code": "extraction_failed", "message": "The language model's answer could not be used. Try again."},
    )


@router.post("/company/profile/import/url", response_model=ProfileDraft)
async def import_profile_url(
    body: ImportUrlIn,
    session: SessionOut = Depends(get_current_session),
) -> ProfileDraft:
    try:
        return await import_profile_from_url(body.url)
    except UrlBlocked as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_url", "message": str(exc)}) from exc
    except SiteUnreachable as exc:
        raise HTTPException(status_code=502, detail={"code": "site_unreachable", "message": str(exc)}) from exc
    except (ProfileLLMError, ProfileExtractionError) as exc:
        raise _model_failure(exc) from exc


@router.post("/company/profile/import/pdf", response_model=ProfileDraft)
async def import_profile_pdf(
    file: UploadFile = File(...),
    session: SessionOut = Depends(get_current_session),
) -> ProfileDraft:
    # Read one byte past the limit so an oversized upload is noticed without reading all of it.
    limit = settings.PROFILE_IMPORT_PDF_MAX_MB * 1024 * 1024
    content = await file.read(limit + 1)
    try:
        return await import_profile_from_pdf(content)
    except PdfRejected as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    except (ProfileLLMError, ProfileExtractionError) as exc:
        raise _model_failure(exc) from exc
