from types import SimpleNamespace
from uuid import uuid4

import pytest

from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse
from evoagent.memory.extraction import ModelMemoryExtractor, extract_proposals
from evoagent.memory.schema import MemoryError
from evoagent.providers.mock import MockProvider


def source(content="请记住：以后都用中文"):
    return SimpleNamespace(id=uuid4(), content=content)


@pytest.mark.parametrize(
    "body",
    [
        '[{"fact_key":"language","content":"以后都用英文","kind":"preference"}]',
        '[{"fact_key":"language","content":"以后都用中文","status":"confirmed"}]',
        "not-json",
    ],
)
async def test_model_memory_extractor_rejects_invention_and_authority(body):
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content=body),
                finish_reason=FinishReason.STOP,
            )
        ]
    )
    with pytest.raises(MemoryError):
        await ModelMemoryExtractor(provider, "mock").generate(source())


async def test_model_memory_extractor_has_no_tools_and_returns_only_quotes():
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content='[{"fact_key":"language","content":"以后都用中文","kind":"preference"}]',
                ),
                finish_reason=FinishReason.STOP,
            )
        ]
    )
    candidates = await ModelMemoryExtractor(provider, "mock").generate(source())
    assert candidates[0].content == "以后都用中文"
    assert provider.requests[0].tool_definitions == ()
    assert provider.requests[0].max_output_tokens == 1000


async def test_helper_rejects_before_provider_and_never_promotes_one_shot():
    provider = MockProvider([])
    with pytest.raises(MemoryError):
        await ModelMemoryExtractor(provider, "mock").generate(source("api_key=secret"))
    with pytest.raises(MemoryError):
        await ModelMemoryExtractor(provider, "mock").generate(source("x" * 9000))
    assert provider.requests == ()
    assert extract_proposals(source("计算 1 + 1")) == ()
    with pytest.raises(MemoryError):
        extract_proposals(source("请记住本次用中文"))
