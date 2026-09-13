from fastapi import APIRouter, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.prompt_center.schemas import (
    ProjectPromptTemplateListResponse,
    ProjectPromptTemplateResponse,
    ProjectPromptVersionCreate,
    PromptCopyRequest,
    PromptCreate,
    PromptListResponse,
    PromptRenderRequest,
    PromptRenderResponse,
    PromptResponse,
    PromptUpdate,
    PromptVersionCreate,
    PromptVersionResponse,
    SystemPromptTemplateUpdate,
)
from app.modules.prompt_center.service import (
    copy_prompt,
    create_prompt,
    create_version,
    list_project_prompt_templates,
    list_project_prompt_versions,
    list_prompts,
    list_versions,
    render_prompt,
    restore_project_prompt_default,
    restore_system_prompt_template,
    rollback_version,
    save_project_prompt_version,
    update_prompt,
    update_system_prompt_template,
)

router = APIRouter()


@router.get("", response_model=PromptListResponse, summary="Prompt 列表")
def list_prompts_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    task_type: str | None = None,
    include_disabled: bool = False,
) -> PromptListResponse:
    items = list_prompts(
        session, current_user, task_type=task_type, include_disabled=include_disabled
    )
    return PromptListResponse(items=items, total=len(items))


@router.post("", response_model=PromptResponse, status_code=status.HTTP_201_CREATED)
def create_prompt_route(
    payload: PromptCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return create_prompt(session, current_user, payload)


@router.get(
    "/project-templates",
    response_model=ProjectPromptTemplateListResponse,
    summary="项目提示词模板",
)
def list_project_prompt_templates_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectPromptTemplateListResponse:
    items = list_project_prompt_templates(session, current_user, project_id)
    return ProjectPromptTemplateListResponse(items=items, total=len(items))


@router.get(
    "/project-templates/{base_prompt_id}/versions",
    response_model=list[PromptVersionResponse],
)
def list_project_prompt_versions_route(
    base_prompt_id: int,
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[PromptVersionResponse]:
    return [
        PromptVersionResponse.model_validate(item)
        for item in list_project_prompt_versions(
            session, current_user, project_id, base_prompt_id
        )
    ]


@router.post(
    "/project-templates/{base_prompt_id}/versions",
    response_model=ProjectPromptTemplateResponse,
)
def save_project_prompt_version_route(
    base_prompt_id: int,
    project_id: int,
    payload: ProjectPromptVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectPromptTemplateResponse:
    return save_project_prompt_version(
        session, current_user, project_id, base_prompt_id, payload
    )


@router.post(
    "/project-templates/{base_prompt_id}/restore",
    response_model=ProjectPromptTemplateResponse,
)
def restore_project_prompt_default_route(
    base_prompt_id: int,
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ProjectPromptTemplateResponse:
    return restore_project_prompt_default(
        session, current_user, project_id, base_prompt_id
    )


@router.patch(
    "/system-templates/{prompt_id}",
    response_model=PromptResponse,
)
def update_system_prompt_template_route(
    prompt_id: int,
    payload: SystemPromptTemplateUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return update_system_prompt_template(session, current_user, prompt_id, payload)


@router.post(
    "/system-templates/{prompt_id}/restore",
    response_model=PromptResponse,
)
def restore_system_prompt_template_route(
    prompt_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return restore_system_prompt_template(session, current_user, prompt_id)


@router.patch("/{prompt_id}", response_model=PromptResponse)
def update_prompt_route(
    prompt_id: int,
    payload: PromptUpdate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return update_prompt(session, current_user, prompt_id, payload)


@router.post("/{prompt_id}/copy", response_model=PromptResponse, status_code=201)
def copy_prompt_route(
    prompt_id: int,
    payload: PromptCopyRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return copy_prompt(session, current_user, prompt_id, payload)


@router.post("/{prompt_id}/render", response_model=PromptRenderResponse)
def render_prompt_route(
    prompt_id: int,
    payload: PromptRenderRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptRenderResponse:
    return render_prompt(session, current_user, prompt_id, payload)


@router.get("/{prompt_id}/versions", response_model=list[PromptVersionResponse])
def list_versions_route(
    prompt_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[PromptVersionResponse]:
    return [
        PromptVersionResponse.model_validate(item)
        for item in list_versions(session, current_user, prompt_id)
    ]


@router.post("/{prompt_id}/versions", response_model=PromptResponse)
def create_version_route(
    prompt_id: int,
    payload: PromptVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return create_version(session, current_user, prompt_id, payload)


@router.post("/{prompt_id}/versions/{version_id}/rollback", response_model=PromptResponse)
def rollback_version_route(
    prompt_id: int,
    version_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> PromptResponse:
    return rollback_version(session, current_user, prompt_id, version_id)
