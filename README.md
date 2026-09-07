# EvoAgent

EvoAgent 是一个从零实现的、可测试的 Agent Runtime。项目最终目标是在可靠任务执行的基础上，建立可验证、可版本化、可回滚的 Skill 生命周期。

当前实现进度：阶段一模块 0～9、阶段二模块 0～12 已完成，v0.2 范围已经冻结。项目具备持久化 Task API、PostgreSQL Job Lease、独立单 Worker、版本化快照与恢复、分类重试、权限审批、副作用账本、SSE、完整 Trace 和本地 Viewer。

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

默认 CLI 配置使用 `mock` Provider。也可以直接提交一个任务：

```powershell
.\.venv\Scripts\evoagent "介绍一下当前项目"
```

若要连接真实模型服务，复制 `.env.example` 为 `.env`，将 Provider 改为 `openai_compatible`，并填写 API Key、Base URL 和模型名。当前适配的是 OpenAI-compatible `/chat/completions` 流式接口，不自动重试。

内置工具包括：

- `calculator`：受限算术表达式计算；
- `file_read`：只允许读取 Workspace 内的 UTF-8 普通文件；
- `web_fetch`：只读取公开 HTTP/HTTPS 文本资源，并限制重定向、超时和响应大小。
- `file_write`：原子写入当前 Run 的受控目录，覆盖操作需要审批；
- `web_search`：通过可替换 SearchProvider 搜索，默认 Mock，可选 Brave；
- `ask_user`：通过数据库审批流暂停并取得用户答复；
- `shell`：默认关闭；显式配置 argv 可执行文件白名单后才可使用。

## 阶段二数据库

安装 Docker 后，可以一条命令启动 PostgreSQL、迁移、API 和单 Worker：

```powershell
docker compose up --build
```

也可以只启动本地 PostgreSQL，再分别运行进程：

```powershell
docker compose up -d postgres
$env:EVOAGENT_DATABASE_URL="postgresql+asyncpg://evoagent:evoagent@127.0.0.1:5432/evoagent"
.\.venv\Scripts\alembic upgrade head
```

启动 API：

```powershell
.\.venv\Scripts\evoagent-api
```

启动 Worker：

```powershell
.\.venv\Scripts\evoagent-worker
```

打开 `http://127.0.0.1:8000/docs` 查看 OpenAPI，打开 `http://127.0.0.1:8000/viewer` 查看 Trace。Task API 只负责提交和控制任务；后台执行由 `JobWorker` 完成，不会在 HTTP 请求中运行 Agent。Worker 的默认 Mock 模式会离线执行“搜索—生成 report.md—最终回答”闭环。事件流位于 `/api/v1/tasks/{task_id}/events`，完整运行记录位于 `/api/v1/runs/{run_id}/trace`。

SQLite 只用于本地快速测试；PostgreSQL 迁移、跨连接事件序号和 Worker 租约竞争由 CI 的真实 PostgreSQL 服务验证。

详细设计见：

- `docs/EvoAgent-项目设计与分阶段实现计划.md`
- `docs/阶段一-可测试Agent内核架构与实现指南.md`
- `docs/阶段二-可靠可追踪任务执行架构与实现指南.md`
- `docs/阶段二-安全边界.md`
- `docs/阶段二-演示与故障注入.md`
- `docs/开发进度与决策记录.md`
- `docs/EvoAgent-源码讲解与学习手册.md`
