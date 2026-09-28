# EvoAgent

EvoAgent 是一个从零实现的、可测试的 Agent Runtime：在可靠任务执行的基础上，建立可验证、可版本化、可回滚的 Skill 生命周期。

当前目标是**本机个人通用 Agent**：用户在网页中授权项目或目录，Agent 能发现和读取内容、精确修改文件、运行测试、根据失败继续修正，也能研究公开资料、处理日常文件，并从真实任务中提炼有可验证收益的 Skill。用户可查看进度、补充要求、审查差异与产物。以上是[实施目标](docs/EvoAgent-项目设计与分阶段实现计划.md)，不表示已经实现。

## 当前成熟度（2026-09-28，`c2` 分支）

- 版本 `0.4.0.dev0`，**正式 `v0.4` 未放行**；默认 `docker-compose.yml` 仍是 Mock Provider、Mock 搜索、记忆关闭。
- **已实现**：Agent 内核、PostgreSQL 租约与检查点恢复、审批与副作用账本、上下文预算与压缩、版本化记忆、混合检索与原生维度向量、MCP 审核与卸载、独立执行容器、持久队列与多 Worker、管理页面、显式任务验收条件、个人模式启动入口。
- **已在真实环境验证**：真实模型网页对话与工具任务、显式验收通过/失败、Worker 存活探测、来源追溯（Mock 搜索）、Skill/记忆/MCP 专项链路、双 Worker 运行中故障恢复与 UNKNOWN 人工处理。
- **项目工作能力**：项目目录发现、精确编辑、失败测试修正与复测已有开发样本实证；Windows 可信本机模式可对本机目录授权，并在逐次审批后运行本机命令，见[操作说明](docs/可信本机模式.md)。
- **尚未验证**：跨不同真实项目的稳定完成率、多来源引用的人评、长上下文与复杂组合任务、Skill 净收益及 M7 发布集。Windows 本机命令使用当前用户权限，没有 OS 级隔离；默认 Compose 仍是 Mock。

完整事实清单（含证据标识、环境、提交与最近一次回归数字）见[当前状态](docs/当前状态.md)；下一步见[当前实现计划](docs/EvoAgent-项目设计与分阶段实现计划.md)。

## 最短启动步骤

需要 Python 3.12/3.13 与 Docker（含 Compose v2），以及一个支持 tool calling 的 OpenAI-compatible 模型服务。

以下是 Windows PowerShell 的最短启动步骤。

```powershell
# 1. 安装项目
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"

# 2. 建私有配置并填写 Key / Base URL / 模型名 / 模型实际上下文窗口
Copy-Item .env.personal.example .env.personal

# 3. 本地检查（不访问远端、不消耗额度）
.\.venv\Scripts\python.exe scripts/personal_preflight.py
docker compose -f docker-compose.yml -f deploy/personal/compose.yml config --quiet

# 4. 启动并打开 http://127.0.0.1:8000/ui/
docker compose -p evoagent-personal -f docker-compose.yml -f deploy/personal/compose.yml up -d --build --wait
```

关闭时用相同的项目名和两个 Compose 文件执行 `down`（**不要**加 `-v`，否则会删除会话、任务与 Workspace）：

```powershell
docker compose -p evoagent-personal -f docker-compose.yml -f deploy/personal/compose.yml down
```

首条任务、验收条件、错误码排障与可选能力见[个人模式操作手册](docs/个人模式操作手册.md)。
需要直接访问 Windows 项目及本机工具链时，按[可信本机模式](docs/可信本机模式.md)另行启动独立实例。

只想离线看完整链路时，不需要任何 API Key：

```powershell
.\.venv\Scripts\evoagent --demo --show-events
```

不配置真实模型的完整环境（Mock 演示）仍然可用，但属于离线/开发用途，不作为当前推荐入口：

```powershell
docker compose up --build          # Mock Provider，网页入口 http://127.0.0.1:8000/ui/
```

阶段三的确定性 Skill 生命周期演示、阶段四的演示与发布证据门禁脚本见[归档索引](docs/archive/README.md)中对应的模块说明；这些命令针对专用测试库，不要指向日常数据。

## 运行检查

```powershell
ruff check .
ruff format --check .
pytest
```

前端（仅在开发或构建管理页面时需要 Node.js 24 与 pnpm 11）：在 `frontend/` 下执行 `pnpm install --frozen-lockfile`、`pnpm typecheck`、`pnpm test`、`pnpm build`、`pnpm e2e`。

文档相对链接检查（移动或重命名文档后必跑）：

```powershell
.\.venv\Scripts\python.exe scripts/check_doc_links.py .
```

## 文档导航

| 你的问题 | 看这份 |
| --- | --- |
| 怎么运行、怎么发第一条任务 | [个人模式操作手册](docs/个人模式操作手册.md) |
| 现在能做什么、什么已经真实验证 | [当前状态](docs/当前状态.md) |
| 接下来做什么、验收门槛 | [个人通用 Agent 当前实现计划](docs/EvoAgent-项目设计与分阶段实现计划.md) |
| 全部文档怎么找 | [docs/README](docs/README.md) |
| 源码怎么读 | [源码地图](docs/源码地图.md) → [手册总目录](docs/manual/00-总览与目录.md) |
| 技术取舍为什么这样定 | [ADR 索引](docs/adr/README.md) |
| 真实评测与故障实验的结论 | [评测索引](docs/evaluations/README.md)、[证据索引](docs/reports/README.md) |
| 阶段一至四当时怎么设计与验收 | [归档索引](docs/archive/README.md) |

页面、Trace、审批与失败恢复的展示语义见[运行模式与任务验收条件](docs/Agent运行模式与任务验收条件-2026-09-26.md)；真实任务评测与失败样本见[质量评测](docs/evaluations/Agent真实任务质量评测-2026-09-26.md)和[整体能力核查](docs/evaluations/整体Agent能力核查-2026-09-26.md)。
