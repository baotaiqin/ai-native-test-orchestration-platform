import base64
import binascii
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.time import to_utc_isoformat
from app.modules.resource_registry.schemas import validate_secret_free_payload

_TEMPLATE = re.compile(r"\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}")
_SECRET_TEMPLATE = re.compile(r"^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$")
_SAFE_NAME = r"^[A-Za-z][A-Za-z0-9_.-]*$"
_WEB_TAB_ALIAS = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"


def _normalize_http_url_or_template(value: str) -> str:
    normalized = value.strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        raise ValueError("Web URL 无效")
    without_templates = _TEMPLATE.sub("", normalized)
    if "{{" in without_templates or "}}" in without_templates:
        raise ValueError("Web URL 模板格式无效")
    if normalized.startswith("{{"):
        if _TEMPLATE.match(normalized) is None:
            raise ValueError("Web URL 模板格式无效")
        return normalized
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("Web URL 格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Web URL 必须使用 http 或 https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Web URL 不允许携带用户名或密码")
    return normalized


class WebCaseStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class WebCaseVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class WebAssetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class LocatorStrategy(StrEnum):
    CSS = "css"
    XPATH = "xpath"
    TEXT = "text"
    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEST_ID = "test_id"


class LocatorSource(StrEnum):
    MANUAL = "MANUAL"
    IMPORTED = "IMPORTED"
    HEALED = "HEALED"


class WebFailurePolicy(StrEnum):
    STOP = "STOP"
    CONTINUE = "CONTINUE"


class WebLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: LocatorStrategy | None = None
    value: str | None = Field(default=None, min_length=1, max_length=2000)
    element_version_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_direct_or_reference(self) -> "WebLocator":
        direct = self.strategy is not None or self.value is not None
        reference = self.element_version_id is not None
        if direct == reference or (direct and (self.strategy is None or self.value is None)):
            raise ValueError("Locator 必须且只能是完整 direct locator 或 element_version_id 引用")
        return self


class _WebActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_ms: int = Field(default=30000, ge=100, le=600000)
    failure_policy: WebFailurePolicy = WebFailurePolicy.STOP


class GotoAction(_WebActionBase):
    type: Literal["GOTO"]
    url: str = Field(min_length=1, max_length=2048)


class ReloadAction(_WebActionBase):
    type: Literal["RELOAD"]


class BackAction(_WebActionBase):
    type: Literal["BACK"]


class ForwardAction(_WebActionBase):
    type: Literal["FORWARD"]


class FillAction(_WebActionBase):
    type: Literal["FILL"]
    locator: WebLocator
    value: str = Field(max_length=10000)


class ClickAction(_WebActionBase):
    type: Literal["CLICK"]
    locator: WebLocator


class DoubleClickAction(_WebActionBase):
    type: Literal["DOUBLE_CLICK"]
    locator: WebLocator


class RightClickAction(_WebActionBase):
    type: Literal["RIGHT_CLICK"]
    locator: WebLocator


class ClearAction(_WebActionBase):
    type: Literal["CLEAR"]
    locator: WebLocator


class HoverAction(_WebActionBase):
    type: Literal["HOVER"]
    locator: WebLocator


class DragDropAction(_WebActionBase):
    type: Literal["DRAG_DROP"]
    locator: WebLocator
    value: str = Field(min_length=1, max_length=2000)


class UploadAction(_WebActionBase):
    type: Literal["UPLOAD"]
    locator: WebLocator
    key: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=1_400_000, repr=False)

    @model_validator(mode="after")
    def validate_managed_file(self) -> "UploadAction":
        if self.key in {".", ".."} or "/" in self.key or "\\" in self.key:
            raise ValueError("UPLOAD 文件名无效")
        try:
            content = base64.b64decode(self.value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("UPLOAD 文件内容必须是有效 Base64") from exc
        if not content or len(content) > 1024 * 1024:
            raise ValueError("UPLOAD 文件必须为 1～1048576 bytes")
        return self


class DownloadAction(_WebActionBase):
    type: Literal["DOWNLOAD"]
    locator: WebLocator
    value: str = Field(default="", max_length=255)


class SelectAction(_WebActionBase):
    type: Literal["SELECT"]
    locator: WebLocator
    value: str = Field(min_length=1, max_length=2000)


class CheckAction(_WebActionBase):
    type: Literal["CHECK"]
    locator: WebLocator


class UncheckAction(_WebActionBase):
    type: Literal["UNCHECK"]
    locator: WebLocator


class RadioAction(_WebActionBase):
    type: Literal["RADIO"]
    locator: WebLocator


class PressAction(_WebActionBase):
    type: Literal["PRESS"]
    locator: WebLocator
    key: str = Field(min_length=1, max_length=64)


class EnterAction(_WebActionBase):
    type: Literal["ENTER"]
    locator: WebLocator


class TabAction(_WebActionBase):
    type: Literal["TAB"]
    locator: WebLocator


class WaitElementAction(_WebActionBase):
    type: Literal["WAIT_ELEMENT"]
    locator: WebLocator


class WaitUrlAction(_WebActionBase):
    type: Literal["WAIT_URL"]
    url: str = Field(min_length=1, max_length=2048)


class WaitNetworkIdleAction(_WebActionBase):
    type: Literal["WAIT_NETWORK_IDLE"]


class WaitTimeAction(_WebActionBase):
    type: Literal["WAIT_TIME"]
    value: str = Field(pattern=r"^[1-9][0-9]{0,5}$")

    @field_validator("value")
    @classmethod
    def validate_duration(cls, value: str) -> str:
        if int(value) > 600_000:
            raise ValueError("WAIT_TIME 时长不能超过 600000ms")
        return value


class WaitTextAction(_WebActionBase):
    type: Literal["WAIT_TEXT"]
    locator: WebLocator
    value: str = Field(min_length=1, max_length=10000)


class CookieAction(_WebActionBase):
    type: Literal["COOKIE"]
    key: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=10000)


class LocalStorageAction(_WebActionBase):
    type: Literal["LOCAL_STORAGE"]
    key: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=10000)


class SessionStorageAction(_WebActionBase):
    type: Literal["SESSION_STORAGE"]
    key: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=10000)


class JsEvalAction(_WebActionBase):
    type: Literal["JS_EVAL"]
    value: str = Field(min_length=1, max_length=10000)


class _WebTabActionBase(_WebActionBase):
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)
    value: str = Field(min_length=1, max_length=64, pattern=_WEB_TAB_ALIAS)


class NewTabAction(_WebTabActionBase):
    type: Literal["NEW_TAB"]
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _normalize_http_url_or_template(value)

    @field_validator("value")
    @classmethod
    def reject_initial_alias(cls, value: str) -> str:
        if value == "main":
            raise ValueError("NEW_TAB 不能使用初始页别名 main")
        return value


class SwitchTabAction(_WebTabActionBase):
    type: Literal["SWITCH_TAB"]


class CloseTabAction(_WebTabActionBase):
    type: Literal["CLOSE_TAB"]


WebAction = (
    GotoAction
    | ReloadAction
    | BackAction
    | ForwardAction
    | FillAction
    | ClickAction
    | DoubleClickAction
    | RightClickAction
    | ClearAction
    | HoverAction
    | DragDropAction
    | UploadAction
    | DownloadAction
    | SelectAction
    | CheckAction
    | UncheckAction
    | RadioAction
    | PressAction
    | EnterAction
    | TabAction
    | WaitElementAction
    | WaitUrlAction
    | WaitNetworkIdleAction
    | WaitTimeAction
    | WaitTextAction
    | CookieAction
    | LocalStorageAction
    | SessionStorageAction
    | JsEvalAction
    | NewTabAction
    | SwitchTabAction
    | CloseTabAction
)


class AssertVisible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_VISIBLE"]
    locator: WebLocator
    timeout_ms: int = Field(default=30000, ge=100, le=600000)


class AssertExists(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_EXISTS"]
    locator: WebLocator
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_ENABLED"]
    locator: WebLocator
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertHidden(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_HIDDEN"]
    locator: WebLocator
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertClickable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_CLICKABLE"]
    locator: WebLocator
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_TEXT"]
    locator: WebLocator
    expected: str = Field(max_length=10000)
    timeout_ms: int = Field(default=30000, ge=100, le=600000)


class AssertTextEqual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_TEXT_EQUAL"]
    locator: WebLocator
    expected: str = Field(max_length=10000)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertInputValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_INPUT_VALUE"]
    locator: WebLocator
    expected: str = Field(max_length=10000)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertUrl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_URL"]
    expected: str = Field(min_length=1, max_length=2048)
    timeout_ms: int = Field(default=30000, ge=100, le=600000)


class AssertTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_TITLE"]
    expected: str = Field(max_length=10000)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_ATTRIBUTE"]
    locator: WebLocator
    key: str = Field(min_length=1, max_length=256)
    expected: str = Field(max_length=10000)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertElementCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_ELEMENT_COUNT"]
    locator: WebLocator
    expected: str = Field(pattern=r"^(0|[1-9][0-9]{0,5})$")
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertDownloadSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_DOWNLOAD_SUCCESS"]
    expected: str = Field(default="", max_length=255)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertNetworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_NETWORK_REQUEST"]
    expected: str = Field(min_length=1, max_length=2048)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)


class AssertScreenshotVisualCompare(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_SCREENSHOT_VISUAL_COMPARE"]
    expected: str = Field(min_length=1, max_length=1_400_000, repr=False)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)

    @field_validator("expected")
    @classmethod
    def validate_baseline_png(cls, value: str) -> str:
        try:
            content = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Screenshot baseline 必须是有效 Base64") from exc
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 1024 * 1024:
            raise ValueError("Screenshot baseline 必须是 1 MiB 以内 PNG")
        return value


class AssertAiSemantic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ASSERT_AI_SEMANTIC"]
    prompt_id: int = Field(gt=0, strict=True)
    criteria: str = Field(min_length=1, max_length=4000)
    confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    timeout_ms: int = Field(default=30000, ge=100, le=600000, strict=True)

    @field_validator("criteria")
    @classmethod
    def validate_criteria(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != value:
            raise ValueError("AI Semantic criteria 必须已 trim")
        return value


WebAssertion = (
    AssertVisible
    | AssertExists
    | AssertEnabled
    | AssertHidden
    | AssertClickable
    | AssertText
    | AssertTextEqual
    | AssertInputValue
    | AssertUrl
    | AssertTitle
    | AssertAttribute
    | AssertElementCount
    | AssertDownloadSuccess
    | AssertNetworkRequest
    | AssertScreenshotVisualCompare
    | AssertAiSemantic
)


class WebProxyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=256, repr=False)
    password: str | None = Field(default=None, max_length=256, repr=False)

    @field_validator("server")
    @classmethod
    def validate_server(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != value or "\r" in value or "\n" in value:
            raise ValueError("Proxy server 无效")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("Proxy server 无效") from exc
        if parsed.scheme.lower() not in {"http", "https", "socks5"} or not parsed.netloc:
            raise ValueError("Proxy server 必须使用 http、https 或 socks5")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Proxy server 不允许内嵌认证信息")
        return value

    @field_validator("username", "password")
    @classmethod
    def validate_secret_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _SECRET_TEMPLATE.fullmatch(value) is None:
            raise ValueError("Proxy 认证信息必须使用 {{secret.NAME}}")
        return value

    @model_validator(mode="after")
    def validate_credentials_pair(self) -> "WebProxyConfig":
        if (self.username is None) != (self.password is None):
            raise ValueError("Proxy username/password 必须同时配置")
        return self


class WebBrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_width: int = Field(default=1280, ge=320, le=7680, strict=True)
    window_height: int = Field(default=720, ge=240, le=4320, strict=True)
    language: str | None = Field(
        default=None,
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    )
    user_agent: str | None = Field(default=None, min_length=1, max_length=512)
    proxy: WebProxyConfig | None = None
    download_path: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or "\r" in value or "\n" in value):
            raise ValueError("User-Agent 无效")
        return value

    @field_validator("download_path")
    @classmethod
    def validate_download_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            value != value.strip()
            or "\\" in value
            or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
        ):
            raise ValueError("Download path 必须是安全相对路径")
        return value


class WebCaseContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_url: str = Field(min_length=1, max_length=2048)
    actions: list[WebAction] = Field(min_length=1, max_length=200)
    natural_language_steps: list[str] = Field(default_factory=list, max_length=200)
    assertions: list[WebAssertion] = Field(default_factory=list, max_length=100)
    session_profile_id: int | None = Field(default=None, gt=0)
    browser: Literal["CHROME"] = "CHROME"
    headless: bool = True
    browser_config: WebBrowserConfig = Field(default_factory=WebBrowserConfig)
    total_timeout_ms: int = Field(default=900000, ge=1000, le=86_400_000)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=100)

    @field_validator("natural_language_steps")
    @classmethod
    def normalize_natural_language_steps(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            step = value.strip()
            if not step:
                raise ValueError("自然语言步骤不能包含空白项")
            if len(step) > 2000:
                raise ValueError("单个自然语言步骤不能超过 2000 个字符")
            normalized.append(step)
        return normalized

    @field_validator("start_url")
    @classmethod
    def validate_url_template(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or "{{" in value
            and not all(_TEMPLATE.fullmatch(item) for item in re.findall(r"\{\{[^}]+\}\}", value))
        ):
            raise ValueError("Web Case start_url 模板格式无效")
        return value

    @model_validator(mode="after")
    def validate_payload_size_and_safety(self) -> "WebCaseContent":
        payload = self.model_dump(mode="python")
        # Login flows may reference a managed Secret by name, but the immutable
        # Web DSL must never contain the resolved value or embed it in text.
        for action in payload.get("actions", []):
            value = action.get("value") if isinstance(action, dict) else None
            if isinstance(value, str) and _SECRET_TEMPLATE.fullmatch(value):
                action["value"] = "[MANAGED_SECRET_REFERENCE]"
        browser_config = payload.get("browser_config")
        proxy = browser_config.get("proxy") if isinstance(browser_config, dict) else None
        if isinstance(proxy, dict):
            for field_name in ("username", "password"):
                value = proxy.get(field_name)
                if value is None or (
                    isinstance(value, str) and _SECRET_TEMPLATE.fullmatch(value)
                ):
                    proxy.pop(field_name, None)
        try:
            validate_secret_free_payload(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("Web Case 内容不能包含 Secret 或凭据") from exc
        encoded = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), default=str
        )
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("Web Case Version 内容不能超过 2MB")
        return self


class WebCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=255)
    content: WebCaseContent
    change_note: str | None = Field(default="创建 Web Case", max_length=500)


class WebCaseVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: WebCaseContent
    change_note: str = Field(min_length=1, max_length=500)


class WebCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=255)


class WebCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    code: str
    name: str
    status: WebCaseStatus
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebCaseVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    web_case_id: int
    version_no: int
    content: WebCaseContent
    change_note: str | None
    status: WebCaseVersionStatus
    approved_by: str | None
    approved_at: datetime | None
    created_by: str
    created_at: datetime

    @field_serializer("approved_at", "created_at", when_used="json")
    def serialize_date(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class WebCaseDetailResponse(WebCaseResponse):
    current_version: WebCaseVersionResponse | None = None


class WebCaseListResponse(BaseModel):
    items: list[WebCaseResponse]
    total: int


class WebPageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    code: str = Field(min_length=2, max_length=64, pattern=_SAFE_NAME)
    name: str = Field(min_length=2, max_length=255)
    url_pattern: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=2000)


class WebPageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=255)
    url_pattern: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=2000)


class WebPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    code: str
    name: str
    url_pattern: str | None
    description: str | None
    status: WebAssetStatus
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebElementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    page_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    element_type: str = Field(default="OTHER", min_length=1, max_length=32)


class WebElementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    element_type: str | None = Field(default=None, min_length=1, max_length=32)


class ElementLocatorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: LocatorStrategy
    value: str = Field(min_length=1, max_length=2000)
    priority: int = Field(ge=1, le=20)
    source: LocatorSource = LocatorSource.MANUAL


class WebElementVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=1000)
    element_type: str = Field(default="OTHER", min_length=1, max_length=32)
    locators: list[ElementLocatorCreate] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_priorities(self) -> "WebElementVersionCreate":
        priorities = [item.priority for item in self.locators]
        if len(priorities) != len(set(priorities)):
            raise ValueError("同一 Element Version 的 Locator priority 必须唯一")
        return self


class ElementLocatorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    element_version_id: int
    strategy: LocatorStrategy
    value: str
    priority: int
    source: LocatorSource


class WebElementVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    element_id: int
    version_no: int
    description: str | None
    element_type: str
    locators: list[ElementLocatorResponse]
    created_by: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_date(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class WebElementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    page_id: int
    name: str
    description: str | None
    element_type: str
    status: WebAssetStatus
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return to_utc_isoformat(value) or ""


class SessionProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    environment_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=2, max_length=128, pattern=_SAFE_NAME)
    storage_state: dict[str, Any] = Field(min_length=1, max_length=100)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)
    expires_at: datetime | None = None
    refresh_ttl_seconds: int = Field(default=86400, ge=60, le=2_592_000)
    login_web_case_id: int | None = Field(default=None, gt=0)
    login_web_case_version_id: int | None = Field(default=None, gt=0)
    expiry_condition: "SessionRecoveryCondition | None" = None
    success_condition: "SessionRecoveryCondition | None" = None

    @model_validator(mode="after")
    def validate_storage_state(self) -> "SessionProfileCreate":
        recovery = (
            self.login_web_case_id,
            self.login_web_case_version_id,
            self.expiry_condition,
            self.success_condition,
        )
        if any(item is not None for item in recovery) and not all(
            item is not None for item in recovery
        ):
            raise ValueError("Session 恢复必须同时配置登录 Case/Version、失效条件和成功条件")
        try:
            validate_secret_free_payload(self.metadata)
        except (TypeError, ValueError) as exc:
            raise ValueError("Session Profile metadata 不能包含敏感数据") from exc
        encoded = json.dumps(
            self.storage_state, ensure_ascii=False, separators=(",", ":"), default=str
        )
        if len(encoded.encode("utf-8")) > 1_000_000:
            raise ValueError("Session Profile Storage State 不能超过 1MB")
        return self


class SessionProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=128, pattern=_SAFE_NAME)
    storage_state: dict[str, Any] | None = Field(
        default=None, min_length=1, max_length=100, repr=False
    )
    metadata: dict[str, str] | None = Field(default=None, max_length=20)
    expires_at: datetime | None = None
    refresh_ttl_seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    status: WebAssetStatus | None = None
    login_web_case_id: int | None = Field(default=None, gt=0)
    login_web_case_version_id: int | None = Field(default=None, gt=0)
    expiry_condition: "SessionRecoveryCondition | None" = None
    success_condition: "SessionRecoveryCondition | None" = None

    @model_validator(mode="after")
    def validate_update(self) -> "SessionProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("Session Profile 更新至少需要一个字段")
        if "storage_state" in self.model_fields_set and self.storage_state is None:
            raise ValueError("更新 storage_state 时不能为 null")
        recovery_fields = {
            "login_web_case_id",
            "login_web_case_version_id",
            "expiry_condition",
            "success_condition",
        }
        supplied = recovery_fields & self.model_fields_set
        if supplied and supplied != recovery_fields:
            raise ValueError("更新 Session 恢复配置时必须同时提交全部四个字段")
        if supplied:
            recovery = (
                self.login_web_case_id,
                self.login_web_case_version_id,
                self.expiry_condition,
                self.success_condition,
            )
            if any(item is not None for item in recovery) and not all(
                item is not None for item in recovery
            ):
                raise ValueError("Session 恢复配置必须完整或全部清空")
        if self.metadata is not None:
            try:
                validate_secret_free_payload(self.metadata)
            except (TypeError, ValueError) as exc:
                raise ValueError("Session Profile metadata 不能包含敏感数据") from exc
        if self.storage_state is not None:
            encoded = json.dumps(
                self.storage_state, ensure_ascii=False, separators=(",", ":"), default=str
            )
            if len(encoded.encode("utf-8")) > 1_000_000:
                raise ValueError("Session Profile Storage State 不能超过 1MB")
        return self


class SessionProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    environment_id: int | None
    name: str
    status: WebAssetStatus
    metadata: dict[str, str] | None = Field(
        default=None,
        validation_alias="profile_metadata",
        serialization_alias="metadata",
    )
    expires_at: datetime | None
    storage_state_fingerprint: str
    revision: int
    refresh_ttl_seconds: int
    login_web_case_id: int | None
    login_web_case_version_id: int | None
    expiry_condition: "SessionRecoveryCondition | None"
    success_condition: "SessionRecoveryCondition | None"
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("expires_at", "created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return to_utc_isoformat(value)


class SessionRecoveryCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["URL_EQUALS", "URL_CONTAINS", "LOCATOR_VISIBLE", "LOCATOR_HIDDEN"]
    value: str | None = Field(default=None, min_length=1, max_length=2048)
    locator: WebLocator | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "SessionRecoveryCondition":
        if self.type.startswith("URL_"):
            if self.value is None or self.locator is not None:
                raise ValueError("URL 条件必须且只能配置 value")
        elif self.locator is None or self.value is not None:
            raise ValueError("Locator 条件必须且只能配置 locator")
        return self


SessionProfileCreate.model_rebuild()
SessionProfileUpdate.model_rebuild()
SessionProfileResponse.model_rebuild()
