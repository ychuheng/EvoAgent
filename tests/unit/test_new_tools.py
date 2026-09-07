import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from evoagent.core.models import ToolRisk
from evoagent.tools.base import ToolPermissionError
from evoagent.tools.builtin.file_write import FileWriteArguments, FileWriteTool
from evoagent.tools.builtin.shell import ShellArguments, ShellTool
from evoagent.tools.builtin.web_search import (
    MockSearchProvider,
    SearchResult,
    WebSearchArguments,
    WebSearchTool,
)
from evoagent.tools.sandbox import RunSandbox, ShellSandbox


@pytest.mark.asyncio
async def test_file_write_is_atomic_and_confined_to_run_directory(tmp_path: Path) -> None:
    tool = FileWriteTool(RunSandbox(tmp_path, uuid4()))

    relative = await tool.invoke(FileWriteArguments(path="reports/result.md", content="完成"))

    assert relative == "reports/result.md"
    assert (
        tool.effective_risk(FileWriteArguments(path="result.md", content="x", overwrite=True))
        is ToolRisk.R2
    )
    with pytest.raises(ToolPermissionError):
        await tool.invoke(FileWriteArguments(path="../escape.txt", content="bad"))


@pytest.mark.asyncio
async def test_mock_search_provider_makes_search_deterministic() -> None:
    result = SearchResult(
        title="EvoAgent",
        url="https://example.com/evoagent",
        snippet="测试结果",
        source="mock",
    )
    tool = WebSearchTool(MockSearchProvider([result]))

    payload = json.loads(await tool.invoke(WebSearchArguments(query="agent")))

    assert payload == [result.model_dump(mode="json")]


@pytest.mark.asyncio
async def test_shell_uses_argv_allowlist_and_restricted_workspace(tmp_path: Path) -> None:
    sandbox = ShellSandbox(
        tmp_path,
        allowed_executables=(sys.executable,),
        timeout_seconds=5,
    )
    tool = ShellTool(sandbox)
    result = json.loads(
        await tool.invoke(ShellArguments(argv=(sys.executable, "-c", "print('sandbox-ok')")))
    )

    assert result["stdout"].strip() == "sandbox-ok"
    with pytest.raises(ToolPermissionError):
        await sandbox.run(("not-allowed", "--version"))
