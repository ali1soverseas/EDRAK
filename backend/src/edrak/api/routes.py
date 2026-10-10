"""FastAPI router for EDRAK platform endpoints."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from edrak.api.auth import authenticate_user, end_session, register_user, start_session
from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.run_manager import run_manager
from edrak.api.schemas import (
    AnalysisDetailOut,
    AnalysisForm,
    AnalysisSummaryOut,
    CompanyDocumentOut,
    CompanyFields,
    CompanySetupOut,
    CreateAccountRequest,
    DocumentSize,
    DraftPlanRequest,
    RejectPlanRequest,
    RunViewOut,
    SessionOut,
    SignInRequest,
)
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    ResearchPlan,
    ResearchTask,
    TriggerType,
    UseCase,
    WorkerType,
)
from edrak.core.config import settings
from edrak.db.repository import (
    add_company_document,
    create_or_update_analysis,
    delete_company_document,
    get_analysis,
    get_brief,
    get_company_profile,
    list_analyses,
    list_company_documents,
    save_company_profile,
    update_analysis_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Map of source toggles to worker types
SOURCE_TO_WORKER = {
    "internal_files": WorkerType.INTERNAL_INTELLIGENCE,
    "competitor_web": WorkerType.COMPETITOR_INTELLIGENCE,
    "news_open_data": WorkerType.MARKET_INTELLIGENCE,
    "reviews_social": WorkerType.CUSTOMER_TRENDS,
}

FOCUS_PHRASE = {
    "features": "Product features",
    "pricing": "Pricing and packaging",
    "positioning": "Positioning",
    "sentiment": "Customer sentiment",
    "demand": "Demand and market size",
    "regulation": "Regulation and licensing",
    "competition": "Competition",
    "customers": "Customers",
}


def derive_title(form: AnalysisForm) -> str:
    if form.use_case == UseCase.PRODUCT_LAUNCH and form.offering.strip():
        return form.offering.strip()[:48]
    if form.use_case == UseCase.MARKET_ENTRY_EXPANSION and form.market.strip():
        return f"Expansion into {form.market.strip()[:36]}"
    if form.competitors:
        comps = form.competitors[:2]
        extra = len(form.competitors) - 2
        extra_str = f" +{extra}" if extra > 0 else ""
        return f"Competitors: {', '.join(comps)}{extra_str}"[:56]
    return (form.goal.strip() or "Untitled analysis")[:48]


# ----------------------------------------------------------------------
# Auth & Session
# ----------------------------------------------------------------------


@router.post("/auth/session", response_model=Optional[SessionOut])
async def get_session(session: SessionOut = Depends(get_current_session)) -> SessionOut:
    return session


@router.post("/auth/sign-in", response_model=SessionOut)
async def sign_in(
    req: SignInRequest,
    response: Response,
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> SessionOut:
    user = await authenticate_user(db, req.email, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_credentials",
        )
    token = await start_session(db, user["user_id"])
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
    )
    from edrak.db.repository import get_workspace

    ws = await get_workspace(db, user["workspace_id"])
    return SessionOut(
        user={
            "user_id": user["user_id"],
            "email": user["email"],
            "name": user["name"],
            "title": user.get("title"),
        },
        workspace={
            "workspace_id": user["workspace_id"],
            "name": ws["name"] if ws else "Default Workspace",
        },
    )


@router.post("/auth/create-account", response_model=SessionOut)
async def create_account(
    req: CreateAccountRequest,
    response: Response,
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> SessionOut:
    try:
        user, workspace = await register_user(
            db,
            email=req.email,
            password=req.password,
            name=req.name,
            title=req.title,
            workspace_name=req.workspace_name,
        )
    except ValueError as exc:
        if str(exc) == "email_taken":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email_taken",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    token = await start_session(db, user["user_id"])
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
    )
    return SessionOut(
        user={
            "user_id": user["user_id"],
            "email": user["email"],
            "name": user["name"],
            "title": user.get("title"),
        },
        workspace=workspace,
    )


@router.post("/auth/sign-out")
async def sign_out(
    response: Response,
    session_token: Optional[str] = Cookie(None),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if session_token:
        await end_session(db, session_token)
    response.delete_cookie("session_token")
    return {"status": "signed_out"}


# ----------------------------------------------------------------------
# Company Setup
# ----------------------------------------------------------------------


@router.get("/company", response_model=CompanySetupOut)
async def get_company(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> CompanySetupOut:
    profile = await get_company_profile(db, session.workspace.workspace_id)
    documents = await list_company_documents(db, session.workspace.workspace_id)

    if not profile:
        profile = {
            "name": "GitLab",
            "aliases": ["GitLab Inc."],
            "industry": "saas",
            "description": "GitLab is an enterprise AI-powered DevSecOps platform.",
            "offerings": ["GitLab Duo", "GitLab CI/CD", "GitLab Ultimate"],
            "markets": ["Global Enterprise DevSecOps"],
            "strategic_goals": "Expand AI-assisted DevSecOps market share with GitLab Duo.",
            "website": "https://about.gitlab.com",
            "socials": [],
            "completed": True,
        }

    return CompanySetupOut(
        name=profile.get("name", ""),
        aliases=profile.get("aliases", []),
        industry=profile.get("industry"),
        description=profile.get("description", ""),
        offerings=profile.get("offerings", []),
        markets=profile.get("markets", []),
        strategic_goals=profile.get("strategic_goals", ""),
        website=profile.get("website", ""),
        socials=profile.get("socials", []),
        documents=[CompanyDocumentOut(**d) for d in documents],
        completed=bool(profile.get("completed", False)),
    )


@router.put("/company", response_model=CompanySetupOut)
async def save_company(
    fields: CompanyFields,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> CompanySetupOut:
    fields_dict = fields.model_dump()
    await save_company_profile(
        db,
        session.workspace.workspace_id,
        fields_dict,
        completed=True,
    )

    # Save to disk as active_profile.json
    active_profile_path = settings.BASE_DIR / "data" / "profiles" / "active_profile.json"
    try:
        active_profile_path.parent.mkdir(parents=True, exist_ok=True)
        disk_data = {
            "name": fields.name.strip() or "Unnamed Company",
            "aliases": fields.aliases,
            "industry": fields.industry,
            "description": fields.description,
            "notes": fields.description,
            "offerings": fields.offerings,
            "products": fields.offerings,
            "markets": fields.markets,
            "strategic_goals": fields.strategic_goals,
            "website": fields.website,
            "socials": [s.model_dump() for s in fields.socials],
        }
        with open(active_profile_path, "w", encoding="utf-8") as f:
            json.dump(disk_data, f, indent=2)
    except Exception as exc:
        logger.warning("Could not write active_profile.json to disk: %s", exc)

    documents = await list_company_documents(db, session.workspace.workspace_id)
    return CompanySetupOut(
        **fields_dict,
        documents=[CompanyDocumentOut(**d) for d in documents],
        completed=True,
    )


@router.post("/company/documents", response_model=list[CompanyDocumentOut])
async def upload_documents(
    files: list[UploadFile] = File(...),
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[CompanyDocumentOut]:
    from edrak.api.ingestion import (
        extract_document_text,
        index_text_into_rag,
        ingest_company_profile_data,
        is_company_profile_json,
    )

    saved_docs: list[CompanyDocumentOut] = []
    upload_dir = settings.BASE_DIR / "data" / "internal"
    upload_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        filename = file.filename or f"doc_{doc_id}.txt"
        dest_path = upload_dir / filename

        content = await file.read()
        dest_path.write_bytes(content)

        extracted_text, size_dict = extract_document_text(filename, content)

        # 1. Auto-detect and ingest Company Profile JSON
        if filename.lower().endswith(".json"):
            try:
                parsed_json = json.loads(content.decode("utf-8", errors="replace"))
                if is_company_profile_json(parsed_json):
                    await ingest_company_profile_data(
                        parsed_json, session.workspace.workspace_id, db
                    )
            except Exception as exc:
                logger.warning("Failed parsing uploaded JSON %s: %s", filename, exc)

        # 2. Ingest text into RAG vector store for PDF, MD, TXT, DOCX, etc.
        if extracted_text:
            index_text_into_rag(filename, extracted_text)

        doc = await add_company_document(
            db,
            document_id=doc_id,
            workspace_id=session.workspace.workspace_id,
            filename=filename,
            size_dict=size_dict,
            status="indexed",
        )
        saved_docs.append(CompanyDocumentOut(**doc))

    return saved_docs



@router.get("/company/documents", response_model=list[CompanyDocumentOut])
async def list_documents(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[CompanyDocumentOut]:
    docs = await list_company_documents(db, session.workspace.workspace_id)
    return [CompanyDocumentOut(**d) for d in docs]


@router.delete("/company/documents/{document_id}")
async def remove_document(
    document_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    cursor = await db.execute(
        "SELECT filename FROM company_documents WHERE document_id = ? AND workspace_id = ?;",
        (document_id, session.workspace.workspace_id),
    )
    row = await cursor.fetchone()
    if row:
        filename = row["filename"]
        # Remove from data/internal
        file_path = settings.BASE_DIR / "data" / "internal" / filename
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception as exc:
                logger.warning("Could not delete file %s from disk: %s", file_path, exc)

        # Remove from ChromaDB collection
        try:
            from edrak.rag.indexer import InternalIndexer

            indexer = InternalIndexer()
            indexer.collection.delete(where={"filename": filename})
        except Exception as exc:
            logger.warning("Could not remove RAG chunks for %s: %s", filename, exc)

    deleted = await delete_company_document(db, document_id, session.workspace.workspace_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="not_found")
    return {"status": "deleted"}


# ----------------------------------------------------------------------
# Analyses & Planning
# ----------------------------------------------------------------------


@router.get("/analyses", response_model=list[AnalysisSummaryOut])
async def get_analyses_list(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[AnalysisSummaryOut]:
    rows = await list_analyses(db, session.workspace.workspace_id)
    return [AnalysisSummaryOut(**r) for r in rows]


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetailOut)
async def get_analysis_detail(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> AnalysisDetailOut:
    row = await get_analysis(db, analysis_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")

    form = json.loads(row["form_json"])
    request = json.loads(row["request_json"])
    plan = json.loads(row["plan_json"]) if row.get("plan_json") else None

    allowed_sources = [k for k, v in form.get("sources", {}).items() if v]

    return AnalysisDetailOut(
        analysis_id=row["analysis_id"],
        title=row["title"],
        status=row["status"],
        request=BusinessRequest.model_validate(request),
        plan=ResearchPlan.model_validate(plan) if plan else None,
        form=AnalysisForm(**form),
        rejection_reason=row.get("rejection_reason"),
        allowed_sources=allowed_sources,
    )


@router.post("/analyses/draft", response_model=AnalysisDetailOut)
async def draft_plan(
    req: DraftPlanRequest,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> AnalysisDetailOut:
    form = req.form
    analysis_id = req.analysis_id or f"analysis-{uuid.uuid4().hex[:8]}"
    title = derive_title(form)

    # Fetch company profile from DB or disk
    company_data = await get_company_profile(db, session.workspace.workspace_id)
    company_name = company_data.get("name", "GitLab") if company_data else "GitLab"
    company_aliases = company_data.get("aliases", []) if company_data else []
    company_offerings = company_data.get("offerings", []) if company_data else []
    company_desc = company_data.get("description", "") if company_data else ""

    profile = CompanyProfile(
        name=company_name,
        aliases=company_aliases,
        products=company_offerings,
        notes=company_desc or None,
    )

    targets = list(dict.fromkeys([c.strip() for c in form.competitors if c.strip()] + ([form.market.strip()] if form.market.strip() else [])))
    focus_areas = [FOCUS_PHRASE.get(f, f) for f in form.focus if f in FOCUS_PHRASE]

    business_request = BusinessRequest(
        request_id=analysis_id,
        goal=form.goal.strip() or "Strategic competitive assessment",
        company_profile=profile,
        business_context=BusinessContext(
            use_case=form.use_case,
            targets=targets,
            focus_areas=focus_areas,
            time_window_days=form.time_window_days,
            trigger=TriggerType.ON_DEMAND,
        ),
    )

    # Determine allowed workers based on selected sources
    allowed_workers = [
        worker
        for src_key, worker in SOURCE_TO_WORKER.items()
        if form.sources.get(src_key, True)
    ]
    if not allowed_workers:
        allowed_workers = list(SOURCE_TO_WORKER.values())

    # Draft plan using LlmPlanner with fallback
    plan: Optional[ResearchPlan] = None
    try:
        from edrak.orchestration.planner import LlmPlanner

        full_plan = LlmPlanner().plan(business_request)
        # Filter tasks to allowed workers only
        filtered_tasks = [t for t in full_plan.tasks if t.worker in allowed_workers]
        if filtered_tasks:
            plan = ResearchPlan(
                request_id=analysis_id,
                tasks=filtered_tasks,
                rationale=full_plan.rationale,
            )
    except Exception as exc:
        logger.info("Planner using standard task formulation: %s", exc)

    if not plan:
        tasks = [
            ResearchTask(
                parent_request_id=analysis_id,
                worker=w,
                goal=f"Analyze {w.value.replace('_', ' ')} regarding {business_request.goal}",
                focus=f"Evaluate {' and '.join(focus_areas) or 'key trends and indicators'}",
                company_profile=profile,
                business_context=business_request.business_context,
            )
            for w in allowed_workers
        ]
        plan = ResearchPlan(
            request_id=analysis_id,
            tasks=tasks,
            rationale=f"Comprehensive multi-domain research covering {len(tasks)} intelligence workers.",
        )

    # Save to database with status 'awaiting_approval'
    await create_or_update_analysis(
        db,
        analysis_id=analysis_id,
        workspace_id=session.workspace.workspace_id,
        title=title,
        status="awaiting_approval",
        form_dict=form.model_dump(),
        request_dict=business_request.model_dump(mode="json"),
        plan_dict=plan.model_dump(mode="json") if plan else None,
        rejection_reason=None,
    )

    return AnalysisDetailOut(
        analysis_id=analysis_id,
        title=title,
        status="awaiting_approval",
        request=business_request,
        plan=plan,
        form=form,
        rejection_reason=None,
        allowed_sources=[k for k, v in form.sources.items() if v],
    )


@router.post("/analyses/{analysis_id}/approve")
async def approve_plan(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    analysis = await get_analysis(db, analysis_id, session.workspace.workspace_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="not_found")

    await run_manager.start_run(analysis_id, session.user.name)
    return {"status": "started", "analysis_id": analysis_id}


@router.post("/analyses/{analysis_id}/reject")
async def reject_plan(
    analysis_id: str,
    req: RejectPlanRequest,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    analysis = await get_analysis(db, analysis_id, session.workspace.workspace_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="not_found")

    await update_analysis_status(db, analysis_id, "draft", rejection_reason=req.reason)
    return {"status": "rejected", "analysis_id": analysis_id}


@router.post("/analyses/{analysis_id}/rerun")
async def rerun_analysis(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    analysis = await get_analysis(db, analysis_id, session.workspace.workspace_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="not_found")

    form_dict = json.loads(analysis["form_json"])
    form = AnalysisForm(**form_dict)
    draft_req = DraftPlanRequest(form=form, analysis_id=analysis_id)
    res = await draft_plan(draft_req, session=session, db=db)
    return {"analysis_id": res.analysis_id}


# ----------------------------------------------------------------------
# Live Run
# ----------------------------------------------------------------------


@router.get("/analyses/{analysis_id}/run", response_model=RunViewOut)
async def get_run_view(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
) -> RunViewOut:
    view = await run_manager.get_run_view(analysis_id)
    if not view:
        raise HTTPException(status_code=404, detail="not_found")
    return view


@router.get("/analyses/{analysis_id}/run/stream")
async def stream_run(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
):
    stream = run_manager.get_stream(analysis_id)
    if not stream:
        # If run is already completed or not active, yield single snapshot event
        view = await run_manager.get_run_view(analysis_id)
        if not view:
            raise HTTPException(status_code=404, detail="not_found")

        async def single_event():
            from edrak.api.streaming import format_sse_event

            yield format_sse_event("complete", {"state": view.state, "brief_id": view.brief_id})

        return StreamingResponse(single_event(), media_type="text/event-stream")

    return StreamingResponse(
        stream.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/analyses/{analysis_id}/cancel")
async def cancel_run_endpoint(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
) -> dict[str, str]:
    await run_manager.cancel_run(analysis_id)
    return {"status": "cancelled", "analysis_id": analysis_id}


# ----------------------------------------------------------------------
# Brief
# ----------------------------------------------------------------------


@router.get("/briefs/{brief_id}")
async def get_brief_endpoint(
    brief_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    brief = await get_brief(db, brief_id)
    if not brief:
        raise HTTPException(status_code=404, detail="not_found")
    return brief
