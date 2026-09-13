import re

import pytest

from app.core.exceptions import ResourceConflictError
from app.modules.environments.resolver import deserialize_variable, resolve_template


def test_deserialize_environment_variable_types() -> None:
    assert deserialize_variable("12", "NUMBER") == 12
    assert deserialize_variable("12.5", "NUMBER") == 12.5
    assert deserialize_variable("true", "BOOLEAN") is True
    assert deserialize_variable('{"retry":2}', "JSON") == {"retry": 2}
    assert deserialize_variable('["admin","viewer"]', "LIST") == ["admin", "viewer"]


def test_resolve_template_preserves_full_value_type() -> None:
    context = {"retry": 2, "enabled": True, "config": {"timeout": 5}}
    assert resolve_template("{{retry}}", context) == 2
    assert resolve_template("enabled={{enabled}}", context) == "enabled=true"
    assert resolve_template("config={{config}}", context) == 'config={"timeout":5}'


def test_resolve_dynamic_variables() -> None:
    generated_uuid = resolve_template("{{$uuid}}", {})
    generated_email = resolve_template("{{$random_email}}", {})
    generated_number = resolve_template("{{$random_int}}", {})
    assert re.fullmatch(r"[0-9a-f-]{36}", generated_uuid)
    assert generated_email.endswith("@example.test")
    assert isinstance(generated_number, int)
    assert 100000 <= generated_number <= 999999


def test_undefined_variable_is_rejected() -> None:
    with pytest.raises(ResourceConflictError, match="变量未定义"):
        resolve_template("{{missing}}", {})
