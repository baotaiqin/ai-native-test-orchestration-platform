"""有界 Web Evidence 文件、manifest 与安全审计。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from runner.errors import ProtocolError

MAX_EVIDENCE_COUNT = 64
MAX_EVIDENCE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_NAME_LENGTH = 128
ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

EVIDENCE_SPECS: dict[str, tuple[str, int, bytes | None]] = {
    "SCREENSHOT": ("image/png", 5 * 1024 * 1024, b"\x89PNG\r\n\x1a\n"),
    "PLAYWRIGHT_TRACE": ("application/zip", 20 * 1024 * 1024, b"PK"),
    "CONSOLE_ERROR": ("application/json", 2 * 1024 * 1024, None),
    "NETWORK_ERROR": ("application/json", 2 * 1024 * 1024, None),
    "WEB_SUMMARY": ("application/json", 2 * 1024 * 1024, None),
}
EVIDENCE_PREFIXES = {
    "SCREENSHOT": "screenshot",
    "PLAYWRIGHT_TRACE": "trace",
    "CONSOLE_ERROR": "console-error",
    "NETWORK_ERROR": "network-error",
    "WEB_SUMMARY": "summary",
}
ARTIFACT_EXTENSIONS = {
    "SCREENSHOT": ".png",
    "PLAYWRIGHT_TRACE": ".zip",
    "CONSOLE_ERROR": ".json",
    "NETWORK_ERROR": ".json",
    "WEB_SUMMARY": ".json",
}
METADATA_KEYS = {
    "SCREENSHOT": {"width", "height", "title", "page_url"},
    "PLAYWRIGHT_TRACE": {"browser", "page_count", "duration_ms"},
    "CONSOLE_ERROR": {"level", "message", "source", "line", "column", "count", "timestamp"},
    "NETWORK_ERROR": {
        "method",
        "status",
        "category",
        "url",
        "message",
        "duration_ms",
        "timestamp",
    },
    "WEB_SUMMARY": {
        "title",
        "final_url",
        "page_count",
        "duration_ms",
        "error_count",
        "status",
    },
}
_TRACE_SECRET_MARKER = re.compile(
    rb"(?i)(cookie|storage[_-]?state|authorization|bearer|password|token|secret|credential|"
    rb"set-cookie|request[_-]?body|response[_-]?body|dom[_-]?content|page\.content)"
)
_TRACE_URL_WITH_QUERY = re.compile(rb"(?i)https?://[^\s\"'<>]*[?#][^\s\"'<>]*")
_TRACE_URL_WITH_QUERY_TEXT = re.compile(r"(?i)https?://[^\s\"'<>]*[?#][^\s\"'<>]*")
_UNSAFE_JSON_TEXT = re.compile(
    r"(?i)(?:bearer\s+(?!\[REDACTED\])\S+|"
    r"(?:authorization|cookie|token|password|secret|credential|api[_-]?key)"
    r"\s*[:=]\s*(?!\[REDACTED\])\S+)"
)


class EvidenceValidationError(ProtocolError):
    """本地 Evidence 无法安全验证。"""


@dataclass(frozen=True, repr=False)
class EvidenceManifestItem:
    artifact_type: str
    relative_path: str
    metadata: Mapping[str, Any]

    def __repr__(self) -> str:
        return (
            "EvidenceManifestItem("
            f"artifact_type={self.artifact_type!r}, relative_path='[REDACTED]', "
            f"metadata_keys={len(self.metadata)})"
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "relative_path": self.relative_path,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, repr=False)
class WebEvidenceManifest:
    items: tuple[EvidenceManifestItem, ...] = ()

    def __repr__(self) -> str:
        return f"WebEvidenceManifest(items={len(self.items)})"

    def to_wire(self) -> list[dict[str, Any]]:
        return [item.to_wire() for item in self.items]


@dataclass(frozen=True, repr=False)
class ValidatedEvidenceArtifact:
    path: Path
    artifact_type: str
    artifact_name: str
    mime: str
    size: int
    sha256: str
    metadata: Mapping[str, Any]

    def __repr__(self) -> str:
        return (
            "ValidatedEvidenceArtifact("
            f"artifact_type={self.artifact_type!r}, artifact_name={self.artifact_name!r}, "
            f"mime={self.mime!r}, size={self.size!r}, sha256='[REDACTED]')"
        )


def validate_manifest(
    root: str | Path,
    raw: object,
) -> tuple[ValidatedEvidenceArtifact, ...]:
    """Validate a child-produced manifest without reading unbounded data."""

    root_path = _resolved_root(root)
    if not isinstance(raw, list) or len(raw) > MAX_EVIDENCE_COUNT:
        raise EvidenceValidationError("Web Evidence manifest 数量无效")
    result: list[ValidatedEvidenceArtifact] = []
    seen_paths: set[Path] = set()
    total_bytes = 0
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {
            "artifact_type",
            "relative_path",
            "metadata",
        }:
            raise EvidenceValidationError("Web Evidence manifest 字段无效")
        artifact_type = item.get("artifact_type")
        relative_path = item.get("relative_path")
        metadata = item.get("metadata")
        if not isinstance(artifact_type, str) or artifact_type not in EVIDENCE_SPECS:
            raise EvidenceValidationError("Web Evidence 类型不受支持")
        if not isinstance(relative_path, str) or not 1 <= len(relative_path) <= 255:
            raise EvidenceValidationError("Web Evidence 路径无效")
        if "\\" in relative_path or "\x00" in relative_path:
            raise EvidenceValidationError("Web Evidence 路径无效")
        if not isinstance(metadata, Mapping):
            raise EvidenceValidationError("Web Evidence metadata 无效")
        safe_metadata = _validate_metadata(artifact_type, metadata)
        path = _contained_path(root_path, relative_path)
        if path in seen_paths:
            raise EvidenceValidationError("Web Evidence 路径重复")
        seen_paths.add(path)
        try:
            artifact = _validate_file(path, artifact_type, safe_metadata, relative_path)
        except EvidenceValidationError:
            if artifact_type == "PLAYWRIGHT_TRACE":
                # Trace is optional by design: an unprovable trace is dropped, never uploaded.
                continue
            raise
        total_bytes += artifact.size
        if total_bytes > MAX_EVIDENCE_TOTAL_BYTES:
            raise EvidenceValidationError("Web Evidence 总大小超过限制")
        result.append(artifact)
    return tuple(result)


def validate_artifact(
    root: str | Path,
    artifact_type: str,
    relative_path: str,
    metadata: Mapping[str, Any],
) -> ValidatedEvidenceArtifact:
    """Validate one artifact, primarily for protocol/client unit tests."""

    root_path = _resolved_root(root)
    if artifact_type not in EVIDENCE_SPECS:
        raise EvidenceValidationError("Web Evidence 类型不受支持")
    path = _contained_path(root_path, relative_path)
    safe_metadata = _validate_metadata(artifact_type, metadata)
    return _validate_file(path, artifact_type, safe_metadata, relative_path)


def audit_trace_file(path: str | Path, *, forbidden_values: Sequence[str] = ()) -> bool:
    """Return whether a trace ZIP is safe enough to expose to the backend."""

    try:
        trace_path = Path(path)
        size = trace_path.stat().st_size
        if size > EVIDENCE_SPECS["PLAYWRIGHT_TRACE"][1]:
            return False
        with trace_path.open("rb") as handle:
            data = handle.read(EVIDENCE_SPECS["PLAYWRIGHT_TRACE"][1] + 1)
        if len(data) != size:
            return False
    except OSError:
        return False
    return audit_trace_bytes(data, forbidden_values=forbidden_values)


def sanitize_trace_file(path: str | Path, *, forbidden_values: Sequence[str] = ()) -> bool:
    """Remove rendered input values and unsafe URL components before auditing."""

    trace_path = Path(path)
    try:
        size = trace_path.stat().st_size
        if size > EVIDENCE_SPECS["PLAYWRIGHT_TRACE"][1]:
            return False
        with trace_path.open("rb") as handle:
            source = handle.read(EVIDENCE_SPECS["PLAYWRIGHT_TRACE"][1] + 1)
        if len(source) != size or not source.startswith(b"PK"):
            return False
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            entries = archive.infolist()
            if len(entries) > 5_000:
                return False
            seen_names: set[str] = set()
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as cleaned:
                total_uncompressed = 0
                for info in entries:
                    safe_name = _normalized_archive_name(info.filename)
                    if safe_name is None:
                        return False
                    if safe_name in seen_names:
                        return False
                    seen_names.add(safe_name)
                    if info.is_dir():
                        continue
                    if info.file_size > 10 * 1024 * 1024:
                        return False
                    total_uncompressed += info.file_size
                    if total_uncompressed > 40 * 1024 * 1024:
                        return False
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        return False
                    sanitized = _sanitize_trace_data(
                        info.filename, data, forbidden_values=forbidden_values
                    )
                    if sanitized is None:
                        return False
                    cleaned.writestr(safe_name, sanitized)
            trace_path.write_bytes(output.getvalue())
    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False
    return audit_trace_file(trace_path)


def audit_trace_bytes(data: bytes, *, forbidden_values: Sequence[str] = ()) -> bool:
    if len(data) > EVIDENCE_SPECS["PLAYWRIGHT_TRACE"][1] or not data.startswith(b"PK"):
        return False
    lowered_forbidden = tuple(
        value.encode("utf-8", "ignore").lower()
        for value in forbidden_values
        if isinstance(value, str) and value
    )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 5_000:
                return False
            total_uncompressed = 0
            file_count = 0
            seen_names: set[str] = set()
            for info in entries:
                safe_name = _normalized_archive_name(info.filename)
                if safe_name is None:
                    return False
                if safe_name in seen_names:
                    return False
                seen_names.add(safe_name)
                if info.is_dir():
                    continue
                if info.file_size > 10 * 1024 * 1024:
                    return False
                total_uncompressed += info.file_size
                if total_uncompressed > 40 * 1024 * 1024:
                    return False
                content = archive.read(info)
                if len(content) != info.file_size:
                    return False
                file_count += 1
                lowered = content.lower()
                if _TRACE_SECRET_MARKER.search(content) or _TRACE_URL_WITH_QUERY.search(content):
                    return False
                if any(value in lowered for value in lowered_forbidden):
                    return False
    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False
    return file_count > 0


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Encode a validated flat metadata object deterministically."""

    try:
        encoded = json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvidenceValidationError("Web Evidence JSON 无效") from exc
    if len(encoded) > EVIDENCE_SPECS["WEB_SUMMARY"][1]:
        raise EvidenceValidationError("Web Evidence JSON 超过限制")
    return encoded


def safe_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", "", ""))


def _resolved_root(root: str | Path) -> Path:
    try:
        path = Path(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise EvidenceValidationError("Web Evidence 临时目录无效") from exc
    if not path.is_dir():
        raise EvidenceValidationError("Web Evidence 临时目录无效")
    return path


def _contained_path(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise EvidenceValidationError("Web Evidence 路径越界")
    try:
        resolved = (root / candidate).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise EvidenceValidationError("Web Evidence 路径越界或文件不存在") from exc
    if not resolved.is_file():
        raise EvidenceValidationError("Web Evidence 文件无效")
    return resolved


def _validate_file(
    path: Path,
    artifact_type: str,
    metadata: Mapping[str, Any],
    relative_path: str,
) -> ValidatedEvidenceArtifact:
    mime, max_size, magic = EVIDENCE_SPECS[artifact_type]
    try:
        size = path.stat().st_size
        if size < 1 or size > max_size:
            raise EvidenceValidationError("Web Evidence 文件大小无效")
        with path.open("rb") as handle:
            data = handle.read(max_size + 1)
    except OSError as exc:
        raise EvidenceValidationError("Web Evidence 文件不可读") from exc
    if len(data) != size or len(data) > max_size:
        raise EvidenceValidationError("Web Evidence 文件大小无效")
    if magic is not None and not data.startswith(magic):
        raise EvidenceValidationError("Web Evidence 文件类型无效")
    if artifact_type == "PLAYWRIGHT_TRACE" and not audit_trace_bytes(data):
        raise EvidenceValidationError("Web Evidence Trace 未通过安全审计")
    if artifact_type in {"CONSOLE_ERROR", "NETWORK_ERROR", "WEB_SUMMARY"}:
        if _validate_flat_json(data) != dict(metadata):
            raise EvidenceValidationError("Web Evidence JSON 与 metadata 不一致")
    digest = hashlib.sha256(data).hexdigest()
    semantic = _semantic_prefix(artifact_type, relative_path)
    artifact_name = f"{semantic}-{digest[:16]}"
    if not ARTIFACT_NAME_PATTERN.fullmatch(artifact_name):
        raise EvidenceValidationError("Web Evidence artifact_name 无效")
    return ValidatedEvidenceArtifact(
        path=path,
        artifact_type=artifact_type,
        artifact_name=artifact_name,
        mime=mime,
        size=size,
        sha256=digest,
        metadata=dict(metadata),
    )


def _validate_metadata(artifact_type: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) - METADATA_KEYS[artifact_type]:
        raise EvidenceValidationError("Web Evidence metadata 字段无效")
    result = dict(value)
    for key, item in result.items():
        if not _is_json_scalar(item):
            raise EvidenceValidationError("Web Evidence metadata 必须是平面 JSON")
        if key in {"page_url", "final_url", "url", "source"} and item is not None:
            if key in {"page_url", "final_url", "url"} and safe_url(item) != item:
                raise EvidenceValidationError("Web Evidence URL 无效")
            if key == "source" and isinstance(item, str) and item:
                if safe_url(item) != item and not item.startswith("[REDACTED]"):
                    raise EvidenceValidationError("Web Evidence source URL 无效")
    try:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvidenceValidationError("Web Evidence metadata 无效") from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise EvidenceValidationError("Web Evidence metadata 超过限制")
    return result


def _validate_flat_json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError("Web Evidence JSON 无效") from exc
    if not isinstance(value, Mapping) or not all(_is_json_scalar(item) for item in value.values()):
        raise EvidenceValidationError("Web Evidence JSON 必须是平面对象")
    if any(
        isinstance(item, str)
        and (_UNSAFE_JSON_TEXT.search(item) or _TRACE_URL_WITH_QUERY_TEXT.search(item))
        for item in value.values()
    ):
        raise EvidenceValidationError("Web Evidence JSON 包含不安全文本")
    return dict(value)


def _is_json_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _normalized_archive_name(name: str) -> str | None:
    """Normalize Windows/ZIP separators while preserving traversal checks."""

    if not isinstance(name, str) or "\x00" in name:
        return None
    normalized = name.replace("\\", "/")
    is_directory = normalized.endswith("/")
    normalized = normalized.rstrip("/")
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        return None
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    safe_name = "/".join(parts)
    return safe_name + "/" if is_directory else safe_name


_SENSITIVE_TRACE_KEYS = {
    "cookie",
    "setcookie",
    "storagestate",
    "authorization",
    "bearer",
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "apikey",
    "requestbody",
    "responsebody",
    "domcontent",
    "pagecontent",
}
_ERROR_DETAIL_KEYS = {"message", "stack", "name", "detail", "description"}


def _sanitize_trace_data(
    name: str,
    data: bytes,
    *,
    forbidden_values: Sequence[str],
) -> bytes | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if not name.lower().endswith((".trace", ".network")):
        return _redact_trace_text(text, forbidden_values).encode("utf-8")
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if not content.strip():
            lines.append(line)
            continue
        try:
            value = json.loads(content, object_pairs_hook=_trace_object_without_duplicates)
        except (TypeError, ValueError):
            return _redact_trace_text(text, forbidden_values).encode("utf-8")
        valid, sanitized = _sanitize_trace_json(value, forbidden_values=forbidden_values)
        if not valid:
            return None
        try:
            encoded = json.dumps(
                sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError, OverflowError):
            return None
        lines.append(encoded + ending)
    return "".join(lines).encode("utf-8")


def _sanitize_trace_json(
    value: object,
    *,
    forbidden_values: Sequence[str],
    inside_error: bool = False,
) -> tuple[bool, object]:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                return False, None
            normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized_key in _SENSITIVE_TRACE_KEYS:
                return False, None
            if inside_error and normalized_key in _ERROR_DETAIL_KEYS:
                result[key] = "[REDACTED]"
                continue
            child_inside_error = inside_error or normalized_key in {"error", "errors"}
            valid, sanitized = _sanitize_trace_json(
                item,
                forbidden_values=forbidden_values,
                inside_error=child_inside_error,
            )
            if not valid:
                return False, None
            result[key] = sanitized
        return True, result
    if isinstance(value, list):
        result: list[object] = []
        for item in value:
            valid, sanitized = _sanitize_trace_json(
                item,
                forbidden_values=forbidden_values,
                inside_error=inside_error,
            )
            if not valid:
                return False, None
            result.append(sanitized)
        return True, result
    if isinstance(value, str):
        if inside_error:
            return True, "[REDACTED]"
        return True, _redact_trace_text(value, forbidden_values)
    return True, value


def _redact_trace_text(text: str, forbidden_values: Sequence[str]) -> str:
    for value in forbidden_values:
        if isinstance(value, str) and value:
            text = text.replace(value, "[REDACTED]")
    return _TRACE_URL_WITH_QUERY_TEXT.sub(_redact_trace_url, text)


def _trace_object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _semantic_prefix(artifact_type: str, relative_path: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(relative_path).stem).strip("-_")
    fallback = EVIDENCE_PREFIXES[artifact_type]
    if artifact_type == "SCREENSHOT":
        semantic = stem if stem.startswith("screenshot") else f"screenshot-{stem}"
    elif artifact_type == "WEB_SUMMARY":
        semantic = "web-summary"
    elif artifact_type == "PLAYWRIGHT_TRACE":
        semantic = "playwright-trace"
    else:
        semantic = stem if stem.startswith(fallback) else f"{fallback}-{stem}"
    semantic = semantic[:110].rstrip("-_")
    return semantic or fallback


def _redact_trace_url(match: re.Match[str]) -> str:
    value = match.group(0)
    parsed = urlsplit(value)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", "", ""))
    return "[REDACTED_URL]"
