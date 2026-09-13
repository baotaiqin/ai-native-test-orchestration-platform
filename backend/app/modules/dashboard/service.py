from datetime import UTC, datetime, time, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundError
from app.core.redaction import redact_text
from app.core.time import to_utc_aware, utc_now_aware
from app.infrastructure.redis.client import RedisRunnerHeartbeatStore
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.models import Project, ProjectMember
from app.modules.projects.service import get_project, is_admin
from app.modules.reports.schemas import ReportRate
from app.modules.requirement_reviews.models import RequirementReview
from app.modules.requirements.models import Requirement
from app.modules.runners.models import Runner
from app.modules.runners.schemas import OnlineStatus, RunnerStatus
from app.modules.runners.service import get_runner_online_state
from app.modules.runs.enums import RunStatus
from app.modules.runs.models import TestRun
from app.modules.scenarios.models import Scenario
from app.modules.test_cases.models import AiCaseGeneration, AiCaseSuggestion, TestCase
from app.modules.web_cases.models import WebCase, WebCaseVersion
from app.modules.web_healing.models import WebHealingProposal
from app.modules.web_recording_ai.models import WebRecordingAiSuggestion
from app.modules.web_recordings.models import WebRecording

from .schemas import (
    DashboardCaseInventory,
    DashboardDateRange,
    DashboardPendingReviews,
    DashboardQuery,
    DashboardRecentRun,
    DashboardResponse,
    DashboardRunnerOverview,
    DashboardTodayRuns,
)

DASHBOARD_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
RECENT_RUN_LIMIT = 10


def _visible_active_projects(
    session: Session, user: CurrentUser, project_id: int | None
) -> list[Project]:
    if project_id is not None:
        project = get_project(session, user, project_id)
        if project.status != "ACTIVE":
            raise ResourceNotFoundError("项目不存在")
        return [project]
    statement = select(Project).where(Project.status == "ACTIVE")
    if not is_admin(user):
        statement = statement.join(ProjectMember).where(
            ProjectMember.user_id == user.id
        )
    return list(session.scalars(statement.order_by(Project.id.asc())).all())


def _count(session: Session, statement) -> int:
    return int(session.scalar(statement) or 0)


def _case_inventory(
    session: Session, project_ids: list[int]
) -> DashboardCaseInventory:
    api_cases = _count(
        session,
        select(func.count(TestCase.id)).where(
            TestCase.project_id.in_(project_ids),
            TestCase.status != "ARCHIVED",
            TestCase.case_type == "API",
        ),
    )
    web_cases = _count(
        session,
        select(func.count(WebCase.id)).where(
            WebCase.project_id.in_(project_ids),
            WebCase.status != "ARCHIVED",
        ),
    )
    scenarios = _count(
        session,
        select(func.count(Scenario.id)).where(
            Scenario.project_id.in_(project_ids),
            Scenario.status != "ARCHIVED",
        ),
    )
    return DashboardCaseInventory(
        api_cases=api_cases,
        web_cases=web_cases,
        case_total=api_cases + web_cases,
        scenarios=scenarios,
        definition=(
            "API Case 为非 ARCHIVED test_cases(case_type=API)；Web Case 为非 "
            "ARCHIVED web_cases；Scenario 单独统计且不计入 case_total"
        ),
    )


def _today_range(now: datetime) -> tuple[datetime, datetime]:
    aware_now = to_utc_aware(now)
    local_date = aware_now.astimezone(DASHBOARD_TIMEZONE).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=DASHBOARD_TIMEZONE)
    start = local_start.astimezone(UTC)
    end = (local_start + timedelta(days=1)).astimezone(UTC)
    return start, end


def _today_runs(
    session: Session,
    project_ids: list[int],
    start: datetime,
    end: datetime,
) -> DashboardTodayRuns:
    rows = session.execute(
        select(TestRun.status, func.count(TestRun.id))
        .where(
            TestRun.project_id.in_(project_ids),
            TestRun.created_at >= start.replace(tzinfo=None),
            TestRun.created_at < end.replace(tzinfo=None),
        )
        .group_by(TestRun.status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    success = counts.get(RunStatus.SUCCESS.value, 0)
    failed = counts.get(RunStatus.FAILED.value, 0)
    timeout = counts.get(RunStatus.TIMEOUT.value, 0)
    cancelled = counts.get(RunStatus.CANCELLED.value, 0)
    denominator = success + failed + timeout
    total = sum(counts.values())
    unfinished = total - success - failed - timeout - cancelled
    return DashboardTodayRuns(
        total=total,
        success=success,
        failed=failed,
        timeout=timeout,
        cancelled=cancelled,
        unfinished=unfinished,
        success_rate=ReportRate(
            numerator=success,
            denominator=denominator,
            value=success / denominator if denominator else None,
            definition=(
                "今日 created_at 落在上海自然日且状态为 SUCCESS 的 Run / "
                "(SUCCESS + FAILED + TIMEOUT)；CANCELLED 与未完成不进入分母"
            ),
        ),
    )


def _pending_reviews(
    session: Session, project_ids: list[int]
) -> DashboardPendingReviews:
    requirement_reviews = _count(
        session,
        select(func.count(distinct(RequirementReview.id)))
        .join(Requirement, Requirement.id == RequirementReview.requirement_id)
        .where(
            RequirementReview.project_id.in_(project_ids),
            RequirementReview.status == "DRAFT",
            Requirement.project_id == RequirementReview.project_id,
            Requirement.status == "ACTIVE",
            Requirement.current_version_id.is_not(None),
        ),
    )
    ai_case_suggestions = _count(
        session,
        select(func.count(distinct(AiCaseSuggestion.id)))
        .join(AiCaseGeneration, AiCaseGeneration.id == AiCaseSuggestion.generation_id)
        .join(Requirement, Requirement.id == AiCaseGeneration.requirement_id)
        .where(
            AiCaseSuggestion.status == "DRAFT",
            AiCaseGeneration.project_id.in_(project_ids),
            Requirement.project_id == AiCaseGeneration.project_id,
            Requirement.status == "ACTIVE",
            Requirement.current_version_id.is_not(None),
        ),
    )
    recording_suggestions = _count(
        session,
        select(func.count(distinct(WebRecordingAiSuggestion.id)))
        .join(WebRecording, WebRecording.id == WebRecordingAiSuggestion.recording_id)
        .where(
            WebRecordingAiSuggestion.project_id.in_(project_ids),
            WebRecordingAiSuggestion.status == "DRAFT",
            WebRecordingAiSuggestion.draft_key == "DRAFT",
            WebRecordingAiSuggestion.ai_call_id.is_not(None),
            WebRecording.project_id == WebRecordingAiSuggestion.project_id,
            WebRecording.status == "COMPLETED",
            WebRecording.confirmed_web_case_id.is_(None),
        ),
    )
    healing_proposals = _count(
        session,
        select(func.count(distinct(WebHealingProposal.id)))
        .join(WebCase, WebCase.id == WebHealingProposal.web_case_id)
        .join(
            WebCaseVersion,
            and_(
                WebCaseVersion.id == WebHealingProposal.web_case_version_id,
                WebCaseVersion.web_case_id == WebHealingProposal.web_case_id,
            ),
        )
        .where(
            WebHealingProposal.project_id.in_(project_ids),
            WebHealingProposal.status == "DRAFT",
            WebHealingProposal.draft_key == "DRAFT",
            WebHealingProposal.ai_call_id.is_not(None),
            WebHealingProposal.proposed_locator.is_not(None),
            WebHealingProposal.reason != "待生成",
            WebCase.project_id == WebHealingProposal.project_id,
            WebCase.status != "ARCHIVED",
        ),
    )
    total = (
        requirement_reviews
        + ai_case_suggestions
        + recording_suggestions
        + healing_proposals
    )
    return DashboardPendingReviews(
        requirement_reviews=requirement_reviews,
        ai_case_suggestions=ai_case_suggestions,
        web_recording_ai_suggestions=recording_suggestions,
        web_healing_proposals=healing_proposals,
        total=total,
        definition=(
            "仅统计四类可处理 DRAFT；已归档/停用父资产、未完成 AI 占位和已确认录制不计入"
        ),
    )


def _runner_overview(
    session: Session,
    user: CurrentUser,
    store: RedisRunnerHeartbeatStore,
) -> DashboardRunnerOverview:
    if not is_admin(user):
        return DashboardRunnerOverview(
            visibility="ADMIN_ONLY",
            available=None,
            status="HIDDEN",
        )
    runners = list(
        session.scalars(select(Runner).order_by(Runner.id.asc())).all()
    )
    active = [item for item in runners if item.status == RunnerStatus.ACTIVE.value]
    online = 0
    offline = 0
    unknown = 0
    redis_available = True
    for runner in active:
        online_status, _, available = get_runner_online_state(runner, store)
        redis_available = redis_available and available
        if online_status == OnlineStatus.ONLINE:
            online += 1
        elif online_status == OnlineStatus.OFFLINE:
            offline += 1
        else:
            unknown += 1
    if not redis_available:
        return DashboardRunnerOverview(
            visibility="VISIBLE",
            available=False,
            status="UNKNOWN",
            registered_total=len(runners),
            active_total=len(active),
            online=None,
            offline=None,
            unknown=len(active),
        )
    return DashboardRunnerOverview(
        visibility="VISIBLE",
        available=True,
        status="KNOWN",
        registered_total=len(runners),
        active_total=len(active),
        online=online,
        offline=offline,
        unknown=unknown,
    )


def _recent_runs(
    session: Session, project_ids: list[int]
) -> list[DashboardRecentRun]:
    rows = session.execute(
        select(TestRun, Project.name)
        .join(Project, Project.id == TestRun.project_id)
        .where(TestRun.project_id.in_(project_ids), Project.status == "ACTIVE")
        .order_by(TestRun.created_at.desc(), TestRun.id.desc())
        .limit(RECENT_RUN_LIMIT)
    ).all()
    return [
        DashboardRecentRun(
            run_id=run.id,
            run_code=redact_text(run.run_code),
            run_type=run.run_type,
            project_id=run.project_id,
            project_name=redact_text(project_name),
            status=run.status,
            created_at=to_utc_aware(run.created_at),
            ended_at=to_utc_aware(run.ended_at),
            report_path=f"/api/v1/reports/{quote(run.id, safe='')}",
        )
        for run, project_name in rows
    ]


def get_dashboard(
    session: Session,
    user: CurrentUser,
    query: DashboardQuery,
    store: RedisRunnerHeartbeatStore,
    *,
    observed_at: datetime | None = None,
) -> DashboardResponse:
    now = to_utc_aware(observed_at or utc_now_aware())
    projects = _visible_active_projects(session, user, query.project_id)
    project_ids = [item.id for item in projects]
    start, end = _today_range(now)
    return DashboardResponse(
        generated_at=now,
        project_scope_id=query.project_id,
        active_project_count=len(projects),
        date_range=DashboardDateRange(
            start_utc=start,
            end_utc_exclusive=end,
        ),
        cases=_case_inventory(session, project_ids),
        today_runs=_today_runs(session, project_ids, start, end),
        pending_reviews=_pending_reviews(session, project_ids),
        runners=_runner_overview(session, user, store),
        recent_runs=_recent_runs(session, project_ids),
    )
