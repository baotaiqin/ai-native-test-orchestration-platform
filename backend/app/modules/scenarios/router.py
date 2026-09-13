from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.scenarios.executor import execute_preview
from app.modules.scenarios.schemas import (
    ScenarioAiBaselineResponse,
    ScenarioCreate,
    ScenarioDetailResponse,
    ScenarioExecutionPreviewRequest,
    ScenarioExecutionPreviewResponse,
    ScenarioListResponse,
    ScenarioPreviewProfileResponse,
    ScenarioPreviewProfileSave,
    ScenarioResponse,
    ScenarioValidationResponse,
    ScenarioVersionCreate,
    ScenarioVersionResponse,
)
from app.modules.scenarios.service import (
    approve_scenario,
    archive_scenario,
    create_scenario,
    create_scenario_version,
    delete_scenario,
    get_scenario,
    get_scenario_ai_baseline,
    get_scenario_preview_defaults,
    get_scenario_preview_profile,
    list_scenario_versions,
    list_scenarios,
    save_scenario_preview_profile,
    validate_dsl,
)

router = APIRouter()


@router.post("/execute-preview", response_model=ScenarioExecutionPreviewResponse)
def execute_preview_route(
    payload: ScenarioExecutionPreviewRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioExecutionPreviewResponse:
    return execute_preview(payload, session, current_user)


@router.post("/validate", response_model=ScenarioValidationResponse)
def validate_dsl_route(
    payload: ScenarioCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioValidationResponse:
    return validate_dsl(session, current_user, payload)


@router.post("", response_model=ScenarioDetailResponse, status_code=status.HTTP_201_CREATED)
def create_scenario_route(
    payload: ScenarioCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioDetailResponse:
    return create_scenario(session, current_user, payload)


@router.get("", response_model=ScenarioListResponse)
def list_scenarios_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
) -> ScenarioListResponse:
    return list_scenarios(session, current_user, project_id)


@router.get("/{scenario_id}", response_model=ScenarioDetailResponse)
def get_scenario_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioDetailResponse:
    return get_scenario(session, current_user, scenario_id)


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    delete_scenario(session, current_user, scenario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{scenario_id}/ai-baseline", response_model=ScenarioAiBaselineResponse
)
def get_scenario_ai_baseline_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioAiBaselineResponse:
    return get_scenario_ai_baseline(session, current_user, scenario_id)


@router.get(
    "/{scenario_id}/preview-profile", response_model=ScenarioPreviewProfileResponse
)
def get_scenario_preview_profile_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioPreviewProfileResponse:
    return get_scenario_preview_profile(session, current_user, scenario_id)


@router.get(
    "/{scenario_id}/preview-profile/defaults",
    response_model=ScenarioPreviewProfileResponse,
)
def get_scenario_preview_defaults_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioPreviewProfileResponse:
    return get_scenario_preview_defaults(session, current_user, scenario_id)


@router.put(
    "/{scenario_id}/preview-profile", response_model=ScenarioPreviewProfileResponse
)
def save_scenario_preview_profile_route(
    scenario_id: int,
    payload: ScenarioPreviewProfileSave,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioPreviewProfileResponse:
    return save_scenario_preview_profile(session, current_user, scenario_id, payload)


@router.get("/{scenario_id}/versions", response_model=list[ScenarioVersionResponse])
def list_scenario_versions_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> list[ScenarioVersionResponse]:
    return list_scenario_versions(session, current_user, scenario_id)


@router.post(
    "/{scenario_id}/versions",
    response_model=ScenarioVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_scenario_version_route(
    scenario_id: int,
    payload: ScenarioVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioVersionResponse:
    return create_scenario_version(session, current_user, scenario_id, payload)


@router.post("/{scenario_id}/archive", response_model=ScenarioResponse)
def archive_scenario_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioResponse:
    return archive_scenario(session, current_user, scenario_id)


@router.post("/{scenario_id}/approve", response_model=ScenarioResponse)
def approve_scenario_route(
    scenario_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> ScenarioResponse:
    return approve_scenario(session, current_user, scenario_id)
