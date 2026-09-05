# EvoAgent

EvoAgent 是一个从零实现的、可测试的 Agent Runtime。项目最终目标是在可靠任务执行的基础上，建立可验证、可版本化、可回滚的 Skill 生命周期。

当前实现进度：阶段一的模块 0～6，包括工程配置、核心数据契约、运行事件、工具系统、MockProvider、ContextBuilder 和 AgentLoop；下一步实现 AgentRunner。

## 环境要求

- Python 3.12 或 3.13

## 本地安装

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

macOS/Linux：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
```

## 运行检查

```bash
ruff check .
ruff format --check .
pytest
```

默认配置使用 `mock` Provider，不需要 API Key。复制 `.env.example` 为 `.env` 后，可以修改本地配置；真实 Provider 会在后续模块实现。

详细设计见：

- `docs/EvoAgent-项目设计与分阶段实现计划.md`
- `docs/阶段一-可测试Agent内核架构与实现指南.md`
- `docs/开发进度与决策记录.md`
- `docs/EvoAgent-源码讲解与学习手册.md`
