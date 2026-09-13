from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from sqlalchemy.orm import sessionmaker

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.modules.requirement_reviews.schemas import (
    RequirementReviewDecision,
    RequirementReviewEdit,
    RequirementReviewGenerate,
    RequirementReviewListResponse,
    RequirementReviewResponse,
)
from app.modules.requirement_reviews.service import (
    create_review_task,
    create_revision_plan_task,
    decide_review,
    delete_review,
    edit_review,
    get_review,
    list_project_reviews,
    list_reviews,
    process_review_task,
    process_revision_plan_task,
)

router = APIRouter()


@router.get("/ai-reviews/{review_id}", response_model=RequirementReviewResponse)
def get_review_route(
    review_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementReviewResponse:
    return get_review(session, current_user, review_id)


@router.post(
    "/ai-reviews/{review_id}/revision-plan",
    response_model=RequirementReviewResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_revision_plan_route(
    review_id: int,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementReviewResponse:
    review = create_revision_plan_task(session, current_user, review_id)
    revision_plan = review.context_snapshot.get("revision_plan", {})
    if revision_plan.get("status") in {"QUEUED", "RUNNING"} and not review.reused:
        session_factory = sessionmaker(
            bind=session.get_bind(), autoflush=False, expire_on_commit=False
        )
        background_tasks.add_task(
            process_revision_plan_task, session_factory, review.id, current_user
        )
    return review


@router.get(
    "/{requirement_id}/ai-reviews", response_model=RequirementReviewListResponse
)
def list_reviews_route(
    requirement_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementReviewListResponse:
    return list_reviews(session, current_user, requirement_id)


@router.get(
    "/projects/{project_id}/ai-reviews", response_model=RequirementReviewListResponse
)
def list_project_reviews_route(
    project_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> RequirementReviewListResponse:
    return list_project_reviews(
        session, current_user, project_id, page=page, page_size=page_size
    )


@router.post(
    "/{requirement_id}/ai-reviews",
    response_model=RequirementReviewResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_review_route(
    requirement_id: int,
    payload: RequirementReviewGenerate,
    background_tasks: BackgroundTasks,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementReviewResponse:
    review = create_review_task(session, current_user, requirement_id, payload)
    if review.generation_status in {"QUEUED", "RUNNING"} and not review.reused:
        session_factory = sessionmaker(
            bind=session.get_bind(), autoflush=False, expire_on_commit=False
        )
        background_tasks.add_task(
            process_review_task, session_factory, review.id, current_user
        )
    return review


@router.delete("/ai-reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review_route(
    review_id: int,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> Response:
    delete_review(session, current_user, review_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/ai-reviews/{review_id}", response_model=RequirementReviewResponse
)
def edit_review_route(
    review_id: int,
    payload: RequirementReviewEdit,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementReviewResponse:
    return edit_review(session, current_user, review_id, payload)


@router.post(
    "/ai-reviews/{review_id}/decision", response_model=RequirementReviewResponse
)
def decide_review_route(
    review_id: int,
    payload: RequirementReviewDecision,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> RequirementReviewResponse:
    return decide_review(session, current_user, review_id, payload)
