"""RabbitMQ 配置与 Pika 连接适配。"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from runner.errors import ConfigurationError, TransportError

RABBITMQ_URL_ENV = "AI_TEST_RABBITMQ_URL"


@dataclass(frozen=True, repr=False)
class RabbitMqConfig:
    """只在进程内保存 RabbitMQ URL；不得写入 Runner state。"""

    url: str = field(repr=False)
    redacted_url: str = field(init=False)

    def __post_init__(self) -> None:
        raw = self.url.strip() if isinstance(self.url, str) else ""
        if not raw or len(raw) > 2048:
            raise ConfigurationError("RabbitMQ URL 为空或长度无效")
        try:
            parsed = urlsplit(raw)
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ConfigurationError("RabbitMQ URL 格式无效") from exc
        if scheme not in {"amqp", "amqps"} or not parsed.netloc or not hostname:
            raise ConfigurationError("RabbitMQ URL 必须使用 amqp 或 amqps")
        if parsed.fragment:
            raise ConfigurationError("RabbitMQ URL 不允许携带 fragment")
        object.__setattr__(self, "url", raw)
        object.__setattr__(self, "redacted_url", _redact_url(parsed, scheme, hostname))

    @classmethod
    def from_environment(cls, explicit_url: str | None = None) -> RabbitMqConfig:
        value = explicit_url if explicit_url is not None else os.environ.get(RABBITMQ_URL_ENV)
        if not value:
            raise ConfigurationError(f"未设置 {RABBITMQ_URL_ENV}")
        return cls(value)

    def __repr__(self) -> str:
        return f"RabbitMqConfig(redacted_url={self.redacted_url!r})"


def _redact_url(parsed: object, scheme: str, hostname: str) -> str:
    parsed_url = parsed
    port = getattr(parsed_url, "port", None)
    host = f"[{hostname}]" if ":" in hostname else hostname
    port_text = f":{port}" if port is not None else ""
    path = getattr(parsed_url, "path", "") or "/"
    return f"{scheme}://{host}{port_text}{path}"


class PikaBroker:
    """可注入连接工厂的 Pika BlockingConnection 适配器。"""

    def __init__(
        self,
        config: RabbitMqConfig,
        *,
        connection_factory: Callable[[RabbitMqConfig], object] | None = None,
    ) -> None:
        self.config = config
        self._connection_factory = connection_factory

    def connect(self) -> object:
        if self._connection_factory is not None:
            return self._connection_factory(self.config)
        try:
            import pika
        except ImportError as exc:
            raise ConfigurationError("Runner 未安装 pika 依赖，无法连接 RabbitMQ") from exc
        try:
            return pika.BlockingConnection(pika.URLParameters(self.config.url))
        except Exception as exc:
            raise TransportError(
                "RabbitMQ 连接失败",
                transient=is_transient_rabbitmq_error(exc),
            ) from exc


def is_transient_rabbitmq_error(error: BaseException) -> bool:
    """识别可安全重建连接/通道的 Pika 故障，不把认证/协议错误当成瞬时故障。"""

    if isinstance(error, TransportError):
        return error.transient

    # BlockingConnection 在 TCP 建连窗口中可能直接传播标准库 socket
    # 异常，而不是 pika.exceptions.AMQPConnectionError。仅接受明确的连接
    # 被拒绝/重置/中止、写端断开和超时；不把通用 OSError（例如 DNS 或
    # 本地配置错误）误判为可无限重试的 broker 故障。
    if isinstance(
        error,
        (
            ConnectionRefusedError,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            TimeoutError,
        ),
    ):
        return True

    try:
        import pika.exceptions as pika_exceptions
    except ImportError:
        return False

    permanent_names = {
        "AuthenticationError",
        "ProbableAccessDeniedError",
        "ProbableAuthenticationError",
        "IncompatibleProtocolError",
    }
    if type(error).__name__ in permanent_names:
        return False

    # RabbitMQ 在 broker 重启、停止或连接被管理员强制回收时，会通过
    # Connection.Close/Channel.Close 携带 320 (CONNECTION_FORCED)。这不是
    # 凭据、vhost 或 topology 配置错误；BlockingConnection 会把它作为
    # ConnectionClosedByBroker/ChannelClosedByBroker 抛出。只有明确的
    # CONNECTION_FORCED 才按断线重连，其他关闭码继续 fail closed，避免
    # 把 403/404/406 等永久配置或权限错误变成无限重连。
    connection_closed = getattr(pika_exceptions, "ConnectionClosed", None)
    channel_closed = getattr(pika_exceptions, "ChannelClosed", None)
    if (isinstance(connection_closed, type) and isinstance(error, connection_closed)) or (
        isinstance(channel_closed, type) and isinstance(error, channel_closed)
    ):
        return getattr(error, "reply_code", None) == 320

    transient_names = {
        "AMQPConnectionError",
        "AMQPHeartbeatTimeout",
        "ChannelWrongStateError",
        "ConnectionWrongStateError",
        "StreamLostError",
        "ConnectionBlockedTimeout",
    }
    for name in transient_names:
        error_type = getattr(pika_exceptions, name, None)
        if isinstance(error_type, type) and isinstance(error, error_type):
            return True
    return False
