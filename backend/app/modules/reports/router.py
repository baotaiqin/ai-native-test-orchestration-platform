from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Response

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency

from .exports import create_report_export, export_response_headers
from .schemas import (
    ReportCasePage,
    ReportDetailResponse,
    ReportEvidencePage,
    ReportEvidencePageQuery,
    ReportListQuery,
    ReportListResponse,
    ReportPageQuery,
    ReportRequirementSourcePage,
    ReportRequirementSourcePageQuery,
    ReportStepPage,
    ReportStepPageQuery,
)
from .service import (
    get_report_detail,
    list_report_cases,
    list_report_evidence,
    list_report_requirement_sources,
    list_report_steps,
    list_reports,
)

router = APIRouter()
RunIdPath = Annotated[str, Path(min_length=1, max_length=128)]


@router.get("", response_model=ReportListResponse)
def list_reports_route(
    filters: Annotated[ReportListQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ReportListResponse:
    return list_reports(session, current_user, filters)


@router.get("/{run_id}/cases", response_model=ReportCasePage)
def list_report_cases_route(
    run_id: RunIdPath,
    filters: Annotated[ReportPageQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ReportCasePage:
    return list_report_cases(session, current_user, run_id, filters)


@router.get("/{run_id}/steps", response_model=ReportStepPage)
def list_report_steps_route(
    run_id: RunIdPath,
    filters: Annotated[ReportStepPageQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ReportStepPage:
    return list_report_steps(session, current_user, run_id, filters)


@router.get("/{run_id}/evidence", response_model=ReportEvidencePage)
def list_report_evidence_route(
    run_id: RunIdPath,
    filters: Annotated[ReportEvidencePageQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ReportEvidencePage:
    return list_report_evidence(session, current_user, run_id, filters)


@router.get(
    "/{run_id}/requirement-sources",
    response_model=ReportRequirementSourcePage,
)
def list_report_requirement_sources_route(
    run_id: RunIdPath,
    filters: Annotated[ReportRequirementSourcePageQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ReportRequirementSourcePage:
    return list_report_requirement_sources(
        session, current_user, run_id, filters
    )


@router.get("/{run_id}/export", response_class=Response)
def export_report_route(
    run_id: RunIdPath,
    export_format: Annotated[
        Literal["markdown", "html"],
        Query(alias="format", description="只读附件格式：markdown 或 html"),
    ],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    exported = create_report_export(
        session,
        current_user,
        run_id,
        export_format,
    )
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers=export_response_headers(exported),
    )


@router.get("/{run_id}", response_model=ReportDetailResponse)
def get_report_detail_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ReportDetailResponse:
    return get_report_detail(session, current_user, run_id)
