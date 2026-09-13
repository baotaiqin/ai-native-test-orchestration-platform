from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentUserDependency, DatabaseSessionDependency
from app.infrastructure.object_store.client import ObjectStore, get_object_store
from app.infrastructure.rabbitmq.client import TaskPublisher, get_task_publisher
from app.infrastructure.redis.client import (
    RedisRunEventStream,
    RedisRunnerHeartbeatStore,
    get_run_event_stream,
    get_runner_heartbeat_store,
)
from app.modules.web_healing.schemas import (
    WebHealingProposalCreateRequest,
    WebHealingProposalDecisionRequest,
    WebHealingProposalListResponse,
    WebHealingProposalRejectRequest,
    WebHealingProposalResponse,
    WebHealingProposalValidateRequest,
)
from app.modules.web_healing.service import (
    accept_healing_proposal,
    generate_healing_proposal,
    list_healing_proposals,
    reject_healing_proposal,
    validate_healing_proposal,
)

router = APIRouter()
RunIdPath = Annotated[str, Path(min_length=1, max_length=128)]
ProposalIdPath = Annotated[int, Path(gt=0)]
RunHeartbeatStoreDependency = Annotated[
    RedisRunnerHeartbeatStore, Depends(get_runner_heartbeat_store)
]
TaskPublisherDependency = Annotated[TaskPublisher, Depends(get_task_publisher)]
RunEventStreamDependency = Annotated[RedisRunEventStream, Depends(get_run_event_stream)]
ObjectStoreDependency = Annotated[ObjectStore, Depends(get_object_store)]


@router.post(
    "/{run_id}/web-healing-proposals",
    response_model=WebHealingProposalResponse,
)
def generate_healing_proposal_route(
    run_id: RunIdPath,
    payload: WebHealingProposalCreateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    evidence_store: ObjectStoreDependency,
) -> WebHealingProposalResponse:
    return generate_healing_proposal(
        session, current_user, run_id, payload, evidence_store
    )


@router.get(
    "/{run_id}/web-healing-proposals",
    response_model=WebHealingProposalListResponse,
)
def list_healing_proposals_route(
    run_id: RunIdPath,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    case_run_id: int | None = Query(default=None, gt=0),
) -> WebHealingProposalListResponse:
    return list_healing_proposals(session, current_user, run_id, case_run_id)


@router.post(
    "/{run_id}/web-healing-proposals/{proposal_id}/validate",
    response_model=WebHealingProposalResponse,
)
def validate_healing_proposal_route(
    run_id: RunIdPath,
    proposal_id: ProposalIdPath,
    payload: WebHealingProposalValidateRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
    store: RunHeartbeatStoreDependency,
    publisher: TaskPublisherDependency,
    event_stream: RunEventStreamDependency,
) -> WebHealingProposalResponse:
    return validate_healing_proposal(
        session,
        current_user,
        run_id,
        proposal_id,
        payload,
        store,
        publisher,
        event_stream,
    )


@router.post(
    "/{run_id}/web-healing-proposals/{proposal_id}/accept",
    response_model=WebHealingProposalResponse,
)
def accept_healing_proposal_route(
    run_id: RunIdPath,
    proposal_id: ProposalIdPath,
    payload: WebHealingProposalDecisionRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebHealingProposalResponse:
    return accept_healing_proposal(session, current_user, run_id, proposal_id, payload)


@router.post(
    "/{run_id}/web-healing-proposals/{proposal_id}/reject",
    response_model=WebHealingProposalResponse,
)
def reject_healing_proposal_route(
    run_id: RunIdPath,
    proposal_id: ProposalIdPath,
    payload: WebHealingProposalRejectRequest,
    session: DatabaseSessionDependency,
    current_user: CurrentUserDependency,
) -> WebHealingProposalResponse:
    return reject_healing_proposal(session, current_user, run_id, proposal_id, payload)
