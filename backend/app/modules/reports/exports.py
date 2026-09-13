import hashlib
import html
import json
import math
import re
import string
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypeVar

from fastapi import status
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.time import utc_now_aware
from app.modules.auth.schemas import CurrentUser
from app.modules.runs.enums import TERMINAL_RUN_STATUSES

from .schemas import (
    ReportAiAuditGroup,
    ReportCase,
    ReportDataField,
    ReportDetailResponse,
    ReportEvidence,
    ReportEvidencePageQuery,
    ReportPageQuery,
    ReportRequirementSource,
    ReportRequirementSourcePageQuery,
    ReportStep,
    ReportStepPageQuery,
)
from .service import (
    get_report_detail,
    list_report_cases,
    list_report_evidence,
    list_report_requirement_sources,
    list_report_steps,
)

ReportExportFormat = Literal["markdown", "html"]

MAX_EXPORT_CASES = 2_000
MAX_EXPORT_STEPS = 10_000
MAX_EXPORT_EVIDENCE = 5_000
MAX_EXPORT_REQUIREMENT_SOURCES = 2_000
MAX_EXPORT_BYTES = 16 * 1024 * 1024
EXPORT_PAGE_SIZE = 100

REPORT_EXPORT_LIMIT_ERROR_CODE = "REPORT_EXPORT_LIMIT_EXCEEDED"
REPORT_EXPORT_LIMIT_ERROR_MESSAGE = (
    "报告超过同步导出上限，请缩小运行规模或使用报告分页页面查看"
)
REPORT_EXPORT_SNAPSHOT_ERROR_CODE = "REPORT_EXPORT_SNAPSHOT_CHANGED"
REPORT_EXPORT_SNAPSHOT_ERROR_MESSAGE = "报告在导出期间发生变化，请重试"
REPORT_EXPORT_ENCODING_ERROR_CODE = "REPORT_EXPORT_ENCODING_ERROR"
REPORT_EXPORT_ENCODING_ERROR_MESSAGE = "报告包含无法安全编码为 UTF-8 的文本"

HTML_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; "
    "object-src 'none'; script-src 'none'; connect-src 'none'; img-src 'none'; "
    "media-src 'none'; font-src 'none'; style-src 'unsafe-inline'"
)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKDOWN_ESCAPABLE = string.punctuation.replace("\\", "")


@dataclass(frozen=True)
class ReportExportFile:
    content: bytes
    filename: str
    media_type: str
    content_security_policy: str | None = None


@dataclass(frozen=True)
class _ReportExportSnapshot:
    generated_at: datetime
    detail: ReportDetailResponse
    cases: tuple[ReportCase, ...]
    steps: tuple[ReportStep, ...]
    evidence: tuple[ReportEvidence, ...]
    requirement_sources: tuple[ReportRequirementSource, ...]

    @property
    def may_change(self) -> bool:
        return self.detail.summary.status not in TERMINAL_RUN_STATUSES


class _Utf8Writer:
    def __init__(self, maximum_bytes: int | None = None) -> None:
        self.maximum_bytes = maximum_bytes if maximum_bytes is not None else MAX_EXPORT_BYTES
        self._chunks: list[bytes] = []
        self.byte_count = 0

    def append(self, value: str) -> None:
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise AppError(
                REPORT_EXPORT_ENCODING_ERROR_CODE,
                REPORT_EXPORT_ENCODING_ERROR_MESSAGE,
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        observed = self.byte_count + len(encoded)
        if observed > self.maximum_bytes:
            _raise_export_limit("utf8_bytes", self.maximum_bytes, observed)
        self._chunks.append(encoded)
        self.byte_count = observed

    def build(self) -> bytes:
        return b"".join(self._chunks)


def _raise_export_limit(resource: str, limit: int, observed: int) -> None:
    raise AppError(
        REPORT_EXPORT_LIMIT_ERROR_CODE,
        REPORT_EXPORT_LIMIT_ERROR_MESSAGE,
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        details={"resource": resource, "limit": limit, "observed": observed},
    )


def _enforce_count_limit(resource: str, observed: int, limit: int) -> None:
    if observed > limit:
        _raise_export_limit(resource, limit, observed)


def _raise_snapshot_changed() -> None:
    raise AppError(
        REPORT_EXPORT_SNAPSHOT_ERROR_CODE,
        REPORT_EXPORT_SNAPSHOT_ERROR_MESSAGE,
        status_code=status.HTTP_409_CONFLICT,
    )


def _snapshot_engine(source_session: Session) -> Engine:
    bind = source_session.get_bind()
    if isinstance(bind, Engine):
        return bind
    if isinstance(bind, Connection):
        return bind.engine
    _raise_snapshot_changed()
    raise AssertionError("unreachable")


def _is_single_database_memory_sqlite(engine: Engine) -> bool:
    if engine.dialect.name != "sqlite":
        return False
    database = engine.url.database
    return database in (None, "", ":memory:") or "mode=memory" in str(engine.url)


def _source_has_external_transaction(source_session: Session) -> bool:
    bind = source_session.get_bind()
    return bool(
        source_session.in_transaction()
        or (isinstance(bind, Connection) and bind.in_transaction())
    )


def _begin_consistent_read(connection: Connection) -> Connection:
    dialect_name = connection.dialect.name
    if dialect_name == "mysql":
        # WITH CONSISTENT SNAPSHOT is effective only at REPEATABLE READ. Applying
        # the isolation option to this owned connection avoids changing global
        # engine configuration or the caller's transaction.
        connection = connection.execution_options(isolation_level="REPEATABLE READ")
        connection.exec_driver_sql(
            "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
        )
        return connection
    if dialect_name == "sqlite":
        # Python's sqlite driver can have a SQLAlchemy logical transaction while
        # SELECT statements still run without a database-level BEGIN. An explicit
        # BEGIN makes the first report SELECT establish the WAL read snapshot.
        connection.exec_driver_sql("BEGIN")
        return connection
    _raise_snapshot_changed()
    raise AssertionError("unreachable")


@contextmanager
def _consistent_read_session(source_session: Session) -> Iterator[Session]:
    engine = _snapshot_engine(source_session)
    if _is_single_database_memory_sqlite(engine) and _source_has_external_transaction(
        source_session
    ):
        # A memory SQLite StaticPool can return the caller's exact DBAPI
        # connection. Refuse rather than begin/rollback inside an external
        # transaction that this export does not own.
        _raise_snapshot_changed()

    connection = engine.connect()
    snapshot_session: Session | None = None
    try:
        connection = _begin_consistent_read(connection)
        snapshot_session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
            join_transaction_mode="rollback_only",
        )
        yield snapshot_session
    finally:
        try:
            if snapshot_session is not None:
                snapshot_session.close()
        finally:
            try:
                # Only the dedicated connection is rolled back. The source
                # Session may own an unrelated transaction and is untouched.
                if connection.in_transaction():
                    connection.rollback()
            finally:
                connection.close()


_PageItem = TypeVar("_PageItem")


def _collect_all(
    total: int,
    fetch: Callable[[int], Any],
) -> tuple[_PageItem, ...]:
    if total == 0:
        return ()
    items: list[_PageItem] = []
    seen: set[Any] = set()
    page_count = math.ceil(total / EXPORT_PAGE_SIZE)
    for page_number in range(1, page_count + 1):
        page = fetch(page_number)
        if page.total != total or page.page != page_number:
            _raise_snapshot_changed()
        for item in page.items:
            item_id = item.id
            if item_id in seen:
                _raise_snapshot_changed()
            seen.add(item_id)
            items.append(item)
    if len(items) != total:
        _raise_snapshot_changed()
    return tuple(items)


def _load_snapshot(
    session: Session,
    user: CurrentUser,
    run_id: str,
    *,
    generated_at: datetime | None = None,
) -> _ReportExportSnapshot:
    with _consistent_read_session(session) as snapshot_session:
        observed_at = generated_at or utc_now_aware()
        detail = get_report_detail(
            snapshot_session,
            user,
            run_id,
            observed_at=observed_at,
        )
        _enforce_count_limit("cases", detail.cases.total, MAX_EXPORT_CASES)
        _enforce_count_limit("steps", detail.steps.total, MAX_EXPORT_STEPS)
        _enforce_count_limit("evidence", detail.evidence.total, MAX_EXPORT_EVIDENCE)
        _enforce_count_limit(
            "requirement_sources",
            detail.requirement_sources.total,
            MAX_EXPORT_REQUIREMENT_SOURCES,
        )

        cases = _collect_all(
            detail.cases.total,
            lambda page: list_report_cases(
                snapshot_session,
                user,
                run_id,
                ReportPageQuery(page=page, page_size=EXPORT_PAGE_SIZE),
            ),
        )
        steps = _collect_all(
            detail.steps.total,
            lambda page: list_report_steps(
                snapshot_session,
                user,
                run_id,
                ReportStepPageQuery(page=page, page_size=EXPORT_PAGE_SIZE),
            ),
        )
        evidence = _collect_all(
            detail.evidence.total,
            lambda page: list_report_evidence(
                snapshot_session,
                user,
                run_id,
                ReportEvidencePageQuery(page=page, page_size=EXPORT_PAGE_SIZE),
            ),
        )
        requirement_sources = _collect_all(
            detail.requirement_sources.total,
            lambda page: list_report_requirement_sources(
                snapshot_session,
                user,
                run_id,
                ReportRequirementSourcePageQuery(
                    page=page, page_size=EXPORT_PAGE_SIZE
                ),
            ),
        )
        return _ReportExportSnapshot(
            generated_at=observed_at,
            detail=detail,
            cases=cases,
            steps=steps,
            evidence=evidence,
            requirement_sources=requirement_sources,
        )


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _plain(value: Any) -> str:
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, datetime):
        text = value.isoformat()
    elif isinstance(value, Enum):
        text = str(value.value)
    elif isinstance(value, (BaseModel, dict, list, tuple)):
        text = _json(value)
    else:
        text = str(value)
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise AppError(
            REPORT_EXPORT_ENCODING_ERROR_CODE,
            REPORT_EXPORT_ENCODING_ERROR_MESSAGE,
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    return _CONTROL_CHARACTERS.sub("�", text).replace("\r\n", "\n").replace("\r", "\n")


def _markdown(value: Any) -> str:
    text = _plain(value).replace("\n", " ↵ ").replace("\t", " ")
    text = text.replace("\\", "\\\\")
    for marker in _MARKDOWN_ESCAPABLE:
        text = text.replace(marker, f"\\{marker}")
    return text


def _html(value: Any) -> str:
    return html.escape(_plain(value), quote=True)


def _safe_filename(run_id: str, extension: str) -> str:
    encoded = _plain(run_id).encode("utf-8", errors="strict")
    slug = _UNSAFE_FILENAME.sub("_", run_id).strip("._-")[:64] or "run"
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"ai-test-report-{slug}-{digest}.{extension}"


def _summary_rows(snapshot: _ReportExportSnapshot) -> list[tuple[str, Any]]:
    summary = snapshot.detail.summary
    return [
        ("生成时刻（UTC）", snapshot.generated_at),
        ("快照可能继续变化", snapshot.may_change),
        ("Run ID", summary.run_id),
        ("Run Code", summary.run_code),
        ("Run Type", summary.run_type),
        ("Run Status", summary.status),
        ("Trigger Type", summary.trigger_type),
        ("Created At", summary.created_at),
        ("Started At", summary.started_at),
        ("Ended At", summary.ended_at),
        ("Updated At", summary.updated_at),
        ("Duration", summary.duration),
        ("Error Type", summary.error_type),
        ("Error Message", summary.error_message),
        ("Project", summary.project),
        ("Environment", summary.environment),
        ("Runner", summary.runner),
        ("Target", summary.target),
    ]


def _count_rows(snapshot: _ReportExportSnapshot) -> list[tuple[str, Any]]:
    summary = snapshot.detail.summary
    return [
        ("Run 原始记录计数", summary.recorded_counts),
        ("Case 实际分组计数", summary.case_status_counts),
        ("Case 成功率", summary.case_success_rate),
        ("完整 Case 数", len(snapshot.cases)),
        ("完整 Step 数", len(snapshot.steps)),
        ("完整 Evidence 数", len(snapshot.evidence)),
        ("完整需求来源数", len(snapshot.requirement_sources)),
    ]


def _markdown_table(writer: _Utf8Writer, rows: Iterable[tuple[str, Any]]) -> None:
    writer.append("| 字段 | 值 |\n| --- | --- |\n")
    for label, value in rows:
        writer.append(f"| {_markdown(label)} | {_markdown(value)} |\n")
    writer.append("\n")


def _markdown_data_field(
    writer: _Utf8Writer, label: str, field: ReportDataField
) -> None:
    writer.append(f"- {_markdown(label)} availability: {_markdown(field.availability)}\n")
    writer.append(f"- {_markdown(label)} note: {_markdown(field.note)}\n")
    writer.append(f"- {_markdown(label)} value: {_markdown(field.value)}\n")


def _markdown_ai_group(
    writer: _Utf8Writer, label: str, group: ReportAiAuditGroup
) -> None:
    writer.append(f"### {_markdown(label)}\n\n")
    _markdown_table(
        writer,
        [
            ("Total", group.total),
            ("Has More", group.has_more),
            ("受保护应用路径", group.continuation_path),
        ],
    )
    if group.latest is None:
        writer.append("无已记录的最新审计引用。\n\n")
        return
    _markdown_table(
        writer,
        [
            ("ID", group.latest.id),
            ("Kind", group.latest.kind),
            ("CaseRun ID", group.latest.case_run_id),
            ("Status", group.latest.status),
            ("AI Call ID", group.latest.ai_call_id),
            ("Created At", group.latest.created_at),
            ("公开安全摘要", group.latest.summary),
        ],
    )


def _render_markdown(snapshot: _ReportExportSnapshot) -> bytes:
    writer = _Utf8Writer()
    writer.append("# AI 原生智能测试编排平台 - Run 报告\n\n")
    writer.append(
        "> 只读导出快照。数据来自受保护报告公开模型；未记录内容不会从当前配置重建。\n\n"
    )
    if snapshot.may_change:
        writer.append("> 当前 Run 尚未终态，本文件仅表示生成时刻的快照，后续数据可能变化。\n\n")
    writer.append("## Run 摘要与引用\n\n")
    _markdown_table(writer, _summary_rows(snapshot))
    writer.append("## 计数与口径\n\n")
    _markdown_table(writer, _count_rows(snapshot))

    writer.append("## 运行需求来源快照\n\n")
    for capture in snapshot.detail.requirement_sources.captures:
        writer.append(f"### CaseRun {_markdown(capture.case_run_id)} 捕获状态\n\n")
        _markdown_table(
            writer,
            [
                ("Status", capture.status),
                ("Captured At", capture.captured_at),
                ("Captured At Time Basis", capture.captured_at_time_basis),
                ("Target Type", capture.target_type),
                ("Target Asset ID", capture.target_asset_id),
                ("Target Version ID", capture.target_version_id),
                ("Source Count", capture.total),
                ("Consistency Basis", capture.consistency_basis),
                ("说明", capture.note),
            ],
        )
    writer.append(
        f"### 来源明细（完整，共 {len(snapshot.requirement_sources)} 条）\n\n"
    )
    writer.append(
        "| ID | CaseRun ID | 顺序 | 原关联 ID | 需求 ID | 编码 | 标题 | 需求版本 | "
        "关联资产类型 | 运行目标版本 | 关联资产版本 | 资产版本绑定 | 绑定说明 | 关系 | 来源 | "
        "置信度 | 捕获时刻 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
        "--- | --- | --- | --- | --- |\n"
    )
    for source in snapshot.requirement_sources:
        values = (
            source.id,
            source.case_run_id,
            source.sequence_no,
            source.original_link_id,
            source.requirement_id,
            source.requirement_code,
            source.requirement_title,
            {
                "id": source.requirement_version_id,
                "version_no": source.requirement_version_no,
                "content_hash": source.requirement_content_hash,
                "source_type": source.requirement_source_type,
                "binding": source.requirement_version_binding,
            },
            source.link_asset_type,
            source.target_version_id,
            source.link_asset_version_id,
            source.asset_version_binding,
            source.binding_note,
            source.relation_type,
            source.source,
            source.confidence,
            source.captured_at,
        )
        writer.append("| " + " | ".join(_markdown(value) for value in values) + " |\n")
    writer.append("\n")

    writer.append(f"## Cases（完整，共 {len(snapshot.cases)} 条）\n\n")
    for case in snapshot.cases:
        writer.append(f"### Case {_markdown(case.sequence_no)} / ID {_markdown(case.id)}\n\n")
        _markdown_table(
            writer,
            [
                ("Run ID", case.run_id),
                ("Target", case.target),
                ("Status", case.status),
                ("Duration ms", case.duration_ms),
                ("Retry Count", case.retry_count),
                ("Started At", case.started_at),
                ("Ended At", case.ended_at),
                ("Error Type", case.error_type),
                ("Error Message", case.error_message),
                ("Result Availability", case.execution_result.availability),
                ("Result Outcome", case.execution_result.outcome),
                ("Result Status", case.execution_result.status),
                ("Result Retry Count", case.execution_result.retry_count),
                ("Result Error Type", case.execution_result.error_type),
                ("Result Error Message", case.execution_result.error_message),
                ("Result Completed At", case.execution_result.completed_at),
            ],
        )
        result = case.execution_result
        _markdown_data_field(writer, "请求配置快照", result.actual_request)
        _markdown_data_field(writer, "Response", result.response)
        _markdown_data_field(writer, "Extractions", result.extractions)
        _markdown_data_field(writer, "Assertions", result.assertions)
        _markdown_data_field(writer, "Traces", result.traces)
        writer.append("\n")

    writer.append(f"## Steps（完整，共 {len(snapshot.steps)} 条）\n\n")
    writer.append(
        "| ID | CaseRun ID | 顺序 | Node ID | 名称 | 类型 | 状态 | 耗时 ms | 重试 | "
        "开始 | 结束 | Error Type | Error Message |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )
    for step in snapshot.steps:
        values = (
            step.id,
            step.case_run_id,
            step.sequence_no,
            step.node_id,
            step.name,
            step.type,
            step.status,
            step.duration_ms,
            step.retry_count,
            step.started_at,
            step.ended_at,
            step.error_type,
            step.error_message,
        )
        writer.append("| " + " | ".join(_markdown(value) for value in values) + " |\n")
    writer.append("\n")

    writer.append(f"## Evidence（完整，共 {len(snapshot.evidence)} 条）\n\n")
    writer.append(
        "| ID | CaseRun ID | StepRun ID | 类型 | 文件名 | MIME | 字节 | SHA256 | 元数据 | "
        "创建时刻 | 受保护下载路径 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )
    for artifact in snapshot.evidence:
        values = (
            artifact.id,
            artifact.case_run_id,
            artifact.step_run_id,
            artifact.artifact_type,
            artifact.file_name,
            artifact.mime,
            artifact.size,
            artifact.sha256,
            artifact.metadata,
            artifact.created_at,
            artifact.download_path,
        )
        writer.append("| " + " | ".join(_markdown(value) for value in values) + " |\n")
    writer.append("\n")

    writer.append("## 已有 AI 审计引用\n\n")
    related = snapshot.detail.related_ai
    _markdown_ai_group(writer, "Web Failure Analyses", related.failure_analyses)
    _markdown_ai_group(writer, "Web Healing Proposals", related.healing_proposals)
    writer.append(f"Raw AI Content Exposed: {_markdown(related.raw_ai_content_exposed)}\n")
    return writer.build()


_HTML_STYLE = """
:root {
  color-scheme: light;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #172033;
  background: #f5f7fb;
}
body { margin: 0; padding: 32px; }
.page { max-width: 1240px; margin: auto; }
.hero, .section, .card {
  background: #fff;
  border: 1px solid #dfe5ef;
  border-radius: 14px;
  box-shadow: 0 4px 18px rgba(30, 45, 75, .06);
}
.hero { padding: 28px; margin-bottom: 20px; }
.section { padding: 22px; margin: 18px 0; }
.card { padding: 18px; margin: 14px 0; }
h1, h2, h3 { margin-top: 0; color: #102247; }
h1 { font-size: 28px; }
h2 { font-size: 21px; border-bottom: 2px solid #e8edf5; padding-bottom: 10px; }
h3 { font-size: 17px; }
.notice {
  padding: 12px 14px;
  border-radius: 10px;
  background: #fff7df;
  border: 1px solid #f0ce75;
}
.muted { color: #5e6d85; }
.table-wrap { overflow-wrap: anywhere; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
.kv-table th { width: 140px; min-width: 120px; }
th, td {
  border: 1px solid #dfe5ef;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}
th { background: #edf2fa; color: #22365d; }
pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.pill {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  background: #e7efff;
  color: #174a9b;
  font-weight: 600;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 14px;
}
@media print {
  body { padding: 0; background: #fff; }
  .hero, .section, .card { box-shadow: none; break-inside: avoid; }
}
""".strip()


def _html_table(writer: _Utf8Writer, rows: Iterable[tuple[str, Any]]) -> None:
    writer.append(
        '<div class="table-wrap"><table class="kv-table"><thead><tr>'
        "<th>字段</th><th>值</th></tr>"
        "</thead><tbody>"
    )
    for label, value in rows:
        writer.append(f"<tr><th>{_html(label)}</th><td><pre>{_html(value)}</pre></td></tr>")
    writer.append("</tbody></table></div>")


def _html_data_field(writer: _Utf8Writer, label: str, field: ReportDataField) -> None:
    writer.append(f'<div class="card"><h3>{_html(label)}</h3>')
    _html_table(
        writer,
        [
            ("Availability", field.availability),
            ("Note", field.note),
            ("Value", field.value),
        ],
    )
    writer.append("</div>")


def _html_ai_group(
    writer: _Utf8Writer, label: str, group: ReportAiAuditGroup
) -> None:
    writer.append(f'<div class="card"><h3>{_html(label)}</h3>')
    rows: list[tuple[str, Any]] = [
        ("Total", group.total),
        ("Has More", group.has_more),
        ("受保护应用路径", group.continuation_path),
    ]
    if group.latest is not None:
        rows.extend(
            [
                ("Latest ID", group.latest.id),
                ("Kind", group.latest.kind),
                ("CaseRun ID", group.latest.case_run_id),
                ("Status", group.latest.status),
                ("AI Call ID", group.latest.ai_call_id),
                ("Created At", group.latest.created_at),
                ("公开安全摘要", group.latest.summary),
            ]
        )
    else:
        rows.append(("Latest", None))
    _html_table(writer, rows)
    writer.append("</div>")


def _render_html(snapshot: _ReportExportSnapshot) -> bytes:
    writer = _Utf8Writer()
    writer.append(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{HTML_CSP}">'
        '<meta name="referrer" content="no-referrer">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>AI Test Run Report</title><style>'
    )
    writer.append(_HTML_STYLE)
    writer.append('</style></head><body><main class="page">')
    writer.append('<header class="hero"><h1>AI 原生智能测试编排平台 - Run 报告</h1>')
    writer.append(
        '<p class="muted">只读导出快照。数据来自受保护报告公开模型；'
        "未记录内容不会从当前配置重建。</p>"
    )
    writer.append(
        f'<p>Run ID：<span class="pill">{_html(snapshot.detail.summary.run_id)}</span></p>'
    )
    if snapshot.may_change:
        writer.append(
            '<p class="notice">当前 Run 尚未终态，本文件仅表示生成时刻的快照，'
            "后续数据可能变化。</p>"
        )
    writer.append('</header><section class="section"><h2>Run 摘要与引用</h2>')
    _html_table(writer, _summary_rows(snapshot))
    writer.append('</section><section class="section"><h2>计数与口径</h2>')
    _html_table(writer, _count_rows(snapshot))
    writer.append("</section>")

    writer.append('<section class="section"><h2>运行需求来源快照</h2>')
    for capture in snapshot.detail.requirement_sources.captures:
        writer.append(
            '<article class="card" data-record-kind="requirement-capture"><h3>'
            f"CaseRun {_html(capture.case_run_id)} 捕获状态</h3>"
        )
        _html_table(
            writer,
            [
                ("Status", capture.status),
                ("Captured At", capture.captured_at),
                ("Captured At Time Basis", capture.captured_at_time_basis),
                ("Target Type", capture.target_type),
                ("Target Asset ID", capture.target_asset_id),
                ("Target Version ID", capture.target_version_id),
                ("Source Count", capture.total),
                ("Consistency Basis", capture.consistency_basis),
                ("说明", capture.note),
            ],
        )
        writer.append("</article>")
    writer.append(
        f'<h3>来源明细（完整，共 {len(snapshot.requirement_sources)} 条）</h3>'
        '<div class="table-wrap"><table><thead><tr><th>ID</th><th>CaseRun ID</th>'
        '<th>顺序</th><th>原关联 ID</th><th>需求 ID</th><th>编码</th><th>标题</th>'
        '<th>需求版本</th><th>关联资产类型</th><th>运行目标版本</th><th>关联资产版本</th>'
        '<th>资产版本绑定</th><th>绑定说明</th>'
        '<th>关系</th><th>来源</th><th>置信度</th><th>捕获时刻</th>'
        '</tr></thead><tbody>'
    )
    for source in snapshot.requirement_sources:
        values = (
            source.id,
            source.case_run_id,
            source.sequence_no,
            source.original_link_id,
            source.requirement_id,
            source.requirement_code,
            source.requirement_title,
            {
                "id": source.requirement_version_id,
                "version_no": source.requirement_version_no,
                "content_hash": source.requirement_content_hash,
                "source_type": source.requirement_source_type,
                "binding": source.requirement_version_binding,
            },
            source.link_asset_type,
            source.target_version_id,
            source.link_asset_version_id,
            source.asset_version_binding,
            source.binding_note,
            source.relation_type,
            source.source,
            source.confidence,
            source.captured_at,
        )
        writer.append('<tr data-record-kind="requirement-source">')
        for value in values:
            writer.append(f"<td><pre>{_html(value)}</pre></td>")
        writer.append("</tr>")
    writer.append("</tbody></table></div></section>")

    writer.append(
        f'<section class="section"><h2>Cases（完整，共 {len(snapshot.cases)} 条）</h2>'
    )
    for case in snapshot.cases:
        writer.append(
            '<article class="card" data-record-kind="case"><h3>'
            f"Case {_html(case.sequence_no)} / ID {_html(case.id)}</h3>"
        )
        _html_table(
            writer,
            [
                ("Run ID", case.run_id),
                ("Target", case.target),
                ("Status", case.status),
                ("Duration ms", case.duration_ms),
                ("Retry Count", case.retry_count),
                ("Started At", case.started_at),
                ("Ended At", case.ended_at),
                ("Error Type", case.error_type),
                ("Error Message", case.error_message),
                ("Result Availability", case.execution_result.availability),
                ("Result Outcome", case.execution_result.outcome),
                ("Result Status", case.execution_result.status),
                ("Result Retry Count", case.execution_result.retry_count),
                ("Result Error Type", case.execution_result.error_type),
                ("Result Error Message", case.execution_result.error_message),
                ("Result Completed At", case.execution_result.completed_at),
            ],
        )
        result = case.execution_result
        writer.append('<div class="grid">')
        _html_data_field(writer, "请求配置快照", result.actual_request)
        _html_data_field(writer, "Response", result.response)
        _html_data_field(writer, "Extractions", result.extractions)
        _html_data_field(writer, "Assertions", result.assertions)
        _html_data_field(writer, "Traces", result.traces)
        writer.append("</div></article>")
    writer.append("</section>")

    writer.append(
        f'<section class="section"><h2>Steps（完整，共 {len(snapshot.steps)} 条）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>ID</th><th>CaseRun ID</th><th>顺序</th>'
        '<th>Node ID</th><th>名称</th><th>类型</th><th>状态</th><th>耗时 ms</th>'
        '<th>重试</th><th>开始</th><th>结束</th><th>Error Type</th><th>Error Message</th>'
        '</tr></thead><tbody>'
    )
    for step in snapshot.steps:
        values = (
            step.id,
            step.case_run_id,
            step.sequence_no,
            step.node_id,
            step.name,
            step.type,
            step.status,
            step.duration_ms,
            step.retry_count,
            step.started_at,
            step.ended_at,
            step.error_type,
            step.error_message,
        )
        writer.append('<tr data-record-kind="step">')
        for value in values:
            writer.append(f"<td><pre>{_html(value)}</pre></td>")
        writer.append("</tr>")
    writer.append("</tbody></table></div></section>")

    writer.append(
        f'<section class="section"><h2>Evidence（完整，共 {len(snapshot.evidence)} 条）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>ID</th><th>CaseRun ID</th>'
        '<th>StepRun ID</th><th>类型</th><th>文件名</th><th>MIME</th><th>字节</th>'
        '<th>SHA256</th><th>元数据</th><th>创建时刻</th><th>受保护下载路径</th>'
        '</tr></thead><tbody>'
    )
    for artifact in snapshot.evidence:
        values = (
            artifact.id,
            artifact.case_run_id,
            artifact.step_run_id,
            artifact.artifact_type,
            artifact.file_name,
            artifact.mime,
            artifact.size,
            artifact.sha256,
            artifact.metadata,
            artifact.created_at,
            artifact.download_path,
        )
        writer.append('<tr data-record-kind="evidence">')
        for value in values:
            writer.append(f"<td><pre>{_html(value)}</pre></td>")
        writer.append("</tr>")
    writer.append("</tbody></table></div></section>")

    related = snapshot.detail.related_ai
    writer.append('<section class="section"><h2>已有 AI 审计引用</h2><div class="grid">')
    _html_ai_group(writer, "Web Failure Analyses", related.failure_analyses)
    _html_ai_group(writer, "Web Healing Proposals", related.healing_proposals)
    writer.append("</div>")
    _html_table(writer, [("Raw AI Content Exposed", related.raw_ai_content_exposed)])
    writer.append("</section></main></body></html>")
    return writer.build()


def create_report_export(
    session: Session,
    user: CurrentUser,
    run_id: str,
    export_format: ReportExportFormat,
    *,
    generated_at: datetime | None = None,
) -> ReportExportFile:
    snapshot = _load_snapshot(session, user, run_id, generated_at=generated_at)
    if export_format == "markdown":
        return ReportExportFile(
            content=_render_markdown(snapshot),
            filename=_safe_filename(run_id, "md"),
            media_type="text/markdown; charset=utf-8",
        )
    return ReportExportFile(
        content=_render_html(snapshot),
        filename=_safe_filename(run_id, "html"),
        media_type="text/html; charset=utf-8",
        content_security_policy=HTML_CSP,
    )


def export_response_headers(export: ReportExportFile) -> dict[str, str]:
    headers = {
        "Content-Disposition": f'attachment; filename="{export.filename}"',
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    if export.content_security_policy is not None:
        headers["Content-Security-Policy"] = export.content_security_policy
    return headers
