from fastapi import APIRouter, BackgroundTasks, Query, status
from sqlalchemy.orm import sessionmaker

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.api_definitions.schemas import (
    ApiDefinitionListResponse,
    ApiDefinitionResponse,
    ApiDesignSuggestionCreate,
    ApiDesignSuggestionDecision,
    ApiDesignSuggestionEdit,
    ApiDesignSuggestionListResponse,
    ApiDesignSuggestionResponse,
    ApiScenarioPlanCreate,
    ApiScenarioPlanGenerate,
    ApiScenarioPlanGenerateResponse,
    ApiScenarioPlanListResponse,
    ApiScenarioPlanResponse,
    OpenApiImportRequest,
    OpenApiImportResponse,
    OpenApiPreviewResponse,
)
from app.modules.api_definitions.service import (
    create_design_suggestion_task,
    create_plan_item_suggestion_tasks,
    create_scenario_plan_task,
    decide_design_suggestion,
    edit_design_suggestion,
    get_definition,
    import_openapi,
    list_definitions,
    list_design_suggestions,
    list_scenario_plans,
    preview_import,
    process_design_suggestion_task,
    process_scenario_plan_task,
)

router = APIRouter()


@router.post("/import/preview", response_model=OpenApiPreviewResponse, summary="预览 OpenAPI 导入")
def preview_import_route(
    payload: OpenApiImportRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> OpenApiPreviewResponse:
    return preview_import(session, current_user, payload)


@router.post(
    "/import",
    response_model=OpenApiImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="确认导入 OpenAPI",
)
def import_openapi_route(
    payload: OpenApiImportRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> OpenApiImportResponse:
    return import_openapi(session, current_user, payload)


@router.get("", response_model=ApiDefinitionListResponse, summary="API 定义列表")
def list_definitions_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    include_removed: bool = False,
) -> ApiDefinitionListResponse:
    return list_definitions(
        session, current_user, project_id, include_removed=include_removed
    )


@router.get(
    "/imports/{import_id}/ai-suggestions",
    response_model=ApiDesignSuggestionListResponse,
    summary="OpenAPI AI Case/Scenario 建议历史",
)
def list_design_suggestions_route(
    import_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiDesignSuggestionListResponse:
    return list_design_suggestions(session, current_user, import_id)


@router.get(
    "/imports/{import_id}/scenario-plans",
    response_model=ApiScenarioPlanListResponse,
    summary="场景方案目录历史",
)
def list_scenario_plans_route(
    import_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiScenarioPlanListResponse:
    return list_scenario_plans(session, current_user, import_id)


@router.post(
    "/imports/{import_id}/scenario-plans",
    response_model=ApiScenarioPlanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="根据固定需求范围生成场景方案目录",
)
def create_scenario_plan_route(
    import_id: int,
    payload: ApiScenarioPlanCreate,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiScenarioPlanResponse:
    plan = create_scenario_plan_task(session, current_user, import_id, payload)
    if plan.generation_status in {"QUEUED", "RUNNING"} and not plan.reused:
        session_factory = sessionmaker(
            bind=session.get_bind(), autoflush=False, expire_on_commit=False
        )
        background_tasks.add_task(
            process_scenario_plan_task, session_factory, plan.id, current_user
        )
    return plan


@router.post(
    "/scenario-plans/{plan_id}/generate",
    response_model=ApiScenarioPlanGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="为选中的候选场景分别生成完整编排",
)
def generate_scenario_plan_items_route(
    plan_id: int,
    payload: ApiScenarioPlanGenerate,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiScenarioPlanGenerateResponse:
    response, created_ids = create_plan_item_suggestion_tasks(
        session, current_user, plan_id, payload
    )
    session_factory = sessionmaker(
        bind=session.get_bind(), autoflush=False, expire_on_commit=False
    )
    for suggestion_id in created_ids:
        background_tasks.add_task(
            process_design_suggestion_task,
            session_factory,
            suggestion_id,
            current_user,
        )
    return response


@router.post(
    "/imports/{import_id}/ai-suggestions",
    response_model=ApiDesignSuggestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="从固定 OpenAPI 导入版本生成 AI 建议",
)
def generate_design_suggestion_route(
    import_id: int,
    payload: ApiDesignSuggestionCreate,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiDesignSuggestionResponse:
    suggestion = create_design_suggestion_task(
        session, current_user, import_id, payload
    )
    if (
        suggestion.generation_status in {"QUEUED", "RUNNING"}
        and not suggestion.reused
    ):
        session_factory = sessionmaker(
            bind=session.get_bind(), autoflush=False, expire_on_commit=False
        )
        background_tasks.add_task(
            process_design_suggestion_task,
            session_factory,
            suggestion.id,
            current_user,
        )
    return suggestion


@router.patch(
    "/ai-suggestions/{suggestion_id}",
    response_model=ApiDesignSuggestionResponse,
    summary="编辑 Swagger AI 建议草稿",
)
def edit_design_suggestion_route(
    suggestion_id: int,
    payload: ApiDesignSuggestionEdit,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiDesignSuggestionResponse:
    return edit_design_suggestion(session, current_user, suggestion_id, payload)


@router.post(
    "/ai-suggestions/{suggestion_id}/decision",
    response_model=ApiDesignSuggestionResponse,
    summary="接受或拒绝 Swagger AI 建议",
)
def decide_design_suggestion_route(
    suggestion_id: int,
    payload: ApiDesignSuggestionDecision,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiDesignSuggestionResponse:
    return decide_design_suggestion(session, current_user, suggestion_id, payload)


@router.get("/{definition_id}", response_model=ApiDefinitionResponse, summary="API 定义详情")
def get_definition_route(
    definition_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ApiDefinitionResponse:
    return get_definition(session, current_user, definition_id)
