from fastapi import APIRouter

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.ai_gateway.schemas import AiGenerateRequest, AiGenerateResponse
from app.modules.ai_gateway.service import generate

router = APIRouter()


@router.post("/generate", response_model=AiGenerateResponse, summary="执行 AI 任务")
def generate_route(
    payload: AiGenerateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> AiGenerateResponse:
    return generate(session, current_user, payload)
