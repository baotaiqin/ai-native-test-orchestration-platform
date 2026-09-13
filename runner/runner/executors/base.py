"""执行器最小协议，避免消费者依赖具体执行器实现。"""

from __future__ import annotations

from typing import Protocol

from runner.executors.api import ApiExecutionResult, ApiRequestTemplate


class BaseExecutor(Protocol):
    def execute(self, request: ApiRequestTemplate) -> ApiExecutionResult:
        """执行一个已经由后端下发且经过严格校验的请求模板。"""
