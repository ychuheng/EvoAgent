# ADR-016：Windows 可信本机执行与项目授权

- 日期：2026-09-28
- 状态：已接受；首个可用切片已实现，安全验收未关闭
- 范围：个人单用户本机模式、项目绑定、项目命令

## 背景

Docker 个人模式把宿主项目映射到 `/app/projects`，可以在 Linux Worker 中使用 Landlock/seccomp 等约束命令，但无法直接运行 Windows 上的解释器、构建工具或项目路径。用户的目标包含“项目可绑定也可不绑定，并由本人选择权限范围后操作本机环境”。继续把 Docker 项目视图称为完整的 Windows 主机操作，会混淆两种不同的权限保证。

## 决策

1. 保留 Docker 个人模式；另外提供显式启动的 Windows 可信本机模式。后者使用独立 PostgreSQL/Redis、Workspace 和环回端口，不能与 Docker 实例混为一套会话数据。
2. 项目登记保留 `read` / `read_write`、根路径规范化和授权版本复查。会话 `project_id` 是默认选择；Task 请求显式带 `project_id` 可覆盖一次，显式 `null` 表示该任务不使用会话默认项目。无项目任务没有项目文件与命令工具。
3. Windows 项目命令仅在显式本机模式、可写项目和非空可执行文件白名单下装配；每条命令强制 R2 人工审批。审批显示 argv。命令工作目录仍在项目根内，限制执行时长与输出量，超时/取消尝试终止进程树。无可靠进程级断网机制，因此命令须显式 `allow_network=true`。
4. 获批 Windows 进程使用当前用户权限。项目根对内置文件工具有效，**不对解释器、构建程序及子进程形成 OS 级约束**。Shell 解释器可按白名单显式启用，其脚本正文不能用简单路径词法检查证明安全。界面和文档必须把这点写明，不能把审批叫作沙箱。
5. `runtime-info.execution_mode` 区分 `container` 与 `trusted_windows_host`。API 只监听环回地址；默认 Compose 不自动启用本机模式。

## 代价与后续条件

本机模式提高了真实 Windows 项目可操作性，同时扩大了每次获批命令的影响范围。当前没有 Windows Job Object/受限令牌/AppContainer 级隔离，也没有可靠的每进程网络目的地和 CPU/内存配额。进程树清理、NTFS 权限、文件锁、junction、跨平台行尾及不同真实项目需要独立负向验收。进一步收紧宿主执行必须另立设计与测试，不能沿用 Docker 沙箱证据。

## 实现与证据

- [本机启动入口](../../scripts/host_mode.py)、[配置校验](../../src/evoagent/config.py)、[任务授权覆盖](../../src/evoagent/tasks/service.py)
- [本机命令执行](../../src/evoagent/projects/commands.py)、[命令工具风险](../../src/evoagent/tools/builtin/project_command.py)
- [开发验证](../evaluations/Windows可信本机模式开发验证-2026-09-28.md)、[操作说明](../可信本机模式.md)、[安全边界](../安全边界说明.md)
