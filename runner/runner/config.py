"""Runner 非敏感配置与路径约定。"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from runner.errors import ConfigurationError


def normalize_backend_url(value: str) -> str:
    """校验并规范化后端地址，避免把凭证放进 URL。"""

    if not isinstance(value, str):
        raise ConfigurationError("backend URL 必须是字符串")
    raw = value.strip()
    if not raw:
        raise ConfigurationError("backend URL 不能为空")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("backend URL 必须使用 http 或 https")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("backend URL 不允许携带用户名或密码")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("backend URL 不允许携带 query 或 fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def normalize_tags(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """按后端 Runner Schema 的规则去重并规范化 tags。"""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        if not isinstance(value, str):
            raise ConfigurationError("Runner tag 必须是字符串")
        tag = value.strip().lower()
        if not tag or len(tag) > 64:
            raise ConfigurationError("Runner tag 长度无效")
        if not all(character.isalnum() or character in "_.-" for character in tag):
            raise ConfigurationError("Runner tag 仅支持字母、数字、下划线、短横线和点")
        if tag not in seen:
            normalized.append(tag)
            seen.add(tag)
    return tuple(normalized)


def default_tags() -> tuple[str, ...]:
    return ("windows",) if platform.system().lower() == "windows" else ()


def default_state_dir() -> Path:
    """返回当前用户范围的状态目录，不使用项目目录保存凭证。"""

    if os.name == "nt":
        app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "AI Native Test Platform" / "runner"
    return Path.home() / ".local" / "share" / "ai-native-test-platform" / "runner"


@dataclass(frozen=True)
class RunnerConfig:
    """不包含 registration token 或 credential 的 Runner 配置。"""

    backend_url: str
    name: str
    tags: tuple[str, ...] = ()
    api_slots: int = 1
    web_slots: int = 0
    performance_slots: int = 0
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    max_attempts: int = 4
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8.0

    def __post_init__(self) -> None:
        normalized_url = normalize_backend_url(self.backend_url)
        normalized_name = self.name.strip() if isinstance(self.name, str) else ""
        if not normalized_name or len(normalized_name) > 128:
            raise ConfigurationError("Runner name 不能为空且不能超过 128 个字符")
        object.__setattr__(self, "backend_url", normalized_url)
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "tags", normalize_tags(self.tags))
        for field_name in ("api_slots", "web_slots", "performance_slots"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10000:
                raise ConfigurationError(f"{field_name} 必须在 0 到 10000 之间")
        if not 0.1 <= self.connect_timeout_seconds <= 120:
            raise ConfigurationError("connect timeout 必须在 0.1 到 120 秒之间")
        if not 0.1 <= self.read_timeout_seconds <= 600:
            raise ConfigurationError("read timeout 必须在 0.1 到 600 秒之间")
        if not 1 <= self.max_attempts <= 5:
            raise ConfigurationError("最大请求次数必须在 1 到 5 次之间")
        if not 0 <= self.backoff_base_seconds <= 60:
            raise ConfigurationError("退避基数必须在 0 到 60 秒之间")
        if not 0 <= self.backoff_max_seconds <= 300:
            raise ConfigurationError("退避上限必须在 0 到 300 秒之间")

    def to_mapping(self) -> dict[str, object]:
        return {
            "version": 1,
            "backend_url": self.backend_url,
            "name": self.name,
            "tags": list(self.tags),
            "api_slots": self.api_slots,
            "web_slots": self.web_slots,
            "performance_slots": self.performance_slots,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "read_timeout_seconds": self.read_timeout_seconds,
            "max_attempts": self.max_attempts,
            "backoff_base_seconds": self.backoff_base_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
        }

    @classmethod
    def from_mapping(cls, value: object) -> RunnerConfig:
        if not isinstance(value, dict):
            raise ConfigurationError("Runner 配置必须是 JSON 对象")
        try:
            return cls(
                backend_url=value["backend_url"],
                name=value["name"],
                tags=tuple(value.get("tags", ())),
                api_slots=value.get("api_slots", 1),
                web_slots=value.get("web_slots", 0),
                performance_slots=value.get("performance_slots", 0),
                connect_timeout_seconds=value.get("connect_timeout_seconds", 5.0),
                read_timeout_seconds=value.get("read_timeout_seconds", 15.0),
                max_attempts=value.get("max_attempts", 4),
                backoff_base_seconds=value.get("backoff_base_seconds", 0.5),
                backoff_max_seconds=value.get("backoff_max_seconds", 8.0),
            )
        except KeyError as exc:
            raise ConfigurationError("Runner 配置缺少必要字段") from exc
