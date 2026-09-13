import json
import re
from typing import Any


def parse_json_once(raw: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"


def deterministic_repair(raw: str) -> str:
    """执行一次保守修复；它不是正常解析主路径。"""
    content = raw.strip().lstrip("\ufeff")
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.IGNORECASE)
    if fenced:
        content = fenced.group(1).strip()
    content = re.sub(r",\s*([}\]])", r"\1", content)
    return content


def validate_json_schema(value: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _validate(value, schema, "$", errors)
    return errors


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _validate(value: Any, schema: Any, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        errors.append(f"{path}: 应为 {expected}")
        return
    if isinstance(expected, list) and not any(_matches_type(value, item) for item in expected):
        errors.append(f"{path}: 类型不在 {expected} 中")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 值不在允许范围 {schema['enum']} 中")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 值必须为 {schema['const']!r}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required if isinstance(required, list) else []:
            if key not in value:
                errors.append(f"{path}.{key}: 缺少必填字段")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value:
                    _validate(value[key], child_schema, f"{path}.{key}", errors)
            if schema.get("additionalProperties") is False:
                for key in value.keys() - properties.keys():
                    errors.append(f"{path}.{key}: 不允许的额外字段")
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errors.append(f"{path}: 元素数量不能少于 {schema['minItems']}")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            errors.append(f"{path}: 元素数量不能多于 {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]", errors)
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            errors.append(f"{path}: 字符长度不能少于 {schema['minLength']}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: 不符合 pattern {pattern}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: 不能小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: 不能大于 {schema['maximum']}")
