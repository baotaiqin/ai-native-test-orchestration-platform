import json
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.exceptions import (
    AuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
    RunStateConflictError,
    RunValidationError,
)
from app.core.time import utc_now_naive
from app.infrastructure.rabbitmq.client import TaskPublishResult
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment, EnvironmentVariable
from app.modules.projects.models import Project, ProjectBusinessCounter, ProjectMember
from app.modules.reports.schemas import ReportPageQuery, ReportStepPageQuery
from app.modules.reports.service import list_report_cases, list_report_steps
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.run_requirement_snapshots.models import (
    RunRequirementCapture,
    RunRequirementSource,
)
from app.modules.runners.models import Runner, RunnerCapability, RunnerSlot, RunnerTag
from app.modules.runners.schemas import RunnerSlotType
from app.modules.runners.security import digest_secret
from app.modules.runs.enums import RunStatus
from app.modules.runs.models import (
    CaseRun,
    RunApiExecutionResult,
    RunDispatchOutbox,
    RunScenarioExecutionResult,
    RunWebExecutionResult,
    StepRun,
    TestRun,
)
from app.modules.runs.schemas import (
    ExecutionStartRequest,
    RunClaimRequest,
    RunCreateRequest,
    WebExecutionCompleteRequest,
    WebExecutionTrace,
)
from app.modules.runs.service import (
    cancel_run,
    claim_run,
    complete_web_execution,
    create_run,
    dispatch_run,
    get_web_execution_plan,
    start_web_execution,
    validate_run_request,
)
from app.modules.secrets.models import Secret
from app.modules.test_cases.models import RequirementCaseLink, TestCase, TestCaseVersion
from app.modules.web_cases.models import (
    SessionProfile,
    WebCase,
    WebCaseVersion,
    WebElement,
    WebElementLocator,
    WebElementVersion,
    WebPage,
)
from app.modules.web_cases.schemas import (
    ElementLocatorCreate,
    SessionProfileCreate,
    SessionProfileUpdate,
    WebCaseContent,
    WebCaseCreate,
    WebCaseStatus,
    WebCaseUpdate,
    WebCaseVersionCreate,
    WebCaseVersionResponse,
    WebCaseVersionStatus,
    WebElementCreate,
    WebElementUpdate,
    WebElementVersionCreate,
    WebLocator,
    WebPageCreate,
    WebPageUpdate,
)
from app.modules.web_cases.service import (
    approve_web_case,
    archive_session_profile,
    archive_web_case,
    archive_web_element,
    archive_web_page,
    create_session_profile,
    create_web_case,
    create_web_case_version,
    create_web_element,
    create_web_element_version,
    create_web_page,
    list_web_case_versions,
    restore_session_profile,
    restore_web_element,
    restore_web_page,
    update_session_profile,
    update_web_case,
    update_web_element,
    update_web_page,
)
from app.modules.web_healing.models import WebHealingProposal, WebHealingValidation

ADMIN = CurrentUser(id="admin", username="admin", display_name="Admin", roles=["ADMIN"])
RUNNER_CREDENTIAL = "web-runner-credential"
NEW_PAGE_ACTION_TYPES = ("RELOAD", "BACK", "FORWARD", "WAIT_NETWORK_IDLE")
NEW_LOCATOR_ACTION_TYPES = (
    "DOUBLE_CLICK",
    "RIGHT_CLICK",
    "CLEAR",
    "HOVER",
    "CHECK",
    "UNCHECK",
    "RADIO",
    "ENTER",
    "TAB",
)
NEW_ACTION_TYPES = (*NEW_PAGE_ACTION_TYPES, *NEW_LOCATOR_ACTION_TYPES)
NEW_NO_EXPECTED_ASSERTION_TYPES = (
    "ASSERT_EXISTS",
    "ASSERT_ENABLED",
    "ASSERT_HIDDEN",
    "ASSERT_CLICKABLE",
)
NEW_LOCATOR_EXPECTED_ASSERTION_TYPES = ("ASSERT_TEXT_EQUAL", "ASSERT_INPUT_VALUE")
NEW_LOCATOR_ASSERTION_TYPES = (
    *NEW_NO_EXPECTED_ASSERTION_TYPES,
    *NEW_LOCATOR_EXPECTED_ASSERTION_TYPES,
)
NEW_EXPECTED_ASSERTION_TYPES = (*NEW_LOCATOR_EXPECTED_ASSERTION_TYPES, "ASSERT_TITLE")
NEW_ASSERTION_TYPES = (*NEW_LOCATOR_ASSERTION_TYPES, "ASSERT_TITLE")


class FakeHeartbeat:
    def get_heartbeat(self, runner_id: str) -> dict[str, str] | None:
        return {"runner_id": runner_id, "status": "ONLINE"}


class FakePublisher:
    def publish(self, *, routing_key: str, payload: dict[str, Any]) -> TaskPublishResult:
        return TaskPublishResult(published=True)


class FakeEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_event(self, project_id: int, run_id: str, event: dict[str, Any]) -> str:
        self.events.append({"project_id": project_id, "run_id": run_id, **event})
        return f"{len(self.events)}-0"


@pytest.fixture
def web_context() -> Generator[tuple[sessionmaker[Session], int, int], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = [
        Project.__table__,
        ProjectMember.__table__,
        ProjectBusinessCounter.__table__,
        Environment.__table__,
        EnvironmentVariable.__table__,
        Secret.__table__,
        Runner.__table__,
        RunnerCapability.__table__,
        RunnerSlot.__table__,
        RunnerTag.__table__,
        Requirement.__table__,
        RequirementVersion.__table__,
        TestCase.__table__,
        TestCaseVersion.__table__,
        WebCase.__table__,
        WebCaseVersion.__table__,
        WebPage.__table__,
        WebElement.__table__,
        WebElementVersion.__table__,
        WebElementLocator.__table__,
        SessionProfile.__table__,
        TestRun.__table__,
        CaseRun.__table__,
        RequirementCaseLink.__table__,
        RunRequirementCapture.__table__,
        RunRequirementSource.__table__,
        StepRun.__table__,
        RunDispatchOutbox.__table__,
        RunApiExecutionResult.__table__,
        RunScenarioExecutionResult.__table__,
        RunWebExecutionResult.__table__,
        WebHealingProposal.__table__,
        WebHealingValidation.__table__,
    ]
    for table in tables:
        table.create(engine)
    with factory() as session:
        project = Project(
            name="Web Project", code="WEB_PROJECT", owner_id=ADMIN.id, status="ACTIVE"
        )
        project.members.append(ProjectMember(user_id=ADMIN.id, role="PROJECT_OWNER"))
        session.add(project)
        session.flush()
        environment = Environment(
            project_id=project.id,
            name="Test",
            code="TEST",
            base_url="https://env.example",
            enabled=True,
        )
        runner = Runner(
            id="runner-web-test",
            name="Web Runner",
            hostname="WEB-RUNNER",
            chrome_version="120",
            credential_digest=digest_secret(RUNNER_CREDENTIAL),
            status="ACTIVE",
            heartbeat_interval_seconds=30,
        )
        runner.capabilities.append(RunnerCapability(capability="WEB", status="READY"))
        runner.slots.append(RunnerSlot(slot_type="WEB", total=2, available=2))
        session.add_all([environment, runner])
        session.flush()
        session.add_all(
            [
                EnvironmentVariable(
                    environment_id=environment.id,
                    key="tenant_number",
                    value="7",
                    value_type="NUMBER",
                ),
                EnvironmentVariable(
                    environment_id=environment.id,
                    key="feature_enabled",
                    value="true",
                    value_type="BOOLEAN",
                ),
                EnvironmentVariable(
                    environment_id=environment.id,
                    key="payload_config",
                    value='{"mode":"safe"}',
                    value_type="JSON",
                ),
            ]
        )
        session.commit()
        yield factory, project.id, environment.id


def _content(*, element_version_id: int | None = None) -> WebCaseContent:
    locator = (
        {"element_version_id": element_version_id}
        if element_version_id is not None
        else {"strategy": "css", "value": "#submit"}
    )
    return WebCaseContent.model_validate(
        {
            "start_url": "https://example.test",
            "actions": [
                {"type": "GOTO", "url": "https://example.test"},
                {"type": "CLICK", "locator": locator},
            ],
            "assertions": [{"type": "ASSERT_VISIBLE", "locator": locator}],
        }
    )


def _expanded_action(
    action_type: str,
    *,
    locator: dict[str, Any] | None = None,
    timeout_ms: int = 1_000,
    failure_policy: str = "STOP",
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": action_type,
        "timeout_ms": timeout_ms,
        "failure_policy": failure_policy,
    }
    if action_type in NEW_LOCATOR_ACTION_TYPES:
        action["locator"] = locator or {"strategy": "css", "value": "#target"}
    return action


def _expanded_content(*, element_version_id: int | None = None) -> WebCaseContent:
    locator = (
        {"element_version_id": element_version_id}
        if element_version_id is not None
        else {"strategy": "css", "value": "#target"}
    )
    return WebCaseContent.model_validate(
        {
            "start_url": "https://example.test/expanded",
            "actions": [
                *[
                    _expanded_action(action_type, failure_policy="CONTINUE")
                    for action_type in NEW_PAGE_ACTION_TYPES
                ],
                *[
                    _expanded_action(action_type, locator=locator, timeout_ms=2_000)
                    for action_type in NEW_LOCATOR_ACTION_TYPES
                ],
            ],
            "assertions": [],
            "total_timeout_ms": 30_000,
        }
    )


def _content_with_action(action: dict[str, Any]) -> WebCaseContent:
    return WebCaseContent.model_validate(
        {
            "start_url": "https://example.test/action",
            "actions": [action],
            "assertions": [],
        }
    )


def _expanded_assertion(
    assertion_type: str,
    *,
    locator: dict[str, Any] | None = None,
    expected: str = "",
    timeout_ms: Any = 1_000,
) -> dict[str, Any]:
    assertion: dict[str, Any] = {
        "type": assertion_type,
        "timeout_ms": timeout_ms,
    }
    if assertion_type in NEW_LOCATOR_ASSERTION_TYPES:
        assertion["locator"] = locator or {"strategy": "css", "value": "#target"}
    if assertion_type in NEW_EXPECTED_ASSERTION_TYPES:
        assertion["expected"] = expected
    return assertion


def _content_with_assertion(assertion: dict[str, Any]) -> WebCaseContent:
    return WebCaseContent.model_validate(
        {
            "start_url": "https://example.test/assertion",
            "actions": [{"type": "RELOAD"}],
            "assertions": [assertion],
        }
    )


def _expanded_assertion_content(*, element_version_id: int | None = None) -> WebCaseContent:
    locator = (
        {"element_version_id": element_version_id}
        if element_version_id is not None
        else {"strategy": "css", "value": "#target"}
    )
    return WebCaseContent.model_validate(
        {
            "start_url": "https://example.test/assertions",
            "actions": [
                {
                    "type": "ENTER",
                    "locator": locator,
                    "timeout_ms": 1_500,
                    "failure_policy": "CONTINUE",
                }
            ],
            "assertions": [
                _expanded_assertion("ASSERT_EXISTS", locator=locator),
                _expanded_assertion("ASSERT_ENABLED", locator=locator),
                _expanded_assertion("ASSERT_HIDDEN", locator=locator),
                _expanded_assertion("ASSERT_CLICKABLE", locator=locator),
                _expanded_assertion("ASSERT_TEXT_EQUAL", locator=locator, expected=""),
                _expanded_assertion(
                    "ASSERT_INPUT_VALUE", locator=locator, expected="input value"
                ),
                _expanded_assertion("ASSERT_TITLE", expected=""),
            ],
            "total_timeout_ms": 30_000,
        }
    )


def _context_content(*, session_profile_id: int | None = None) -> WebCaseContent:
    return WebCaseContent.model_validate(
        {
            "start_url": "{{base_url}}/login",
            "actions": [
                {"type": "GOTO", "url": "{{base_url}}/tenant/{{tenant_number}}"},
                {
                    "type": "FILL",
                    "locator": {"strategy": "css", "value": "#config"},
                    "value": "{{payload_config}}",
                },
            ],
            "assertions": [
                {
                    "type": "ASSERT_URL",
                    "expected": "{{base_url}}/tenant/{{tenant_number}}",
                }
            ],
            "session_profile_id": session_profile_id,
        }
    )


def test_natural_language_steps_are_backward_compatible_bounded_and_persisted(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, _ = web_context
    legacy = _content()
    assert legacy.natural_language_steps == []

    content = WebCaseContent.model_validate(
        {
            **legacy.model_dump(mode="python"),
            "natural_language_steps": ["  Open the login page  ", "Submit credentials"],
        }
    )
    assert content.natural_language_steps == ["Open the login page", "Submit credentials"]

    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Natural Language", content=content),
        )
        assert detail.current_version is not None
        assert (
            detail.current_version.content.natural_language_steps
            == content.natural_language_steps
        )

        new_version = create_web_case_version(
            session,
            ADMIN,
            detail.id,
            WebCaseVersionCreate(content=content, change_note="natural language steps"),
        )
        assert new_version.content.natural_language_steps == content.natural_language_steps

    with pytest.raises(ValidationError):
        WebCaseContent.model_validate(
            {**legacy.model_dump(mode="python"), "natural_language_steps": ["  "]}
        )
    with pytest.raises(ValidationError):
        WebCaseContent.model_validate(
            {**legacy.model_dump(mode="python"), "natural_language_steps": ["x" * 2001]}
        )


def test_natural_language_steps_have_a_bounded_collection_size() -> None:
    legacy = _content()
    with pytest.raises(ValidationError):
        WebCaseContent.model_validate(
            {**legacy.model_dump(mode="python"), "natural_language_steps": ["step"] * 201}
        )


@pytest.mark.parametrize("action_type", NEW_ACTION_TYPES)
def test_v1_expanded_web_action_dsl_accepts_each_frozen_action(action_type: str) -> None:
    action = _content_with_action(_expanded_action(action_type)).actions[0]

    assert action.type == action_type
    assert action.timeout_ms == 1_000
    assert action.failure_policy == "STOP"
    assert (getattr(action, "locator", None) is not None) == (
        action_type in NEW_LOCATOR_ACTION_TYPES
    )


@pytest.mark.parametrize("action_type", NEW_ACTION_TYPES)
@pytest.mark.parametrize("timeout_ms", (99, 600_001))
def test_v1_expanded_web_action_dsl_rejects_out_of_range_budget(
    action_type: str, timeout_ms: int
) -> None:
    with pytest.raises(ValidationError):
        _content_with_action(_expanded_action(action_type, timeout_ms=timeout_ms))


@pytest.mark.parametrize("action_type", NEW_LOCATOR_ACTION_TYPES)
def test_v1_expanded_locator_action_requires_original_locator(action_type: str) -> None:
    action = _expanded_action(action_type)
    action.pop("locator")

    with pytest.raises(ValidationError):
        _content_with_action(action)


@pytest.mark.parametrize("action_type", NEW_LOCATOR_ACTION_TYPES)
@pytest.mark.parametrize(
    ("field", "value"),
    (("url", "https://override.invalid"), ("value", "override"), ("key", "Escape")),
)
def test_v1_expanded_locator_action_rejects_semantic_override(
    action_type: str, field: str, value: str
) -> None:
    action = _expanded_action(action_type)
    action[field] = value

    with pytest.raises(ValidationError):
        _content_with_action(action)


@pytest.mark.parametrize("action_type", NEW_PAGE_ACTION_TYPES)
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("url", "https://override.invalid"),
        ("locator", {"strategy": "css", "value": "#target"}),
        ("value", "override"),
        ("key", "Escape"),
    ),
)
def test_v1_expanded_page_action_rejects_business_fields(
    action_type: str, field: str, value: Any
) -> None:
    action = _expanded_action(action_type)
    action[field] = value

    with pytest.raises(ValidationError):
        _content_with_action(action)


@pytest.mark.parametrize(
    "action",
    (
        {"type": "RUN_ARBITRARY_METHOD"},
        {"type": "RELOAD", "extra": "forbidden"},
        {"type": "DOUBLE_CLICK", "locator": {"strategy": "css"}},
        {
            "type": "DOUBLE_CLICK",
            "locator": {"strategy": "css", "value": "#target", "extra": "forbidden"},
        },
        {
            "type": "DOUBLE_CLICK",
            "locator": {
                "strategy": "css",
                "value": "#target",
                "element_version_id": 1,
            },
        },
        {"type": "WAIT_NETWORK_IDLE", "failure_policy": "RETRY_ONCE"},
    ),
)
def test_v1_expanded_web_action_dsl_fails_closed(action: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _content_with_action(action)


@pytest.mark.parametrize(
    "action",
    (
        {
            "type": "NEW_TAB",
            "url": "https://tabs.example.test/alpha",
            "value": "alpha",
            "timeout_ms": 100,
            "failure_policy": "CONTINUE",
        },
        {
            "type": "NEW_TAB",
            "url": "{{base_url}}/tabs/{{tab_path}}",
            "value": "Tab_2-long",
            "timeout_ms": 600_000,
            "failure_policy": "STOP",
        },
        {
            "type": "SWITCH_TAB",
            "value": "main",
            "timeout_ms": 1_000,
            "failure_policy": "STOP",
        },
        {
            "type": "CLOSE_TAB",
            "value": "alpha",
            "timeout_ms": 1_000,
            "failure_policy": "CONTINUE",
        },
    ),
)
def test_v1_tabs_dsl_accepts_only_frozen_asset_shapes(action: dict[str, Any]) -> None:
    parsed = _content_with_action(action).actions[0]
    wire = parsed.model_dump(mode="json")

    expected_keys = {"type", "value", "timeout_ms", "failure_policy"}
    if action["type"] == "NEW_TAB":
        expected_keys.add("url")
    assert set(wire) == expected_keys
    assert wire == action


def test_v1_tabs_runtime_state_decisions_remain_runner_owned() -> None:
    content = WebCaseContent.model_validate(
        {
            "start_url": "https://tabs.example.test/main",
            "actions": [
                {"type": "NEW_TAB", "url": "https://tabs.example.test/a", "value": "alpha"},
                {"type": "NEW_TAB", "url": "https://tabs.example.test/b", "value": "alpha"},
                {"type": "SWITCH_TAB", "value": "unknown"},
                {"type": "CLOSE_TAB", "value": "main"},
            ],
        }
    )

    assert [item.type for item in content.actions] == [
        "NEW_TAB",
        "NEW_TAB",
        "SWITCH_TAB",
        "CLOSE_TAB",
    ]


@pytest.mark.parametrize(
    "action",
    (
        {"type": "NEW_TAB", "value": "alpha"},
        {"type": "NEW_TAB", "url": "https://tabs.example.test"},
        {"type": "NEW_TAB", "url": "https://tabs.example.test", "value": "main"},
        {"type": "NEW_TAB", "url": "ftp://tabs.example.test", "value": "alpha"},
        {"type": "NEW_TAB", "url": "javascript:alert(1)", "value": "alpha"},
        {"type": "NEW_TAB", "url": "/relative", "value": "alpha"},
        {
            "type": "NEW_TAB",
            "url": "https://user:password@tabs.example.test",
            "value": "alpha",
        },
        {"type": "NEW_TAB", "url": "https://{{bad template}}", "value": "alpha"},
        {"type": "NEW_TAB", "url": "https://tabs.example.test\r\n/x", "value": "alpha"},
        {
            "type": "NEW_TAB",
            "url": "https://tabs.example.test",
            "value": "alpha",
            "locator": {"strategy": "css", "value": "body"},
        },
        {
            "type": "NEW_TAB",
            "url": "https://tabs.example.test",
            "value": "alpha",
            "key": "Enter",
        },
        {"type": "SWITCH_TAB", "value": "alpha", "url": "https://tabs.example.test"},
        {
            "type": "CLOSE_TAB",
            "value": "alpha",
            "locator": {"strategy": "css", "value": "body"},
        },
        {"type": "SWITCH_TAB", "value": "1alpha"},
        {"type": "SWITCH_TAB", "value": " alpha"},
        {"type": "CLOSE_TAB", "value": "alpha.beta"},
        {"type": "CLOSE_TAB", "value": "a" * 65},
        {"type": "SWITCH_TAB", "value": "alpha", "timeout_ms": 99},
        {"type": "SWITCH_TAB", "value": "alpha", "timeout_ms": 600_001},
        {"type": "SWITCH_TAB", "value": "alpha", "timeout_ms": True},
        {"type": "SWITCH_TAB", "value": "alpha", "timeout_ms": "1000"},
        {"type": "CLOSE_TAB", "value": "alpha", "failure_policy": "RETRY_ONCE"},
        {"type": "CLOSE_TAB", "value": "alpha", "extra": "forbidden"},
    ),
)
def test_v1_tabs_dsl_rejects_invalid_or_extra_fields(action: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _content_with_action(action)


@pytest.mark.parametrize("assertion_type", NEW_ASSERTION_TYPES)
def test_v1_rule_assertion_dsl_accepts_each_frozen_assertion(assertion_type: str) -> None:
    assertion = _content_with_assertion(_expanded_assertion(assertion_type)).assertions[0]

    assert assertion.type == assertion_type
    assert assertion.timeout_ms == 1_000
    assert (getattr(assertion, "locator", None) is not None) == (
        assertion_type in NEW_LOCATOR_ASSERTION_TYPES
    )
    if assertion_type in NEW_EXPECTED_ASSERTION_TYPES:
        assert assertion.expected == ""
    else:
        assert not hasattr(assertion, "expected")


@pytest.mark.parametrize("assertion_type", NEW_EXPECTED_ASSERTION_TYPES)
def test_v1_rule_assertion_dsl_accepts_empty_expected(assertion_type: str) -> None:
    assertion = _content_with_assertion(
        _expanded_assertion(assertion_type, expected="")
    ).assertions[0]

    assert assertion.expected == ""


@pytest.mark.parametrize("assertion_type", NEW_ASSERTION_TYPES)
@pytest.mark.parametrize("timeout_ms", (99, 600_001, True, "1000"))
def test_v1_rule_assertion_dsl_rejects_invalid_timeout(
    assertion_type: str, timeout_ms: Any
) -> None:
    with pytest.raises(ValidationError):
        _content_with_assertion(
            _expanded_assertion(assertion_type, timeout_ms=timeout_ms)
        )


@pytest.mark.parametrize("assertion_type", NEW_LOCATOR_ASSERTION_TYPES)
def test_v1_rule_assertion_dsl_requires_original_locator(assertion_type: str) -> None:
    assertion = _expanded_assertion(assertion_type)
    assertion.pop("locator")

    with pytest.raises(ValidationError):
        _content_with_assertion(assertion)


@pytest.mark.parametrize("assertion_type", NEW_NO_EXPECTED_ASSERTION_TYPES)
@pytest.mark.parametrize("expected", (None, ""))
def test_v1_rule_assertion_dsl_rejects_expected_when_not_applicable(
    assertion_type: str, expected: str | None
) -> None:
    assertion = _expanded_assertion(assertion_type)
    assertion["expected"] = expected

    with pytest.raises(ValidationError):
        _content_with_assertion(assertion)


@pytest.mark.parametrize("assertion_type", NEW_EXPECTED_ASSERTION_TYPES)
@pytest.mark.parametrize("expected", (None, 1, "x" * 10_001))
def test_v1_rule_assertion_dsl_rejects_missing_type_or_oversized_expected(
    assertion_type: str, expected: Any
) -> None:
    assertion = _expanded_assertion(assertion_type)
    if expected is None:
        assertion.pop("expected")
    else:
        assertion["expected"] = expected

    with pytest.raises(ValidationError):
        _content_with_assertion(assertion)


@pytest.mark.parametrize(
    "locator",
    (None, {"strategy": "css", "value": "#target"}),
)
def test_v1_rule_assertion_title_rejects_locator_field(locator: Any) -> None:
    assertion = _expanded_assertion("ASSERT_TITLE")
    assertion["locator"] = locator

    with pytest.raises(ValidationError):
        _content_with_assertion(assertion)


@pytest.mark.parametrize(
    "assertion",
    (
        {"type": "ASSERT_ARBITRARY_RULE"},
        {"type": "ASSERT_EXISTS", "locator": {"strategy": "css", "value": "#target"},
         "extra": "forbidden"},
        {"type": "ASSERT_ENABLED", "locator": {"strategy": "css"}},
        {
            "type": "ASSERT_TEXT_EQUAL",
            "locator": {"strategy": "css", "value": "#target", "extra": "forbidden"},
            "expected": "text",
        },
        {
            "type": "ASSERT_INPUT_VALUE",
            "locator": {
                "strategy": "css",
                "value": "#target",
                "element_version_id": 1,
            },
            "expected": "value",
        },
        {"type": "ASSERT_TITLE", "expected": "title", "value": "not-applicable"},
    ),
)
def test_v1_rule_assertion_dsl_fails_closed(assertion: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _content_with_assertion(assertion)


def test_v1_legacy_assertions_keep_their_existing_asset_semantics() -> None:
    content = WebCaseContent.model_validate(
        {
            "start_url": "https://example.test/legacy",
            "actions": [{"type": "GOTO", "url": "https://example.test/legacy"}],
            "assertions": [
                {
                    "type": "ASSERT_VISIBLE",
                    "locator": {"strategy": "css", "value": "#target"},
                },
                {
                    "type": "ASSERT_TEXT",
                    "locator": {"strategy": "css", "value": "#target"},
                    "expected": "contains",
                },
                {"type": "ASSERT_URL", "expected": "https://example.test/legacy"},
            ],
        }
    )

    assert [item.type for item in content.assertions] == [
        "ASSERT_VISIBLE",
        "ASSERT_TEXT",
        "ASSERT_URL",
    ]


def test_web_case_approval_and_version_change_returns_to_draft(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Login Web", content=_content()),
        )
        assert detail.status == "DRAFT"
        approved = approve_web_case(session, ADMIN, detail.id)
        assert approved.status == "APPROVED"
        assert approve_web_case(session, ADMIN, detail.id).status == "APPROVED"
        approved_version_id = detail.current_version_id
        assert approved_version_id is not None
        fixed_payload = RunCreateRequest(
            project_id=project_id,
            environment_id=environment_id,
            runner_id="runner-web-test",
            run_type="WEB_CASE",
            web_case_id=detail.id,
            web_case_version_id=approved_version_id,
            required_slot_type=RunnerSlotType.WEB,
        )
        validation, _ = validate_run_request(
            session, ADMIN, fixed_payload, FakeHeartbeat()
        )
        assert validation.valid
        assert validation.resolved_web_case_version_id == approved_version_id
        run = create_run(session, ADMIN, fixed_payload, FakeHeartbeat(), FakeEvents())
        version = create_web_case_version(
            session,
            ADMIN,
            detail.id,
            WebCaseVersionCreate(content=_content(), change_note="new locator"),
        )
        assert version.version_no == 2
        assert version.status == "DRAFT"
        assert session.get(WebCase, detail.id).status == "DRAFT"
        payload = RunCreateRequest(
            project_id=project_id,
            environment_id=environment_id,
            runner_id="runner-web-test",
            run_type="WEB_CASE",
            web_case_id=detail.id,
            required_slot_type=RunnerSlotType.WEB,
        )
        validation, _ = validate_run_request(session, ADMIN, payload, FakeHeartbeat())
        assert not validation.valid
        assert validation.resolved_web_case_version_id == version.id
        assert any(item.code == "WEB_CASE_VERSION_NOT_APPROVED" for item in validation.issues)
        with pytest.raises(RunValidationError):
            create_run(session, ADMIN, payload, FakeHeartbeat(), FakeEvents())
        assert session.scalars(select(TestRun.id)).all() == [run.id]
        dispatched = dispatch_run(
            session, ADMIN, run.id, FakeHeartbeat(), FakePublisher(), FakeEvents()
        )
        claim = claim_run(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            FakeEvents(),
        )
        assert claim.status == RunStatus.ASSIGNED
        plan = get_web_execution_plan(session, run.id, RUNNER_CREDENTIAL, dispatched.message_id)
        assert plan.web_case_version_id == approved_version_id
        start_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            ExecutionStartRequest(message_id=dispatched.message_id, case_run_id=plan.case_run_id),
            FakeEvents(),
        )
        completed = complete_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            WebExecutionCompleteRequest(
                message_id=dispatched.message_id,
                case_run_id=plan.case_run_id,
                outcome="SUCCESS",
                traces=[
                    {"node_id": "action_1", "status": "SUCCESS"},
                    {"node_id": "action_2", "status": "SUCCESS"},
                    {"node_id": "assertion_1", "status": "SUCCESS"},
                ],
            ),
            FakeEvents(),
        )
        assert completed.run_status == RunStatus.SUCCESS


def test_web_case_approval_rejects_missing_managed_secret(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, _ = web_context
    content = _content_with_action(
        {
            "type": "FILL",
            "locator": {"strategy": "test_id", "value": "username"},
            "value": "{{secret.AI_TEST_USERNAME}}",
        }
    )
    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Managed Login", content=content),
        )
        with pytest.raises(ResourceConflictError, match="AI_TEST_USERNAME"):
            approve_web_case(session, ADMIN, detail.id)

        session.add(
            Secret(
                project_id=project_id,
                environment_id=None,
                name="AI_TEST_USERNAME",
                secret_type="PASSWORD",
                encrypted_value="encrypted-test-value",
                fingerprint="test-fingerprint",
                enabled=True,
            )
        )
        session.commit()
        assert approve_web_case(session, ADMIN, detail.id).status == "APPROVED"


def test_v1_expanded_actions_persist_approve_and_keep_existing_run_version_pinned(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    heartbeat = FakeHeartbeat()
    events = FakeEvents()
    with factory() as session:
        first_content = _expanded_content()
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Expanded Actions", content=first_content),
        )
        first_version_id = detail.current_version_id
        assert first_version_id is not None
        assert detail.current_version is not None
        assert [item.type for item in detail.current_version.content.actions] == list(
            NEW_ACTION_TYPES
        )
        approve_web_case(session, ADMIN, detail.id)
        payload = RunCreateRequest(
            project_id=project_id,
            environment_id=environment_id,
            runner_id="runner-web-test",
            run_type="WEB_CASE",
            web_case_id=detail.id,
            required_slot_type=RunnerSlotType.WEB,
        )
        first_run = create_run(session, ADMIN, payload, heartbeat, events)
        assert first_run.web_case_version_id == first_version_id

        second_content = first_content.model_copy(
            update={"start_url": "https://example.test/expanded-v2"}
        )
        second_version = create_web_case_version(
            session,
            ADMIN,
            detail.id,
            WebCaseVersionCreate(content=second_content, change_note="expanded actions v2"),
        )
        validation, _ = validate_run_request(session, ADMIN, payload, heartbeat)
        assert not validation.valid
        assert any(
            item.code == "WEB_CASE_VERSION_NOT_APPROVED" for item in validation.issues
        )
        approve_web_case(session, ADMIN, detail.id)
        second_run = create_run(session, ADMIN, payload, heartbeat, events)
        assert second_run.web_case_version_id == second_version.id
        versions = list_web_case_versions(session, ADMIN, detail.id)
        assert [(item.version_no, item.status) for item in versions] == [
            (2, WebCaseVersionStatus.APPROVED),
            (1, WebCaseVersionStatus.APPROVED),
        ]

        dispatched = dispatch_run(
            session, ADMIN, first_run.id, heartbeat, FakePublisher(), events
        )
        claim_run(
            session,
            first_run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            events,
        )
        plan = get_web_execution_plan(
            session, first_run.id, RUNNER_CREDENTIAL, dispatched.message_id
        )
        assert plan.schema_version == 1
        assert plan.web_case_version_id == first_version_id
        assert plan.start_url == first_content.start_url
        wire_actions = [item.model_dump(mode="json") for item in plan.actions]
        assert [item["type"] for item in wire_actions] == list(NEW_ACTION_TYPES)
        assert all(
            set(item)
            == {"type", "timeout_ms", "failure_policy", "url", "locator", "value", "key"}
            for item in wire_actions
        )
        for item in wire_actions[: len(NEW_PAGE_ACTION_TYPES)]:
            assert item["locator"] is None
            assert item["url"] is item["value"] is item["key"] is None
        for item in wire_actions[len(NEW_PAGE_ACTION_TYPES) :]:
            assert item["locator"] == {
                "element_version_id": None,
                "candidates": [{"strategy": "css", "value": "#target", "priority": 1}],
            }
            assert item["url"] is item["value"] is item["key"] is None

        archive_web_case(session, ADMIN, detail.id)
        with pytest.raises(RunValidationError):
            create_run(session, ADMIN, payload, heartbeat, events)


def test_v1_expanded_locator_actions_reject_invalid_owned_element_versions(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, _ = web_context
    with factory() as session:
        with pytest.raises(ResourceConflictError):
            create_web_case(
                session,
                ADMIN,
                WebCaseCreate(
                    project_id=project_id,
                    name="Missing Element Version",
                    content=_expanded_content(element_version_id=999_999),
                ),
            )

        page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(project_id=project_id, code="ARCHIVED", name="Archived Page"),
        )
        element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(project_id=project_id, page_id=page.id, name="Archived Element"),
        )
        version = create_web_element_version(
            session,
            ADMIN,
            element.id,
            WebElementVersionCreate(
                locators=[ElementLocatorCreate(strategy="css", value="#archived", priority=1)]
            ),
        )
        archive_web_element(session, ADMIN, element.id)
        with pytest.raises(ResourceConflictError):
            create_web_case(
                session,
                ADMIN,
                WebCaseCreate(
                    project_id=project_id,
                    name="Archived Element Version",
                    content=_expanded_content(element_version_id=version.id),
                ),
            )

        other_project = Project(
            name="Other Web Project",
            code="OTHER_WEB_PROJECT",
            owner_id=ADMIN.id,
            status="ACTIVE",
        )
        other_project.members.append(ProjectMember(user_id=ADMIN.id, role="PROJECT_OWNER"))
        session.add(other_project)
        session.commit()
        other_page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(project_id=other_project.id, code="OTHER", name="Other Page"),
        )
        other_element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(
                project_id=other_project.id,
                page_id=other_page.id,
                name="Other Element",
            ),
        )
        other_version = create_web_element_version(
            session,
            ADMIN,
            other_element.id,
            WebElementVersionCreate(
                locators=[ElementLocatorCreate(strategy="css", value="#other", priority=1)]
            ),
        )
        with pytest.raises(ResourceConflictError):
            create_web_case(
                session,
                ADMIN,
                WebCaseCreate(
                    project_id=project_id,
                    name="Cross Project Element Version",
                    content=_expanded_content(element_version_id=other_version.id),
                ),
            )


def test_v1_rule_assertions_persist_pin_plan_complete_and_match_reports(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    heartbeat = FakeHeartbeat()
    events = FakeEvents()
    with factory() as session:
        page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(project_id=project_id, code="RULES", name="Rule Assertions"),
        )
        element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(project_id=project_id, page_id=page.id, name="Rule Target"),
        )
        element_version = create_web_element_version(
            session,
            ADMIN,
            element.id,
            WebElementVersionCreate(
                locators=[
                    ElementLocatorCreate(strategy="text", value="Rule Target", priority=2),
                    ElementLocatorCreate(strategy="css", value="#rule-target", priority=1),
                ]
            ),
        )
        first_content = _expanded_assertion_content(element_version_id=element_version.id)
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(
                project_id=project_id,
                name="Rule Assertion Case",
                content=first_content,
            ),
        )
        first_version_id = detail.current_version_id
        assert first_version_id is not None
        assert detail.current_version is not None
        assert [item.type for item in detail.current_version.content.assertions] == list(
            NEW_ASSERTION_TYPES
        )
        approve_web_case(session, ADMIN, detail.id)
        payload = RunCreateRequest(
            project_id=project_id,
            environment_id=environment_id,
            runner_id="runner-web-test",
            run_type="WEB_CASE",
            web_case_id=detail.id,
            required_slot_type=RunnerSlotType.WEB,
        )
        first_run = create_run(session, ADMIN, payload, heartbeat, events)
        assert first_run.web_case_version_id == first_version_id

        second_content = first_content.model_copy(
            update={"start_url": "https://example.test/assertions-v2"}
        )
        second_version = create_web_case_version(
            session,
            ADMIN,
            detail.id,
            WebCaseVersionCreate(content=second_content, change_note="rule assertions v2"),
        )
        validation, _ = validate_run_request(session, ADMIN, payload, heartbeat)
        assert not validation.valid
        assert any(
            item.code == "WEB_CASE_VERSION_NOT_APPROVED" for item in validation.issues
        )
        approve_web_case(session, ADMIN, detail.id)
        second_run = create_run(session, ADMIN, payload, heartbeat, events)
        assert second_run.web_case_version_id == second_version.id
        assert [(item.version_no, item.status) for item in list_web_case_versions(
            session, ADMIN, detail.id
        )] == [
            (2, WebCaseVersionStatus.APPROVED),
            (1, WebCaseVersionStatus.APPROVED),
        ]
        outsider = CurrentUser(
            id="rule-outsider",
            username="rule-outsider",
            display_name="Rule Outsider",
            roles=[],
        )
        with pytest.raises(ResourceNotFoundError):
            list_web_case_versions(session, outsider, detail.id)

        dispatched = dispatch_run(
            session, ADMIN, first_run.id, heartbeat, FakePublisher(), events
        )
        claim_run(
            session,
            first_run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            events,
        )
        plan = get_web_execution_plan(
            session, first_run.id, RUNNER_CREDENTIAL, dispatched.message_id
        )
        assert plan.schema_version == 1
        assert plan.web_case_version_id == first_version_id
        assert plan.start_url == first_content.start_url
        wire_assertions = [item.model_dump(mode="json") for item in plan.assertions]
        assert [item["type"] for item in wire_assertions] == list(NEW_ASSERTION_TYPES)
        assert all(
            set(item)
            == {
                "type",
                "timeout_ms",
                "locator",
                "expected",
                "key",
                "prompt_id",
                "criteria",
                "confidence_threshold",
            }
            for item in wire_assertions
        )
        for item in wire_assertions[: len(NEW_LOCATOR_ASSERTION_TYPES)]:
            assert item["locator"] == {
                "element_version_id": element_version.id,
                "candidates": [
                    {"strategy": "css", "value": "#rule-target", "priority": 1},
                    {"strategy": "text", "value": "Rule Target", "priority": 2},
                ],
            }
        assert [item["expected"] for item in wire_assertions] == [
            None,
            None,
            None,
            None,
            "",
            "input value",
            "",
        ]
        assert wire_assertions[-1]["locator"] is None

        start_web_execution(
            session,
            first_run.id,
            RUNNER_CREDENTIAL,
            ExecutionStartRequest(
                message_id=dispatched.message_id,
                case_run_id=plan.case_run_id,
            ),
            events,
        )
        safe_marker = "w5-secret-value"
        traces = [
            {"node_id": "action_1", "status": "SUCCESS", "duration_ms": 1},
            {"node_id": "assertion_1", "status": "SUCCESS", "duration_ms": 2},
            {"node_id": "assertion_2", "status": "SUCCESS", "duration_ms": 3},
            {"node_id": "assertion_3", "status": "SUCCESS", "duration_ms": 4},
            {"node_id": "assertion_4", "status": "SUCCESS", "duration_ms": 4},
            {"node_id": "assertion_5", "status": "SUCCESS", "duration_ms": 4},
            {
                "node_id": "assertion_6",
                "status": "FAILED",
                "duration_ms": 5,
                "error_type": "ASSERTION_FAILED",
                "error_message": f"token={safe_marker}",
            },
            {"node_id": "assertion_7", "status": "SUCCESS", "duration_ms": 0},
        ]
        incomplete = WebExecutionCompleteRequest(
            message_id=dispatched.message_id,
            case_run_id=plan.case_run_id,
            outcome="FAILED",
            traces=traces[:-1],
            error_type="ASSERTION_FAILED",
            error_message=f"authorization={safe_marker}",
        )
        with pytest.raises(RunStateConflictError):
            complete_web_execution(
                session,
                first_run.id,
                RUNNER_CREDENTIAL,
                incomplete,
                events,
            )
        completed = complete_web_execution(
            session,
            first_run.id,
            RUNNER_CREDENTIAL,
            WebExecutionCompleteRequest(
                message_id=dispatched.message_id,
                case_run_id=plan.case_run_id,
                outcome="FAILED",
                traces=traces,
                error_type="ASSERTION_FAILED",
                error_message=f"authorization={safe_marker}",
            ),
            events,
        )
        assert completed.run_status == RunStatus.FAILED
        assert safe_marker not in completed.model_dump_json()

        report_steps = list_report_steps(
            session,
            ADMIN,
            first_run.id,
            ReportStepPageQuery(case_run_id=plan.case_run_id, page_size=20),
        )
        assert [item.node_id for item in report_steps.items] == [
            "action_1",
            "assertion_1",
            "assertion_2",
            "assertion_3",
            "assertion_4",
            "assertion_5",
            "assertion_6",
            "assertion_7",
        ]
        assert [item.name for item in report_steps.items[1:]] == list(NEW_ASSERTION_TYPES)
        assert report_steps.items[6].status == "FAILED"
        assert safe_marker not in report_steps.model_dump_json()
        report_cases = list_report_cases(
            session,
            ADMIN,
            first_run.id,
            ReportPageQuery(page_size=20),
        )
        report_wire = report_cases.model_dump(mode="json")
        assert [item["node_id"] for item in report_wire["items"][0]["execution_result"][
            "traces"
        ]["value"]] == [item["node_id"] for item in traces]
        assert safe_marker not in json.dumps(report_wire)

        archive_web_case(session, ADMIN, detail.id)
        with pytest.raises(RunValidationError):
            create_run(session, ADMIN, payload, heartbeat, events)


def test_v1_rule_assertions_reject_invalid_owned_element_versions(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, _ = web_context
    with factory() as session:
        with pytest.raises(ResourceConflictError):
            create_web_case(
                session,
                ADMIN,
                WebCaseCreate(
                    project_id=project_id,
                    name="Missing Assertion Element Version",
                    content=_expanded_assertion_content(element_version_id=999_999),
                ),
            )

        page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(
                project_id=project_id,
                code="ARCHIVED_RULE",
                name="Archived Rule Page",
            ),
        )
        element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(
                project_id=project_id,
                page_id=page.id,
                name="Archived Rule Element",
            ),
        )
        version = create_web_element_version(
            session,
            ADMIN,
            element.id,
            WebElementVersionCreate(
                locators=[
                    ElementLocatorCreate(strategy="css", value="#archived-rule", priority=1)
                ]
            ),
        )
        archive_web_element(session, ADMIN, element.id)
        with pytest.raises(ResourceConflictError):
            create_web_case(
                session,
                ADMIN,
                WebCaseCreate(
                    project_id=project_id,
                    name="Archived Assertion Element Version",
                    content=_expanded_assertion_content(element_version_id=version.id),
                ),
            )

        other_project = Project(
            name="Other Rule Project",
            code="OTHER_RULE_PROJECT",
            owner_id=ADMIN.id,
            status="ACTIVE",
        )
        other_project.members.append(ProjectMember(user_id=ADMIN.id, role="PROJECT_OWNER"))
        session.add(other_project)
        session.commit()
        other_page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(
                project_id=other_project.id,
                code="OTHER_RULE",
                name="Other Rule Page",
            ),
        )
        other_element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(
                project_id=other_project.id,
                page_id=other_page.id,
                name="Other Rule Element",
            ),
        )
        other_version = create_web_element_version(
            session,
            ADMIN,
            other_element.id,
            WebElementVersionCreate(
                locators=[
                    ElementLocatorCreate(strategy="css", value="#other-rule", priority=1)
                ]
            ),
        )
        with pytest.raises(ResourceConflictError):
            create_web_case(
                session,
                ADMIN,
                WebCaseCreate(
                    project_id=project_id,
                    name="Cross Project Assertion Element Version",
                    content=_expanded_assertion_content(element_version_id=other_version.id),
                ),
            )


def test_archived_web_case_response_preserves_case_status(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, _ = web_context
    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Archived Web", content=_content()),
        )

        archived = archive_web_case(session, ADMIN, detail.id)

        assert archived.status == WebCaseStatus.ARCHIVED
        assert archived.model_dump()["status"] == "ARCHIVED"


def test_retired_web_case_version_response_is_parseable() -> None:
    response = WebCaseVersionResponse(
        id=1,
        web_case_id=1,
        version_no=1,
        content=_content(),
        change_note=None,
        status="RETIRED",
        approved_by=None,
        approved_at=None,
        created_by="admin",
        created_at=datetime(2026, 1, 1),
    )

    assert response.status == WebCaseVersionStatus.RETIRED


def test_web_element_version_has_deterministic_unique_locator_priority(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    with factory() as session:
        page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(project_id=project_id, code="LOGIN", name="Login"),
        )
        element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(project_id=project_id, page_id=page.id, name="Submit"),
        )
        version = create_web_element_version(
            session,
            ADMIN,
            element.id,
            WebElementVersionCreate(
                locators=[
                    ElementLocatorCreate(strategy="text", value="Submit", priority=2),
                    ElementLocatorCreate(strategy="css", value="#submit", priority=1),
                ]
            ),
        )
        assert [item.priority for item in version.locators] == [1, 2]
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(
                project_id=project_id,
                name="Locator Candidates",
                content=_content(element_version_id=version.id),
            ),
        )
        approve_web_case(session, ADMIN, detail.id)
        run = create_run(
            session,
            ADMIN,
            RunCreateRequest(
                project_id=project_id,
                environment_id=environment_id,
                runner_id="runner-web-test",
                run_type="WEB_CASE",
                web_case_id=detail.id,
                required_slot_type=RunnerSlotType.WEB,
            ),
            FakeHeartbeat(),
            FakeEvents(),
        )
        dispatched = dispatch_run(
            session, ADMIN, run.id, FakeHeartbeat(), FakePublisher(), FakeEvents()
        )
        claim_run(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            FakeEvents(),
        )
        plan = get_web_execution_plan(session, run.id, RUNNER_CREDENTIAL, dispatched.message_id)
        candidates = plan.actions[1].locator.candidates
        assert [(item.priority, item.strategy, item.value) for item in candidates] == [
            (1, "css", "#submit"),
            (2, "text", "Submit"),
        ]
        with pytest.raises(ValueError):
            WebElementVersionCreate(
                locators=[
                    ElementLocatorCreate(strategy="css", value="#a", priority=1),
                    ElementLocatorCreate(strategy="text", value="A", priority=1),
                ]
            )


def test_web_case_project_isolation_and_strict_locator_schema(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, _ = web_context
    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Isolated", content=_content()),
        )
        outsider = CurrentUser(
            id="outsider", username="outsider", display_name="Outsider", roles=[]
        )
        with pytest.raises(ResourceNotFoundError):
            create_web_case_version(
                session,
                outsider,
                detail.id,
                WebCaseVersionCreate(content=_content(), change_note="nope"),
            )
        with pytest.raises(ValueError):
            WebLocator.model_validate({"strategy": "css", "value": "#x", "extra": "nope"})


def test_web_asset_updates_archive_restore_and_project_isolation(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    with factory() as session:
        case = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Editable Case", content=_content()),
        )
        page = create_web_page(
            session,
            ADMIN,
            WebPageCreate(project_id=project_id, code="EDIT", name="Editable Page"),
        )
        element = create_web_element(
            session,
            ADMIN,
            WebElementCreate(project_id=project_id, page_id=page.id, name="Editable Element"),
        )
        profile = create_session_profile(
            session,
            ADMIN,
            SessionProfileCreate(
                project_id=project_id,
                environment_id=environment_id,
                name="Editable-Session",
                storage_state={"cookies": [{"name": "sid", "value": "old-secret"}]},
            ),
        )
        stored_profile = session.get(SessionProfile, profile.id)
        assert stored_profile is not None
        old_ciphertext = stored_profile.storage_state_ciphertext
        old_fingerprint = stored_profile.storage_state_fingerprint

        assert update_web_case(
            session, ADMIN, case.id, WebCaseUpdate(name="Renamed Case")
        ).name == "Renamed Case"
        assert update_web_page(
            session,
            ADMIN,
            page.id,
            WebPageUpdate(name="Renamed Page", url_pattern="/renamed"),
        ).url_pattern == "/renamed"
        assert update_web_element(
            session,
            ADMIN,
            element.id,
            WebElementUpdate(name="Renamed Element", element_type="BUTTON"),
        ).name == "Renamed Element"
        updated_profile = update_session_profile(
            session,
            ADMIN,
            profile.id,
            SessionProfileUpdate(
                storage_state={"cookies": [{"name": "sid", "value": "new-secret"}]},
                metadata={"source": "rotated"},
            ),
        )
        assert updated_profile.metadata == {"source": "rotated"}
        assert "storage_state" not in updated_profile.model_dump()
        stored_profile = session.get(SessionProfile, profile.id)
        assert stored_profile is not None
        assert stored_profile.storage_state_ciphertext != old_ciphertext
        assert stored_profile.storage_state_fingerprint != old_fingerprint

        assert archive_web_page(session, ADMIN, page.id).status == "ARCHIVED"
        assert restore_web_page(session, ADMIN, page.id).status == "ACTIVE"
        assert archive_web_element(session, ADMIN, element.id).status == "ARCHIVED"
        assert restore_web_element(session, ADMIN, element.id).status == "ACTIVE"
        assert archive_session_profile(session, ADMIN, profile.id).status == "ARCHIVED"
        assert restore_session_profile(session, ADMIN, profile.id).status == "ACTIVE"

        outsider = CurrentUser(
            id="outsider", username="outsider", display_name="Outsider", roles=[]
        )
        with pytest.raises(ResourceNotFoundError):
            update_web_case(session, outsider, case.id, WebCaseUpdate(name="Nope"))
        with pytest.raises(ResourceNotFoundError):
            update_session_profile(
                session, outsider, profile.id, SessionProfileUpdate(name="Nope")
            )


def test_expired_session_profile_uses_fixed_login_plan_and_cas_refresh(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    heartbeat = FakeHeartbeat()
    events = FakeEvents()
    with factory() as session:
        login_case = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Session Login", content=_content()),
        )
        approve_web_case(session, ADMIN, login_case.id)
        assert login_case.current_version_id is not None
        profile = create_session_profile(
            session,
            ADMIN,
            SessionProfileCreate(
                project_id=project_id,
                environment_id=environment_id,
                name="Recoverable-Session",
                storage_state={"cookies": [{"name": "sid", "value": "expired"}]},
                expires_at=utc_now_naive() + timedelta(hours=1),
                refresh_ttl_seconds=600,
                login_web_case_id=login_case.id,
                login_web_case_version_id=login_case.current_version_id,
                expiry_condition={"type": "URL_CONTAINS", "value": "/login"},
                success_condition={"type": "URL_CONTAINS", "value": "/home"},
            ),
        )
        stored = session.get(SessionProfile, profile.id)
        assert stored is not None
        stored.created_at = utc_now_naive() - timedelta(hours=2)
        stored.expires_at = utc_now_naive() - timedelta(seconds=1)
        session.commit()
        original_fingerprint = stored.storage_state_fingerprint
        original_revision = stored.revision

        target_content = _content().model_copy(update={"session_profile_id": profile.id})
        target = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(
                project_id=project_id,
                name="Recoverable Target",
                content=target_content,
            ),
        )
        approve_web_case(session, ADMIN, target.id)
        run = create_run(
            session,
            ADMIN,
            RunCreateRequest(
                project_id=project_id,
                environment_id=environment_id,
                runner_id="runner-web-test",
                run_type="WEB_CASE",
                web_case_id=target.id,
                required_slot_type=RunnerSlotType.WEB,
            ),
            heartbeat,
            events,
        )
        dispatched = dispatch_run(
            session, ADMIN, run.id, heartbeat, FakePublisher(), events
        )
        claim_run(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            events,
        )
        plan = get_web_execution_plan(
            session, run.id, RUNNER_CREDENTIAL, dispatched.message_id
        )
        assert plan.session is not None
        assert plan.session.storage_state is None
        assert plan.session.recovery is not None
        assert plan.session.recovery.force_refresh
        assert plan.session.recovery.login_web_case_version_id == login_case.current_version_id
        start_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            ExecutionStartRequest(
                message_id=dispatched.message_id, case_run_id=plan.case_run_id
            ),
            events,
        )
        completed = complete_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            WebExecutionCompleteRequest(
                message_id=dispatched.message_id,
                case_run_id=plan.case_run_id,
                outcome="SUCCESS",
                traces=[
                    {"node_id": "action_1", "status": "SUCCESS"},
                    {"node_id": "action_2", "status": "SUCCESS"},
                    {"node_id": "assertion_1", "status": "SUCCESS"},
                ],
                refreshed_session={
                    "profile_id": profile.id,
                    "expected_revision": original_revision,
                    "expected_fingerprint": original_fingerprint,
                    "storage_state": {
                        "cookies": [{"name": "sid", "value": "refreshed-secret"}]
                    },
                },
                session_recovery={
                    "status": "SUCCESS",
                    "reason": "PROFILE_EXPIRED",
                    "login_web_case_version_id": login_case.current_version_id,
                    "duration_ms": 42,
                    "error_type": None,
                },
            ),
            events,
        )
        assert completed.session_recovery is not None
        assert completed.session_recovery.persistence_status == "UPDATED"
        session.refresh(stored)
        assert stored.revision == original_revision + 1
        assert stored.storage_state_fingerprint != original_fingerprint
        assert stored.expires_at is not None and stored.expires_at > utc_now_naive()
        result = session.scalar(
            select(RunWebExecutionResult).where(RunWebExecutionResult.run_id == run.id)
        )
        assert result is not None
        assert result.session_recovery["persistence_status"] == "UPDATED"
        assert "refreshed-secret" not in json.dumps(result.session_recovery)


def test_web_case_run_dispatch_claim_plan_start_complete_is_idempotent(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    heartbeat = FakeHeartbeat()
    events = FakeEvents()
    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Runnable", content=_content()),
        )
        approve_web_case(session, ADMIN, detail.id)
        payload = RunCreateRequest(
            project_id=project_id,
            environment_id=environment_id,
            runner_id="runner-web-test",
            run_type="WEB_CASE",
            web_case_id=detail.id,
            required_slot_type=RunnerSlotType.WEB,
        )
        run = create_run(session, ADMIN, payload, heartbeat, events)
        dispatched = dispatch_run(session, ADMIN, run.id, heartbeat, FakePublisher(), events)
        outbox = session.scalar(
            select(RunDispatchOutbox).where(RunDispatchOutbox.run_id == run.id)
        )
        assert outbox is not None
        assert set(outbox.payload) == {
            "schema_version",
            "message_id",
            "run_id",
            "runner_id",
            "run_type",
            "required_slot_type",
            "attempt",
            "enqueued_at",
        }
        assert "locator" not in outbox.payload and "session" not in outbox.payload
        claim = claim_run(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            events,
        )
        assert claim.status == RunStatus.ASSIGNED
        plan = get_web_execution_plan(session, run.id, RUNNER_CREDENTIAL, dispatched.message_id)
        assert plan.web_case_version_id == detail.current_version_id
        assert plan.actions[1].locator is not None
        assert [candidate.priority for candidate in plan.actions[1].locator.candidates] == [1]
        started = start_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            ExecutionStartRequest(message_id=dispatched.message_id, case_run_id=plan.case_run_id),
            events,
        )
        assert started.status == RunStatus.RUNNING
        assert start_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            ExecutionStartRequest(message_id=dispatched.message_id, case_run_id=plan.case_run_id),
            events,
        ).idempotent
        traces = [
            {"node_id": "action_1", "status": "SUCCESS", "duration_ms": 5},
            {"node_id": "action_2", "status": "SUCCESS", "duration_ms": 5},
            {"node_id": "assertion_1", "status": "SUCCESS", "duration_ms": 5},
        ]
        request = WebExecutionCompleteRequest(
            message_id=dispatched.message_id,
            case_run_id=plan.case_run_id,
            outcome="SUCCESS",
            traces=traces,
        )
        completed = complete_web_execution(session, run.id, RUNNER_CREDENTIAL, request, events)
        assert completed.run_status == RunStatus.SUCCESS
        repeated = complete_web_execution(session, run.id, RUNNER_CREDENTIAL, request, events)
        assert repeated.idempotent
        assert (
            session.scalar(
                select(RunWebExecutionResult).where(RunWebExecutionResult.run_id == run.id)
            )
            is not None
        )
        assert sum(item["to_status"] == "SUCCESS" for item in events.events) >= 1
        with pytest.raises(AuthenticationError):
            get_web_execution_plan(session, run.id, "wrong-credential", dispatched.message_id)


def test_web_plan_minimizes_typed_environment_context_and_protected_session_state(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    with factory() as session:
        profile = create_session_profile(
            session,
            ADMIN,
            SessionProfileCreate(
                project_id=project_id,
                environment_id=environment_id,
                name="Browser-Session",
                storage_state={"cookies": [{"name": "sid", "value": "session-secret"}]},
                metadata={"source": "test"},
            ),
        )
        assert profile.metadata == {"source": "test"}
        assert "storage_state" not in profile.model_dump()
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(
                project_id=project_id,
                name="Context Web",
                content=_context_content(session_profile_id=profile.id),
            ),
        )
        approve_web_case(session, ADMIN, detail.id)
        payload = RunCreateRequest(
            project_id=project_id,
            environment_id=environment_id,
            runner_id="runner-web-test",
            run_type="WEB_CASE",
            web_case_id=detail.id,
            required_slot_type=RunnerSlotType.WEB,
        )
        validation, _ = validate_run_request(session, ADMIN, payload, FakeHeartbeat())
        assert validation.valid
        run = create_run(session, ADMIN, payload, FakeHeartbeat(), FakeEvents())
        dispatched = dispatch_run(
            session, ADMIN, run.id, FakeHeartbeat(), FakePublisher(), FakeEvents()
        )
        claim_run(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            FakeEvents(),
        )
        plan = get_web_execution_plan(session, run.id, RUNNER_CREDENTIAL, dispatched.message_id)
        assert plan.initial_context == {
            "run_id": run.id,
            "web_case_id": detail.id,
            "project_id": project_id,
            "environment_id": environment_id,
            "base_url": "https://env.example",
            "tenant_number": 7,
            "payload_config": {"mode": "safe"},
        }
        assert plan.session is not None
        assert plan.session.storage_state["cookies"][0]["value"] == "session-secret"
        cancel_run(session, ADMIN, run.id, FakeEvents())
        cancelling_plan = get_web_execution_plan(
            session, run.id, RUNNER_CREDENTIAL, dispatched.message_id
        )
        assert cancelling_plan.session is None


def test_web_complete_uses_cancellation_first_and_trace_schema_is_strict(
    web_context: tuple[sessionmaker[Session], int, int],
) -> None:
    factory, project_id, environment_id = web_context
    heartbeat = FakeHeartbeat()
    events = FakeEvents()
    with factory() as session:
        detail = create_web_case(
            session,
            ADMIN,
            WebCaseCreate(project_id=project_id, name="Cancel Web", content=_content()),
        )
        approve_web_case(session, ADMIN, detail.id)
        run = create_run(
            session,
            ADMIN,
            RunCreateRequest(
                project_id=project_id,
                environment_id=environment_id,
                runner_id="runner-web-test",
                run_type="WEB_CASE",
                web_case_id=detail.id,
                required_slot_type=RunnerSlotType.WEB,
            ),
            heartbeat,
            events,
        )
        dispatched = dispatch_run(session, ADMIN, run.id, heartbeat, FakePublisher(), events)
        claim_run(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            RunClaimRequest(message_id=dispatched.message_id),
            events,
        )
        plan = get_web_execution_plan(session, run.id, RUNNER_CREDENTIAL, dispatched.message_id)
        start_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            ExecutionStartRequest(message_id=dispatched.message_id, case_run_id=plan.case_run_id),
            events,
        )
        cancelled = cancel_run(session, ADMIN, run.id, events)
        assert cancelled.status == RunStatus.CANCELLING
        traces = [
            {"node_id": "action_1", "status": "SUCCESS"},
            {"node_id": "action_2", "status": "SUCCESS"},
            {"node_id": "assertion_1", "status": "SUCCESS"},
        ]
        completed = complete_web_execution(
            session,
            run.id,
            RUNNER_CREDENTIAL,
            WebExecutionCompleteRequest(
                message_id=dispatched.message_id,
                case_run_id=plan.case_run_id,
                outcome="SUCCESS",
                traces=traces,
            ),
            events,
        )
        assert completed.outcome == "CANCELLED"
        assert completed.run_status == RunStatus.CANCELLED
        assert completed.traces == []
        result = session.scalar(
            select(RunWebExecutionResult).where(RunWebExecutionResult.run_id == run.id)
        )
        assert result is not None and result.outcome == "CANCELLED"
        with pytest.raises(ValidationError):
            WebExecutionCompleteRequest(
                message_id=dispatched.message_id,
                case_run_id=plan.case_run_id,
                outcome="SUCCESS",
                traces=[{"node_id": "action_1", "status": "SUCCESS", "secret": "x"}],
            )
        redacted = WebExecutionTrace(
            node_id="action_1", status="FAILED", error_message="Bearer session-secret"
        )
        assert "session-secret" not in (redacted.error_message or "")
