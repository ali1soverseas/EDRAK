"""Draft a company profile from outside sources. A draft is returned, never saved: the person reviews
it in the company setup screen and saves it with PUT /company."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError

from edrak.api.deps import get_current_session
from edrak.api.schemas import SessionOut
from edrak.company_profile import ProfileDraft, ProfileExtractionError, ProfileLLMError
from edrak.company_profile.pdf_import import PdfRejected, import_profile_from_pdf
from edrak.company_profile.url_import import SiteUnreachable, UrlBlocked, import_profile_from_url
from edrak.core.config import settings

router = APIRouter(tags=["company"])


class ImportUrlIn(BaseModel):
    url: str


def _clean_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


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


@router.post("/company/profile/import/json", response_model=ProfileDraft)
async def import_profile_json(
    file: UploadFile = File(...),
    session: SessionOut = Depends(get_current_session),
) -> ProfileDraft:
    content = await file.read(2 * 1024 * 1024)
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_json", "message": "The uploaded file is not valid JSON."},
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_json", "message": "The profile JSON must contain an object."},
        )

    from edrak.company_profile.models import ProfileFields, ProfileDraft
    from edrak.contracts.request import SocialAccount

    notes = data.get("notes") or data.get("description") or data.get("self_description")
    profile = ProfileFields(
        name=data.get("name"),
        aliases=_clean_strings(data.get("aliases")),
        notes=notes if isinstance(notes, str) else None,
        products=_clean_strings(data.get("products") or data.get("offerings")),
        target_customers=_clean_strings(data.get("target_customers") or data.get("target_customer_groups")),
        industry=data.get("industry") if isinstance(data.get("industry"), str) else None,
        markets=_clean_strings(data.get("markets")),
        goals=_clean_strings(data.get("goals") or data.get("strategic_goals")),
    )

    links: list[SocialAccount] = []
    if isinstance(data.get("website"), str) and data["website"].strip():
        links.append(SocialAccount(platform="website", url=data["website"].strip()))
    for account in data.get("social_accounts") or []:
        if not isinstance(account, dict):
            continue
        platform = account.get("platform")
        url = account.get("url")
        if isinstance(platform, str) and isinstance(url, str):
            try:
                links.append(SocialAccount(platform=platform, url=url))
            except ValidationError:
                continue

    field_sources = {
        key: ["profile.json"]
        for key, value in (
            ("name", profile.name),
            ("aliases", profile.aliases),
            ("notes", profile.notes),
            ("products", profile.products),
            ("target_customers", profile.target_customers),
            ("industry", profile.industry),
            ("markets", profile.markets),
            ("goals", profile.goals),
        )
        if value
    }
    if links:
        field_sources["links"] = ["profile.json"]
    gaps = [key for key in ("name", "aliases", "notes", "products", "target_customers", "industry", "markets", "goals") if not getattr(profile, key)]
    return ProfileDraft(
        profile=profile,
        links=links,
        field_sources=field_sources,
        gaps=gaps,
        warnings=[],
        sources=[{"id": "profile.json", "type": "page_metadata", "ref": file.filename or "profile.json", "title": "Uploaded company profile"}],
    )
