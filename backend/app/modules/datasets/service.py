import re
from copy import deepcopy
from typing import Any

import pymysql
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.core.time import utc_now_naive
from app.modules.auth.schemas import CurrentUser
from app.modules.database_connections.models import DatabaseConnection
from app.modules.datasets.models import Dataset, DatasetVersion
from app.modules.datasets.parsers import (
    MAX_COLUMNS,
    MAX_ROWS,
    ParsedDataset,
    generate_faker_dataset,
    list_xlsx_sheets,
    parse_csv,
    parse_xlsx,
)
from app.modules.datasets.schemas import (
    CsvDatasetConfig,
    DatasetColumn,
    DatasetCreate,
    DatasetDetailResponse,
    DatasetFakerPreviewRequest,
    DatasetIteration,
    DatasetIterationPreviewRequest,
    DatasetIterationPreviewResponse,
    DatasetListResponse,
    DatasetMySQLPreviewRequest,
    DatasetPreviewResponse,
    DatasetResponse,
    DatasetSourceType,
    DatasetVersionCreate,
    DatasetVersionResponse,
    ExcelDatasetConfig,
    FakerDatasetConfig,
    MySQLDatasetConfig,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.secrets.service import resolve_secret
from app.modules.test_cases.runtime import _validated_sql, resolve_runtime_value

RESERVED_CONTEXT_KEYS = {
    "dataset_id", "dataset_version_id", "row_index", "parameters", "context", "__resources"
}


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower()
            in {"password", "password_secret", "secret", "secret_value", "encrypted_value"}
            or _contains_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    if isinstance(value, tuple):
        return any(_contains_secret_key(item) for item in value)
    return False


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("同一项目中的数据集名称不能重复") from exc


def _dataset(session: Session, dataset_id: int) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ResourceNotFoundError("数据集不存在")
    return dataset


def _dataset_access(
    session: Session, user: CurrentUser, dataset_id: int, *, writable: bool = False
) -> Dataset:
    dataset = _dataset(session, dataset_id)
    project = get_project(session, user, dataset.project_id)
    if writable:
        ensure_project_writable(session, project, user)
        if project.status == ProjectStatus.ARCHIVED.value:
            raise ResourceConflictError("归档项目不能修改数据集")
    return dataset


def _snapshot_rows(snapshot: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    if not snapshot or len(snapshot) > MAX_ROWS:
        raise ResourceConflictError(f"数据集行数必须在 1 到 {MAX_ROWS} 之间")
    rows: list[dict[str, Any]] = []
    columns: list[str] | None = None
    for position, item in enumerate(snapshot, start=1):
        if not isinstance(item, dict):
            raise ResourceConflictError("数据集快照行必须是对象")
        row_index = item.get("row_index", position)
        values = item.get("data", item.get("values", item))
        if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index != position:
            raise ResourceConflictError("数据集 row_index 必须严格连续为 1..N")
        if not isinstance(values, dict):
            raise ResourceConflictError("数据集快照行 data 必须是对象")
        row_columns = [str(key) for key in values]
        if columns is None:
            columns = row_columns
            if not columns:
                raise ResourceConflictError("数据集快照第一行不能没有列")
            if len(columns) > MAX_COLUMNS:
                raise ResourceConflictError(f"数据集列数不能超过 {MAX_COLUMNS} 列")
        elif row_columns != columns:
            raise ResourceConflictError("数据集各行必须使用第一行冻结的列集合和顺序")
        for column in row_columns:
            if not column.strip() or column in RESERVED_CONTEXT_KEYS:
                raise ResourceConflictError(f"数据集列名非法或覆盖保留 Context Key：{column}")
        rows.append({"row_index": position, "data": deepcopy(values)})
    return columns or [], rows


def _column_models(columns: list[str] | list[DatasetColumn]) -> list[DatasetColumn]:
    return [
        item if isinstance(item, DatasetColumn) else DatasetColumn(name=item)
        for item in columns
    ]


def _preview(
    parsed: ParsedDataset,
    source_type: DatasetSourceType,
    config: dict[str, Any],
) -> DatasetPreviewResponse:
    return DatasetPreviewResponse(
        source_type=source_type,
        columns=_column_models(parsed.columns),
        row_count=len(parsed.rows),
        rows=parsed.rows[:100],
        snapshot=parsed.rows,
        source_metadata=parsed.metadata,
        config=config,
    )


def preview_csv(data: bytes, delimiter: str = ",") -> DatasetPreviewResponse:
    return _preview(parse_csv(data, delimiter), DatasetSourceType.CSV, {"delimiter": delimiter})


def preview_xlsx(
    data: bytes, sheet_name: str | None = None, header_row: int = 1
) -> DatasetPreviewResponse:
    return _preview(
        parse_xlsx(data, sheet_name, header_row),
        DatasetSourceType.EXCEL,
        {"sheet_name": sheet_name, "header_row": header_row},
    )


def preview_xlsx_sheet_names(data: bytes) -> list[str]:
    return list_xlsx_sheets(data)


def preview_faker(payload: DatasetFakerPreviewRequest) -> DatasetPreviewResponse:
    parsed = generate_faker_dataset(
        [item.model_dump(mode="json") for item in payload.config.fields],
        payload.config.row_count,
        payload.config.seed,
    )
    return _preview(parsed, DatasetSourceType.FAKER, payload.config.model_dump(mode="json"))


def preview_mysql(
    session: Session, user: CurrentUser, payload: DatasetMySQLPreviewRequest
) -> DatasetPreviewResponse:
    project = get_project(session, user, payload.project_id)
    config = session.get(DatabaseConnection, payload.config.connection_id)
    if config is None or not config.enabled or config.project_id != project.id:
        raise ResourceConflictError("MySQL 连接不存在、已停用或不属于当前项目")
    statement = _validated_sql(payload.config.sql)
    params = resolve_runtime_value(payload.config.params, {})
    password = resolve_secret(session, config.password_secret_id, project.id)
    connection = None
    try:
        connection = pymysql.connect(
            host=config.host, port=config.port, user=config.username, password=password,
            database=config.database_name, charset="utf8mb4", connect_timeout=5,
            read_timeout=5, write_timeout=5, ssl={} if config.ssl_enabled else None,
            autocommit=False, cursorclass=pymysql.cursors.DictCursor,
        )
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            rows_raw = list(cursor.fetchmany(payload.config.max_rows + 1))
            description = cursor.description or []
        truncated = len(rows_raw) > payload.config.max_rows
        rows_raw = rows_raw[: payload.config.max_rows]
    except pymysql.MySQLError as exc:
        raise ResourceConflictError("MySQL 数据集查询失败，请检查只读 SQL 和参数") from exc
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
    columns = [str(item[0]) for item in description]
    if not columns and rows_raw:
        columns = list(rows_raw[0])
    if not columns:
        raise ResourceConflictError("MySQL 查询没有返回列")
    if len(columns) > MAX_COLUMNS or len(columns) != len(set(columns)):
        raise ResourceConflictError("MySQL 返回列为空、重复或超过限制")
    parsed = ParsedDataset(
        columns=columns,
        rows=[
            {"row_index": index, "data": dict(row)}
            for index, row in enumerate(rows_raw, start=1)
        ],
        metadata={
            "connection_id": config.id,
            "database": config.database_name,
            "truncated": truncated,
            "phase3_policy": "saved_preview_snapshot_only",
        },
    )
    # The password was used only in memory and is never included in this config.
    safe_config = payload.config.model_dump(mode="json")
    return _preview(parsed, DatasetSourceType.MYSQL, safe_config)


def _validate_source_config(
    source_type: DatasetSourceType, config: dict[str, Any]
) -> dict[str, Any]:
    schemas = {
        DatasetSourceType.CSV: CsvDatasetConfig,
        DatasetSourceType.EXCEL: ExcelDatasetConfig,
        DatasetSourceType.FAKER: FakerDatasetConfig,
        DatasetSourceType.MYSQL: MySQLDatasetConfig,
    }
    try:
        parsed = schemas[source_type].model_validate(config)
    except ValidationError as exc:
        raise ResourceConflictError("数据集来源配置不合法，不能绕过预览校验") from exc
    if _contains_secret_key(parsed.model_dump(mode="python")):
        raise ResourceConflictError("数据集配置不能包含数据库密码或 Secret 明文")
    if source_type == DatasetSourceType.MYSQL:
        _validated_sql(parsed.sql)
    return parsed.model_dump(mode="json")


_ITERATION_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def validate_iteration_mapping(
    columns: list[str], mapping: dict[str, str], prefix: str = ""
) -> dict[str, str]:
    column_set = set(columns)
    unknown = set(mapping).difference(column_set)
    if unknown:
        raise ResourceConflictError("列映射引用了不存在的数据集列")
    targets: dict[str, str] = {}
    for column in columns:
        target = mapping.get(column, column)
        if not _ITERATION_NAME_RE.fullmatch(target):
            raise ResourceConflictError(f"数据集列映射目标名称非法：{target}")
        if target.startswith("__"):
            raise ResourceConflictError(f"数据集列不能以 __ 开头：{target}")
        full_name = f"{prefix}.{target}" if prefix else target
        if full_name in RESERVED_CONTEXT_KEYS or full_name.startswith("__"):
            raise ResourceConflictError(f"数据集列不能覆盖保留 Context Key：{full_name}")
        if full_name in targets.values():
            raise ResourceConflictError(f"数据集列映射目标重复：{full_name}")
        targets[column] = full_name
    return targets


def create_dataset(
    session: Session, user: CurrentUser, payload: DatasetCreate
) -> DatasetDetailResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建数据集")
    if _contains_secret_key(payload.source_metadata):
        raise ResourceConflictError("数据集来源元数据不能包含数据库密码或 Secret 明文")
    columns, rows = _snapshot_rows(payload.snapshot)
    supplied_columns = [item.name for item in payload.columns]
    if supplied_columns and supplied_columns != columns:
        raise ResourceConflictError("列定义与数据快照不一致")
    safe_config = _validate_source_config(payload.source_type, payload.config)
    if payload.source_type == DatasetSourceType.MYSQL:
        connection = session.get(DatabaseConnection, int(safe_config["connection_id"]))
        if connection is None or connection.project_id != project.id or not connection.enabled:
            raise ResourceConflictError("MySQL 连接不存在、已停用或不属于当前项目")
    dataset = Dataset(
        project_id=project.id, name=payload.name.strip(), status="ACTIVE", created_by=user.id
    )
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        dataset_id=dataset.id, version_no=1, source_type=payload.source_type.value,
        columns=[item.model_dump(mode="json") for item in _column_models(columns)],
        row_count=len(rows), config=safe_config, source_metadata=deepcopy(payload.source_metadata),
        actual_data=_json_safe(rows), created_by=user.id,
    )
    session.add(version)
    session.flush()
    dataset.current_version_id = version.id
    _commit(session)
    session.refresh(dataset)
    session.refresh(version)
    return _detail_response(dataset, version)


def create_dataset_version(
    session: Session,
    user: CurrentUser,
    dataset_id: int,
    payload: DatasetVersionCreate,
) -> DatasetVersionResponse:
    dataset = _dataset_access(session, user, dataset_id, writable=True)
    if dataset.status == "ARCHIVED":
        raise ResourceConflictError("归档数据集不能创建新版本")
    if _contains_secret_key(payload.source_metadata):
        raise ResourceConflictError("数据集来源元数据不能包含数据库密码或 Secret 明文")
    columns, rows = _snapshot_rows(payload.snapshot)
    supplied_columns = [item.name for item in payload.columns]
    if supplied_columns and supplied_columns != columns:
        raise ResourceConflictError("列定义与数据快照不一致")
    safe_config = _validate_source_config(payload.source_type, payload.config)
    if payload.source_type == DatasetSourceType.MYSQL:
        connection = session.get(DatabaseConnection, int(safe_config["connection_id"]))
        if (
            connection is None
            or connection.project_id != dataset.project_id
            or not connection.enabled
        ):
            raise ResourceConflictError("MySQL 连接不存在、已停用或不属于当前项目")
    latest = session.scalar(
        select(DatasetVersion.version_no)
        .where(DatasetVersion.dataset_id == dataset.id)
        .order_by(DatasetVersion.version_no.desc())
        .limit(1)
    )
    version = DatasetVersion(
        dataset_id=dataset.id,
        version_no=(latest or 0) + 1,
        source_type=payload.source_type.value,
        columns=[item.model_dump(mode="json") for item in _column_models(columns)],
        row_count=len(rows),
        config=safe_config,
        source_metadata=deepcopy(payload.source_metadata),
        actual_data=_json_safe(rows),
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    dataset.current_version_id = version.id
    _commit(session)
    session.refresh(version)
    return DatasetVersionResponse.model_validate(version)


def _detail_response(dataset: Dataset, version: DatasetVersion | None) -> DatasetDetailResponse:
    return DatasetDetailResponse(
        **DatasetResponse.model_validate(dataset).model_dump(),
        current_version=DatasetVersionResponse.model_validate(version) if version else None,
    )


def list_datasets(session: Session, user: CurrentUser, project_id: int) -> DatasetListResponse:
    get_project(session, user, project_id)
    items = list(
        session.scalars(
            select(Dataset)
            .where(Dataset.project_id == project_id)
            .order_by(Dataset.id.desc())
        ).all()
    )
    return DatasetListResponse(
        items=[DatasetResponse.model_validate(item) for item in items], total=len(items)
    )


def get_dataset(session: Session, user: CurrentUser, dataset_id: int) -> DatasetDetailResponse:
    dataset = _dataset_access(session, user, dataset_id)
    version = (
        session.get(DatasetVersion, dataset.current_version_id)
        if dataset.current_version_id
        else None
    )
    return _detail_response(dataset, version)


def list_dataset_versions(
    session: Session, user: CurrentUser, dataset_id: int
) -> list[DatasetVersionResponse]:
    dataset = _dataset_access(session, user, dataset_id)
    versions = list(
        session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no.desc())
        ).all()
    )
    return [DatasetVersionResponse.model_validate(item) for item in versions]


def archive_dataset(session: Session, user: CurrentUser, dataset_id: int) -> DatasetResponse:
    dataset = _dataset_access(session, user, dataset_id, writable=True)
    dataset.status = "ARCHIVED"
    dataset.archived_at = utc_now_naive()
    session.commit()
    session.refresh(dataset)
    return DatasetResponse.model_validate(dataset)


def preview_iterations(
    session: Session, user: CurrentUser, payload: DatasetIterationPreviewRequest
) -> DatasetIterationPreviewResponse:
    dataset = _dataset_access(session, user, payload.dataset_id)
    version_id = payload.dataset_version_id or dataset.current_version_id
    version = session.get(DatasetVersion, version_id) if version_id else None
    if version is None or version.dataset_id != dataset.id:
        raise ResourceNotFoundError("数据集版本不存在或不属于该数据集")
    column_names = [str(item["name"]) for item in version.columns]
    validate_iteration_mapping(column_names, payload.column_mapping, payload.prefix)
    if payload.offset >= version.row_count:
        return DatasetIterationPreviewResponse(
            items=[], total=version.row_count, limit=payload.limit, offset=payload.offset
        )
    mapping = payload.column_mapping
    items: list[DatasetIteration] = []
    for row in version.actual_data[payload.offset : payload.offset + payload.limit]:
        row_index = int(row["row_index"])
        values = dict(row["data"])
        parameters = {
            mapping.get(column, column): value for column, value in values.items()
        }
        context = {
            (f"{payload.prefix}.{key}" if payload.prefix else key): value
            for key, value in parameters.items()
        }
        items.append(DatasetIteration(
            dataset_id=dataset.id, dataset_version_id=version.id, row_index=row_index,
            parameters=parameters, context=context,
        ))
    return DatasetIterationPreviewResponse(
        items=items, total=version.row_count, limit=payload.limit, offset=payload.offset
    )
