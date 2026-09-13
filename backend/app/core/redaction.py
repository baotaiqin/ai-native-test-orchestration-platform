import json
import math
import re
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlsplit, urlunsplit
from uuid import UUID

REDACTED = "<redacted>"
TRUNCATED = "<truncated>"
CYCLE = "<cycle>"
MAX_DEPTH = 6
MAX_ITEMS = 64
MAX_NODES = 256
MAX_TEXT_LENGTH = 16_384
MAX_TOTAL_TEXT = 65_536
MAX_KEY_LENGTH = 128
MAX_EXCEPTION_FRAMES = 20

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_KEY_CHARACTER = re.compile(r"[^a-z0-9]+")
_URI = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+",
    re.IGNORECASE,
)
_AUTH_SCHEME = re.compile(
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
_SENSITIVE_LABEL = (
    r"(?:password|passwd|pwd|token|access[ _-]?token|refresh[ _-]?token|"
    r"registration[ _-]?token|authorization|cookie|secret|credential|"
    r"api[ _-]?key|client[ _-]?secret|private[ _-]?key|storage[ _-]?state|"
    r"raw[ _-]?response|repair[ _-]?response)"
)
_QUOTED_ASSIGNMENT = re.compile(
    rf"(?is)(?P<prefix>[\"']?\b{_SENSITIVE_LABEL}\b[\"']?\s*[:=]\s*)"
    rf"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_UNQUOTED_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>[\"']?\b{_SENSITIVE_LABEL}\b[\"']?\s*[:=]\s*)"
    r"(?P<value>[^\r\n,;&]*?)"
    r"(?=(?:\s+[A-Za-z_][A-Za-z0-9_.-]*\s*[:=])|[\r\n,;&]|$)"
)
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "registration_token",
        "authorization",
        "cookie",
        "set_cookie",
        "secret",
        "credential",
        "credentials",
        "api_key",
        "client_secret",
        "private_key",
        "storage_state",
        "raw_response",
        "repair_response",
    }
)
_SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "credential",
    "authorization",
    "client_secret",
    "private_key",
    "api_key",
    "storage_state",
    "raw_response",
    "repair_response",
)
_TOKEN_AUDIT_FIELDS = frozenset(
    {
        "input_token",
        "output_token",
        "total_token",
        "prompt_token",
        "completion_token",
        "cached_token",
        "token_count",
        "max_token",
    }
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "registration_token",
        "authorization",
        "cookie",
        "secret",
        "credential",
        "api_key",
        "client_secret",
        "private_key",
        "signature",
        "sig",
    }
)


@dataclass
class _RedactionState:
    remaining_nodes: int = MAX_NODES
    remaining_text: int = MAX_TOTAL_TEXT
    seen: set[int] = field(default_factory=set)


def normalize_key(value: str) -> str:
    separated = _CAMEL_BOUNDARY.sub("_", value)
    return _NON_KEY_CHARACTER.sub("_", separated.casefold()).strip("_")


def is_sensitive_key(key: str, value: Any = None) -> bool:
    normalized = normalize_key(key)
    if normalized in _TOKEN_AUDIT_FIELDS and isinstance(
        value, (int, float, Decimal)
    ):
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized.endswith(("_token", "_secret")) or normalized.startswith(
        "secret_"
    ):
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = normalize_key(key)
    return (
        normalized in _SENSITIVE_QUERY_KEYS
        or normalized.endswith(
            ("_token", "_secret", "_api_key", "_signature", "_sig")
        )
        or is_sensitive_key(key)
    )


def _redact_uri(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ").,;}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        has_userinfo = parsed.username is not None or parsed.password is not None
        if parsed.netloc and not has_userinfo:
            if parsed.netloc.endswith(":"):
                return REDACTED + trailing
            # Accessing ``port`` validates ambiguous ``host:value`` authorities.
            # Invalid authorities fail closed instead of being emitted unchanged.
            _ = parsed.port
        query_parts: list[str] = []
        has_sensitive_query = False
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            sensitive = _is_sensitive_query_key(key)
            has_sensitive_query = has_sensitive_query or sensitive
            safe_value = REDACTED if sensitive else value
            query_parts.append(
                f"{quote_plus(key)}={quote_plus(safe_value, safe='<>')}"
            )
        if not has_userinfo and not has_sensitive_query:
            return raw + trailing
        safe_netloc = parsed.netloc
        if has_userinfo:
            hostname = parsed.hostname
            if not hostname:
                return REDACTED + trailing
            safe_netloc = f"[{hostname}]" if ":" in hostname else hostname
            if parsed.port is not None:
                safe_netloc = f"{safe_netloc}:{parsed.port}"
            safe_netloc = f"{REDACTED}@{safe_netloc}"
        return (
            urlunsplit(
                (
                    parsed.scheme,
                    safe_netloc,
                    parsed.path,
                    "&".join(query_parts),
                    parsed.fragment,
                )
            )
            + trailing
        )
    except (TypeError, ValueError):
        return REDACTED + trailing


def _replace_quoted_assignment(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{match.group('quote')}{REDACTED}{match.group('quote')}"


def _replace_unquoted_assignment(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{REDACTED}"


def _limit_text(value: str, state: _RedactionState) -> str:
    available = min(MAX_TEXT_LENGTH, max(0, state.remaining_text))
    if available == 0:
        return TRUNCATED
    if len(value) <= available:
        state.remaining_text -= len(value)
        return value
    suffix = "…<truncated>"
    kept = max(0, available - len(suffix))
    state.remaining_text -= available
    return value[:kept] + suffix


def _redact_text(value: str, state: _RedactionState, *, depth: int) -> str:
    candidate = value[:MAX_TEXT_LENGTH]
    stripped = candidate.strip()
    if depth < MAX_DEPTH and stripped.startswith(("{", "[", '"')):
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, RecursionError):
            pass
        else:
            redacted_json = redact_value(parsed, _state=state, _depth=depth + 1)
            serialized = json.dumps(
                redacted_json,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return _limit_text(serialized, state)
    redacted = _URI.sub(_redact_uri, candidate)
    redacted = _QUOTED_ASSIGNMENT.sub(_replace_quoted_assignment, redacted)
    redacted = _UNQUOTED_ASSIGNMENT.sub(_replace_unquoted_assignment, redacted)
    redacted = _AUTH_SCHEME.sub(REDACTED, redacted)
    if len(value) > MAX_TEXT_LENGTH:
        redacted += "…<truncated>"
    return _limit_text(redacted, state)


def redact_text(value: str) -> str:
    return _redact_text(value, _RedactionState(), depth=0)


def _safe_mapping_key(key: Any, index: int) -> str:
    if isinstance(key, str):
        return key[:MAX_KEY_LENGTH]
    return f"<non-string-key-{index}>"


def _mapping_marks_value_sensitive(value: Mapping[Any, Any]) -> bool:
    for index, (key, item) in enumerate(value.items()):
        if index >= MAX_ITEMS:
            break
        if (
            isinstance(key, str)
            and normalize_key(key) in {"key", "name", "header_name"}
            and isinstance(item, str)
            and is_sensitive_key(item)
        ):
            return True
    return False


def redact_value(
    value: Any,
    *,
    _state: _RedactionState | None = None,
    _depth: int = 0,
) -> Any:
    state = _state or _RedactionState()
    if state.remaining_nodes <= 0 or _depth >= MAX_DEPTH:
        return TRUNCATED
    state.remaining_nodes -= 1
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        return _redact_text(value, state, depth=_depth)
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Path):
        return value.name[:MAX_KEY_LENGTH]
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in state.seen:
            return CYCLE
        state.seen.add(identity)
        sensitive_value_context = _mapping_marks_value_sensitive(value)
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS or state.remaining_nodes <= 0:
                result[TRUNCATED] = TRUNCATED
                break
            safe_key = _safe_mapping_key(key, index)
            normalized = normalize_key(safe_key)
            if (
                (isinstance(key, str) and len(key) > MAX_KEY_LENGTH)
                or is_sensitive_key(safe_key, item)
                or (
                    sensitive_value_context
                    and normalized in {"value", "default_value"}
                )
            ):
                result[safe_key] = REDACTED
            else:
                result[safe_key] = redact_value(
                    item, _state=state, _depth=_depth + 1
                )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in state.seen:
            return CYCLE
        state.seen.add(identity)
        items: list[Any] = []
        for index, item in enumerate(value):
            if index >= MAX_ITEMS or state.remaining_nodes <= 0:
                items.append(TRUNCATED)
                break
            items.append(redact_value(item, _state=state, _depth=_depth + 1))
        return items
    return f"<unsupported:{type(value).__name__[:MAX_KEY_LENGTH]}>"


def redact_exception(
    exc_info: tuple[type[BaseException], BaseException, TracebackType] | tuple[None, None, None],
) -> dict[str, Any]:
    exc_type, exc_value, exc_traceback = exc_info
    if exc_type is None or exc_value is None:
        return {"type": "Exception", "message": "exception details unavailable", "frames": []}
    try:
        message = str(exc_value)
    except Exception:  # noqa: BLE001 - exception __str__ is untrusted diagnostic data
        message = "exception message unavailable"
    frames = []
    if exc_traceback is not None:
        for frame in traceback.extract_tb(exc_traceback, limit=MAX_EXCEPTION_FRAMES):
            frames.append(
                {
                    "file": Path(frame.filename).name[:MAX_KEY_LENGTH],
                    "line": frame.lineno,
                    "function": frame.name[:MAX_KEY_LENGTH],
                }
            )
    return {
        "type": exc_type.__name__[:MAX_KEY_LENGTH],
        "message": redact_text(message),
        "frames": frames,
    }
