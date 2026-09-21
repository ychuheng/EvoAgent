# ADR-011：MCP 工具契约冻结与统一安全执行

- 日期：2026-09-21
- 状态：模块 9 已实现；本地协议、持久化与恢复测试见运行说明
- 关联：[阶段四指南](阶段四-记忆检索与协议扩展架构与实现指南.md)、[模块 9 运行说明](阶段四-模块9验收与运行说明.md)

## 问题

模块 8 能发现工具并保存审核，但仍没有运行时授权。直接把 Server 的目录加入 Registry，会让模型、审批和恢复使用不同版本的契约；把目录 hash 加进副作用语义键，又会让同一个未决写入在升级后变成新的可执行动作。

本模块将“工具能力契约”和“业务动作身份”分开。目录、审核与策略限定一次授权可以用于什么；稳定 Server UUID、原工具名、Task 和规范参数限定需要去重的动作是什么。

## 适配器与参数

`MCPToolAdapter` 保留 BaseTool 继承关系，使用 `ValidatedMCPArguments` 承载已验证的 JSON。`definition()` 输出远端冻结的 inputSchema，`canonical_arguments()` 返回解包后的参数。Registry 的 manifest 改为读取 definition.parameters；内置工具的空绑定不进入 manifest，保持原有配置哈希兼容。

映射名使用 Server UUID 前 8 位、最长 29 字符的安全 slug 和完整 UUID/原名哈希的前 16 位。名称不会随展示名、目录版本或审核变化；Registry 继续拒绝重复名称，因此即使出现哈希碰撞，也会停止装配而不是覆盖工具。

Draft 2020-12 是唯一支持方言。远程引用、改变基址、无法解析的引用和循环引用拒绝；非循环 JSON Pointer 本地引用允许。Schema 原有 64 KiB/深度 16 限制之外，引用展开限制深度 32、访问 10000 节点；参数限制 1 MiB、实例深度 32。错误不回显完整参数。

## 发现、审核、激活分别提交

`config.enabled` 只允许建立连接。每个新目录仍创建未批准、R3、非幂等写的初始 Review。远端 annotations 只保留作审核证据，不能改变本地风险。

新增 `execution_state`、`active_catalog_id`、`execution_version`。管理 API 的 execution 操作显式选择目录，并用配置版本与执行状态版本进行条件更新。只有当前配置下最新目录可激活；只有其中 approved 的工具能被新 Run 选择。目录激活不等于批准目录中全部工具。

执行状态版本独立于连接配置 lock_version。正常激活目录不会把它的 config_version 自己变成过期版本，也不会改写旧 Run 的工具列表。配置修改后必须重新发现和激活。

## Run 的冻结边界

`runs.tool_catalog_snapshot` 保存完整工具 Schema、Server/目录身份、目录 hash、配置版本、审核记录 ID 和风险/副作用分类。NULL 表示尚未决定，空数组表示已决定不使用 MCP。字段首次写入受 LeaseGuard 保护，后续 ORM 修改被拒绝。

Runner 在 Skill 选择和 RunConfigSnapshot 之前装配 MCP；恢复只读取旧快照。升级前已保存 config_snapshot 的 Run 首次经过新代码时冻结空 MCP 列表，避免给旧任务追加能力。baseline/pinned_skill 不选择 MCP；Skill 的既有低风险工具白名单没有扩展。

list_changed 只发布新目录。当前实现采用保守策略：已知 latest_revision 变化，旧契约立即失效；调用前再发现并比较整个目录 hash。当前协议没有按旧版本 tools/call 的保证，不能假定同名就代表同语义。新目录需要重新审核、激活，旧 Run 需要结束后由新 Run 使用新契约。

## 授权、账本与提交

执行链固定为 Executor 校验 → 租约/撤销检查 → Policy → Approval → Effect 占位 → Adapter → 连接任务 tools/call → 结果检查 → 输出存储 → 受保护提交。没有新增 tools/call API，也没有 Provider 回调执行入口；缺少持久化中间件时 Executor 拒绝 MCP 调用。

`tool_calls.execution_binding` 保存冻结契约与 policy_hash。审批通过关联的 ToolCall 绑定契约和规范参数，跨 call_id 复用时必须完全匹配。审批决定时重新检查目录和审核，已失效审批不能被批准后继续执行。

副作用仍在 Task 范围内按稳定映射名和规范参数去重，目录版本不进入语义键。ToolEffect 通过 tool_call_id 关联执行绑定；改变目录不能绕过旧 EXECUTING/UNKNOWN。已提交效果若属于另一契约，不冒充新契约的成功结果，返回 tool_manifest_changed。

所有写分类（包括本地声明的 idempotent_write）默认只发送一次，尚未引入远端业务幂等键契约。传输失败、超时、isError、输出不合约都不能证明远端未写入，账本进入 UNKNOWN；人工 retry/committed 回执流程沿用阶段二。JSON-RPC request ID 不作为业务幂等键。

成功提交事务再次锁定 Server 并检查绑定；撤销后不提交成功，尚未确认的效果转 UNKNOWN。失去租约时旧 Worker 不可落库，由恢复协调将遗留 EXECUTING 转 UNKNOWN。

## 连接与卸载

SDK Session 的创建和关闭仍由专属任务负责。工具操作通过队列交给该任务；具体请求子任务仅用于等待和取消，不负责进入/退出 SDK 上下文。每连接串行调用，远端 I/O 在数据库事务外。

正常 draining 先持久化状态，阻止新选择和新调用；本进程等待当前调用结束后关闭连接。其他执行进程在调用期间约每 100ms 检查数据库，看到 draining 后让在途调用完成并关闭。Run 结束也关闭它自己的 Manager。没有后台运行工具的常驻 Worker Session。

disabled 先提交撤销，再关闭本地连接；其他进程通过调用检查撤销并取消等待。取消不代表远端事务回滚，写结果不明确时保持 UNKNOWN。轮询存在调度和数据库延迟，不承诺分布式零延迟撤销。

## 结果边界与代价

支持 text 和 structuredContent，输出为确定结构的 JSON 文本；outputSchema 同时受 SDK 与冻结契约检查。拒绝图片、音频、链接和嵌入资源，不自动下载。结果上限 2 MiB，超出模型预览预算的正文沿用 ToolOutputStore / Artifact。

只读工具遇到连接关闭、超时或传输失败最多重试一次，重试先关闭旧连接再重新发现；业务错误、协议错误、契约变更、输出校验错误不重试。持久化运行的 Executor 仍保持串行执行。

代价是每次调用前完整发现、执行期间数据库轮询以及保守拒绝旧目录。当前选择优先保证契约一致，未来若实现服务端明确的版本调用和业务幂等契约，可以在独立评审后降低这些成本。

## 验证边界

验证使用锁定 `mcp==1.30.0` 的实际 SDK 和受信本地 stdio 服务，并覆盖 Runner、Approval、Effect、连接关闭与迁移。公网 TLS、第三方 Server 的业务回执、PostgreSQL 实机并发和敌对程序容器隔离不由这些测试证明；隔离仍属于模块 10。
