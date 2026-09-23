# EvoAgent

EvoAgent 是一个从零实现的、可测试的 Agent Runtime。项目最终目标是在可靠任务执行的基础上，建立可验证、可版本化、可回滚的 Skill 生命周期。

当前版本 `0.4.0.dev0`：前三阶段已完成；阶段四已实现模块 0～13 的工程代码，包括 Redis 唤醒/配额、双 Worker 调度、独立维护队列、Eval fencing，以及隔离的运行时实验和分账报告。PostgreSQL/pgvector、Redis、双真实进程与 Mock 实验已有验收；真实模型报告单独记录，不把小样本结论当成阶段四整体效果证明。

最新交付：[模块 14 最终验收与交付说明](docs/阶段四-模块14最终验收与交付说明.md)、[源码手册第 98～101 章](docs/EvoAgent-源码讲解与学习手册.md)、[ADR-015](docs/ADR-015-阶段四交付证据与发布门禁.md)。四条 Demo、全新/旧库迁移、容器与页面回归、真实 Skill 配对已交付；真实 Embedding 语义验收仍待配置，正式 v0.4 门禁未放行，保留 dev0。

管理页面：[模块 13 页面运行说明](docs/阶段四-模块13验收与运行说明.md)、[源码手册第 94～97 章](docs/EvoAgent-源码讲解与学习手册.md)。新增 Memory 管理、MCP 目录审核/卸载和上下文证据页面，保留原 Skill/Eval 三个页面。前端 12 项组件测试、6 个浏览器流程通过，后端 PostgreSQL/Redis 回归 404 passed、13 skipped。

调度与实验入口：[模块 11～12 运行说明](docs/阶段四-模块11至12验收与运行说明.md)、[源码手册第 86～93 章](docs/EvoAgent-源码讲解与学习手册.md)、[ADR-013](docs/ADR-013-数据库持久队列与Redis唤醒限流.md)、[ADR-014](docs/ADR-014-独立运行时实验与隔离评测.md)。数据库最新迁移为 `20260922_0012`。

阶段四最新部署与验收见[模块 6～7 运行说明](docs/阶段四-模块6至7验收与运行说明.md)，源码讲解见[学习手册第 69～73 章](docs/EvoAgent-源码讲解与学习手册.md)。升级需执行 migration `20260920_0007`，PostgreSQL 必须安装 vector 扩展；Compose/CI 已使用 pgvector 镜像。SQLite 使用 JSON 向量替身，不证明向量 SQL 通过。默认仍为词法 Skill 检索，`EVOAGENT_RETRIEVAL_BACKEND=hybrid` 开启混合检索，`EVOAGENT_MEMORY_RETRIEVAL_ENABLED=true` 开启已确认记忆注入；新链路要求快照 v2。旧活动配置不自动改写，配置不兼容时拒绝恢复。

模块 8 使用锁定的官方 `mcp==1.30.0` SDK，项目接受协议 `2025-11-25`，支持受信 stdio fixture 和预设 Streamable HTTP 端点；发现结果不会自动进入模型工具列表。部署、API 和测试边界见[MCP 运行说明](docs/阶段四-模块8验收与运行说明.md)，源码见[手册第 74～77 章](docs/EvoAgent-源码讲解与学习手册.md)。升级需安装新增依赖并迁移到 `20260920_0008`。

模块 9 通过统一 ToolExecutor 接入已审核并显式激活的 MCP 目录，支持动态参数验证、审批绑定、写入 UNKNOWN 和安全卸载。模块 9 的迁移为 `20260921_0009`；操作步骤见[模块 9 运行说明](docs/阶段四-模块9验收与运行说明.md)，详细源码见[手册第 78～81 章](docs/EvoAgent-源码讲解与学习手册.md)，设计取舍见[ADR-011](docs/ADR-011-MCP工具契约冻结与统一安全执行.md)。默认不启用执行，历史 Run 和 baseline/pinned 评测不自动增加 MCP 工具。

模块 10 新增独立 sandbox-controller、固定 digest 容器规格、租约与取消清理、受控 Artifact 输入输出、MCP 容器 stdio，以及 web_fetch 的 DNS/IP 绑定。最新迁移为 `20260921_0010`；详见[模块 10 运行说明](docs/阶段四-模块10验收与运行说明.md)、[手册第 82～85 章](docs/EvoAgent-源码讲解与学习手册.md)和[ADR-012](docs/ADR-012-独立执行容器与受控网络出口.md)。Shell 默认关闭，无宿主 fallback；容器固定禁网。Windows 契约回归通过，并已在 Docker Desktop Linux Engine 中完成 11 项真实容器资源、网络、清理与文件边界验收。

记忆提议、确认和归档操作见[模块 3～5 运行说明](docs/阶段四-模块3至5验收与运行说明.md)。Redis 已在模块 11 实现，Memory 管理前端已在模块 13 接入，使用步骤见[页面运行说明](docs/阶段四-模块13验收与运行说明.md)。

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

若要连接真实模型服务，复制 `.env.example` 为 `.env`，将 Provider 改为 `openai_compatible`，并填写 API Key、Base URL、模型名及核对过的 `EVOAGENT_CONTEXT_WINDOW_TOKENS`。当前适配的是 OpenAI-compatible `/chat/completions` 流式接口，不自动重试。

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

## 阶段四模块 0～2

Worker 已接通自动恢复，使用启动唯一身份和租约 epoch 保护运行记录写入。默认每轮按 `bounded` 策略检查上下文：输入包含工具 Schema，输出预留实际传给 Provider，只裁剪显式可选资料；受保护内容超限时停止请求。

升级前停掉所有旧 Worker，执行 `alembic upgrade head`，再启动同版本 API/Worker。不能混跑不检查 epoch 的旧进程。预算、计数器或代码版本变化会拒绝恢复不兼容 Run。

窗口配置、`legacy` 兼容模式、进程故障测试与未完成的环境验收见[阶段四模块 0～2 验收与运行说明](docs/阶段四-模块0至2验收与运行说明.md)。真实兼容服务当前使用标记为 `estimated` 的计数；严格模式会拒绝未验证计数器。后续已补齐摘要与 Memory 基础，MCP 和 Redis 仍未实现。

## 阶段四模块 3～5

持久化 Worker 默认采用 v2 快照，支持受限历史摘录、完整工具输出归档与 `artifact_read`。Session 消息有稳定序号和固定历史截止点；长期记忆支持原话提议、人工确认、拒绝、撤销、有效期与词法查询。

归档和正文清理使用持久化维护任务，由现有 Worker 消费。删除接口返回 `maintenance_job_id`，可查询处理状态并重试失败任务。默认无需额外模型；可选 `EVOAGENT_MEMORY_EXTRACTOR_MODEL` 只生成待确认候选。操作示例、升级步骤和遗忘范围见[模块 3～5 运行说明](docs/阶段四-模块3至5验收与运行说明.md)。

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

SQLite 只用于本地快速测试；PostgreSQL 迁移、跨连接事件序号和 Worker 租约竞争由 CI 及 Docker Desktop 中的真实 PostgreSQL 服务验证。

详细设计见：

- `docs/EvoAgent-项目设计与分阶段实现计划.md`
- `docs/阶段一-可测试Agent内核架构与实现指南.md`
- `docs/阶段二-可靠可追踪任务执行架构与实现指南.md`
- `docs/阶段二-安全边界.md`
- `docs/阶段二-演示与故障注入.md`
- `docs/阶段三-可验证Skill生命周期架构与实现指南.md`
- `docs/阶段三-演示与安全边界.md`
- [阶段四：记忆、检索与协议扩展架构与实现指南（设计，尚未实施）](docs/阶段四-记忆检索与协议扩展架构与实现指南.md)
- `docs/ADR-006-配对评测硬门禁与人工发布.md`
- `docs/ADR-007-租约隔离与请求上下文预算.md`
- `docs/阶段四-模块0至2验收与运行说明.md`
- `docs/开发进度与决策记录.md`
- `docs/EvoAgent-源码讲解与学习手册.md`


## 阶段四收尾验收

```powershell
# 先按模块 14 说明启动专用 PostgreSQL/Redis 测试环境；不要使用业务库。
.venv\Scripts\python.exe scripts/phase4_demos.py --output output/phase4-demos.json
.venv\Scripts\python.exe scripts/phase4_release_check.py
```

第二条命令当前预期退出 2：报告哈希有效，但 `real_embedding` 为 pending。`--allow-pending` 只审计已有证据，不改变 `release_ready=false`。本轮后端全量 408 passed/13 skipped，新增证据门禁两项另行通过；Linux Docker 11 项、前端组件 12 项/浏览器 6 项通过。真实算术 Skill 12 对均可比，两臂各 11/12，无安全回归；保留 Unicode 负号导致的字面验证失败，不宣称成功率提升。完整范围见模块 14 说明。
