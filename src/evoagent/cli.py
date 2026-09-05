"""EvoAgent 第一阶段的命令行入口。"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from evoagent.config import ProviderName, Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    RunStatus,
    ToolCall,
)
from evoagent.core.runner import AgentRunner
from evoagent.providers.base import ModelProvider
from evoagent.providers.mock import MockProvider
from evoagent.providers.openai_compatible import OpenAICompatibleProvider
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.builtin.file_read import FileReadTool
from evoagent.tools.builtin.web_fetch import WebFetchTool
from evoagent.tools.guards import URLGuard
from evoagent.tools.registry import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evoagent",
        description="运行一个可测试、受控工具调用的 EvoAgent 任务。",
    )
    parser.add_argument("task", nargs="?", help="希望 Agent 完成的任务")
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        help="添加一段外部上下文，可以重复使用",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="使用 MockProvider 运行 calculator 的确定性演示",
    )
    parser.add_argument(
        "--show-events",
        action="store_true",
        help="在结果后输出本次 Run 的 RuntimeEvent",
    )
    return parser


async def run_cli(argv: Sequence[str] | None = None) -> int:
    """解析参数、组装 Runtime、执行任务并返回进程退出码。"""

    args = build_parser().parse_args(argv)
    settings = Settings(provider=ProviderName.MOCK) if args.demo else Settings()
    task = args.task
    if args.demo:
        task = task or "计算 12 * (3 + 4)"
    elif task is None:
        task = input("请输入任务：").strip()

    provider = _build_provider(settings, task, demo=args.demo)
    web_fetch = WebFetchTool(
        URLGuard(),
        timeout_seconds=settings.tool_timeout_seconds,
    )
    registry = ToolRegistry(
        [
            CalculatorTool(),
            FileReadTool(settings.workspace),
            web_fetch,
        ]
    )
    runner = AgentRunner(settings, ContextBuilder(), provider, registry)

    try:
        result = await runner.run(task, external_context=args.context)
    finally:
        await web_fetch.aclose()
        if isinstance(provider, OpenAICompatibleProvider):
            await provider.aclose()

    if result.status is RunStatus.COMPLETED:
        print(result.final_answer)
        exit_code = 0
    else:
        print(
            f"[{result.status.value}] {result.error_code}: {result.error_message or ''}",
            file=sys.stderr,
        )
        exit_code = 1

    if args.show_events:
        for event in result.events:
            print(json.dumps(event.model_dump(mode="json"), ensure_ascii=False))
    return exit_code


def _build_provider(settings: Settings, task: str, *, demo: bool) -> ModelProvider:
    if demo:
        return MockProvider(
            [
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                call_id="demo-calculation",
                                name="calculator",
                                arguments={"expression": "12 * (3 + 4)"},
                            ),
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        content="计算结果是 84。",
                    ),
                    finish_reason=FinishReason.STOP,
                ),
            ]
        )
    if settings.provider is ProviderName.MOCK:
        return MockProvider(
            [
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        content=f"MockProvider 已收到任务：{task}",
                    ),
                    finish_reason=FinishReason.STOP,
                )
            ]
        )

    if settings.api_key is None or settings.base_url is None:
        raise RuntimeError("real provider configuration was not validated")
    return OpenAICompatibleProvider(
        api_key=settings.api_key,
        base_url=str(settings.base_url),
        timeout_seconds=settings.model_timeout_seconds,
    )


def main() -> None:
    """控制台脚本入口。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run_cli()))


if __name__ == "__main__":
    main()
