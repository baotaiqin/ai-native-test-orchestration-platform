import asyncio
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidDocumentError, ResourceConflictError
from app.modules.auth.schemas import CurrentUser
from app.modules.datasets.models import Dataset, DatasetVersion
from app.modules.datasets.parsers import (
    MAX_FILE_BYTES,
    MAX_XLSX_FILE_BYTES,
    generate_faker_dataset,
    parse_csv,
    parse_xlsx,
)
from app.modules.datasets.router import (
    preview_csv_route,
    preview_excel_route,
    preview_excel_sheets_route,
)
from app.modules.datasets.schemas import (
    DatasetCreate,
    DatasetIterationPreviewRequest,
    DatasetMySQLPreviewRequest,
    DatasetSourceType,
    DatasetVersionCreate,
    MySQLDatasetConfig,
)
from app.modules.datasets.service import (
    _snapshot_rows,
    _validate_source_config,
    archive_dataset,
    create_dataset,
    create_dataset_version,
    preview_iterations,
    preview_mysql,
)
from app.modules.projects.models import Project, ProjectMember
from app.modules.test_cases.schemas import CaseDataSourceConfig, CaseType, SuggestedCase
from app.modules.test_cases.service import _validate_data_source

USER = CurrentUser(id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"])


def _xlsx_bytes(sheet_xml: str, shared: str = "") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            '</Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if shared:
            archive.writestr("xl/sharedStrings.xml", shared)
    return output.getvalue()


class _OversizedUpload:
    def __init__(self, filename: str, size: int) -> None:
        self.filename = filename
        self.size = size
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return b"x" * min(self.size, size)


@pytest.mark.parametrize(
    ("route", "filename", "maximum"),
    [
        (preview_csv_route, "data.csv", MAX_FILE_BYTES),
        (preview_excel_route, "data.xlsx", MAX_XLSX_FILE_BYTES),
        (preview_excel_sheets_route, "data.xlsx", MAX_XLSX_FILE_BYTES),
    ],
)
def test_upload_routes_read_only_max_plus_one(
    route: Any, filename: str, maximum: int
) -> None:
    upload = _OversizedUpload(filename, maximum + 1)

    async def invoke() -> Any:
        if route is preview_csv_route:
            return await route(upload, USER, ",")
        if route is preview_excel_route:
            return await route(upload, USER, None, 1)
        return await route(upload, USER)

    with pytest.raises(InvalidDocumentError, match="不能超过"):
        asyncio.run(invoke())
    assert upload.read_sizes == [maximum + 1]


def test_csv_handles_bom_quotes_newlines_and_delimiter() -> None:
    raw = b"\xef\xbb\xbfusername;note\r\nalice;\"line 1\r\nline 2\"\r\n"
    parsed = parse_csv(raw, ";")
    assert parsed.columns == ["username", "note"]
    assert parsed.rows[0] == {
        "row_index": 1,
        "data": {"username": "alice", "note": "line 1\r\nline 2"},
    }


@pytest.mark.parametrize(
    "raw, message",
    [
        (b",b\n1,2\n", "表头"),
        (b"a,a\n1,2\n", "重复"),
        (b"a,b\n1\n", "列数"),
    ],
)
def test_csv_rejects_invalid_headers_and_shape(raw: bytes, message: str) -> None:
    with pytest.raises(InvalidDocumentError, match=message):
        parse_csv(raw)


def test_csv_enforces_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.datasets.parsers.MAX_ROWS", 2)
    with pytest.raises(InvalidDocumentError, match="2"):
        parse_csv(b"a\n1\n2\n3\n")


def test_snapshot_freezes_first_row_columns_and_requires_contiguous_indices() -> None:
    with pytest.raises(ResourceConflictError, match="冻结"):
        _snapshot_rows(
            [
                {"row_index": 1, "data": {"a": 1}},
                {"row_index": 2, "data": {"a": 2, "b": 3}},
            ]
        )
    with pytest.raises(ResourceConflictError, match="严格连续"):
        _snapshot_rows(
            [
                {"row_index": 1, "data": {"a": 1}},
                {"row_index": 1, "data": {"a": 2}},
            ]
        )
    with pytest.raises(ResourceConflictError, match="严格连续"):
        _snapshot_rows(
            [
                {"row_index": 1, "data": {"a": 1}},
                {"row_index": 3, "data": {"a": 2}},
            ]
        )


def test_xlsx_supports_shared_inline_numeric_boolean_and_empty_cells() -> None:
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="2" uniqueCount="2">'
        '<si><t>name</t></si><si><t>alice</t></si></sst>'
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>score</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>enabled</t></is></c>'
        '<c r="D1" t="inlineStr"><is><t>empty</t></is></c></row>'
        '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>42</v></c>'
        '<c r="C2" t="b"><v>1</v></c><c r="D2"/></row>'
        '</sheetData></worksheet>'
    )
    parsed = parse_xlsx(_xlsx_bytes(sheet, shared), "Data", 1)
    assert parsed.columns == ["name", "score", "enabled", "empty"]
    assert parsed.rows[0]["data"] == {"name": "alice", "score": 42, "enabled": True, "empty": None}


def test_xlsx_rejects_uncached_formula_and_zip_path() -> None:
    formula = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>value</t></is></c></row>'
        '<row r="2"><c r="A2"><f>1+1</f></c></row></sheetData></worksheet>'
    )
    with pytest.raises(InvalidDocumentError, match="公式"):
        parse_xlsx(_xlsx_bytes(formula), "Data", 1)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    with pytest.raises(InvalidDocumentError):
        parse_xlsx(output.getvalue())


@pytest.mark.parametrize(
    "sheet, shared, message",
    [
        (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="x"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
            '</sheetData></worksheet>',
            "",
            "行号",
        ),
        (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A0" t="inlineStr"><is><t>a</t></is></c></row>'
            '</sheetData></worksheet>',
            "",
            "单元格引用",
        ),
        (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
            '<row r="2"><c r="A2"><v>1</v></c><c r="A2"><v>2</v></c></row>'
            '</sheetData></worksheet>',
            "",
            "重复",
        ),
        (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v/></c></row></sheetData></worksheet>',
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si/></sst>',
            "shared string",
        ),
    ],
)
def test_xlsx_rejects_malformed_rows_cells_and_shared_values(
    sheet: str, shared: str, message: str
) -> None:
    with pytest.raises(InvalidDocumentError, match=message):
        parse_xlsx(_xlsx_bytes(sheet, shared), "Data", 1)


def test_xlsx_skips_fully_blank_rows() -> None:
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>name</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>alice</t></is></c></row>'
        '<row r="3"><c r="A3"/></row>'
        '<row r="5"><c r="A5" t="inlineStr"><is><t>bob</t></is></c></row>'
        '</sheetData></worksheet>'
    )
    parsed = parse_xlsx(_xlsx_bytes(sheet), "Data", 1)
    assert [row["row_index"] for row in parsed.rows] == [1, 2]
    assert [row["data"]["name"] for row in parsed.rows] == ["alice", "bob"]


def test_xlsx_enforces_zip_path_compression_and_uncompressed_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_zip = io.BytesIO()
    with zipfile.ZipFile(path_zip, "w") as archive:
        archive.writestr("../../outside.xml", "x")
    with pytest.raises(InvalidDocumentError, match="路径"):
        parse_xlsx(path_zip.getvalue())

    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/large.bin", b"0" * 2_000_000)
    with pytest.raises(InvalidDocumentError, match="压缩比"):
        parse_xlsx(compressed.getvalue())

    monkeypatch.setattr("app.modules.datasets.parsers.MAX_UNCOMPRESSED_XLSX_BYTES", 10)
    with pytest.raises(InvalidDocumentError, match="解压后"):
        parse_xlsx(_xlsx_bytes("<worksheet/>"))


def test_xlsx_enforces_sheet_row_column_and_cell_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.modules.datasets.parsers.MAX_XLSX_ENTRIES", 2)
    with pytest.raises(InvalidDocumentError, match="条目"):
        parse_xlsx(_xlsx_bytes("<worksheet/>"))

    monkeypatch.setattr("app.modules.datasets.parsers.MAX_XLSX_ENTRIES", 1000)
    monkeypatch.setattr("app.modules.datasets.parsers.MAX_ROWS", 2)
    too_many_rows = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>name</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>b</t></is></c></row>'
        '<row r="4"><c r="A4" t="inlineStr"><is><t>c</t></is></c></row>'
        '</sheetData></worksheet>'
    )
    with pytest.raises(InvalidDocumentError, match="行数"):
        parse_xlsx(_xlsx_bytes(too_many_rows))

    monkeypatch.setattr("app.modules.datasets.parsers.MAX_ROWS", 10_000)
    monkeypatch.setattr("app.modules.datasets.parsers.MAX_COLUMNS", 1)
    too_many_columns = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>b</t></is></c></row>'
        '</sheetData></worksheet>'
    )
    with pytest.raises(InvalidDocumentError, match="列数"):
        parse_xlsx(_xlsx_bytes(too_many_columns))

    monkeypatch.setattr("app.modules.datasets.parsers.MAX_COLUMNS", 100)
    monkeypatch.setattr("app.modules.datasets.parsers.MAX_CELL_LENGTH", 2)
    long_cell = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>abc</t></is></c></row>'
        '</sheetData></worksheet>'
    )
    with pytest.raises(InvalidDocumentError, match="单元格"):
        parse_xlsx(_xlsx_bytes(long_cell))


def test_faker_seed_is_repeatable_and_generator_is_strict() -> None:
    fields = [{"name": "email", "generator": "email"}, {"name": "active", "generator": "boolean"}]
    assert generate_faker_dataset(fields, 3, 99).rows == generate_faker_dataset(fields, 3, 99).rows
    with pytest.raises(ResourceConflictError, match="白名单"):
        generate_faker_dataset([{"name": "x", "generator": "password"}], 1, 1)


def test_iteration_mapping_schema_rejects_empty_invalid_and_duplicate_targets() -> None:
    for mapping in (
        {"a": ""},
        {"a": "not valid"},
        {"a": "__private"},
        {"a": "x", "b": "x"},
    ):
        with pytest.raises(ValueError):
            DatasetIterationPreviewRequest(dataset_id=1, column_mapping=mapping)
    for mapping in (
        {"a": ""},
        {"a": "not valid"},
        {"a": "__private"},
        {"a": "x", "b": "x"},
    ):
        with pytest.raises(ValueError):
            CaseDataSourceConfig(dataset_id=1, column_mapping=mapping)


def test_source_config_is_strict_and_normalized_before_persistence() -> None:
    with pytest.raises(ResourceConflictError):
        _validate_source_config(DatasetSourceType.CSV, {"delimiter": ",", "extra": True})
    with pytest.raises(ResourceConflictError):
        _validate_source_config(DatasetSourceType.EXCEL, {"header_row": 1, "extra": True})
    with pytest.raises(ResourceConflictError):
        _validate_source_config(
            DatasetSourceType.FAKER,
            {"fields": [{"name": "x", "generator": "email"}], "row_count": 1, "evil": 1},
        )
    with pytest.raises(ResourceConflictError, match="只读"):
        _validate_source_config(
            DatasetSourceType.MYSQL,
            {"connection_id": 1, "sql": "UPDATE users SET name='x'"},
        )

    session = _session()
    try:
        malicious_configs = [
            (DatasetSourceType.CSV, {"delimiter": ",", "extra": True}),
            (
                DatasetSourceType.FAKER,
                {
                    "fields": [{"name": "x", "generator": "email"}],
                    "row_count": 1,
                    "seed": 1,
                    "extra": True,
                },
            ),
            (DatasetSourceType.MYSQL, {"connection_id": 1, "sql": "DROP TABLE users"}),
        ]
        for index, (source_type, config) in enumerate(malicious_configs, start=1):
            with pytest.raises(ResourceConflictError):
                create_dataset(
                    session,
                    USER,
                    DatasetCreate(
                        project_id=1,
                        name=f"malicious-{index}",
                        source_type=source_type,
                        config=config,
                        snapshot=[{"row_index": 1, "data": {"x": "value"}}],
                    ),
                )
        stored = create_dataset(
            session,
            USER,
            DatasetCreate(
                project_id=1,
                name="normalized-faker",
                source_type=DatasetSourceType.FAKER,
                config={
                    "fields": [{"name": "x", "generator": "email"}],
                    "row_count": 1,
                    "seed": 1,
                },
                snapshot=[{"row_index": 1, "data": {"x": "value"}}],
            ),
        )
        assert stored.current_version is not None
        assert stored.current_version.config == {
            "fields": [{"name": "x", "generator": "email"}],
            "row_count": 1,
            "seed": 1,
        }
        with pytest.raises(ResourceConflictError):
            create_dataset_version(
                session,
                USER,
                stored.id,
                DatasetVersionCreate(
                    source_type=DatasetSourceType.FAKER,
                    config={
                        "fields": [{"name": "x", "generator": "email"}],
                        "row_count": 1,
                        "evil": True,
                    },
                    snapshot=[{"row_index": 1, "data": {"x": "value"}}],
                ),
            )
    finally:
        session.close()


def test_source_metadata_secret_keys_are_rejected_recursively() -> None:
    session = _session()
    try:
        with pytest.raises(ResourceConflictError, match="元数据"):
            create_dataset(
                session,
                USER,
                DatasetCreate(
                    project_id=1,
                    name="secret-source",
                    source_type=DatasetSourceType.CSV,
                    source_metadata={"nested": [{"Password": "never"}]},
                    snapshot=[{"row_index": 1, "data": {"a": "b"}}],
                ),
            )
    finally:
        session.close()


class _MySQLCursor:
    description = [("id",), ("name",)]

    def __enter__(self) -> "_MySQLCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: Any) -> None:
        self.executed = (statement, params)

    def fetchmany(self, size: int) -> list[dict[str, Any]]:
        assert size == 3
        return [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]


class _MySQLConnection:
    def __init__(self) -> None:
        self.cursor_instance = _MySQLCursor()
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _MySQLCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _ConnectionSession:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get(self, _model: Any, _identifier: int) -> Any:
        return self.connection


def test_mysql_dataset_uses_bound_params_and_project_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_config = SimpleNamespace(
        id=9, project_id=1, enabled=True, host="localhost", port=3306,
        username="tester", password_secret_id=3, database_name="testing", ssl_enabled=False,
    )
    database_connection = _MySQLConnection()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.modules.datasets.service.get_project", lambda *_: SimpleNamespace(id=1)
    )
    monkeypatch.setattr("app.modules.datasets.service.resolve_secret", lambda *_: "not-returned")

    def connect(**kwargs: Any) -> _MySQLConnection:
        captured.update(kwargs)
        return database_connection

    monkeypatch.setattr("app.modules.datasets.service.pymysql.connect", connect)
    result = preview_mysql(
        _ConnectionSession(connection_config),
        USER,
        DatasetMySQLPreviewRequest(
            project_id=1,
            config=MySQLDatasetConfig(
                connection_id=9,
                sql="SELECT id, name FROM users WHERE id = %(id)s",
                params={"id": 7},
                max_rows=2,
            ),
        ),
    )
    assert result.row_count == 2
    assert database_connection.cursor_instance.executed == (
        "SELECT id, name FROM users WHERE id = %(id)s", {"id": 7}
    )
    assert database_connection.rolled_back is True
    assert database_connection.closed is True
    assert captured["password"] == "not-returned"
    connection_config.project_id = 2
    with pytest.raises(ResourceConflictError, match="项目"):
        preview_mysql(
            _ConnectionSession(connection_config), USER,
            DatasetMySQLPreviewRequest(
                project_id=1,
                config=MySQLDatasetConfig(connection_id=9, sql="SELECT 1"),
            ),
        )


def _session() -> Session:
    engine = create_engine("sqlite://")
    Project.__table__.create(engine)
    ProjectMember.__table__.create(engine)
    Dataset.__table__.create(engine)
    DatasetVersion.__table__.create(engine)
    session = Session(engine)
    session.add(Project(id=1, name="One", code="ONE", owner_id="dev-admin"))
    session.add(ProjectMember(project_id=1, user_id="dev-admin", role="PROJECT_OWNER"))
    session.commit()
    return session


def _create(session: Session):
    return create_dataset(
        session,
        USER,
        DatasetCreate(
            project_id=1,
            name="orders",
            source_type=DatasetSourceType.CSV,
            config={"delimiter": ","},
            snapshot=[
                {"row_index": 1, "data": {"username": "alice", "expected": "ok"}},
                {"row_index": 2, "data": {"username": "bob", "expected": "fail"}},
                {"row_index": 3, "data": {"username": "carol", "expected": "fail"}},
            ],
        ),
    )


def test_dataset_version_is_append_only_and_iteration_supports_offset_limit() -> None:
    session = _session()
    try:
        detail = _create(session)
        version_one = detail.current_version
        assert version_one is not None
        created = create_dataset_version(
            session,
            USER,
            detail.id,
            DatasetVersionCreate(
                source_type=DatasetSourceType.FAKER,
                config={
                    "fields": [{"name": "username", "generator": "username"}],
                    "row_count": 1,
                    "seed": 7,
                },
                snapshot=[{"row_index": 1, "data": {"username": "new"}}],
            ),
        )
        assert created.version_no == 2
        old = session.get(DatasetVersion, version_one.id)
        assert old is not None and old.row_count == 3
        result = preview_iterations(
            session,
            USER,
            DatasetIterationPreviewRequest(
                dataset_id=detail.id,
                dataset_version_id=version_one.id,
                prefix="data",
                limit=1,
                offset=1,
            ),
        )
        assert result.total == 3
        assert result.items[0].row_index == 2
        assert result.items[0].context == {"data.username": "bob", "data.expected": "fail"}
        with pytest.raises(ResourceConflictError, match="保留"):
            preview_iterations(
                session,
                USER,
                DatasetIterationPreviewRequest(
                    dataset_id=detail.id, dataset_version_id=version_one.id,
                    column_mapping={"username": "row_index"},
                ),
            )
        with pytest.raises(ResourceConflictError, match="重复"):
            preview_iterations(
                session,
                USER,
                DatasetIterationPreviewRequest(
                    dataset_id=detail.id,
                    dataset_version_id=version_one.id,
                    column_mapping={"username": "expected"},
                ),
            )
    finally:
        session.close()


def test_archived_dataset_cannot_create_new_version() -> None:
    session = _session()
    try:
        detail = _create(session)
        archive_dataset(session, USER, detail.id)
        with pytest.raises(ResourceConflictError, match="归档数据集"):
            create_dataset_version(
                session,
                USER,
                detail.id,
                DatasetVersionCreate(
                    source_type=DatasetSourceType.CSV,
                    config={"delimiter": ","},
                    snapshot=[{"row_index": 1, "data": {"a": "b"}}],
                ),
            )
    finally:
        session.close()


def test_case_dataset_binding_resolves_current_version_and_validates_mapping() -> None:
    dataset = SimpleNamespace(
        id=10, project_id=1, status="ACTIVE", current_version_id=20
    )
    version = SimpleNamespace(
        id=20, dataset_id=10, columns=[{"name": "a"}, {"name": "b"}]
    )

    class _Session:
        def get(self, model: Any, identifier: int) -> Any:
            if model is Dataset:
                return dataset if identifier == dataset.id else None
            return version if identifier == version.id else None

    content = SimpleNamespace(
        case_type=SimpleNamespace(value="API"),
        data_source=CaseDataSourceConfig(dataset_id=10, column_mapping={"a": "x"}),
    )
    _validate_data_source(_Session(), 1, content)
    assert content.data_source.dataset_version_id == version.id
    content.data_source = CaseDataSourceConfig(dataset_id=10, column_mapping={"a": "b"})
    with pytest.raises(ResourceConflictError, match="重复"):
        _validate_data_source(_Session(), 1, content)
    dataset.status = "ARCHIVED"
    with pytest.raises(ResourceConflictError, match="归档"):
        _validate_data_source(_Session(), 1, content)


def test_old_case_version_without_data_source_remains_compatible() -> None:
    case = SuggestedCase(
        title="legacy",
        case_type=CaseType.API,
        priority="P2",
        steps=[{"order": 1, "action": "GET /health", "expected": "200"}],
        expected_result="成功",
    )
    assert case.data_source is None


def test_dataset_migration_declares_current_version_fk_and_downgrade_order() -> None:
    migration = Path(__file__).parents[1] / "migrations" / "versions" / "20260821_0014_datasets.py"
    source = migration.read_text(encoding="utf-8")
    versions_table = source.index('op.create_table(\n        "dataset_versions"')
    create_fk = source.index("op.create_foreign_key(")
    drop_constraint = source.index("op.drop_constraint(")
    drop_versions = source.index('op.drop_index(op.f("ix_dataset_versions_dataset_id")')
    assert versions_table < create_fk
    assert '"fk_datasets_current_version_id_dataset_versions"' in source
    assert 'ondelete="SET NULL"' in source
    assert drop_constraint < drop_versions
