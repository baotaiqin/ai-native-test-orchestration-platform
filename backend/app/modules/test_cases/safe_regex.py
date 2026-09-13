"""Conservative, dependency-free regular-expression safety checks.

The checker intentionally scans the pattern as plain text.  It never applies a
regular expression to an untrusted pattern, and it rejects ambiguous constructs
that can cause catastrophic backtracking instead of trying to prove every
possible pattern safe.
"""

from dataclasses import dataclass
from typing import Any

MAX_SAFE_REGEX_LENGTH = 1000


@dataclass
class _GroupState:
    has_alternation: bool = False
    has_quantifier: bool = False
    has_optional: bool = False


@dataclass
class _Atom:
    kind: str
    group: _GroupState | None = None


def safe_regex_errors(pattern: str) -> list[str]:
    """Return conservative safety/syntax-shape errors for a regex pattern."""

    if not isinstance(pattern, str):
        return ["模式必须是字符串"]
    if not pattern:
        return ["模式不能为空"]
    if len(pattern) > MAX_SAFE_REGEX_LENGTH:
        return [f"模式长度不能超过 {MAX_SAFE_REGEX_LENGTH} 字符"]

    errors: list[str] = []
    groups: list[_GroupState] = []
    last_atom: _Atom | None = None
    index = 0
    in_class = False

    def add_error(message: str) -> None:
        if message not in errors:
            errors.append(message)

    def mark_current_quantifier(*, optional: bool) -> None:
        if groups:
            groups[-1].has_quantifier = True
            groups[-1].has_optional |= optional

    while index < len(pattern):
        char = pattern[index]

        if in_class:
            if char == "\\":
                index += 2
                continue
            if char == "]":
                in_class = False
                last_atom = _Atom("class")
            index += 1
            continue

        if char == "\\":
            if index + 1 >= len(pattern):
                add_error("模式包含未闭合转义")
                break
            escaped = pattern[index + 1]
            if escaped.isdigit() or escaped in {"k", "K", "g", "G"}:
                add_error("不允许 backreference")
            last_atom = _Atom("escaped")
            index += 2
            continue

        if char == "[":
            in_class = True
            last_atom = _Atom("class")
            index += 1
            continue

        if char == "(":
            if (
                index + 1 < len(pattern)
                and pattern[index + 1] == "?"
                and (index + 2 >= len(pattern) or pattern[index + 2] != ":")
            ):
                add_error("不允许 lookaround、conditional 或其它扩展分组")
            groups.append(_GroupState())
            last_atom = None
            index += 3 if pattern[index : index + 3] == "(?:" else 1
            continue

        if char == "|":
            if groups:
                groups[-1].has_alternation = True
            else:
                add_error("模式包含未配对的 alternation")
            last_atom = None
            index += 1
            continue

        if char == ")":
            if not groups:
                add_error("模式包含未配对的分组")
                last_atom = _Atom("group", _GroupState())
            else:
                group = groups.pop()
                if groups:
                    parent = groups[-1]
                    parent.has_alternation |= group.has_alternation
                    parent.has_quantifier |= group.has_quantifier
                    parent.has_optional |= group.has_optional
                last_atom = _Atom("group", group)
            index += 1
            continue

        quantifier: tuple[int, int | None, bool] | None = None
        if char in "*+?":
            quantifier = (0 if char in "*?" else 1, None, char in "*?")
        elif char == "{":
            parsed = _parse_braced_quantifier(pattern, index)
            if parsed is not None:
                quantifier, index = parsed
                if last_atom is None:
                    add_error("量词缺少实际匹配项")
                    continue
                # The rest of this branch is shared with single-character
                # quantifiers; do not increment index again below.
                if last_atom.kind == "quantifier":
                    add_error("不允许嵌套量词")
                elif last_atom.kind == "group" and last_atom.group is not None:
                    group = last_atom.group
                    if group.has_alternation or group.has_quantifier or group.has_optional:
                        add_error("不允许量化包含 alternation 或内部可选/重复的分组")
                mark_current_quantifier(optional=quantifier[2])
                last_atom = _Atom("quantifier")
                continue

        if quantifier is not None:
            if last_atom is None:
                add_error("量词缺少实际匹配项")
            elif last_atom.kind == "quantifier":
                add_error("不允许嵌套量词")
            elif last_atom.kind == "group" and last_atom.group is not None:
                group = last_atom.group
                if group.has_alternation or group.has_quantifier or group.has_optional:
                    add_error("不允许量化包含 alternation 或内部可选/重复的分组")
            mark_current_quantifier(optional=quantifier[2])
            last_atom = _Atom("quantifier")
            index += 1
            continue

        last_atom = _Atom("literal")
        index += 1

    if in_class:
        errors.append("模式包含未闭合字符类")
    if groups:
        errors.append("模式包含未闭合分组")
    return errors


def _parse_braced_quantifier(
    pattern: str, start: int
) -> tuple[tuple[int, int | None, bool], int] | None:
    """Parse only an unambiguous {m}, {m,}, or {m,n} quantifier."""

    index = start + 1
    lower_start = index
    while index < len(pattern) and pattern[index].isdigit():
        index += 1
    if index == lower_start:
        return None
    lower = int(pattern[lower_start:index])
    if index >= len(pattern):
        return None
    if pattern[index] == "}":
        return (lower, lower, lower == 0), index + 1
    if pattern[index] != ",":
        return None
    index += 1
    upper_start = index
    while index < len(pattern) and pattern[index].isdigit():
        index += 1
    upper = int(pattern[upper_start:index]) if index > upper_start else None
    if index >= len(pattern) or pattern[index] != "}":
        return None
    if upper is not None and upper < lower:
        return None
    return (lower, upper, lower == 0), index + 1


def schema_pattern_errors(schema: Any, path: str = "$") -> list[str]:
    """Validate every JSON Schema pattern recursively, including combinators."""

    if not isinstance(schema, dict):
        return [f"{path}: Schema 必须是对象"]
    errors: list[str] = []
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            errors.append(f"{path}.pattern: 必须是字符串")
        else:
            errors.extend(f"{path}.pattern: {error}" for error in safe_regex_errors(pattern))

    for keyword in (
        "properties",
        "patternProperties",
        "dependentSchemas",
    ):
        children = schema.get(keyword)
        if isinstance(children, dict):
            for key, child in children.items():
                errors.extend(schema_pattern_errors(child, f"{path}.{keyword}.{key}"))
    for keyword in (
        "items",
        "additionalProperties",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    ):
        child = schema.get(keyword)
        if isinstance(child, dict):
            errors.extend(schema_pattern_errors(child, f"{path}.{keyword}"))
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = schema.get(keyword)
        if isinstance(children, list):
            for index, child in enumerate(children):
                errors.extend(schema_pattern_errors(child, f"{path}.{keyword}[{index}]"))
    return errors
