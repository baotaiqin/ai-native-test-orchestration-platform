"""用于异常和命令行输出的 Secret 脱敏。"""

from __future__ import annotations

import re
from collections.abc import Iterable

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?(?:registration_token|credential|authorization|token|api_key|password|client_secret|cookie|set-cookie|secret)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"
)


def redact_text(
    value: object,
    secrets: Iterable[str] = (),
    *,
    limit: int = 512,
) -> str:
    """截断并移除已知 Secret 以及常见凭证字段中的值。"""

    text = str(value)
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _KEY_VALUE_PATTERN.sub(r"\1[REDACTED]", text)
    if len(text) > limit:
        return f"{text[:limit]}…"
    return text
