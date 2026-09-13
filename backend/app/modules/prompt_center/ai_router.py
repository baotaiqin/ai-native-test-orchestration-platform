from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.prompt_center.schemas import (
    AiCallLogListResponse,
    OutputSchemaCreate,
    OutputSchemaListResponse,
    OutputSchemaResponse,
    OutputSchemaVersionCreate,
    StructuredOutputValidateRequest,
    StructuredOutputValidateResponse,
)
from app.modules.prompt_center.service import (
    create_output_schema,
    create_output_schema_version,
    list_ai_calls,
    list_output_schemas,
    set_output_schema_enabled,
    validate_structured_output,
)

router = APIRouter()


@router.get("/output-schemas", response_model=OutputSchemaListResponse)
def list_output_schemas_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    include_disabled: bool = False,
) -> OutputSchemaListResponse:
    return list_output_schemas(
        session, current_user, include_disabled=include_disabled
    )


@router.post(
    "/output-schemas",
    response_model=OutputSchemaResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_output_schema_route(
    payload: OutputSchemaCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> OutputSchemaResponse:
    return create_output_schema(session, current_user, payload)


@router.post(
    "/output-schemas/{schema_id}/versions",
    response_model=OutputSchemaResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_output_schema_version_route(
    schema_id: int,
    payload: OutputSchemaVersionCreate,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> OutputSchemaResponse:
    return create_output_schema_version(session, current_user, schema_id, payload)


@router.patch(
    "/output-schemas/{schema_id}/enabled", response_model=OutputSchemaResponse
)
def set_output_schema_enabled_route(
    schema_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    enabled: bool = Query(),
) -> OutputSchemaResponse:
    return set_output_schema_enabled(session, current_user, schema_id, enabled)


@router.post(
    "/structured-output/validate", response_model=StructuredOutputValidateResponse
)
def validate_structured_output_route(
    payload: StructuredOutputValidateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> StructuredOutputValidateResponse:
    return validate_structured_output(session, current_user, payload)


@router.get("/calls", response_model=AiCallLogListResponse)
def list_ai_calls_route(
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    project_id: int = Query(gt=0),
    task_type: str | None = None,
) -> AiCallLogListResponse:
    return list_ai_calls(
        session, current_user, project_id, task_type=task_type
    )
