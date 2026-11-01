"""Draft a company profile from outside sources. A draft is returned, never saved: the person reviews
it in the company setup screen and saves it with PUT /company."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from edrak.api.deps import get_current_session
from edrak.api.schemas import SessionOut
from edrak.company_profile import ProfileDraft, ProfileExtractionError, ProfileLLMError
from edrak.company_profile.url_import import SiteUnreachable, UrlBlocked, import_profile_from_url

router = APIRouter(tags=["company"])


class ImportUrlIn(BaseModel):
    url: str


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
    except ProfileLLMError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "model_unavailable", "message": "The language model could not be reached. Try again shortly."},
        ) from exc
    except ProfileExtractionError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "extraction_failed", "message": "The language model's answer could not be used. Try again."},
        ) from exc
