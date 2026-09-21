# ADR-010：MCP 发现连接与目录版本

- 日期：2026-09-21
- 状态：模块 8 已实现，本地 stdio/HTTP fixture 已验收
- 关联：[阶段四指南](阶段四-记忆检索与协议扩展架构与实现指南.md)、[模块 8 运行说明](阶段四-模块8验收与运行说明.md)

## 背景与边界

外部 Server 可以声明工具，但声明中的描述、Schema 与只读提示不能自动获得 EvoAgent 的执行权限。模块 8 先交付可关闭的连接、完整目录、变更证据和本地审核记录；模块 9 再建立 ToolExecutor、Policy、Approval、Effect 和 Run 目录冻结的统一执行链。

## 协议和 SDK

锁定官方 `mcp==1.30.0`，项目只接受协议 `2025-11-25` 和 tools 能力。SDK 自身能兼容的其他协议并不自动成为项目承诺。上游已进入 v2 主线，本模块采用仍维护的 v1 分支，使用与安装版本一致的接口；升级必须重新核对握手与清理测试。[官方 SDK](https://github.com/modelcontextprotocol/python-sdk)、[v1.30.0 源码](https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0)、[Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

不自行实现 JSON-RPC；stdio、Streamable HTTP、ClientSession、分页 DTO 均由官方 SDK 承担。Prompts、Resources、Sampling、Elicitation、Tasks 和 OAuth 产品不在当前业务接口内；Server 额外声明能力可以留作元数据，不自动消费。

## 连接生命周期

每个连接有一个专属 asyncio 任务，SDK transport/ClientSession 的进入、使用和退出都在此任务内完成，避免 AnyIO cancel scope 跨任务退出。Manager 只发送发现请求和关闭信号，不向调用方泄露 Session。

API lifespan 拥有当前管理进程的 Manager，显式 discover 才建连接。普通 Worker/Run 尚不接入；Manager 可以在未来各 Worker 独立实例化，不把 Python 会话对象写入数据库。

初始化超时与发现超时分别受配置限制；初始可恢复传输失败最多两次尝试，中间等待 0.1 秒。稳定连接断开后记 degraded，下次显式 discover 重新建连，不提供无限后台重连。通知驱动刷新；无通知时每 15 秒 ping，健康观察有效 60 秒。

SDK 原始报文日志被抑制，stdio stderr 丢弃，健康记录只保存稳定错误码。这样牺牲远端诊断正文，换取不把凭据或任意 Server 输出落入日志的默认边界；本阶段不提供 stderr 浏览器。

## 传输与秘密

配置只引用部署者预设的 launch_profile_id 或 endpoint_profile_id，API 不接收 argv、任意 URL 或密钥正文。stdio 要求绝对可执行文件、预设参数及 `trusted_fixture=true`；第三方可执行 Server 的隔离仍属于模块 10。

HTTP 默认只接入预设 HTTPS 地址，拒绝 URL 凭据、query 和 fragment。解析结果必须全部是公网 IP，再把实际连接绑定到其中一个 IP，同时保留 Host 和 TLS SNI/证书校验主机名；禁止代理环境继承和重定向。仅明确配置 `local_fixture=true` 的命名 profile 可使用字面 loopback，不放开私网域名。响应流限制 2 MiB，拒绝压缩编码以免解压绕过该限制。

secret_ref 通过部署映射查找 `EVOAGENT_MCP_SECRET_*` 环境变量，连接时解析；stdio 仅增加 MCP_AUTH_TOKEN，HTTP 仅增加 Bearer Header。SDK 的基础进程环境白名单仍适用，应用不会把完整父进程环境传给子进程。

## 不可变目录与本地审核

发现最多 16 页/128 个工具，目录 1 MiB；工具名 128 字符、描述 4096 字符、Schema 64 KiB/深度 16。Schema 按 Draft 2020-12 检查，根必须是 object，禁止远程引用和改变引用基址。参数实例校验及工具调用属于模块 9。

完整分页后排序并计算 hash，身份同时包含协议、Server 身份与 capabilities。相同配置版本下相同目录复用 revision；新增、删除、内容变化有显式 diff。通知发生在分页期间则丢弃当前批次并重读，最多三次。

每个新目录的工具都初始化为未批准、R3、non_idempotent_write，不继承 Server 自报 readOnlyHint，也不继承旧目录的本地批准。人工审核是带 CAS 的追加记录；审核获准仍不启用执行。旧目录、旧审核记录受 ORM 不可变保护。

## 并发与持久化

新增 mcp_servers、mcp_catalogs、mcp_tool_reviews、mcp_health。配置 lock_version 与目录 revision 分别表达部署配置和远端目录的变化。网络调用不持有数据库事务，提交目录前在短事务中检查配置仍启用且版本未变；迟到结果不能复活已禁用 Server。

配置更新使用行锁加条件 UPDATE；目录修订分配使用 CAS，目录和初始审核记录同事务提交；审核绑定当前配置与最新目录，并追加独立版本。健康记录按 server/instance 保存，过期或配置不符返回 stale，不能把历史 ready 当成当前连接可用。

## 验证与剩余工作

本机测试覆盖真实 stdio/HTTP 握手与分页、list_changed、独立 PID 退出、目录拒绝、配置 CAS、审核冲突、有限重连、取消、能力拒绝、网络目标绑定、秘密引用和迁移往返。PostgreSQL 项未配置时明确跳过；公网 TLS 和第三方服务联调没有被本地 fixture 代替。

下一步模块 9 必须在现有 ToolExecutor 下接入动态 Schema、目录冻结、审批和副作用身份。不能因为目录已经存在或审核字段为 true，就从 API 或 Provider 回调绕过统一执行链。
