"""K1：`context_strict` 与真实 Provider 的组合必须在**启动时**被拒绝。

背景（改造方案 §2.2 K1）：`context_strict=True` 只接受被证实过的计数
（`exact` / `exact_mock` / `verified_upper_bound`），而
`policy_from_settings()` 只为 Mock 提供 `MockCounter("exact_mock")`；真实 Provider
拿到 `ConservativeTokenCounter("estimated")`，于是每一次请求都会以
`context_count_unverified` 被拒，Agent 完全不可用。

本文件同时钉住两件事：启动校验拒绝该组合，且**运行时的 strict 闸门不得被放松**。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.config import ProviderName, Settings
from evoagent.core.context_budget import ConservativeTokenCounter, ContextBudget
from evoagent.core.context_policy import (
    BoundedContextPolicy,
    ContextPolicyError,
    policy_from_settings,
)
from evoagent.core.models import Message, MessageRole, ModelRequest


def _settings(
    tmp_path: Path,
    *,
    context_policy: str,
    provider: ProviderName,
    context_strict: bool,
) -> Settings:
    options: dict[str, object] = {
        "_env_file": None,
        "workspace": tmp_path / "workspace",
        "context_policy": context_policy,
        "context_strict": context_strict,
        "provider": provider,
    }
    if provider is ProviderName.OPENAI_COMPATIBLE:
        options |= {
            "api_key": "test-secret",
            "base_url": "https://llm.example.test/v1",
            "model": "example-model",
            # bounded + 真实 Provider 要求显式给出窗口
            "context_window_tokens": 32768,
        }
    return Settings(**options)


@pytest.mark.parametrize(
    ("context_policy", "provider", "context_strict"),
    [
        ("bounded", ProviderName.MOCK, False),
        ("bounded", ProviderName.MOCK, True),
        ("bounded", ProviderName.OPENAI_COMPATIBLE, False),
        ("legacy", ProviderName.MOCK, False),
        ("legacy", ProviderName.MOCK, True),
        ("legacy", ProviderName.OPENAI_COMPATIBLE, False),
        ("legacy", ProviderName.OPENAI_COMPATIBLE, True),
    ],
)
def test_supported_configuration_matrix_starts(
    tmp_path: Path,
    context_policy: str,
    provider: ProviderName,
    context_strict: bool,
) -> None:
    settings = _settings(
        tmp_path,
        context_policy=context_policy,
        provider=provider,
        context_strict=context_strict,
    )
    assert settings.context_policy == context_policy
    assert settings.context_strict is context_strict


@pytest.mark.parametrize("context_policy", ["bounded"])
def test_real_provider_with_strict_is_rejected_at_startup(
    tmp_path: Path, context_policy: str
) -> None:
    """唯一不支持的组合：bounded + strict + 非 Mock Provider。"""

    with pytest.raises(ValidationError, match="context_strict"):
        _settings(
            tmp_path,
            context_policy=context_policy,
            provider=ProviderName.OPENAI_COMPATIBLE,
            context_strict=True,
        )


def test_legacy_policy_is_not_over_blocked(tmp_path: Path) -> None:
    """legacy 不执行 strict 判断，因此该组合必须仍可启动（不能扩大成所有模式不可用）。"""

    settings = _settings(
        tmp_path,
        context_policy="legacy",
        provider=ProviderName.OPENAI_COMPATIBLE,
        context_strict=True,
    )
    decision = policy_from_settings(settings).prepare(
        ModelRequest(
            messages=(Message(role=MessageRole.USER, content="你好"),),
            model="example-model",
        )
    )
    assert decision.estimate is None


def test_strict_gate_still_requires_verified_counting() -> None:
    """运行时的 strict 闸门不因配置校验而放松：估算计数仍被拒绝。"""

    policy = BoundedContextPolicy(
        ContextBudget(context_window=32768, output_tokens=4096, safety_margin=1024),
        ConservativeTokenCounter(),
        strict=True,
    )
    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="你好"),),
        model="example-model",
    )
    assert policy.counter.count_request(request).confidence == "estimated"
    with pytest.raises(ContextPolicyError) as error:
        policy.prepare(request)
    assert error.value.code == "context_count_unverified"


def test_mock_provider_still_gets_exact_counting(tmp_path: Path) -> None:
    """Mock 仍然走 MockCounter，strict 组合可用——这是被允许的另一半。"""

    settings = _settings(
        tmp_path,
        context_policy="bounded",
        provider=ProviderName.MOCK,
        context_strict=True,
    )
    policy = policy_from_settings(settings)
    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="你好"),),
        model="mock-model",
    )
    assert policy.counter.count_request(request).confidence == "exact_mock"
    assert policy.prepare(request).estimate is not None
