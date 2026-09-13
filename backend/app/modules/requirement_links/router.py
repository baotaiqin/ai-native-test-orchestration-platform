from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency

from .schemas import (
    RequirementAssetType,
    RequirementImpactPage,
    RequirementImpactQuery,
    RequirementLinkCreate,
    RequirementLinkPage,
    RequirementLinkQuery,
    RequirementLinkResponse,
    RequirementTracePage,
    RequirementTraceQuery,
)
from .service import (
    analyze_requirement_impact,
    create_requirement_link,
    list_asset_requirements,
    list_requirement_links,
    remove_requirement_link,
    trace_requirement_runs,
)

router = APIRouter()
IdPath = Annotated[int, Path(gt=0, le=2_147_483_647)]


@router.post(
    "/requirements/{requirement_id}/links",
    response_model=RequirementLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建精确需求关联",
)
def create_requirement_link_route(
    requirement_id: IdPath,
    payload: RequirementLinkCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementLinkResponse:
    return create_requirement_link(
        session,
        current_user,
        requirement_id,
        payload,
    )


@router.get(
    "/requirements/{requirement_id}/links",
    response_model=RequirementLinkPage,
    summary="分页读取需求关联",
)
def list_requirement_links_route(
    requirement_id: IdPath,
    filters: Annotated[RequirementLinkQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementLinkPage:
    return list_requirement_links(
        session,
        current_user,
        requirement_id,
        filters,
    )


@router.delete(
    "/requirements/{requirement_id}/links/{link_id}",
    response_model=RequirementLinkResponse,
    summary="保留历史地移除当前需求关联",
)
def remove_requirement_link_route(
    requirement_id: IdPath,
    link_id: IdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementLinkResponse:
    return remove_requirement_link(
        session,
        current_user,
        requirement_id,
        link_id,
    )


@router.get(
    "/requirements/{requirement_id}/impact",
    response_model=RequirementImpactPage,
    summary="确定性需求变化影响范围",
)
def analyze_requirement_impact_route(
    requirement_id: IdPath,
    filters: Annotated[RequirementImpactQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementImpactPage:
    return analyze_requirement_impact(
        session,
        current_user,
        requirement_id,
        filters,
    )


@router.get(
    "/requirements/{requirement_id}/traceability",
    response_model=RequirementTracePage,
    summary="需求到历史 Run 与 Evidence 的不可变追溯",
)
def trace_requirement_runs_route(
    requirement_id: IdPath,
    filters: Annotated[RequirementTraceQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementTracePage:
    return trace_requirement_runs(session, current_user, requirement_id, filters)


@router.get(
    "/test-cases/{asset_id}/requirements",
    response_model=RequirementLinkPage,
    summary="TestCase 反向需求关联",
)
def list_test_case_requirements_route(
    asset_id: IdPath,
    filters: Annotated[RequirementLinkQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementLinkPage:
    return list_asset_requirements(
        session,
        current_user,
        RequirementAssetType.TEST_CASE,
        asset_id,
        filters,
    )


@router.get(
    "/web-cases/{asset_id}/requirements",
    response_model=RequirementLinkPage,
    summary="独立 WebCase 反向需求关联",
)
def list_web_case_requirements_route(
    asset_id: IdPath,
    filters: Annotated[RequirementLinkQuery, Query()],
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementLinkPage:
    return list_asset_requirements(
        session,
        current_user,
        RequirementAssetType.WEB_CASE,
        asset_id,
        filters,
    )
