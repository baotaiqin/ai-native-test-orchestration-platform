from enum import StrEnum


class RunType(StrEnum):
    API_CASE = "API_CASE"
    SCENARIO = "SCENARIO"
    WEB_CASE = "WEB_CASE"


class RunTriggerType(StrEnum):
    MANUAL = "MANUAL"
    API = "API"
    SYSTEM = "SYSTEM"


class RunStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class RunNodeStatus(StrEnum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REVIEW = "REVIEW"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    SKIPPED = "SKIPPED"


class DispatchOutboxStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMEOUT,
    }
)

TERMINAL_NODE_STATUSES = frozenset(
    {
        RunNodeStatus.SUCCESS,
        RunNodeStatus.FAILED,
        RunNodeStatus.REVIEW,
        RunNodeStatus.CANCELLED,
        RunNodeStatus.TIMEOUT,
        RunNodeStatus.SKIPPED,
    }
)
