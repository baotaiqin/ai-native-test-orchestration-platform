from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency

from .schemas import (
    DefectDraftGenerateRequest,
    DefectDraftListResponse,
    DefectDraftResponse,
    DefectDraftUpdateRequest,
)
from .service import (
    export_defect_draft_markdown,
    generate_defect_draft,
    get_defect_draft,
    list_defect_drafts,
    update_defect_draft,
)

router = APIRouter()
DraftIdPath = Annotated[int, Path(gt=0)]
RunIdPath = Annotated[str, Path(min_length=1, max_length=128)]


@router.post(
    "/runs/{run_id}/defect-drafts/generate",
    response_model=DefectDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_defect_draft_route(
    run_id: RunIdPath,
    payload: DefectDraftGenerateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DefectDraftResponse:
    return generate_defect_draft(session, current_user, run_id, payload)


@router.get("/defect-drafts", response_model=DefectDraftListResponse)
def list_defect_drafts_route(
    project_id: Annotated[int, Query(gt=0)],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    run_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    page: Annotated[int, Query(ge=1, le=2_147_483_647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DefectDraftListResponse:
    return list_defect_drafts(
        session,
        current_user,
        project_id,
        run_id=run_id,
        page=page,
        page_size=page_size,
    )


@router.get("/defect-drafts/{draft_id}", response_model=DefectDraftResponse)
def get_defect_draft_route(
    draft_id: DraftIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DefectDraftResponse:
    return get_defect_draft(session, current_user, draft_id)


@router.patch("/defect-drafts/{draft_id}", response_model=DefectDraftResponse)
def update_defect_draft_route(
    draft_id: DraftIdPath,
    payload: DefectDraftUpdateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> DefectDraftResponse:
    return update_defect_draft(session, current_user, draft_id, payload)


@router.get("/defect-drafts/{draft_id}/export", response_class=Response)
def export_defect_draft_route(
    draft_id: DraftIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    exported = export_defect_draft_markdown(session, current_user, draft_id)
    return Response(
        content=exported.content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
