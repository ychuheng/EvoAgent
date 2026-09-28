# EvoAgent

EvoAgent 是一个在本机运行的个人 Agent。你可以在网页中对话、授权目录，让它查找和读取文件、精确修改代码、运行测试、根据失败继续修正，也可以搜索公开资料并生成可预览、可下载的产物。任务过程会记录为事件与 Trace；高风险工具调用需要审批。项目还实现了 Skill 的提炼、评测、人工发布与回滚流程。

**当前是 `0.4.0.dev0` 候选版。** 真实模型已在开发样本中完成读项目、改代码、失败测试修正和本机命令审批；跨多种真实项目的稳定完成率、Skill 净收益和 M7 发布集尚未验证，不能把候选版当成正式发布。准确范围见[当前状态](docs/当前状态.md)。

## 选运行方式

| 方式 | 适合什么任务 | 项目路径与命令 | 入口 |
| --- | --- | --- | --- |
| Windows 可信本机模式 | 使用 Windows 上的项目和本机工具链 | 登记 Windows 绝对路径；白名单命令逐次审批后以当前用户权限运行 | `http://127.0.0.1:18020/ui/` |
| Docker 个人模式 | 在受控的 Linux Worker 内处理项目 | 宿主目录挂到 `/app/projects`；命令使用容器内工具链和 Linux 隔离 | `http://127.0.0.1:8000/ui/` |
| Mock 演示 | 不接模型，只核对执行链路 | 假模型，不代表 Agent 能力 | CLI 或默认 Compose |

本机模式的获批命令**没有 Windows OS 级文件或网络沙箱**，可能触及授权目录之外的资源；它也不能控制桌面鼠标、键盘或任意应用。Docker 模式不能直接运行 Windows 程序。[安全边界说明](docs/安全边界说明.md)区分两种模式。

## Windows 本机模式：最短启动

需要 Windows、Python 3.12/3.13、Docker Desktop、Node.js 24 与 pnpm 11，以及支持 tool calling 的 OpenAI-compatible 对话模型。下列命令在仓库根目录运行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm --dir frontend install --frozen-lockfile
Copy-Item .env.personal.example .env.personal
```

在 `.env.personal` 中填写模型连接、真实上下文窗口、`trial` 费用上限与价格假设，并显式设置 `EVOAGENT_PROJECT_COMMAND_ALLOWLIST`（例如 `python`、`git`；需要 PowerShell 时单独加入 `powershell`）。不要把密钥提交到仓库。随后执行：

```powershell
pnpm --dir frontend run build
.\.venv\Scripts\python.exe scripts/host_mode.py
```

打开 `http://127.0.0.1:18020/ui/`。可以直接创建不绑定项目的会话；要让某条任务操作文件，先在页面登记目录、选择只读或可写，再选择“绑定到本会话”或“仅下一任务使用”。命令审批时核对完整 argv。完整配置、关闭方式及限制见[Windows 可信本机模式](docs/可信本机模式.md)。

若希望使用 Docker 隔离的项目工具链，按[个人模式操作手册](docs/个人模式操作手册.md)配置宿主挂载并启动。首次操作与备份恢复见[用户手册](docs/用户手册.md)。不接模型的 CLI 演示运行 `.\.venv\Scripts\evoagent.exe --demo --show-events`。

## 仓库与验证

- `src/evoagent/`：Agent 内核、持久化运行时、工具、项目授权、Skill 与 API。
- `frontend/`：React 页面；`tests/`：单元、集成、端到端与故障测试。
- `deploy/`、`scripts/`：部署入口、演练和检查工具；`docs/`：操作、架构、计划与证据。

开发检查：`.\.venv\Scripts\ruff.exe check .`、`.\.venv\Scripts\ruff.exe format --check .`、`.\.venv\Scripts\python.exe -m pytest`；前端在 `frontend/` 执行 `pnpm test`、`pnpm build`、`pnpm e2e`。完整集成测试依赖专用 PostgreSQL/Redis；Linux 沙箱测试需要相应容器环境。最近一次 CI 结果以 GitHub Actions 为准。

文档从[文档导航](docs/README.md)进入：先读[用户手册](docs/用户手册.md)，需要实现细节再看[源码地图](docs/源码地图.md)和[手册总目录](docs/manual/00-总览与目录.md)。[当前实施计划](docs/EvoAgent-项目设计与分阶段实现计划.md)记录尚未关闭的验收门槛；历史阶段文档和逐次报告不代表当前状态。
