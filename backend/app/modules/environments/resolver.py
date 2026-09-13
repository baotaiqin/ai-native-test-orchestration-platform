import json
import random
import re
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError
from app.modules.environments.models import EnvironmentVariable
from app.modules.environments.schemas import VariableType

_TEMPLATE_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")
_DYNAMIC_PATTERN = re.compile(r"\{\{\$([A-Za-z_]+(?::[A-Za-z_]+)?)\}\}")


def deserialize_variable(value: str, value_type: str) -> Any:
    kind = VariableType(value_type)
    if kind == VariableType.NUMBER:
        number = float(value)
        return int(number) if number.is_integer() else number
    if kind == VariableType.BOOLEAN:
        return value.lower() == "true"
    if kind in {VariableType.JSON, VariableType.LIST}:
        return json.loads(value)
    return value


def build_environment_context(session: Session, environment_id: int) -> dict[str, Any]:
    statement = select(EnvironmentVariable).where(
        EnvironmentVariable.environment_id == environment_id,
        EnvironmentVariable.enabled.is_(True),
    )
    variables = session.scalars(statement).all()
    return {
        variable.key: deserialize_variable(variable.value, variable.value_type)
        for variable in variables
    }


def _dynamic_value(name: str) -> Any:
    if name == "uuid":
        return str(uuid4())
    if name == "timestamp":
        return int(time.time())
    if name == "random_int":
        return random.SystemRandom().randint(100000, 999999)
    if name in {"random_email", "faker:email"}:
        return f"test-{uuid4().hex[:10]}@example.test"
    if name == "faker:name":
        return f"测试用户{random.SystemRandom().randint(1000, 9999)}"
    raise ResourceConflictError(f"不支持的动态变量：{{$${name}}}")


def resolve_template(template: Any, context: dict[str, Any]) -> Any:
    if not isinstance(template, str):
        return template

    dynamic_match = _DYNAMIC_PATTERN.fullmatch(template)
    if dynamic_match:
        return _dynamic_value(dynamic_match.group(1))
    variable_match = _TEMPLATE_PATTERN.fullmatch(template)
    if variable_match:
        key = variable_match.group(1)
        if key not in context:
            raise ResourceConflictError(f"变量未定义：{key}")
        return context[key]

    def replace_dynamic(match: re.Match[str]) -> str:
        return str(_dynamic_value(match.group(1)))

    def replace_variable(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise ResourceConflictError(f"变量未定义：{key}")
        value = context[key]
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value).lower() if isinstance(value, bool) else str(value)

    return _TEMPLATE_PATTERN.sub(replace_variable, _DYNAMIC_PATTERN.sub(replace_dynamic, template))
