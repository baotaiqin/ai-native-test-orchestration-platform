"""Bounded, dependency-free parsers used by the Dataset workbench."""

import csv
import io
import posixpath
import re
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from random import Random
from typing import Any, BinaryIO
from xml.etree import ElementTree as ET

from app.core.exceptions import InvalidDocumentError, ResourceConflictError
from app.modules.test_cases.runtime import _faker_value
from app.modules.test_cases.schemas import FakerAction, FakerGenerator

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_XLSX_FILE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_XLSX_BYTES = 100 * 1024 * 1024
MAX_XLSX_ENTRIES = 1000
MAX_COLUMNS = 100
MAX_ROWS = 10_000
MAX_CELL_LENGTH = 4_000
MAX_PREVIEW_ROWS = 100
ALLOWED_DELIMITERS = {",", ";", "\t"}
_HEADER_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")


@dataclass(frozen=True)
class ParsedDataset:
    columns: list[str]
    rows: list[dict[str, Any]]
    metadata: dict[str, Any]


def _error(message: str) -> InvalidDocumentError:
    return InvalidDocumentError(message)


def _validate_header(header: list[str]) -> list[str]:
    if not header or not any(cell.strip() for cell in header):
        raise _error("数据集表头不能为空")
    if len(header) > MAX_COLUMNS:
        raise _error(f"数据集列数不能超过 {MAX_COLUMNS} 列")
    normalized: list[str] = []
    for cell in header:
        value = str(cell).strip()
        if not value or not _HEADER_RE.match(value):
            raise _error("数据集表头不能包含空值或控制字符")
        if len(value) > 255:
            raise _error("数据集表头长度不能超过 255 个字符")
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise _error("数据集表头不能重复")
    return normalized


def _validate_cell(value: Any, row_index: int, column: str) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and len(value) > MAX_CELL_LENGTH:
        raise _error(f"第 {row_index} 行、列 {column} 的单元格超过 {MAX_CELL_LENGTH} 字符")
    return value


def _rows_from_matrix(header: list[str], matrix: list[list[Any]]) -> list[dict[str, Any]]:
    columns = _validate_header(header)
    if len(matrix) > MAX_ROWS:
        raise _error(f"数据集行数不能超过 {MAX_ROWS} 行")
    rows: list[dict[str, Any]] = []
    for row_index, raw_row in enumerate(matrix, start=1):
        if len(raw_row) != len(columns):
            raise _error(f"第 {row_index} 行列数不一致，应为 {len(columns)} 列")
        values = {
            column: _validate_cell(raw_row[column_index], row_index, column)
            for column_index, column in enumerate(columns)
        }
        rows.append({"row_index": row_index, "data": values})
    return rows


def parse_csv(data: bytes | bytearray | BinaryIO, delimiter: str = ",") -> ParsedDataset:
    """Parse UTF-8 CSV with strict shape and resource limits."""
    if delimiter not in ALLOWED_DELIMITERS:
        raise _error("CSV 分隔符仅支持逗号、分号或制表符")
    raw = data.read() if hasattr(data, "read") else bytes(data)
    if not raw:
        raise _error("CSV 文件不能为空")
    if len(raw) > MAX_FILE_BYTES:
        raise _error(f"CSV 文件不能超过 {MAX_FILE_BYTES // 1024 // 1024} MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _error("CSV 必须使用 UTF-8 或 UTF-8 BOM 编码") from exc
    if not text.strip():
        raise _error("CSV 文件不能为空")
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter=delimiter, strict=True
        )
        header = next(reader)
        matrix: list[list[str]] = []
        for row in reader:
            if len(matrix) >= MAX_ROWS:
                raise _error(f"数据集行数不能超过 {MAX_ROWS} 行")
            matrix.append(row)
    except csv.Error as exc:
        raise _error(f"CSV 格式无效：{exc}") from exc
    rows = _rows_from_matrix(header, matrix)
    return ParsedDataset(
        columns=_validate_header(header), rows=rows, metadata={"encoding": "UTF-8"}
    )


_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "pkg": "http://schemas.openxmlformats.org/package/2006/relationships"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _safe_xlsx_zip(raw: bytes) -> zipfile.ZipFile:
    if not raw:
        raise _error("XLSX 文件不能为空")
    if len(raw) > MAX_XLSX_FILE_BYTES:
        raise _error("XLSX 文件超过 20 MB 限制")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise _error("XLSX 文件不是有效的 ZIP 文档") from exc
    try:
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        archive.close()
        raise _error("XLSX ZIP 目录无效") from exc
    if len(infos) > MAX_XLSX_ENTRIES:
        archive.close()
        raise _error("XLSX ZIP 条目数量超过限制")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or name.startswith("\\") or posixpath.isabs(name):
            archive.close()
            raise _error("XLSX 包含异常路径")
        parts = [part for part in name.split("/") if part]
        if ".." in parts or info.file_size < 0:
            archive.close()
            raise _error("XLSX 包含异常路径或大小")
        total += info.file_size
        if total > MAX_UNCOMPRESSED_XLSX_BYTES:
            archive.close()
            raise _error("XLSX 解压后大小超过限制")
        if info.compress_size and info.file_size > info.compress_size * 100:
            archive.close()
            raise _error("XLSX 压缩比异常，疑似 ZIP bomb")
    return archive


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(name))
    except KeyError as exc:
        raise _error(f"XLSX 缺少必要文件：{name}") from exc
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError) as exc:
        raise _error(f"XLSX ZIP 内容无效：{name}") from exc
    except ET.ParseError as exc:
        raise _error(f"XLSX XML 无效：{name}") from exc


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml(archive, "xl/sharedStrings.xml")
    values: list[str] = []
    for item in root.findall("main:si", _NS):
        text_nodes = list(item.iter(f"{{{_NS['main']}}}t"))
        if not text_nodes:
            raise _error("XLSX shared string 缺少 value")
        values.append("".join(node.text or "" for node in text_nodes))
        if len(values) > MAX_ROWS * MAX_COLUMNS:
            raise _error("XLSX shared strings 数量超过限制")
    return values


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = _xml(archive, "xl/workbook.xml")
    rels = _xml(archive, "xl/_rels/workbook.xml.rels")
    relation_map = {
        rel.attrib.get("Id"): rel.attrib.get("Target", "")
        for rel in rels.findall("pkg:Relationship", _NS)
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall("main:sheets/main:sheet", _NS):
        name = sheet.attrib.get("name", "")
        relation_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
        target = relation_map.get(relation_id, "")
        if ".." in target.split("/"):
            raise _error("XLSX 工作表路径异常")
        if target.startswith("/"):
            if not target.startswith("/xl/"):
                raise _error("XLSX 工作表路径异常")
            target = target.lstrip("/")
        else:
            target = posixpath.normpath(posixpath.join("xl", target))
        if target not in archive.namelist():
            raise _error(f"XLSX 工作表文件不存在：{name}")
        result.append((name, target))
    if not result:
        raise _error("XLSX 没有可用工作表")
    if len(result) > 100:
        raise _error("XLSX 工作表数量超过限制")
    return result


def list_xlsx_sheets(data: bytes | bytearray | BinaryIO) -> list[str]:
    raw = data.read() if hasattr(data, "read") else bytes(data)
    archive = _safe_xlsx_zip(raw)
    try:
        return [name for name, _ in _sheet_targets(archive)]
    finally:
        archive.close()


def _column_number(reference: str) -> int:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", reference or "")
    if not match:
        raise _error(f"XLSX 单元格引用无效：{reference}")
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    return value


def _xlsx_cell(cell: ET.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    formula = cell.find("main:f", _NS)
    value_node = cell.find("main:v", _NS)
    inline = cell.find("main:is", _NS)
    if formula is not None and value_node is None:
        raise _error("XLSX 包含没有缓存值的公式，已拒绝执行")
    if cell_type == "inlineStr":
        if inline is None:
            raise _error("XLSX inline string 缺少 value")
        value = "".join(
            node.text or "" for node in inline.iter(f"{{{_NS['main']}}}t")
        )
    elif cell_type == "s":
        try:
            if value_node is None or value_node.text is None:
                raise _error("XLSX shared string 缺少 value")
            value = shared[int(value_node.text)]
        except (IndexError, TypeError, ValueError) as exc:
            raise _error("XLSX shared string 引用无效") from exc
    elif value_node is None:
        return None
    elif cell_type == "b":
        value = (value_node.text or "0") == "1"
    else:
        value = value_node.text or ""
        if cell_type is None:
            with suppress(ValueError):
                value = float(value) if "." in value else int(value)
    if isinstance(value, str) and len(value) > MAX_CELL_LENGTH:
        raise _error(f"XLSX 单元格超过 {MAX_CELL_LENGTH} 字符")
    return value


def parse_xlsx(
    data: bytes | bytearray | BinaryIO,
    sheet_name: str | None = None,
    header_row: int = 1,
) -> ParsedDataset:
    if header_row < 1 or header_row > MAX_ROWS:
        raise _error("XLSX 表头行必须在 1 到 10000 之间")
    raw = data.read() if hasattr(data, "read") else bytes(data)
    archive = _safe_xlsx_zip(raw)
    try:
        sheets = _sheet_targets(archive)
        selected = next((item for item in sheets if item[0] == sheet_name), sheets[0])
        if sheet_name is not None and selected[0] != sheet_name:
            raise _error(f"XLSX 工作表不存在：{sheet_name}")
        shared = _shared_strings(archive)
        root = _xml(archive, selected[1])
        rows_by_number: dict[int, dict[int, Any]] = {}
        max_row = 0
        max_col = 0
        for row in root.findall(".//main:sheetData/main:row", _NS):
            row_reference = row.attrib.get("r", "")
            if not re.fullmatch(r"[1-9][0-9]*", row_reference):
                raise _error(f"XLSX 行号无效：{row_reference}")
            row_number = int(row_reference)
            if row_number < 1 or row_number > MAX_ROWS + header_row:
                raise _error("XLSX 行数超过限制")
            if row_number in rows_by_number:
                raise _error(f"XLSX 行号重复：{row_number}")
            values: dict[int, Any] = {}
            for cell in row.findall("main:c", _NS):
                cell_reference = cell.attrib.get("r", "")
                reference_match = re.fullmatch(
                    r"([A-Z]{1,3})([1-9][0-9]*)", cell_reference
                )
                if reference_match is None or int(reference_match.group(2)) != row_number:
                    raise _error(f"XLSX 单元格引用无效：{cell_reference}")
                col_number = _column_number(cell_reference)
                if col_number > MAX_COLUMNS:
                    raise _error(f"XLSX 列数不能超过 {MAX_COLUMNS} 列")
                if col_number in values:
                    raise _error(f"XLSX 行 {row_number} 包含重复单元格列")
                values[col_number] = _xlsx_cell(cell, shared)
                max_col = max(max_col, col_number)
            rows_by_number[row_number] = values
            max_row = max(max_row, row_number)
        raw_header = [
            rows_by_number.get(header_row, {}).get(index)
            for index in range(1, max_col + 1)
        ]
        header = _validate_header(["" if value is None else str(value) for value in raw_header])
        matrix = []
        for number in range(header_row + 1, max_row + 1):
            values = rows_by_number.get(number, {})
            row_values = [values.get(index) for index in range(1, len(header) + 1)]
            if all(value is None or value == "" for value in row_values):
                continue
            matrix.append(row_values)
        rows = _rows_from_matrix(header, matrix)
        return ParsedDataset(
            columns=header,
            rows=rows,
            metadata={
                "sheet_name": selected[0],
                "header_row": header_row,
                "formula_policy": "cached_value_only",
            },
        )
    finally:
        archive.close()


def generate_faker_dataset(
    fields: list[dict[str, Any]], row_count: int, seed: int | None = None
) -> ParsedDataset:
    if not 1 <= row_count <= MAX_ROWS:
        raise ResourceConflictError(f"Faker 行数必须在 1 到 {MAX_ROWS} 之间")
    if not fields or len(fields) > MAX_COLUMNS:
        raise ResourceConflictError(f"Faker 字段数必须在 1 到 {MAX_COLUMNS} 之间")
    columns = _validate_header([str(item.get("name", "")) for item in fields])
    validated: list[tuple[str, FakerGenerator]] = []
    for item, column in zip(fields, columns, strict=True):
        try:
            generator = FakerGenerator(str(item.get("generator", "")))
        except ValueError as exc:
            raise ResourceConflictError(
                f"Faker 生成器不在白名单中：{item.get('generator')}"
            ) from exc
        validated.append((column, generator))
    # Derive a stable per-cell seed while retaining the exact existing Faker
    # whitelist semantics.  It avoids shared mutable RNG state across requests.
    base_seed = 0 if seed is None else seed
    rows: list[dict[str, Any]] = []
    for row_index in range(1, row_count + 1):
        values: dict[str, Any] = {}
        for column, generator in validated:
            cell_seed = Random(f"{base_seed}:{row_index}:{column}").randint(-(2**63), 2**63 - 1)
            values[column] = _faker_value(
                FakerAction(name=column, generator=generator, seed=cell_seed)
            )
        rows.append({"row_index": row_index, "data": values})
    return ParsedDataset(
        columns=columns,
        rows=rows,
        metadata={
            "seed": seed,
            "field_count": len(columns),
            "generator_policy": "existing_whitelist",
        },
    )
