from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.requirements.parser import parse_markdown_tree
from app.modules.requirements.schemas import (
    MarkdownImportRequest,
    MarkdownPreviewResponse,
    RequirementCreate,
    RequirementDiffResponse,
    RequirementDocumentPublishResponse,
    RequirementDocumentVersionCreate,
    RequirementDocumentVersionResponse,
    RequirementImportResponse,
    RequirementResponse,
    RequirementTreeResponse,
    RequirementVersionCreate,
    RequirementVersionResponse,
)
from app.modules.requirements.service import (
    create_requirement,
    create_requirement_version,
    diff_versions,
    get_current_document_version,
    get_requirement,
    import_markdown,
    list_document_versions,
    list_requirement_tree,
    list_versions,
    publish_document_version,
    requirement_response,
    set_current_version,
)

router = APIRouter()


@router.get("", response_model=RequirementTreeResponse, summary="需求树")
def list_requirements_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> RequirementTreeResponse:
    roots, total = list_requirement_tree(session, current_user, project_id)
    current_document_version = get_current_document_version(
        session, current_user, project_id
    )
    return RequirementTreeResponse(
        items=roots,
        total=total,
        current_document_version=(
            RequirementDocumentVersionResponse.model_validate(current_document_version)
            if current_document_version
            else None
        ),
    )


@router.get(
    "/projects/{project_id}/document-versions",
    response_model=list[RequirementDocumentVersionResponse],
    summary="完整需求文档版本列表",
)
def list_document_versions_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[RequirementDocumentVersionResponse]:
    return [
        RequirementDocumentVersionResponse.model_validate(version)
        for version in list_document_versions(session, current_user, project_id)
    ]


@router.post(
    "/projects/{project_id}/document-versions",
    response_model=RequirementDocumentPublishResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发布完整需求文档新版本",
)
def publish_document_version_route(
    project_id: int,
    payload: RequirementDocumentVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementDocumentPublishResponse:
    version, roots, total = publish_document_version(
        session, current_user, project_id, payload
    )
    return RequirementDocumentPublishResponse(
        document_version=RequirementDocumentVersionResponse.model_validate(version),
        root_items=roots,
        active_count=total,
    )


@router.post(
    "", response_model=RequirementResponse, status_code=status.HTTP_201_CREATED, summary="创建需求"
)
def create_requirement_route(
    payload: RequirementCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementResponse:
    requirement = create_requirement(session, current_user, payload)
    return requirement_response(session, requirement)


@router.post(
    "/import/preview", response_model=MarkdownPreviewResponse, summary="预览 Markdown 需求树"
)
def preview_markdown_route(payload: MarkdownImportRequest) -> MarkdownPreviewResponse:
    nodes = parse_markdown_tree(payload.content, payload.filename)
    return MarkdownPreviewResponse(filename=payload.filename, nodes=nodes, total=len(nodes))


@router.post(
    "/import",
    response_model=RequirementImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="确认导入 Markdown",
)
def import_markdown_route(
    payload: MarkdownImportRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementImportResponse:
    roots, count, document_version = import_markdown(session, current_user, payload)
    return RequirementImportResponse(
        root_items=roots,
        created_count=count,
        document_version=RequirementDocumentVersionResponse.model_validate(document_version),
    )


@router.get("/{requirement_id}", response_model=RequirementResponse, summary="需求详情")
def get_requirement_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementResponse:
    return get_requirement(session, current_user, requirement_id)


@router.post(
    "/{requirement_id}/versions", response_model=RequirementResponse, summary="创建需求新版本"
)
def create_version_route(
    requirement_id: int,
    payload: RequirementVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementResponse:
    return create_requirement_version(session, current_user, requirement_id, payload)


@router.get(
    "/{requirement_id}/versions",
    response_model=list[RequirementVersionResponse],
    summary="需求版本列表",
)
def list_versions_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[RequirementVersionResponse]:
    versions = list_versions(session, current_user, requirement_id)
    return [RequirementVersionResponse.model_validate(version) for version in versions]


@router.post(
    "/{requirement_id}/versions/{version_id}/current",
    response_model=RequirementResponse,
    summary="切换当前需求版本",
)
def set_current_version_route(
    requirement_id: int,
    version_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementResponse:
    return set_current_version(session, current_user, requirement_id, version_id)


@router.get(
    "/{requirement_id}/diff", response_model=RequirementDiffResponse, summary="需求版本差异"
)
def diff_versions_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    from_version: int = Query(ge=1),
    to_version: int = Query(ge=1),
) -> RequirementDiffResponse:
    return diff_versions(
        session, current_user, requirement_id, from_version, to_version
    )
