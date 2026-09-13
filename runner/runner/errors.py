"""错误类型与 Runner 本地安全边界。"""


class RunnerError(Exception):
    """Runner 可预期的业务错误。"""


class ConfigurationError(RunnerError):
    """本地配置无效。"""


class ProtectionUnavailableError(RunnerError):
    """当前平台没有可用的凭证保护实现。"""


class ProtectionError(RunnerError):
    """凭证加解密失败。"""


class StateError(RunnerError):
    """本地状态读写或解析失败。"""


class PreflightError(StateError):
    """注册前本地保护或原子存储预检失败。"""


class StateNotFoundError(StateError):
    """本地状态文件不存在。"""


class TransportError(RunnerError):
    """HTTP 传输失败。"""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class ProtocolError(RunnerError):
    """后端响应不符合 Runner 协议。"""


class EnvelopeError(ProtocolError):
    """RabbitMQ 任务信封不符合固定 V1 协议。"""


class AuthenticationError(RunnerError):
    """Registration Token 或 Runner credential 已失效。"""


class RegistrationOutcomeUnknownError(RunnerError):
    """注册请求的服务端结果未知，不能安全重放一次性 Token。"""


class RegistrationPersistenceError(StateError):
    """服务端已完成注册，但本地身份未能安全保存。"""


class ExecutionError(RunnerError):
    """本地执行阶段失败，但可以向后端上报为 EXECUTION_ERROR。"""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "EXECUTION_ERROR",
    ) -> None:
        super().__init__(message)
        self.error_type = error_type


class TargetExecutionError(ExecutionError):
    """目标 API 网络或读取失败，不是 Runner 控制面失败。"""


class TargetTimeoutError(TargetExecutionError):
    """目标 API 请求超时，可安全上报为 TIMEOUT。"""

    def __init__(self) -> None:
        super().__init__("目标 API 请求超时", error_type="TARGET_TIMEOUT")
