# EvoAgent

EvoAgent 是一个从零实现的、可测试的 Agent Runtime。项目最终目标是在可靠任务执行的基础上，建立可验证、可版本化、可回滚的 Skill 生命周期。

当前实现进度：阶段一的模块 0～9 已完成；阶段二的模块 0～3 已完成，包括异步数据库基础设施、Alembic、持久化模型、状态机、Repository、Unit of Work、PersistentEventSink 和基础 Trace 查询。

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

## 运行

先用不需要 API Key 的演示模式验证完整链路：

```powershell
.\.venv\Scripts\evoagent --demo --show-events
```

默认配置使用 `mock` Provider。也可以直接提交一个任务：

```powershell
.\.venv\Scripts\evoagent "介绍一下当前项目"
```

若要连接真实模型服务，复制 `.env.example` 为 `.env`，将 Provider 改为 `openai_compatible`，并填写 API Key、Base URL 和模型名。当前适配的是 OpenAI-compatible `/chat/completions` 流式接口，不自动重试。

内置工具包括：

- `calculator`：受限算术表达式计算；
- `file_read`：只允许读取 Workspace 内的 UTF-8 普通文件；
- `web_fetch`：只读取公开 HTTP/HTTPS 文本资源，并限制重定向、超时和响应大小。

## 阶段二数据库

安装 Docker 后，可以启动本地 PostgreSQL：

```powershell
docker compose up -d postgres
$env:EVOAGENT_DATABASE_URL="postgresql+asyncpg://evoagent:evoagent@127.0.0.1:5432/evoagent"
.\.venv\Scripts\alembic upgrade head
```

当前模块只完成持久化底座，FastAPI 和 Worker 将从模块 4 开始接入。SQLite 只用于本地快速测试；PostgreSQL 迁移和跨连接事件序号由 CI 的真实 PostgreSQL 服务验证。

详细设计见：

- `docs/EvoAgent-项目设计与分阶段实现计划.md`
- `docs/阶段一-可测试Agent内核架构与实现指南.md`
- `docs/阶段二-可靠可追踪任务执行架构与实现指南.md`
- `docs/开发进度与决策记录.md`
- `docs/EvoAgent-源码讲解与学习手册.md`
