from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from sqlalchemy.orm import sessionmaker

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.test_cases.runtime import preview_runtime
from app.modules.test_cases.schemas import (
    CaseDesignPlanResponse,
    CaseDesignTaskCreate,
    CaseDesignTaskListResponse,
    CaseDesignTaskResponse,
    CaseGenerationListResponse,
    CaseGenerationRecompileResponse,
    CaseGenerationRequest,
    CaseGenerationResponse,
    CaseGenerationTaskListResponse,
    CaseGenerationTaskResponse,
    CaseSuggestionBulkDecision,
    CaseSuggestionBulkDeleteRequest,
    CaseSuggestionBulkDeleteResponse,
    CaseSuggestionBulkEdit,
    CaseSuggestionDecision,
    CaseSuggestionEdit,
    CaseSuggestionResponse,
    RequirementCaseLinkResponse,
    RuntimePreviewRequest,
    RuntimePreviewResponse,
    TestCaseCreate,
    TestCaseDetailResponse,
    TestCaseResponse,
    TestCaseVersionCreate,
    TestCaseVersionResponse,
)
from app.modules.test_cases.service import (
    archive_test_case,
    build_case_design_plan,
    bulk_decide_suggestions,
    bulk_delete_suggestions,
    bulk_edit_suggestions,
    create_case_design_task,
    create_case_generation_task,
    create_test_case,
    create_test_case_version,
    decide_suggestion,
    delete_case_design_task,
    edit_suggestion,
    generate_case_suggestions,
    get_test_case,
    list_case_design_tasks,
    list_case_generation_tasks,
    list_case_generations,
    list_project_case_design_tasks,
    list_project_cases,
    list_requirement_case_links,
    list_test_case_versions,
    process_case_design_task,
    process_case_generation_task,
    recompile_generation_cases,
)

router = APIRouter()


@router.post("/runtime/preview", response_model=RuntimePreviewResponse)
def preview_runtime_route(
    payload: RuntimePreviewRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RuntimePreviewResponse:
    return preview_runtime(payload, session, current_user)


@router.post("", response_model=TestCaseDetailResponse, status_code=status.HTTP_201_CREATED)
def create_test_case_route(
    payload: TestCaseCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestCaseDetailResponse:
    return create_test_case(session, current_user, payload)


@router.get("", response_model=list[TestCaseResponse])
def list_project_cases_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> list[TestCaseResponse]:
    return list_project_cases(session, current_user, project_id)


@router.get("/{case_id}", response_model=TestCaseDetailResponse)
def get_test_case_route(
    case_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestCaseDetailResponse:
    return get_test_case(session, current_user, case_id)


@router.get("/{case_id}/versions", response_model=list[TestCaseVersionResponse])
def list_test_case_versions_route(
    case_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[TestCaseVersionResponse]:
    return list_test_case_versions(session, current_user, case_id)


@router.post(
    "/{case_id}/versions",
    response_model=TestCaseVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_test_case_version_route(
    case_id: int,
    payload: TestCaseVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestCaseVersionResponse:
    return create_test_case_version(session, current_user, case_id, payload)


@router.post("/{case_id}/archive", response_model=TestCaseResponse)
def archive_test_case_route(
    case_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> TestCaseResponse:
    return archive_test_case(session, current_user, case_id)


@router.get(
    "/requirements/{requirement_id}/design-plan",
    response_model=CaseDesignPlanResponse,
)
def get_case_design_plan_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    include_api_ids: Annotated[list[int] | None, Query()] = None,
) -> CaseDesignPlanResponse:
    return build_case_design_plan(
        session, current_user, requirement_id, include_api_ids=include_api_ids
    )


@router.get(
    "/requirements/{requirement_id}/design-tasks",
    response_model=CaseDesignTaskListResponse,
)
def list_design_tasks_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> CaseDesignTaskListResponse:
    return list_case_design_tasks(
        session, current_user, requirement_id, page=page, page_size=page_size
    )


@router.get(
    "/projects/{project_id}/design-tasks",
    response_model=CaseDesignTaskListResponse,
)
def list_project_design_tasks_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> CaseDesignTaskListResponse:
    return list_project_case_design_tasks(
        session, current_user, project_id, page=page, page_size=page_size
    )


@router.post(
    "/requirements/{requirement_id}/design-tasks",
    response_model=CaseDesignTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_design_task_route(
    requirement_id: int,
    payload: CaseDesignTaskCreate,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseDesignTaskResponse:
    task = create_case_design_task(session, current_user, requirement_id, payload)
    if task.status in {"QUEUED", "RUNNING"} and not task.reused:
        session_factory = sessionmaker(
            bind=session.get_bind(), autoflush=False, expire_on_commit=False
        )
        background_tasks.add_task(
            process_case_design_task, session_factory, task.id, current_user
        )
    return task


@router.delete("/design-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_design_task_route(
    task_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    delete_case_design_task(session, current_user, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/requirements/{requirement_id}/generations",
    response_model=CaseGenerationListResponse,
)
def list_generations_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseGenerationListResponse:
    return list_case_generations(session, current_user, requirement_id)


@router.get(
    "/requirements/{requirement_id}/generation-tasks",
    response_model=CaseGenerationTaskListResponse,
)
def list_generation_tasks_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseGenerationTaskListResponse:
    return list_case_generation_tasks(session, current_user, requirement_id)


@router.post(
    "/requirements/{requirement_id}/generation-tasks",
    response_model=CaseGenerationTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_generation_task_route(
    requirement_id: int,
    payload: CaseGenerationRequest,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseGenerationTaskResponse:
    task = create_case_generation_task(session, current_user, requirement_id, payload)
    session_factory = sessionmaker(
        bind=session.get_bind(), autoflush=False, expire_on_commit=False
    )
    background_tasks.add_task(
        process_case_generation_task, session_factory, task.id, current_user
    )
    return task


@router.post(
    "/requirements/{requirement_id}/generations",
    response_model=CaseGenerationResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_suggestions_route(
    requirement_id: int,
    payload: CaseGenerationRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseGenerationResponse:
    return generate_case_suggestions(session, current_user, requirement_id, payload)


@router.post(
    "/generations/{generation_id}/recompile",
    response_model=CaseGenerationRecompileResponse,
)
def recompile_generation_cases_route(
    generation_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseGenerationRecompileResponse:
    return recompile_generation_cases(session, current_user, generation_id)


@router.patch(
    "/suggestions/{suggestion_id}", response_model=CaseSuggestionResponse
)
def edit_suggestion_route(
    suggestion_id: int,
    payload: CaseSuggestionEdit,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseSuggestionResponse:
    return edit_suggestion(session, current_user, suggestion_id, payload)


@router.post(
    "/suggestions/{suggestion_id}/decision", response_model=CaseSuggestionResponse
)
def decide_suggestion_route(
    suggestion_id: int,
    payload: CaseSuggestionDecision,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseSuggestionResponse:
    return decide_suggestion(session, current_user, suggestion_id, payload)


@router.post(
    "/suggestions/bulk-decision", response_model=list[CaseSuggestionResponse]
)
def bulk_decide_suggestions_route(
    payload: CaseSuggestionBulkDecision,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[CaseSuggestionResponse]:
    return bulk_decide_suggestions(session, current_user, payload)


@router.post("/suggestions/bulk-edit", response_model=list[CaseSuggestionResponse])
def bulk_edit_suggestions_route(
    payload: CaseSuggestionBulkEdit,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[CaseSuggestionResponse]:
    return bulk_edit_suggestions(session, current_user, payload)


@router.post(
    "/suggestions/bulk-delete", response_model=CaseSuggestionBulkDeleteResponse
)
def bulk_delete_suggestions_route(
    payload: CaseSuggestionBulkDeleteRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> CaseSuggestionBulkDeleteResponse:
    return bulk_delete_suggestions(session, current_user, payload.suggestion_ids)


@router.get(
    "/requirements/{requirement_id}/links",
    response_model=list[RequirementCaseLinkResponse],
)
def list_requirement_links_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[RequirementCaseLinkResponse]:
    return list_requirement_case_links(session, current_user, requirement_id)
