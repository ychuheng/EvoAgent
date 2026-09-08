# EvoAgent

EvoAgent 是一个从零实现的、可测试的 Agent Runtime。项目最终目标是在可靠任务执行的基础上，建立可验证、可版本化、可回滚的 Skill 生命周期。

当前实现进度：阶段一模块 0～9、阶段二模块 0～12、阶段三模块 0～12 已完成。项目已具备可靠任务执行，以及“来源验证与冻结 → DRAFT 候选提炼 → HOLDOUT 配对评测 → 硬门禁 → 人工发布/拒绝 → ACTIVE 检索复用 → 手动回滚”的完整声明式 Skill 生命周期。

## 环境要求

- Python 3.12 或 3.13
- Node.js 24 与 pnpm 11（只在开发或构建管理前端时需要）

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
- `artifact_write`：只创建当前 Run 的新 Artifact，不允许覆盖同名文件。

普通 Task 可选择 `baseline` 或默认的 `retrieval` 运行模式。`pinned_skill` 不向普通 Task API 开放，只允许内部 EvalCoordinator 做配对评测。候选提炼入口为 `/api/v1/skills/extractions`；当配置 `EVOAGENT_SKILL_EXTRACTOR_MODEL` 时使用真实 OpenAI-compatible 模型，否则应用不会假装已经完成模型提炼。

## 阶段三完整演示

先导入并冻结仓库内的 24 条 TRAIN/HOLDOUT 数据集：

```powershell
.\.venv\Scripts\evoagent-eval-dataset open-source-research-v1.json --freeze
```

如果想从一个全新数据库观察完整生命周期，可准备一个空数据库并执行：

```powershell
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\evoagent-phase3-demo
```

演示是确定性的，不消耗模型 API：它会让第一个候选通过并发布，让一个正确性回退候选被拒绝，再发布新版并回滚，最后证明普通 RETRIEVAL Task 只能命中回滚后的 ACTIVE 版本。演示会写数据库和 Artifact，因此建议使用专门的本地演示数据库。

阶段三在本地运行时共有三个后台/前台进程：

```powershell
.\.venv\Scripts\evoagent-api
.\.venv\Scripts\evoagent-worker
.\.venv\Scripts\evoagent-eval-worker
```

普通 Worker 执行 Task；Eval Worker 只协调实验租约、成对顺序和验证收尾。构建 `frontend/` 后，管理界面位于 `http://127.0.0.1:8000/ui/`，OpenAPI 仍位于 `/docs`。评测报告必须通过 `POST /api/v1/eval-experiments/{id}/finalize` 显式冻结和执行门禁，GET 报告接口不会修改状态。

前端开发检查：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm build
pnpm e2e
```

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

启动普通 Worker：

```powershell
.\.venv\Scripts\evoagent-worker
```

启动评测协调 Worker：

```powershell
.\.venv\Scripts\evoagent-eval-worker
```

打开 `http://127.0.0.1:8000/docs` 查看 OpenAPI，打开 `http://127.0.0.1:8000/viewer` 查看 Trace。Task API 只负责提交和控制任务；后台执行由 `JobWorker` 完成，不会在 HTTP 请求中运行 Agent。Worker 的默认 Mock 模式会离线执行“搜索—生成 report.md—最终回答”闭环。事件流位于 `/api/v1/tasks/{task_id}/events`，完整运行记录位于 `/api/v1/runs/{run_id}/trace`。

SQLite 只用于本地快速测试；PostgreSQL 迁移、跨连接事件序号和 Worker 租约竞争由 CI 的真实 PostgreSQL 服务验证。

详细设计见：

- `docs/EvoAgent-项目设计与分阶段实现计划.md`
- `docs/阶段一-可测试Agent内核架构与实现指南.md`
- `docs/阶段二-可靠可追踪任务执行架构与实现指南.md`
- `docs/阶段二-安全边界.md`
- `docs/阶段二-演示与故障注入.md`
- `docs/阶段三-可验证Skill生命周期架构与实现指南.md`
- `docs/阶段三-演示与安全边界.md`
- `docs/ADR-006-配对评测硬门禁与人工发布.md`
- `docs/开发进度与决策记录.md`
- `docs/EvoAgent-源码讲解与学习手册.md`
