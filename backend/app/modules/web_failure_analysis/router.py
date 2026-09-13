from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.web_failure_analysis.schemas import (
    WebFailureAnalysisCreateRequest,
    WebFailureAnalysisListResponse,
    WebFailureAnalysisResponse,
)
from app.modules.web_failure_analysis.service import (
    generate_failure_analysis,
    list_failure_analyses,
)

router = APIRouter()
RunIdPath = Annotated[str, Path(min_length=1, max_length=128)]


@router.post(
    "/{run_id}/web-failure-analyses",
    response_model=WebFailureAnalysisResponse,
)
def generate_failure_analysis_route(
    run_id: RunIdPath,
    payload: WebFailureAnalysisCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebFailureAnalysisResponse:
    return generate_failure_analysis(session, current_user, run_id, payload)


@router.get(
    "/{run_id}/web-failure-analyses",
    response_model=WebFailureAnalysisListResponse,
)
def list_failure_analyses_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    case_run_id: int | None = Query(default=None, gt=0),
) -> WebFailureAnalysisListResponse:
    return list_failure_analyses(session, current_user, run_id, case_run_id)

