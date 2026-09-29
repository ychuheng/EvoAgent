# EvoAgent

EvoAgent 是一个通过本地网页使用的个人 Agent。你用自然语言交代目标，它可以围绕一个授权项目读取文件、修改代码、运行命令和测试，再根据工具结果继续工作。你也可以不选项目，直接对话、搜索公开资料，或让它生成可预览、可下载的文件。

例如，你可以让它“查看这个项目为什么测试失败，修改相关代码，并用同一条命令复测”，也可以让它“检索资料、核对来源，写一份报告”。任务的执行步骤、工具结果和最终答复会保存在会话中；需要你决定的操作会停下来请求审批。

它还提供 Skill 的提炼、评测、人工发布和回滚流程，让可复用的任务做法能够受控地沉淀下来。**这套机制已经实现，但 Skill 能否稳定提高真实任务的完成质量尚未得到正式验证。**

## 从哪里使用

项目提供两个网页运行方式，界面和任务流程相近，操作文件与命令的位置不同：

| 你要操作的环境 | 运行方式 | 打开页面 |
| --- | --- | --- |
| Windows 上的项目，使用本机 Python、Git 或 PowerShell 等工具 | [Windows 本机模式](docs/可信本机模式.md)：网页与 Agent 在 Windows 运行，数据库和 Redis 由 Docker 提供 | `http://127.0.0.1:18020/ui/` |
| 挂载到容器中的项目，使用 Linux 工具链 | [Docker 个人模式](docs/个人模式操作手册.md)：网页与 Agent 在容器运行，项目挂载到 `/app/projects` | `http://127.0.0.1:8000/ui/` |

两种方式的数据彼此独立。Windows 模式中登记 Windows 绝对路径；Docker 模式中登记 `/app/projects` 下的容器路径。初次使用 Windows 项目，可以按下面的步骤启动；需要容器隔离时，直接阅读 [Docker 个人模式操作手册](docs/个人模式操作手册.md)。

## 在 Windows 上启动

需要 Windows、Python 3.12 或 3.13、正在运行的 Docker Desktop、Node.js 24、pnpm 11，以及一个支持工具调用的 OpenAI-compatible 对话模型。以下命令都在仓库根目录的 PowerShell 中执行。

1. 安装依赖并准备私有配置：

   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   pnpm --dir frontend install --frozen-lockfile
   Copy-Item .env.personal.example .env.personal
   ```

   如果使用 Python 3.12，把第一行的 `-3.13` 改为 `-3.12`。

2. 编辑 `.env.personal`，填写 `EVOAGENT_API_KEY`、`EVOAGENT_BASE_URL`、`EVOAGENT_MODEL` 和模型实际支持的 `EVOAGENT_CONTEXT_WINDOW_TOKENS`。本机模式还需要显式设置：

   ```dotenv
   EVOAGENT_PROVIDER=openai_compatible
   EVOAGENT_BUDGET_SCOPE=trial
   EVOAGENT_BUDGET_TRIAL_LIMIT_MICROS=<试跑总上限>
   EVOAGENT_BUDGET_TASK_LIMIT_MICROS=<单任务上限>
   EVOAGENT_BUDGET_INPUT_PRICE_MICROS_PER_MILLION=<每百万输入 Token 的价格假设>
   EVOAGENT_BUDGET_OUTPUT_PRICE_MICROS_PER_MILLION=<每百万输出 Token 的价格假设>
   EVOAGENT_PROJECT_COMMAND_ALLOWLIST='["python","git"]'
   ```

   把尖括号占位值换成按模型服务价格确定的整数；预算字段的单位是微元。命令白名单只填你准备让 Agent 申请执行的程序，程序也必须在本机 PATH 中。配置文件已被 Git 忽略，不要把密钥写进任务内容或提交到仓库。只填密钥而不设置 `EVOAGENT_PROVIDER`，不会启用真实模型。字段解释与更多示例见[本机模式说明](docs/可信本机模式.md)。

3. 构建页面并启动：

   ```powershell
   pnpm --dir frontend run build
   .\.venv\Scripts\python.exe scripts/host_mode.py
   ```

   启动脚本会准备数据库并运行网页与 Worker。打开 `http://127.0.0.1:18020/ui/`，先核对页面显示的模型名。按 Ctrl+C 退出应用；保留数据的停止方式见[本机模式说明](docs/可信本机模式.md)。

## 发起第一条任务

打开页面后，可以先创建一个不绑定项目的会话，发一句普通对话，确认模型能够回复。接下来根据目标选择：

- **操作项目**：在页面登记一个已有目录，授予只读或可写权限；将它绑定到整个会话，或只授权给下一条任务。比如：“读这个项目的测试与相关代码，修复失败用例，然后复测并说明改动。”
- **不操作项目**：直接提问、搜索公开资料，或要求生成文件。生成的产物可在任务页面预览和下载。

运行中可以查看步骤、补充要求、排队下一条任务或取消。遇到命令等需要审批的动作，先检查目标、命令参数和差异预览，再决定是否批准。页面上的“已完成”表示任务运行结束；对开放式回答的事实正确性，仍要核对来源与结果。详细操作见[用户手册](docs/用户手册.md)。

## 权限与使用边界

项目目录需要你明确授权；只读授权不提供修改和项目命令工具。Windows 本机命令逐次请求审批，并以当前 Windows 用户身份运行。**获批的解释器、脚本或构建工具可能访问授权目录外的文件或网络**，因此本机模式不是 Windows 操作系统级沙箱，也不提供鼠标、键盘或桌面应用的自动控制。Docker 模式中的项目命令在 Linux 容器内执行，不能直接调用 Windows 程序。完整说明见[安全边界](docs/安全边界说明.md)。

目前的版本是 `0.4.0.dev0` 候选版。项目已经具备上述使用路径，但跨多个真实项目的稳定完成率、Skill 的净收益和正式发布验收仍需证据；[当前状态](docs/当前状态.md)记录已验证的范围与未完成事项。请不要把候选版用于无人看管的高风险操作。

## 了解和参与项目

- 想学习操作：看[用户手册](docs/用户手册.md)和[故障排查](docs/运营故障指南.md)。
- 想理解代码：从[源码手册](docs/manual/00-总览与目录.md)读起；[源码地图](docs/源码地图.md)可快速定位文件。
- 想了解设计取舍、实施计划和历史记录：从[文档导航](docs/README.md)进入。历史阶段材料记录当时的实现过程，不是使用本项目的前置知识。

源码位于 `src/evoagent/`，网页位于 `frontend/`，测试位于 `tests/`。开发时可运行 `python -m pytest`、`ruff check .` 与 `ruff format --check .`；前端在 `frontend/` 运行 `pnpm test` 和 `pnpm build`。完整集成测试还需要 PostgreSQL、Redis 或 Linux 容器环境，不能仅凭本机通过的测试判断全部 CI 结果。
