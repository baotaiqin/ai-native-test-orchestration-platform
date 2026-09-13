import base64
import binascii
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.time import to_utc_aware
from app.modules.resource_registry.schemas import CleanupConfig, validate_secret_free_payload
from app.modules.test_cases.retry_limit import HISTORICAL_API_STEP_MAX_RETRIES
from app.modules.test_cases.safe_regex import safe_regex_errors, schema_pattern_errors


class CaseType(StrEnum):
    API = "API"
    WEB = "WEB"
    MANUAL = "MANUAL"


class CasePriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class SuggestedStep(BaseModel):
    order: int = Field(ge=1)
    action: str = Field(min_length=1, max_length=1000)
    expected: str = Field(min_length=1, max_length=1000)


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class RequestValueItem(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str = Field(default="", max_length=10000, repr=False)
    enabled: bool = True
    description: str | None = Field(default=None, max_length=500)


class RequestBodyType(StrEnum):
    NONE = "NONE"
    JSON = "JSON"
    FORM_URLENCODED = "FORM_URLENCODED"
    MULTIPART = "MULTIPART"
    RAW = "RAW"


class RequestBody(BaseModel):
    type: RequestBodyType = RequestBodyType.NONE
    content: Any | None = Field(default=None, repr=False)
    content_type: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_content(self) -> "RequestBody":
        if self.type == RequestBodyType.NONE and self.content is not None:
            raise ValueError("NONE 请求体不能包含 content")
        if self.type != RequestBodyType.NONE and self.content is None:
            raise ValueError("非 NONE 请求体必须包含 content")
        if self.type == RequestBodyType.JSON and not isinstance(self.content, (dict, list)):
            raise ValueError("JSON 请求体必须是对象或数组")
        if self.type == RequestBodyType.MULTIPART:
            _validate_multipart_content(self.content)
            if self.content_type is not None:
                raise ValueError("MULTIPART Content-Type 必须由 Runner 生成 boundary")
        return self


def _validate_multipart_content(content: Any) -> None:
    if isinstance(content, dict):
        items = [
            {"name": key, "value": value, "enabled": True}
            for key, value in content.items()
        ]
    elif isinstance(content, list):
        items = content
    else:
        raise ValueError("MULTIPART content 必须是对象或字段列表")
    if len(items) > 200:
        raise ValueError("MULTIPART 字段数量不能超过 200")
    total_size = 0
    for item in items:
        if not isinstance(item, dict) or set(item) - {
            "name",
            "value",
            "enabled",
            "filename",
            "content_type",
            "encoding",
        }:
            raise ValueError("MULTIPART 字段配置无效")
        enabled = item.get("enabled", True)
        name = item.get("name")
        value = item.get("value")
        if type(enabled) is not bool or not isinstance(name, str) or not 1 <= len(name) <= 255:
            raise ValueError("MULTIPART 字段名称或 enabled 无效")
        if not enabled:
            continue
        filename = item.get("filename")
        content_type = item.get("content_type")
        encoding = item.get("encoding", "TEXT")
        if filename is None:
            if encoding != "TEXT" or content_type is not None or not isinstance(value, str):
                raise ValueError("MULTIPART 文本字段配置无效")
            total_size += len(name.encode()) + len(value.encode())
        else:
            if (
                not isinstance(filename, str)
                or not 1 <= len(filename) <= 255
                or filename in {".", ".."}
                or "/" in filename
                or "\\" in filename
                or encoding != "BASE64"
                or not isinstance(value, str)
                or len(value) > 2_800_000
                or not isinstance(content_type, str)
                or not 1 <= len(content_type) <= 255
                or "\r" in content_type
                or "\n" in content_type
            ):
                raise ValueError("MULTIPART 文件字段配置无效")
            try:
                decoded = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("MULTIPART 文件字段不是有效 Base64") from exc
            total_size += len(name.encode()) + len(filename.encode()) + len(decoded)
        if total_size > 2 * 1024 * 1024:
            raise ValueError("MULTIPART 请求体不能超过 2 MiB")


class AuthType(StrEnum):
    NONE = "NONE"
    BEARER = "BEARER"
    BASIC = "BASIC"
    API_KEY = "API_KEY"


class ApiKeyPlacement(StrEnum):
    HEADER = "HEADER"
    QUERY = "QUERY"


class RequestAuth(BaseModel):
    type: AuthType = AuthType.NONE
    token: str | None = Field(default=None, max_length=10000, repr=False)
    username: str | None = Field(default=None, max_length=1000)
    password: str | None = Field(default=None, max_length=10000, repr=False)
    key_name: str | None = Field(default=None, max_length=255)
    key_value: str | None = Field(default=None, max_length=10000, repr=False)
    placement: ApiKeyPlacement = ApiKeyPlacement.HEADER

    @model_validator(mode="after")
    def validate_credentials(self) -> "RequestAuth":
        if self.type == AuthType.BEARER and not self.token:
            raise ValueError("Bearer 认证必须配置 token 或 Secret 模板")
        if self.type == AuthType.BASIC and (not self.username or not self.password):
            raise ValueError("Basic 认证必须配置用户名和密码")
        if self.type == AuthType.API_KEY and (not self.key_name or not self.key_value):
            raise ValueError("API Key 认证必须配置名称和值")
        return self


class ApiRetryCondition(StrEnum):
    TARGET_NETWORK_ERROR = "TARGET_NETWORK_ERROR"
    TARGET_TIMEOUT = "TARGET_TIMEOUT"
    HTTP_5XX = "HTTP_5XX"


_DEFAULT_API_RETRY_CONDITIONS = (
    ApiRetryCondition.TARGET_NETWORK_ERROR,
    ApiRetryCondition.TARGET_TIMEOUT,
    ApiRetryCondition.HTTP_5XX,
)


class ApiRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(
        default=0,
        strict=True,
        ge=0,
        le=HISTORICAL_API_STEP_MAX_RETRIES,
        description=(
            "历史读取兼容 0..3；新建正式 API Case Version 和新执行的 V1 上限由领域门禁限制为 0..1"
        ),
    )
    backoff_ms: int = Field(default=500, ge=0, le=30_000)
    retry_on: list[ApiRetryCondition] = Field(
        default_factory=lambda: list(_DEFAULT_API_RETRY_CONDITIONS),
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_retry_conditions(self) -> "ApiRetryPolicy":
        if len(self.retry_on) != len(set(self.retry_on)):
            raise ValueError("retry_on 不能包含重复条件")
        if self.max_retries > 0 and not self.retry_on:
            raise ValueError("启用重试时 retry_on 至少需要一个条件")
        return self


class ApiRequestTemplate(BaseModel):
    method: HttpMethod
    url: str = Field(min_length=1, max_length=4000, repr=False)
    query_params: list[RequestValueItem] = Field(default_factory=list, max_length=200)
    headers: list[RequestValueItem] = Field(default_factory=list, max_length=200)
    cookies: list[RequestValueItem] = Field(default_factory=list, max_length=100)
    body: RequestBody = Field(default_factory=RequestBody)
    auth: RequestAuth = Field(default_factory=RequestAuth)
    timeout_ms: int = Field(default=30000, ge=100, le=120000)
    follow_redirects: bool = True
    retry_policy: ApiRetryPolicy = Field(default_factory=ApiRetryPolicy)

    @model_validator(mode="after")
    def validate_request(self) -> "ApiRequestTemplate":
        url = self.url.strip()
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("{{")):
            raise ValueError("URL 必须使用 http(s) 或 Runtime Context 模板")
        for label, items in (
            ("Query 参数", self.query_params),
            ("Header", self.headers),
            ("Cookie", self.cookies),
        ):
            names = [item.name.strip().lower() for item in items if item.enabled]
            if len(names) != len(set(names)):
                raise ValueError(f"{label}存在重复名称")
            if any("\r" in item.value or "\n" in item.value for item in items):
                raise ValueError(f"{label}值不能包含换行符")
        return self


class ExtractorSource(StrEnum):
    JSONPATH = "JSONPATH"
    HEADER = "HEADER"
    COOKIE = "COOKIE"


class RuntimeResponseSnapshot(BaseModel):
    status_code: int = Field(default=200, ge=100, le=599)
    json_body: Any | None = None
    text: str | None = Field(default=None, max_length=2_000_000)
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    response_time_ms: int | None = Field(default=None, ge=0, le=86_400_000)

    @model_validator(mode="after")
    def normalize_elapsed_time(self) -> "RuntimeResponseSnapshot":
        if (
            self.elapsed_ms is not None
            and self.response_time_ms is not None
            and self.elapsed_ms != self.response_time_ms
        ):
            raise ValueError("elapsed_ms 与 response_time_ms 必须一致")
        if self.response_time_ms is None and self.elapsed_ms is not None:
            self.response_time_ms = self.elapsed_ms
        elif self.elapsed_ms is None and self.response_time_ms is not None:
            self.elapsed_ms = self.response_time_ms
        return self


class ResponseExtractor(BaseModel):
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    source: ExtractorSource
    expression: str = Field(min_length=1, max_length=1000)
    required: bool = True
    default_value: Any | None = None
    enabled: bool = True


class CaseDataSourceConfig(BaseModel):
    """Optional API Case binding to a project Dataset Version."""

    dataset_id: int = Field(gt=0)
    dataset_version_id: int | None = Field(default=None, gt=0)
    prefix: str = Field(default="", max_length=64, pattern=r"^(|[A-Za-z_][A-Za-z0-9_.-]*)$")
    column_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def validate_mapping(self) -> "CaseDataSourceConfig":
        targets: list[str] = []
        for source, target in self.column_mapping.items():
            if not source.strip() or not target.strip():
                raise ValueError("数据集列映射的源列和目标列不能为空")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", target):
                raise ValueError(f"数据集目标列名非法：{target}")
            if target.startswith("__"):
                raise ValueError("数据集目标列名不能以 __ 开头")
            targets.append(target)
        if len(targets) != len(set(targets)):
            raise ValueError("数据集列映射目标不能重复")
        return self


class AssertionKind(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_SEMANTIC = "AI_SEMANTIC"


class DeterministicAssertionType(StrEnum):
    STATUS_CODE = "STATUS_CODE"
    JSONPATH_EQUAL = "JSONPATH_EQUAL"
    CONTAINS = "CONTAINS"
    REGEX = "REGEX"
    HEADER = "HEADER"
    COOKIE = "COOKIE"
    JSON_SCHEMA = "JSON_SCHEMA"
    RESPONSE_TIME = "RESPONSE_TIME"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"
    ARRAY_LENGTH = "ARRAY_LENGTH"
    TYPE = "TYPE"


class AssertionSource(StrEnum):
    STATUS_CODE = "STATUS_CODE"
    JSON_BODY = "JSON_BODY"
    JSONPATH = "JSONPATH"
    RESPONSE_TEXT = "RESPONSE_TEXT"
    HEADER = "HEADER"
    COOKIE = "COOKIE"
    RESPONSE_TIME = "RESPONSE_TIME"


class AssertionOperator(StrEnum):
    EQ = "EQ"
    CONTAINS = "CONTAINS"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"


_ASSERTION_TYPE_VALUES = {item.value for item in DeterministicAssertionType}
_TYPE_EXPECTED_VALUES = {"object", "array", "string", "number", "integer", "boolean", "null"}


class Assertion(BaseModel):
    """Versioned assertion DSL. `kind` keeps deterministic and AI semantics explicit."""

    model_config = ConfigDict(extra="forbid")

    kind: AssertionKind = AssertionKind.DETERMINISTIC
    type: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    source: AssertionSource | None = None
    expression: str | None = Field(default=None, max_length=4000)
    operator: AssertionOperator | None = None
    expected: Any | None = None
    prompt_id: int | None = Field(default=None, gt=0)
    criteria: str | None = Field(default=None, max_length=4000)
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_definition(self) -> "Assertion":
        self.name = self.name.strip()
        self.type = self.type.strip().upper()
        if not self.name:
            raise ValueError("断言名称不能为空")
        assertion_type = self.type
        if (
            self.kind == AssertionKind.DETERMINISTIC
            and assertion_type == AssertionKind.AI_SEMANTIC.value
        ):
            self.kind = AssertionKind.AI_SEMANTIC
        if self.kind == AssertionKind.AI_SEMANTIC:
            if assertion_type != AssertionKind.AI_SEMANTIC.value:
                raise ValueError("AI 断言 type 必须为 AI_SEMANTIC")
            if any(
                value is not None
                for value in (self.source, self.expression, self.operator, self.expected)
            ):
                raise ValueError("AI 断言不能配置 source/expression/operator/expected")
            if self.prompt_id is None:
                raise ValueError("AI 断言必须配置 prompt_id")
            if not self.criteria or not self.criteria.strip():
                raise ValueError("AI 断言 criteria 不能为空")
            if self.confidence_threshold is None:
                self.confidence_threshold = 0.8
            return self

        if assertion_type not in _ASSERTION_TYPE_VALUES:
            raise ValueError(f"不支持的确定性断言类型：{self.type}")
        if (
            self.prompt_id is not None
            or self.criteria is not None
            or self.confidence_threshold is not None
        ):
            raise ValueError("确定性断言不能配置 AI 字段")
        if assertion_type == DeterministicAssertionType.STATUS_CODE.value:
            self.source = self.source or AssertionSource.STATUS_CODE
            self.operator = self.operator or AssertionOperator.EQ
            self._require_source(AssertionSource.STATUS_CODE)
            self._require_operator(AssertionOperator.EQ)
            if type(self.expected) is not int:
                raise ValueError("Status Code expected 必须是整数，不能是 boolean")
            if not 100 <= self.expected <= 599:
                raise ValueError("Status Code expected 必须在 100～599")
        elif assertion_type == DeterministicAssertionType.JSONPATH_EQUAL.value:
            self.source = self.source or AssertionSource.JSON_BODY
            self.operator = self.operator or AssertionOperator.EQ
            self._require_source(AssertionSource.JSON_BODY, AssertionSource.JSONPATH)
            self._require_expression_jsonpath()
            self._require_operator(AssertionOperator.EQ)
        elif assertion_type == DeterministicAssertionType.CONTAINS.value:
            self.source = self.source or AssertionSource.RESPONSE_TEXT
            self.operator = self.operator or AssertionOperator.CONTAINS
            self._require_source(
                AssertionSource.RESPONSE_TEXT,
                AssertionSource.JSON_BODY,
                AssertionSource.JSONPATH,
                AssertionSource.HEADER,
                AssertionSource.COOKIE,
            )
            if self.source in {
                AssertionSource.JSONPATH,
                AssertionSource.HEADER,
                AssertionSource.COOKIE,
            }:
                self._require_expression()
            self._require_operator(AssertionOperator.CONTAINS)
            if not isinstance(self.expected, str) or not self.expected:
                raise ValueError("Contains expected 必须是非空字符串")
        elif assertion_type == DeterministicAssertionType.REGEX.value:
            self.source = self.source or AssertionSource.RESPONSE_TEXT
            self.operator = self.operator or AssertionOperator.EQ
            self._require_source(
                AssertionSource.RESPONSE_TEXT,
                AssertionSource.JSONPATH,
                AssertionSource.HEADER,
                AssertionSource.COOKIE,
            )
            if self.source != AssertionSource.RESPONSE_TEXT:
                self._require_expression()
            self._require_operator(AssertionOperator.EQ)
            if not isinstance(self.expected, str) or not self.expected:
                raise ValueError("Regex expected 必须是非空正则表达式")
            regex_errors = safe_regex_errors(self.expected)
            if regex_errors:
                raise ValueError(
                    "Regex 模式过于复杂，已拒绝潜在 ReDoS：" + "; ".join(regex_errors[:3])
                )
            try:
                re.compile(self.expected)
            except re.error as exc:
                raise ValueError(f"Regex 模式无效：{exc.msg}") from exc
        elif assertion_type in {
            DeterministicAssertionType.HEADER.value,
            DeterministicAssertionType.COOKIE.value,
        }:
            self.source = self.source or (
                AssertionSource.HEADER
                if assertion_type == DeterministicAssertionType.HEADER.value
                else AssertionSource.COOKIE
            )
            self.operator = self.operator or AssertionOperator.EQ
            expected_source = (
                AssertionSource.HEADER
                if assertion_type == DeterministicAssertionType.HEADER.value
                else AssertionSource.COOKIE
            )
            self._require_source(expected_source)
            self._require_expression()
            self._require_operator(AssertionOperator.EQ, AssertionOperator.CONTAINS)
            if not isinstance(self.expected, str):
                raise ValueError(f"{assertion_type} expected 必须是字符串")
        elif assertion_type == DeterministicAssertionType.JSON_SCHEMA.value:
            self.source = self.source or AssertionSource.JSON_BODY
            self.operator = self.operator or AssertionOperator.EQ
            self._require_source(AssertionSource.JSON_BODY)
            self._require_operator(AssertionOperator.EQ)
            if not isinstance(self.expected, dict):
                raise ValueError("JSON Schema expected 必须是对象")
            schema_errors = schema_pattern_errors(self.expected)
            if schema_errors:
                raise ValueError(
                    "JSON Schema pattern 无效，已拒绝潜在 ReDoS："
                    + "; ".join(schema_errors[:5])
                )
        elif assertion_type == DeterministicAssertionType.RESPONSE_TIME.value:
            self.source = self.source or AssertionSource.RESPONSE_TIME
            self.operator = self.operator or AssertionOperator.LTE
            self._require_source(AssertionSource.RESPONSE_TIME)
            self._require_operator(
                AssertionOperator.EQ,
                AssertionOperator.LT,
                AssertionOperator.LTE,
                AssertionOperator.GT,
                AssertionOperator.GTE,
            )
            if isinstance(self.expected, bool) or not isinstance(self.expected, (int, float)):
                raise ValueError("Response Time expected 必须是数字，不能是 boolean")
            if self.expected < 0:
                raise ValueError("Response Time expected 不能为负数")
        elif assertion_type in {
            DeterministicAssertionType.EXISTS.value,
            DeterministicAssertionType.NOT_EXISTS.value,
        }:
            self.source = self.source or AssertionSource.JSONPATH
            self._require_source(
                AssertionSource.JSONPATH, AssertionSource.HEADER, AssertionSource.COOKIE
            )
            self._require_expression()
            if self.operator is not None:
                self._require_operator(AssertionOperator.EQ)
            if self.expected is not None:
                raise ValueError("Exists / Not Exists 不需要 expected")
        elif assertion_type == DeterministicAssertionType.ARRAY_LENGTH.value:
            self.source = self.source or (
                AssertionSource.JSONPATH if self.expression else AssertionSource.JSON_BODY
            )
            self.operator = self.operator or AssertionOperator.EQ
            self._require_source(AssertionSource.JSON_BODY, AssertionSource.JSONPATH)
            if self.source == AssertionSource.JSONPATH:
                self._require_expression_jsonpath()
            self._require_operator(
                AssertionOperator.EQ,
                AssertionOperator.LT,
                AssertionOperator.LTE,
                AssertionOperator.GT,
                AssertionOperator.GTE,
            )
            if type(self.expected) is not int or self.expected < 0:
                raise ValueError("Array Length expected 必须是非负整数，不能是 boolean")
        elif assertion_type == DeterministicAssertionType.TYPE.value:
            self.source = self.source or (
                AssertionSource.JSONPATH if self.expression else AssertionSource.JSON_BODY
            )
            self.operator = self.operator or AssertionOperator.EQ
            self._require_source(AssertionSource.JSON_BODY, AssertionSource.JSONPATH)
            if self.source == AssertionSource.JSONPATH:
                self._require_expression_jsonpath()
            self._require_operator(AssertionOperator.EQ)
            if self.expected not in _TYPE_EXPECTED_VALUES:
                raise ValueError(
                    f"Type expected 必须是：{', '.join(sorted(_TYPE_EXPECTED_VALUES))}"
                )
        return self

    def _require_source(self, *allowed: AssertionSource) -> None:
        if self.source not in allowed:
            allowed_values = ", ".join(item.value for item in allowed)
            raise ValueError(f"{self.type} source 必须是：{allowed_values}")

    def _require_expression(self) -> None:
        if not self.expression or not self.expression.strip():
            raise ValueError(f"{self.type} expression 不能为空")

    def _require_expression_jsonpath(self) -> None:
        self._require_expression()
        if not self.expression.startswith("$"):
            raise ValueError("JSONPath expression 必须以 $ 开始")

    def _require_operator(self, *allowed: AssertionOperator) -> None:
        if self.operator not in allowed:
            allowed_values = ", ".join(item.value for item in allowed)
            raise ValueError(f"{self.type} operator 必须是：{allowed_values}")


class AssertionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    SKIPPED = "SKIPPED"


class AssertionResult(BaseModel):
    sequence: int = Field(ge=1)
    name: str
    type: str
    status: AssertionStatus
    expected: Any | None = None
    actual: Any | None = None
    message: str
    duration_ms: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None
    ai_call_id: int | None = None
    actual_model: str | None = None
    fallback_used: bool | None = None
    repair_used: bool | None = None

    @model_validator(mode="after")
    def normalize_type(self) -> "AssertionResult":
        self.type = self.type.strip().upper()
        return self


class ActionType(StrEnum):
    SET_VARIABLE = "SET_VARIABLE"
    FAKER = "FAKER"
    SQL_QUERY = "SQL_QUERY"
    PYTHON_SCRIPT = "PYTHON_SCRIPT"
    GET_TOKEN = "GET_TOKEN"
    API_SETUP = "API_SETUP"
    EXTRACT_RESPONSE = "EXTRACT_RESPONSE"
    REGISTER_RESOURCE = "REGISTER_RESOURCE"


class FakerGenerator(StrEnum):
    UUID = "uuid"
    EMAIL = "email"
    USERNAME = "username"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    INTEGER = "integer"
    WORD = "word"
    BOOLEAN = "boolean"


class VariableAction(BaseModel):
    """Legacy SET_VARIABLE payload kept byte-for-byte compatible when serialized."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    value: Any
    enabled: bool = True


class SetVariableAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.SET_VARIABLE] = ActionType.SET_VARIABLE
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    value: Any
    enabled: bool = True


class FakerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.FAKER] = ActionType.FAKER
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    generator: FakerGenerator
    seed: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    enabled: bool = True


class SqlQueryAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.SQL_QUERY] = ActionType.SQL_QUERY
    connection_id: int = Field(gt=0)
    sql: str = Field(min_length=1, max_length=10000)
    params: dict[str, Any] | list[Any] = Field(default_factory=dict)
    result_variable: str = Field(
        default="sql_rows",
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$",
    )
    max_rows: int = Field(default=100, ge=1, le=1000)
    enabled: bool = True


class PythonScriptAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.PYTHON_SCRIPT] = ActionType.PYTHON_SCRIPT
    script: str = Field(min_length=1, max_length=5000)
    enabled: bool = True


class GetTokenAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.GET_TOKEN] = ActionType.GET_TOKEN
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    source: ExtractorSource
    expression: str = Field(min_length=1, max_length=1000)
    required: bool = True
    default_value: Any | None = None
    response: RuntimeResponseSnapshot = Field(default_factory=RuntimeResponseSnapshot)
    request: ApiRequestTemplate | None = None
    enabled: bool = True


class ApiSetupAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.API_SETUP] = ActionType.API_SETUP
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    source: ExtractorSource
    expression: str = Field(min_length=1, max_length=1000)
    required: bool = True
    default_value: Any | None = None
    response: RuntimeResponseSnapshot = Field(default_factory=RuntimeResponseSnapshot)
    request: ApiRequestTemplate
    enabled: bool = True


class ExtractResponseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.EXTRACT_RESPONSE] = ActionType.EXTRACT_RESPONSE
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    source: ExtractorSource
    expression: str = Field(min_length=1, max_length=1000)
    required: bool = True
    default_value: Any | None = None
    enabled: bool = True


class RegisterResourceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ActionType.REGISTER_RESOURCE] = ActionType.REGISTER_RESOURCE
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    resource_type: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    value: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    cleanup: CleanupConfig | None = None
    cleanup_ref: str | None = Field(default=None, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_metadata_security(self) -> "RegisterResourceAction":
        validate_secret_free_payload(self.metadata)
        validate_secret_free_payload(self.value)
        return self


Action = (
    VariableAction
    | SetVariableAction
    | FakerAction
    | SqlQueryAction
    | PythonScriptAction
    | GetTokenAction
    | ApiSetupAction
    | ExtractResponseAction
    | RegisterResourceAction
)

_PRE_ACTION_TYPES = {
    ActionType.SET_VARIABLE,
    ActionType.FAKER,
    ActionType.SQL_QUERY,
    ActionType.PYTHON_SCRIPT,
    ActionType.GET_TOKEN,
    ActionType.API_SETUP,
}
_POST_ACTION_TYPES = {
    ActionType.EXTRACT_RESPONSE,
    ActionType.SET_VARIABLE,
    ActionType.PYTHON_SCRIPT,
    ActionType.REGISTER_RESOURCE,
}


def action_type(action: Action) -> ActionType:
    if isinstance(action, VariableAction):
        return ActionType.SET_VARIABLE
    return action.type


def validate_action_phase(actions: list[Action], phase: str) -> None:
    allowed = _PRE_ACTION_TYPES if phase == "Pre" else _POST_ACTION_TYPES
    for action in actions:
        if action_type(action) not in allowed:
            raise ValueError(f"{phase} Action 不支持类型：{action_type(action).value}")


class SuggestedCase(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    case_type: CaseType
    priority: CasePriority
    preconditions: list[str] = Field(default_factory=list)
    steps: list[SuggestedStep] = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict)
    expected_result: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0, le=1)
    request: ApiRequestTemplate | None = None
    pre_actions: list[Action] = Field(default_factory=list, max_length=100)
    post_actions: list[Action] = Field(default_factory=list, max_length=100)
    extractors: list[ResponseExtractor] = Field(default_factory=list, max_length=200)
    data_source: CaseDataSourceConfig | None = None
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)
    cleanup: list[CleanupConfig] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_actions(self) -> "SuggestedCase":
        validate_action_phase(self.pre_actions, "Pre")
        validate_action_phase(self.post_actions, "Post")
        names = [item.name.strip() for item in self.assertions]
        if len(names) != len(set(names)):
            raise ValueError("断言名称不能重复")
        if self.assertions and self.case_type != CaseType.API:
            raise ValueError("只有 API 用例可以配置 API 断言")
        cleanup_ids = {item.cleanup_id for item in self.cleanup if item.cleanup_id}
        if len(cleanup_ids) != sum(1 for item in self.cleanup if item.cleanup_id):
            raise ValueError("Cleanup ID 不能重复")
        for action in [*self.pre_actions, *self.post_actions]:
            if not isinstance(action, RegisterResourceAction):
                continue
            if action.cleanup_ref and action.cleanup_ref not in cleanup_ids:
                raise ValueError(f"REGISTER_RESOURCE 引用的 Cleanup 不存在：{action.cleanup_ref}")
            if action.cleanup and action.cleanup_ref:
                raise ValueError("REGISTER_RESOURCE 不能同时配置 cleanup 和 cleanup_ref")
        return self


class CaseGenerationResult(BaseModel):
    cases: list[SuggestedCase] = Field(min_length=1, max_length=100)


class ApiCaseIntentScenario(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    BOUNDARY = "BOUNDARY"
    AUTHORIZATION = "AUTHORIZATION"
    RELIABILITY = "RELIABILITY"


class ApiCaseInputStrategy(StrEnum):
    VALID = "VALID"
    OMIT = "OMIT"
    FIXED = "FIXED"
    MINIMUM = "MINIMUM"
    MAXIMUM = "MAXIMUM"
    BELOW_MINIMUM = "BELOW_MINIMUM"
    ABOVE_MAXIMUM = "ABOVE_MAXIMUM"
    EMPTY = "EMPTY"
    INVALID_TYPE = "INVALID_TYPE"


class ApiCaseInputMutation(BaseModel):
    """A semantic input change. The compiler, not the model, owns DSL placement."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=255)
    location: Literal["AUTO", "PATH", "QUERY", "BODY"] = "AUTO"
    strategy: ApiCaseInputStrategy = ApiCaseInputStrategy.VALID
    value: Any | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_fixed_strategy_from_value(cls, value: Any) -> Any:
        """Tolerate compact model output that supplies a value but omits FIXED.

        Several otherwise usable models naturally emit ``{"field": ..., "value": ...}``.
        Treating that as VALID silently discarded the requested value and could turn a
        negative credential test into a successful login.  The platform owns this
        normalization so prompt wording is not a correctness boundary.
        """

        if isinstance(value, dict) and "value" in value and "strategy" not in value:
            return {**value, "strategy": ApiCaseInputStrategy.FIXED.value}
        return value


class ApiCaseExpectedClaim(BaseModel):
    """A response-field claim selected from the OpenAPI response contract."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=500)
    operator: Literal["EQ", "CONTAINS", "EXISTS"] = "EXISTS"
    value: Any | None = None


class ApiCaseIntent(BaseModel):
    """Model-owned test intent without executable request or cleanup DSL."""

    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    checkpoint_keys: list[str] = Field(min_length=1, max_length=30)
    title: str = Field(min_length=2, max_length=255)
    api_definition_id: int = Field(gt=0)
    scenario_type: ApiCaseIntentScenario
    priority: CasePriority = CasePriority.P1
    input_mutations: list[ApiCaseInputMutation] = Field(
        default_factory=list,
        max_length=30,
    )
    expected_status: int = Field(ge=100, le=599)
    expected_claims: list[ApiCaseExpectedClaim] = Field(
        default_factory=list,
        max_length=30,
    )
    auth_mode: Literal["AUTO", "VALID", "OMIT"] = "AUTO"
    retry_on_transient: bool = False
    cleanup_required: bool = False
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def normalize_references(self) -> "ApiCaseIntent":
        self.checkpoint_keys = list(
            dict.fromkeys(item.strip().upper() for item in self.checkpoint_keys)
        )
        if not self.checkpoint_keys:
            raise ValueError("至少需要一个检查点")
        mutation_targets = [
            (item.location, item.field.strip().lower()) for item in self.input_mutations
        ]
        if len(mutation_targets) != len(set(mutation_targets)):
            raise ValueError("同一输入字段不能重复变更")
        return self


class ApiCaseIntentResult(BaseModel):
    """Compact AI output compiled by the platform into CaseGenerationResult."""

    model_config = ConfigDict(extra="forbid")

    intents: list[ApiCaseIntent] = Field(min_length=1, max_length=100)
    gaps: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_case_keys(self) -> "ApiCaseIntentResult":
        keys = [item.case_key for item in self.intents]
        if len(keys) != len(set(keys)):
            raise ValueError("用例意图 case_key 不能重复")
        return self


class TestCheckPoint(BaseModel):
    key: str
    title: str
    source: str


class RecommendedApi(BaseModel):
    api_definition_id: int
    name: str
    method: str
    path: str
    role: str
    required: bool
    reason: str
    check_point_keys: list[str] = Field(default_factory=list)
    selected_by_default: bool


class CaseDesignScopeExclusion(BaseModel):
    requirement_code: str
    requirement_title: str
    verification_type: str
    automation_readiness: str
    reason: str


class CaseDesignPlanResponse(BaseModel):
    requirement_id: int
    requirement_version_id: int
    requirement_version_no: int
    check_points: list[TestCheckPoint]
    recommended_apis: list[RecommendedApi]
    gaps: list[str]
    existing_case_count: int
    source: Literal["AI", "RULE_FALLBACK", "RULE"] = "RULE"
    scope_requirement_count: int = 1
    scope_requirement_total: int = 1
    excluded_requirements: list[CaseDesignScopeExclusion] = Field(default_factory=list)
    platform_completed_checkpoint_count: int = 0
    ignored_ai_reference_count: int = 0


class AiTestCheckPoint(BaseModel):
    key: str = Field(pattern=r"^CP-\d{2,3}$")
    title: str = Field(min_length=2, max_length=100)
    source: str = Field(min_length=1, max_length=255)


class AiRecommendedApiDecision(BaseModel):
    api_definition_id: int = Field(gt=0)
    role: Literal["前置准备", "核心操作", "结果验证", "数据清理"]
    required: bool = False
    reason: str = Field(min_length=2, max_length=500)
    check_point_keys: list[str] = Field(min_length=1, max_length=100)


class AiCaseDesignResult(BaseModel):
    check_points: list[AiTestCheckPoint] = Field(min_length=1, max_length=100)
    recommended_apis: list[AiRecommendedApiDecision] = Field(min_length=1, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=100)


class CaseDesignTaskCreate(BaseModel):
    prompt_id: int = Field(gt=0)
    include_api_ids: list[int] = Field(default_factory=list, max_length=30)
    force_refresh: bool = False


class CaseDesignTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requirement_id: int
    requirement_version_id: int
    requirement_code: str
    requirement_title: str
    requirement_version_no: int
    prompt_id: int
    ai_call_id: int | None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    source: Literal["AI", "RULE_FALLBACK"] | None
    included_api_definition_ids: list[int]
    plan: CaseDesignPlanResponse | None
    error_message: str | None
    reused: bool = False
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class CaseDesignTaskListResponse(BaseModel):
    items: list[CaseDesignTaskResponse]
    total: int
    page: int = 1
    page_size: int = 10
    active_count: int = 0


class CaseGenerationRequest(BaseModel):
    prompt_id: int = Field(gt=0)
    additional_instructions: str | None = Field(default=None, max_length=5000)
    selected_api_definition_ids: list[int] | None = Field(default=None, max_length=30)
    design_task_id: int | None = Field(default=None, gt=0)


class CaseGenerationTaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CaseGenerationTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requirement_id: int
    requirement_version_id: int
    prompt_id: int
    generation_id: int | None
    status: CaseGenerationTaskStatus
    additional_instructions: str | None
    selected_api_definition_ids: list[int]
    coverage_plan: dict
    error_code: str | None
    error_message: str | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class CaseGenerationTaskListResponse(BaseModel):
    items: list[CaseGenerationTaskResponse]
    total: int


class SuggestionStatus(StrEnum):
    DRAFT = "DRAFT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class CaseSuggestionEdit(BaseModel):
    human_result: SuggestedCase
    decision_note: str | None = Field(default=None, max_length=500)


class SuggestionDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class CaseSuggestionDecision(BaseModel):
    action: SuggestionDecision
    decision_note: str | None = Field(default=None, max_length=500)


class CaseSuggestionBulkDecision(BaseModel):
    suggestion_ids: list[int] = Field(min_length=1, max_length=100)
    action: SuggestionDecision
    decision_note: str | None = Field(default=None, max_length=500)


class CaseSuggestionBulkEdit(BaseModel):
    suggestion_ids: list[int] = Field(min_length=1, max_length=100)
    priority: CasePriority | None = None
    tags: list[str] | None = Field(default=None, max_length=50)
    requirement_ids: list[int] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_change(self) -> "CaseSuggestionBulkEdit":
        if self.priority is None and self.tags is None and self.requirement_ids is None:
            raise ValueError("至少需要提交 Priority、Tag 或 Requirement 关联中的一项")
        if self.requirement_ids is not None and any(item <= 0 for item in self.requirement_ids):
            raise ValueError("Requirement ID 必须为正整数")
        if self.tags is not None:
            normalized = [item.strip() for item in self.tags if item.strip()]
            if any(len(item) > 64 for item in normalized):
                raise ValueError("Tag 长度不能超过 64")
            self.tags = list(dict.fromkeys(normalized))
        if self.requirement_ids is not None:
            self.requirement_ids = list(dict.fromkeys(self.requirement_ids))
        return self


class CaseSuggestionBulkDeleteResponse(BaseModel):
    deleted_count: int


class CaseSuggestionBulkDeleteRequest(BaseModel):
    suggestion_ids: list[int] = Field(min_length=1, max_length=100)


class CaseSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    generation_id: int
    sequence_no: int
    status: SuggestionStatus
    structured_result: SuggestedCase
    human_result: SuggestedCase | None
    linked_requirement_ids: list[int]
    decision_note: str | None
    test_case_id: int | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class CaseGenerationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requirement_id: int
    requirement_code: str
    requirement_title: str
    requirement_version_id: int
    requirement_version_no: int
    ai_call_id: int
    actual_model: str
    prompt_version_id: int
    prompt_name: str
    prompt_version_no: int
    output_schema_id: int | None
    output_schema_name: str | None
    output_schema_version_no: int | None
    ai_confidence_by_sequence: dict[int, float] = Field(default_factory=dict)
    fallback_used: bool
    repair_used: bool
    additional_instructions: str | None
    selected_api_definition_ids: list[int]
    coverage_plan: dict
    raw_response: str
    structured_result: CaseGenerationResult
    created_by: str
    created_at: datetime
    suggestions: list[CaseSuggestionResponse] = Field(default_factory=list)


class CaseGenerationListResponse(BaseModel):
    items: list[CaseGenerationResponse]
    total: int


class CaseRecompileItem(BaseModel):
    suggestion_id: int
    case_code: str | None = None
    version_no: int | None = None
    status: Literal["RECOMPILED", "SKIPPED", "FAILED"]
    message: str


class CaseGenerationRecompileResponse(BaseModel):
    generation_id: int
    compiler_version: str
    recompiled_count: int
    skipped_count: int
    failed_count: int
    items: list[CaseRecompileItem]


class TestCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    api_definition_id: int | None
    code: str
    name: str
    case_type: CaseType
    status: str
    source: str
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class TestCaseVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    version_no: int
    content: SuggestedCase
    change_note: str | None
    created_by: str
    created_at: datetime


class TestCaseDetailResponse(TestCaseResponse):
    current_version: TestCaseVersionResponse | None = None


class TestCaseCreate(BaseModel):
    project_id: int = Field(gt=0)
    content: SuggestedCase
    requirement_id: int | None = Field(default=None, gt=0)
    requirement_ids: list[int] = Field(default_factory=list, max_length=100)
    change_note: str | None = Field(default="人工创建", max_length=500)

    @model_validator(mode="after")
    def normalize_requirement_ids(self) -> "TestCaseCreate":
        ids = list(dict.fromkeys(self.requirement_ids))
        if any(item <= 0 for item in ids):
            raise ValueError("关联需求编号必须为正整数")
        if self.requirement_id is not None and self.requirement_id not in ids:
            ids.insert(0, self.requirement_id)
        if len(ids) > 100:
            raise ValueError("单个测试用例最多关联 100 条需求")
        self.requirement_ids = ids
        return self


class TestCaseVersionCreate(BaseModel):
    content: SuggestedCase
    change_note: str = Field(min_length=1, max_length=500)


class RuntimePreviewRequest(BaseModel):
    request: ApiRequestTemplate
    project_id: int | None = Field(default=None, gt=0)
    context: dict[str, Any] = Field(default_factory=dict)
    pre_actions: list[Action] = Field(default_factory=list, max_length=100)
    response: RuntimeResponseSnapshot = Field(default_factory=RuntimeResponseSnapshot)
    extractors: list[ResponseExtractor] = Field(default_factory=list, max_length=200)
    post_actions: list[Action] = Field(default_factory=list, max_length=100)
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_actions(self) -> "RuntimePreviewRequest":
        validate_action_phase(self.pre_actions, "Pre")
        validate_action_phase(self.post_actions, "Post")
        has_sql_query = any(
            action_type(action) == ActionType.SQL_QUERY for action in self.pre_actions
        )
        if has_sql_query and self.project_id is None:
            raise ValueError("SQL_QUERY 预览必须提供 project_id")
        if (
            any(item.kind == AssertionKind.AI_SEMANTIC for item in self.assertions)
            and self.project_id is None
        ):
            raise ValueError("AI 断言预览必须提供 project_id")
        return self


class RuntimeActionTrace(BaseModel):
    sequence: int = Field(ge=1)
    phase: Literal["PRE", "REQUEST", "POST"]
    action_type: str = Field(min_length=1, max_length=100)
    status: Literal["PASSED", "SKIPPED", "FAILED"]
    detail: dict[str, Any] = Field(default_factory=dict)


class RuntimePreviewResponse(BaseModel):
    rendered_request: dict[str, Any]
    context: dict[str, Any]
    extracted: dict[str, Any]
    traces: list[RuntimeActionTrace] = Field(default_factory=list)
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    final_status: Literal["PASS", "FAIL", "REVIEW"] = "PASS"


class RequirementCaseLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    requirement_version_id: int | None
    asset_type: Literal["TEST_CASE"]
    case_type: CaseType
    case_id: int
    case_version_id: int | None
    relation_type: str
    source: str
    confidence: float
    status: Literal["ACTIVE"]
    created_by: str | None
    created_at_time_basis: Literal["UTC", "LEGACY_UNKNOWN"]
    created_at: datetime

    @model_validator(mode="after")
    def expose_reliable_utc_offset(self) -> "RequirementCaseLinkResponse":
        if self.created_at_time_basis == "UTC":
            self.created_at = to_utc_aware(self.created_at)
        return self
