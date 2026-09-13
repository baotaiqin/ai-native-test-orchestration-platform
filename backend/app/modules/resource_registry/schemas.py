import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.exceptions import ResourceConflictError


class CleanupType(StrEnum):
    API = "API"
    SQL = "SQL"


class CleanupPolicy(StrEnum):
    ALWAYS = "ALWAYS"
    ON_SUCCESS = "ON_SUCCESS"
    ON_FAILURE = "ON_FAILURE"
    NEVER = "NEVER"


class CleanupStatus(StrEnum):
    PENDING = "PENDING"
    CLEANING = "CLEANING"
    CLEANED = "CLEANED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class CleanupOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class CleanupHttpMethod(StrEnum):
    DELETE = "DELETE"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"


class CleanupAuth(BaseModel):
    """Secret-free authentication reference for an API cleanup handler.

    A runner may resolve ``credential_ref`` from Runtime Context or ``secret_id``
    from the project Secret store.  Plain credentials are deliberately not part
    of this DSL and therefore cannot be written into a registry snapshot.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["NONE", "BEARER", "BASIC", "API_KEY"] = "NONE"
    credential_ref: str | None = Field(default=None, max_length=1000)
    secret_id: int | None = Field(default=None, gt=0)
    key_name: str | None = Field(default=None, max_length=255)
    placement: Literal["HEADER", "QUERY"] = "HEADER"

    @model_validator(mode="after")
    def validate_reference(self) -> "CleanupAuth":
        if self.secret_id is not None and self.credential_ref is not None:
            raise ValueError("API Cleanup 的 secret_id 与 credential_ref 只能二选一")
        if self.type == "NONE":
            if (
                self.credential_ref is not None
                or self.secret_id is not None
                or self.key_name is not None
                or self.placement != "HEADER"
            ):
                raise ValueError("NONE API Cleanup 不能配置凭据、key_name 或 QUERY placement")
            return self
        if self.credential_ref is not None and not _is_runtime_template(self.credential_ref):
            raise ValueError("API Cleanup credential_ref 必须是 Runtime Context 模板")
        if self.credential_ref is None and self.secret_id is None:
            raise ValueError("API Cleanup 必须配置 Secret ID 或 Runtime Context 凭据引用")
        if self.type in {"BEARER", "BASIC"} and (
            self.key_name is not None or self.placement != "HEADER"
        ):
            raise ValueError("BEARER/BASIC Cleanup 不能配置 API_KEY 的 key_name 或 placement")
        if self.type == "API_KEY" and not self.key_name:
            raise ValueError("API_KEY Cleanup 必须配置 key_name")
        return self


class CleanupConfig(BaseModel):
    """Shared, strict Cleanup DSL used by Cases and Scenario cleanup nodes."""

    model_config = ConfigDict(extra="forbid")

    cleanup_id: str | None = Field(
        default=None, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$"
    )
    cleanup_type: CleanupType
    policy: CleanupPolicy = CleanupPolicy.ALWAYS
    enabled: bool = True
    timeout_ms: int = Field(default=30000, ge=100, le=120000)

    # API cleanup fields.  They intentionally mirror ApiRequestTemplate's
    # request shape while keeping credentials as references only.
    method: CleanupHttpMethod | None = None
    url: str | None = Field(default=None, max_length=4000)
    query_params: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    headers: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    cookies: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    body: dict[str, Any] | None = None
    auth: CleanupAuth = Field(default_factory=CleanupAuth)

    # SQL cleanup fields.  Resource IDs are injected into params, never into
    # the statement text or SQL identifiers.
    connection_id: int | None = Field(default=None, gt=0)
    sql: str | None = Field(default=None, max_length=10000)
    params: dict[str, Any] | list[Any] = Field(default_factory=dict)
    resource_id_param: str | None = Field(
        default=None, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )

    @model_validator(mode="after")
    def validate_definition(self) -> "CleanupConfig":
        self._validate_type_exclusivity()
        if self.cleanup_type == CleanupType.API:
            self._validate_api()
        else:
            self._validate_sql()
        self._validate_size_and_sensitive_values()
        return self

    def _validate_type_exclusivity(self) -> None:
        if self.cleanup_type == CleanupType.API:
            if (
                self.connection_id is not None
                or self.sql is not None
                or self.resource_id_param is not None
            ):
                raise ValueError("API Cleanup 不能配置 SQL 专属字段")
            if self.params not in ({}, []):
                raise ValueError("API Cleanup params 必须为空")
            return
        if self.method is not None or self.url is not None:
            raise ValueError("SQL Cleanup 不能配置 API 专属字段")
        if self.query_params or self.headers or self.cookies or self.body is not None:
            raise ValueError("SQL Cleanup 不能配置 API 请求字段")
        if (
            self.auth.type != "NONE"
            or self.auth.credential_ref is not None
            or self.auth.secret_id is not None
            or self.auth.key_name is not None
            or self.auth.placement != "HEADER"
        ):
            raise ValueError("SQL Cleanup 不能配置 API auth")

    def _validate_api(self) -> None:
        if self.method is None or self.url is None or not self.url.strip():
            raise ValueError("API Cleanup 必须配置 method 和 url")
        if self.cleanup_type == CleanupType.API and self.method not in {
            CleanupHttpMethod.DELETE,
            CleanupHttpMethod.POST,
            CleanupHttpMethod.PUT,
            CleanupHttpMethod.PATCH,
        }:
            raise ValueError("API Cleanup 只允许 DELETE、POST、PUT、PATCH")
        _validate_url_template(self.url)
        self._validate_request_items("query_params", self.query_params)
        self._validate_request_items("headers", self.headers)
        self._validate_request_items("cookies", self.cookies)
        if self.body is not None:
            body_type = self.body.get("type", "NONE")
            if body_type not in {"NONE", "JSON", "FORM_URLENCODED", "MULTIPART", "RAW"}:
                raise ValueError("API Cleanup body.type 无效")
            if body_type == "NONE" and self.body.get("content") is not None:
                raise ValueError("NONE API Cleanup body 不能包含 content")
            if body_type != "NONE" and self.body.get("content") is None:
                raise ValueError("非 NONE API Cleanup body 必须包含 content")
        try:
            from app.modules.test_cases.schemas import ApiRequestTemplate

            ApiRequestTemplate.model_validate(
                {
                    "method": self.method.value,
                    "url": self.url,
                    "query_params": self.query_params,
                    "headers": self.headers,
                    "cookies": self.cookies,
                    "body": self.body or {"type": "NONE"},
                    "timeout_ms": self.timeout_ms,
                    "follow_redirects": False,
                }
            )
        except ValueError as exc:
            raise ValueError(f"API Cleanup 请求模板无效：{exc}") from exc

    @staticmethod
    def _validate_request_items(name: str, items: list[dict[str, Any]]) -> None:
        names: list[str] = []
        for item in items:
            if set(item) - {"name", "value", "enabled", "description"}:
                raise ValueError(f"API Cleanup {name} 字段不支持")
            item_name = item.get("name")
            value = item.get("value", "")
            if not isinstance(item_name, str) or not item_name.strip():
                raise ValueError(f"API Cleanup {name} 名称不能为空")
            if not isinstance(value, str):
                raise ValueError(f"API Cleanup {name} 值必须是字符串")
            if "\r" in value or "\n" in value:
                raise ValueError(f"API Cleanup {name} 值不能包含换行符")
            if item.get("enabled", True):
                names.append(item_name.strip().lower())
            if _is_sensitive_name(item_name) and not _is_runtime_template(value):
                raise ValueError("API Cleanup 敏感参数只能引用单一 Runtime Context 模板")
        if len(names) != len(set(names)):
            raise ValueError(f"API Cleanup {name} 存在重复名称")

    def _validate_sql(self) -> None:
        if self.connection_id is None or not self.sql or not self.sql.strip():
            raise ValueError("SQL Cleanup 必须配置 connection_id 和 sql")
        if not isinstance(self.params, (dict, list)):
            raise ValueError("SQL Cleanup params 必须是对象或数组")
        if isinstance(self.params, dict) and any(
            not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            for key in self.params
        ):
            raise ValueError("SQL Cleanup 参数名称非法")
        from app.modules.resource_registry.security import validate_sql_cleanup

        try:
            validate_sql_cleanup(self.sql)
        except ResourceConflictError as exc:
            raise ValueError(str(exc)) from exc

    def _validate_size_and_sensitive_values(self) -> None:
        serialized = self.model_dump(mode="json")
        if len(json.dumps(serialized, ensure_ascii=False)) > 65536:
            raise ValueError("Cleanup 配置不能超过 64KB")
        validate_secret_free_payload(serialized, allow_auth_reference=True)


_SENSITIVE_NAME_PARTS = {
    "authorization",
    "auth",
    "cookie",
    "set_cookie",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "credential",
}
_RUNTIME_TEMPLATE = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$")
_RUNTIME_TEMPLATE_SEARCH = re.compile(r"\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}")
_CREDENTIAL_SHAPES = (
    re.compile(r"(?i)\bbearer\s+[^\s,;]+"),
    re.compile(
        r"(?i)\b(?:password|token|access[_-]?token|refresh[_-]?token|"
        r"authorization|cookie|set-cookie|secret|api[_-]?key)\s*[:=]\s*"
        r"(?!%s$|\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$)[^\s,;]+"
    ),
)


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _is_sensitive_name(name: str) -> bool:
    normalized = _normalized_name(name)
    if normalized in _SENSITIVE_NAME_PARTS:
        return True
    parts = set(normalized.split("_"))
    return bool(parts & {"token", "password", "passwd", "secret", "credential"}) or any(
        part in normalized for part in ("authorization", "api_key", "apikey")
    )


def _is_runtime_template(value: str) -> bool:
    return bool(_RUNTIME_TEMPLATE.fullmatch(value))


def _validate_url_template(value: str) -> None:
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("API Cleanup URL 不能为空且不能包含空白字符")
    masked = _RUNTIME_TEMPLATE_SEARCH.sub("runtime", value)
    if "{" in masked or "}" in masked:
        raise ValueError("API Cleanup URL 的 Runtime Context 花括号或变量名无效")
    static_url = masked.lower()
    if static_url.startswith(("http://", "https://")):
        return
    if value.startswith("{{") and _RUNTIME_TEMPLATE_SEARCH.match(value):
        return
    raise ValueError("API Cleanup URL 最终必须是 http(s) URL 或合法的完整 Runtime URL 模板")


def cleanup_policy_matches(policy: CleanupPolicy, outcome: CleanupOutcome) -> bool:
    if policy == CleanupPolicy.ALWAYS:
        return True
    if policy == CleanupPolicy.ON_SUCCESS:
        return outcome == CleanupOutcome.SUCCESS
    if policy == CleanupPolicy.ON_FAILURE:
        return outcome in {
            CleanupOutcome.FAILURE,
            CleanupOutcome.CANCELLED,
            CleanupOutcome.TIMEOUT,
        }
    return False


def is_sensitive_name(name: str) -> bool:
    return _is_sensitive_name(name)


def _is_placeholder(value: str) -> bool:
    return value in {"%s", "?"} or bool(re.fullmatch(r"%\([A-Za-z_][A-Za-z0-9_]*\)s", value))


def validate_secret_free_payload(value: Any, *, allow_auth_reference: bool = False) -> None:
    """Reject secret-shaped data before it can enter an asset or registry JSON.

    The checker is intentionally conservative for credential-shaped strings but
    ignores ordinary words such as ``token_count`` unless they carry a value.
    ``auth.secret_id`` and ``auth.credential_ref`` are references, not secrets.
    """

    def visit(item: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                normalized = _normalized_name(key_text)
                child_path = (*path, normalized)
                is_auth_container = allow_auth_reference and not path and normalized == "auth"
                is_auth_reference = allow_auth_reference and path == ("auth",) and normalized in {
                    "secret_id",
                    "credential_ref",
                }
                if _is_sensitive_name(key_text) and not is_auth_container and not is_auth_reference:
                    raise ValueError("Cleanup 配置不能保存 Secret 或明文凭据")
                visit(child, child_path)
            return
        if isinstance(item, list):
            for child in item:
                visit(child, path)
            return
        if isinstance(item, str):
            if _is_runtime_template(item) or _is_placeholder(item):
                return
            if any(pattern.search(item) for pattern in _CREDENTIAL_SHAPES):
                raise ValueError("Cleanup 配置包含疑似明文凭据")

    visit(value)


class ResourceRegisterRequest(BaseModel):
    project_id: int = Field(gt=0)
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    resource_type: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$"
    )
    resource_id: str = Field(min_length=1, max_length=512)
    cleanup: CleanupConfig
    source: str = Field(default="RUNTIME", min_length=1, max_length=64)


class ResourceResponse(BaseModel):
    id: int
    project_id: int
    run_id: str
    resource_type: str
    resource_id: str
    cleanup_type: CleanupType
    cleanup_config: dict[str, Any]
    status: CleanupStatus
    registration_sequence: int
    attempt_count: int
    last_error: str | None
    error_code: str | None
    source: str
    created_at: datetime
    cleaned_at: datetime | None
    updated_at: datetime


class ResourceListResponse(BaseModel):
    items: list[ResourceResponse]
    total: int


class CleanupPlanRequest(BaseModel):
    project_id: int = Field(gt=0)
    run_id: str = Field(min_length=1, max_length=128)
    outcome: CleanupOutcome
    retry_failed: bool = False


class CleanupPlanItem(BaseModel):
    resource: ResourceResponse
    action: Literal["CLEAN", "SKIP"]
    reason: str


class CleanupPlanResponse(BaseModel):
    project_id: int
    run_id: str
    outcome: CleanupOutcome
    items: list[CleanupPlanItem]


class CleanupExecuteRequest(CleanupPlanRequest):
    pass


class CleanupResultItem(BaseModel):
    resource: ResourceResponse
    status: CleanupStatus
    error_code: str | None = None
    message: str | None = None


class CleanupExecuteResponse(BaseModel):
    project_id: int
    run_id: str
    outcome: CleanupOutcome
    items: list[CleanupResultItem]
