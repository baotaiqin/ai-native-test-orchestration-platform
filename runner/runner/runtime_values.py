"""Shared bounded response extraction primitives for formal runtimes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_JSONPATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]|\[['\"]([^'\"]+)['\"]\]")


def jsonpath_get(document: Any, expression: str) -> Any:
    if expression == "$":
        return document
    if not expression.startswith("$"):
        raise ValueError("JSONPath 必须以 $ 开始")
    position = 1
    current = document
    while position < len(expression):
        match = _JSONPATH_TOKEN.match(expression, position)
        if match is None:
            raise ValueError("JSONPath 表达式不受支持")
        key, index, quoted_key = match.groups()
        try:
            current = current[int(index)] if index is not None else current[key or quoted_key]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise KeyError(expression) from exc
        position = match.end()
    return current


def case_insensitive_get(values: Mapping[str, str], name: str) -> str:
    for key, value in values.items():
        if key.lower() == name.lower():
            return value
    raise KeyError(name)
