import pytest

from evoagent.cli import run_cli


@pytest.mark.asyncio
async def test_cli_runs_deterministic_calculator_demo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EVOAGENT_PROVIDER", "mock")

    exit_code = await run_cli(["--demo"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines()[0] == "计算结果是 84。"
    assert captured.err == ""


@pytest.mark.asyncio
async def test_demo_ignores_incomplete_real_provider_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EVOAGENT_PROVIDER", "openai_compatible")
    monkeypatch.delenv("EVOAGENT_API_KEY", raising=False)
    monkeypatch.delenv("EVOAGENT_BASE_URL", raising=False)
    monkeypatch.delenv("EVOAGENT_MODEL", raising=False)

    exit_code = await run_cli(["--demo"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines()[0] == "计算结果是 84。"


@pytest.mark.asyncio
async def test_cli_accepts_task_and_external_context_in_mock_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EVOAGENT_PROVIDER", "mock")

    exit_code = await run_cli(["summarize", "--context", "source material"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MockProvider 已收到任务：summarize" in captured.out


@pytest.mark.asyncio
async def test_cli_can_print_runtime_events(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EVOAGENT_PROVIDER", "mock")

    exit_code = await run_cli(["task", "--show-events"])

    lines = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert any('"type": "run.started"' in line for line in lines)
    assert any('"type": "run.completed"' in line for line in lines)
