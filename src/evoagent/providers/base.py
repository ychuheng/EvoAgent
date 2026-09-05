"""模型服务适配器必须遵守的统一接口与错误类型。"""

from collections.abc import AsyncIterator
from typing import Protocol

from evoagent.core.models import ModelRequest, ProviderEvent


class ModelProvider(Protocol):
    """把统一模型请求转换成异步 ProviderEvent 流。"""

    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        """发送一次模型请求并按顺序产生标准化事件。"""
        ...


class ProviderError(Exception):
    """模型服务已经归一化且可由运行时识别的错误。"""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


class ProviderTimeoutError(ProviderError):
    """单次模型请求超过 Provider 允许的时间。"""

    def __init__(self, message: str = "model request timed out") -> None:
        super().__init__(message, code="provider_timeout")


class ProviderProtocolError(ProviderError):
    """模型服务返回了不完整或不符合约定的事件流。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="provider_protocol_error")
