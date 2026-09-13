import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.exceptions import AuthenticationError, ResourceConflictError
from app.core.time import utc_now_naive
from app.modules.ci_cd.coordinator import WebhookDeliveryCoordinator
from app.modules.ci_cd.models import CiAccessToken, CiRunInvocation, WebhookDelivery
from app.modules.ci_cd.schemas import CiRunRequest, CiTokenCreate, WebhookEndpointCreate
from app.modules.ci_cd.service import (
    WebhookSendResult,
    authenticate_ci_token,
    create_ci_token,
    create_webhook_endpoint,
    retry_webhook_delivery,
    revoke_ci_token,
    trigger_ci_plan,
)
from app.modules.runs.models import TestRun as RunModel
from app.modules.test_plans.models import TestPlanRun as PlanRunModel
from app.modules.test_plans.service import create_test_plan
from tests.test_test_plans import (  # noqa: F401
    FakeEventStream,
    FakeHeartbeatStore,
    FakePublisher,
    _payload,
    _user,
    plan_context,
)


class RecordingSender:
    def __init__(self, status_code: int = 204) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def send(
        self,
        *,
        url: str,
        body: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> WebhookSendResult:
        self.calls.append(
            {
                "url": url,
                "body": body,
                "payload": json.loads(body),
                "headers": headers,
                "timeout_seconds": timeout_seconds,
            }
        )
        return WebhookSendResult(status_code=self.status_code)


def _token_payload(project_id: int, plan_id: int | None = None) -> CiTokenCreate:
    return CiTokenCreate(
        project_id=project_id,
        name="GitHub Actions",
        expires_in_days=30,
        allowed_plan_ids=[plan_id] if plan_id else None,
    )


def _patch_cipher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.ci_cd.service.encrypt_secret",
        lambda value, context: f"enc:{context}:{value}",
    )
    monkeypatch.setattr(
        "app.modules.ci_cd.service.decrypt_secret",
        lambda value, context: value.removeprefix(f"enc:{context}:"),
    )


def test_ci_token_is_shown_once_stored_as_digest_and_revocable(
    plan_context,  # noqa: F811
) -> None:
    factory, ids = plan_context
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        created = create_ci_token(
            session,
            _user(),
            _token_payload(int(ids["project_id"]), plan.id),
        )
        assert created.token.startswith("cit_")
        stored = session.get(CiAccessToken, created.metadata.id)
        assert stored is not None
        assert stored.token_digest != created.token
        assert created.token not in created.metadata.model_dump_json()
        authenticated = authenticate_ci_token(session, created.token)
        assert authenticated.id == stored.id
        assert authenticated.last_used_at is not None

        revoked = revoke_ci_token(session, _user(), stored.id)
        assert revoked.status == "REVOKED"
        with pytest.raises(AuthenticationError):
            authenticate_ci_token(session, created.token)


def test_ci_trigger_is_scoped_and_idempotent(
    plan_context,  # noqa: F811
) -> None:
    factory, ids = plan_context
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        plaintext = create_ci_token(
            session,
            _user(),
            _token_payload(int(ids["project_id"]), plan.id),
        ).token
        token = authenticate_ci_token(session, plaintext)
        request = CiRunRequest(pipeline_ref="build-42", commit_sha="abcdef1")
        first = trigger_ci_plan(
            session,
            token,
            plan.id,
            "pipeline-42-attempt-1",
            request,
            FakeHeartbeatStore(str(ids["runner_id"])),
            FakePublisher(),
            FakeEventStream(),
        )
        replay = trigger_ci_plan(
            session,
            token,
            plan.id,
            "pipeline-42-attempt-1",
            request,
            FakeHeartbeatStore(str(ids["runner_id"])),
            FakePublisher(),
            FakeEventStream(),
        )
        assert replay.plan_run_id == first.plan_run_id
        assert replay.idempotent_replay is True
        assert len(list(session.scalars(select(PlanRunModel)).all())) == 1
        invocation = session.scalar(select(CiRunInvocation))
        assert invocation is not None
        assert invocation.pipeline_ref == "build-42"
        assert session.get(PlanRunModel, first.plan_run_id).trigger_type == "API"  # type: ignore[union-attr]

        with pytest.raises(ResourceConflictError, match="不同"):
            trigger_ci_plan(
                session,
                token,
                plan.id,
                "pipeline-42-attempt-1",
                CiRunRequest(pipeline_ref="different-build", commit_sha="abcdef1"),
                FakeHeartbeatStore(str(ids["runner_id"])),
                FakePublisher(),
                FakeEventStream(),
            )

        with pytest.raises(AuthenticationError):
            trigger_ci_plan(
                session,
                token,
                plan.id + 999,
                "out-of-scope",
                request,
                FakeHeartbeatStore(str(ids["runner_id"])),
                FakePublisher(),
                FakeEventStream(),
            )


def test_background_refresh_enqueues_and_signs_webhook_once(
    plan_context,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_cipher(monkeypatch)
    factory, ids = plan_context
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        endpoint = create_webhook_endpoint(
            session,
            _user(),
            WebhookEndpointCreate(
                project_id=int(ids["project_id"]),
                name="Release Hook",
                url="https://hooks.example.test/private/path?token=hidden",
                signing_secret="signing-secret-123",
            ),
        )
        assert endpoint.target_hint == "https://hooks.example.test/…"
        assert "private" not in endpoint.model_dump_json()
        token = create_ci_token(
            session,
            _user(),
            _token_payload(int(ids["project_id"]), plan.id),
        ).token
        started = trigger_ci_plan(
            session,
            authenticate_ci_token(session, token),
            plan.id,
            "release-17",
            CiRunRequest(pipeline_ref="release-17"),
            FakeHeartbeatStore(str(ids["runner_id"])),
            FakePublisher(),
            FakeEventStream(),
        )
        child = session.scalar(select(RunModel))
        assert child is not None
        child.status = "SUCCESS"
        child.ended_at = utc_now_naive()
        session.commit()

    sender = RecordingSender()
    coordinator = WebhookDeliveryCoordinator(
        session_factory=factory,
        sender=sender,
        now_provider=utc_now_naive,
        interval_seconds=1,
    )
    first = coordinator.scan_once()
    second = coordinator.scan_once()
    assert started.plan_run_id in first.refreshed_plan_run_ids
    assert len(sender.calls) == 1
    assert second.candidate_ids == []
    call = sender.calls[0]
    assert call["url"] == "https://hooks.example.test/private/path?token=hidden"
    assert call["payload"]["status"] == "SUCCESS"
    assert "secret" not in str(call["payload"]).lower()
    timestamp = call["headers"]["X-AI-Test-Timestamp"]
    expected_signature = hmac.new(
        b"signing-secret-123",
        f"{timestamp}.{call['body']}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert call["headers"]["X-AI-Test-Signature-256"] == f"sha256={expected_signature}"
    with factory() as session:
        deliveries = list(session.scalars(select(WebhookDelivery)).all())
        assert len(deliveries) == 1
        assert deliveries[0].status == "SUCCEEDED"
        assert deliveries[0].attempt_count == 1


def test_webhook_retries_then_allows_manual_recovery(
    plan_context,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_cipher(monkeypatch)
    factory, ids = plan_context
    with factory() as session:
        plan = create_test_plan(session, _user(), _payload(ids))
        create_webhook_endpoint(
            session,
            _user(),
            WebhookEndpointCreate(
                project_id=int(ids["project_id"]),
                name="Retry Hook",
                url="https://hooks.example.test/retry",
                max_attempts=2,
            ),
        )
        token = create_ci_token(
            session,
            _user(),
            _token_payload(int(ids["project_id"]), plan.id),
        ).token
        trigger_ci_plan(
            session,
            authenticate_ci_token(session, token),
            plan.id,
            "retry-build",
            CiRunRequest(),
            FakeHeartbeatStore(str(ids["runner_id"])),
            FakePublisher(),
            FakeEventStream(),
        )
        child = session.scalar(select(RunModel))
        assert child is not None
        child.status = "FAILED"
        child.ended_at = utc_now_naive()
        session.commit()

    failing = RecordingSender(status_code=503)
    coordinator = WebhookDeliveryCoordinator(
        session_factory=factory,
        sender=failing,
        now_provider=utc_now_naive,
        interval_seconds=1,
    )
    coordinator.scan_once()
    with factory() as session:
        delivery = session.scalar(select(WebhookDelivery))
        assert delivery is not None and delivery.status == "RETRY"
        delivery.next_attempt_at = utc_now_naive() - timedelta(seconds=1)
        session.commit()
        delivery_id = delivery.id
    coordinator.scan_once()
    with factory() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == "FAILED"
        assert delivery.attempt_count == 2
        recovered = retry_webhook_delivery(
            session,
            _user(),
            delivery.id,
            RecordingSender(status_code=200),
        )
        assert recovered.status == "SUCCEEDED"
        assert recovered.attempt_count == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.test/event",
        "https://user:password@hooks.example.test/event",
        "https://hooks.example.test/event#fragment",
        "https://hooks.example.test:invalid/event",
    ],
)
def test_webhook_configuration_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        WebhookEndpointCreate(project_id=1, name="Unsafe Hook", url=url)
