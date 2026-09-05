"""按照预设脚本产生确定性响应的模型服务。"""

import json
from collections import deque
from collections.abc import AsyncIterator, Iterable, Sequence

from evoagent.core.models import (
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    ProviderEventType,
)
from evoagent.providers.base import ProviderError

MockStep = ModelResponse | Sequence[ProviderEvent] | ProviderError


class MockProviderExhaustedError(ProviderError):
    """测试脚本已用完，但运行时仍然请求模型。"""

    def __init__(self) -> None:
        super().__init__("mock provider script is exhausted", code="mock_script_exhausted")


class MockProvider:
    """每次调用消费一个预设步骤，并记录收到的 ModelRequest。"""

    def __init__(self, steps: Iterable[MockStep]) -> None:
        self._steps = deque(steps)
        self._requests: list[ModelRequest] = []

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        """以只读快照形式返回已经收到的请求。"""

        return tuple(self._requests)

    @property
    def remaining_steps(self) -> int:
        """返回尚未消费的测试步骤数量。"""

        return len(self._steps)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        """产生预设事件；ModelResponse 会被展开成完整的标准事件流。"""

        self._requests.append(request)
        if not self._steps:
            raise MockProviderExhaustedError

        step = self._steps.popleft()
        if isinstance(step, ProviderError):
            raise step

        if isinstance(step, ModelResponse):
            for event in self._events_from_response(step):
                yield event
            return

        for event in step:
            yield event

    @staticmethod
    def _events_from_response(response: ModelResponse) -> tuple[ProviderEvent, ...]:
        events: list[ProviderEvent] = []
        if response.message.content is not None:
            events.append(
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text_delta=response.message.content,
                )
            )
        for index, call in enumerate(response.message.tool_calls):
            events.append(
                ProviderEvent(
                    type=ProviderEventType.TOOL_CALL_DELTA,
                    tool_call_index=index,
                    tool_call_id=call.call_id,
                    tool_name_delta=call.name,
                    arguments_delta=json.dumps(
                        call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                )
            )
        if response.usage is not None:
            events.append(ProviderEvent(type=ProviderEventType.USAGE, usage=response.usage))
        events.append(ProviderEvent(type=ProviderEventType.COMPLETED, response=response))
        return tuple(events)
