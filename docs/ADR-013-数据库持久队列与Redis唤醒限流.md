# ADR-013：数据库持久队列与 Redis 唤醒限流

- 日期：2026-09-22
- 状态：接受
- 对应：阶段四模块 11

## 问题

单进程串行领取限制吞吐；直接启动多个 Worker 会放大服务配额、租约接管和维护任务阻塞问题。消息系统不能成为 Task、Run 或副作用是否成功的第二个事实来源。

## 决定

PostgreSQL 保存持久队列，继续使用 SKIP LOCKED 和 owner/epoch/expiry 条件提交。Worker 标签仅用于辨认，启动 UUID 才区分实例。进程内通过固定数量的执行循环限制活动 Run；单个持久化 Run 的工具调用继续串行。

Redis Pub/Sub 只发送 `scan`。QueueSession 完成数据库 commit 后才 best-effort publish；回滚不发布。消息不携带任务正文或租约。所有 Worker 保留周期扫描，所以通知丢失、重复或断线不会改变任务事实。Redis 数据库编号不隔离 Pub/Sub channel，部署必须设置独立 namespace。

启用 Redis 时，模型、Embedding 和 MCP 服务请求使用 Lua token bucket。脚本使用 Redis TIME，原子补充、扣减和更新过期时间；key 由 namespace 和服务身份摘要组成，不包含密钥。进程内 semaphore 控制单服务同时调用数。等待不占数据库事务，取得配额后再验证租约。Redis 故障时配额拒绝放行，不能降级成无限调用；未配置 Redis 时是明确的本地模式，仅保留进程内并发上限。

模型配额等待超过上限时，AgentLoop 保存请求前 checkpoint；工具配额失败发生在副作用 PREPARED 之前，在完整工具结果边界保存。Run 转 RETRYING，设置 next_attempt_at，下一次领取不增加网络重试次数。总等待受任务年龄及重试时限约束。Embedding 查询配额不可用可以按已有检索规则降级词法，但不会绕过限流发请求。

维护任务使用独立进程，领取递增 epoch，定期续租，失败设置 next_attempt_at，最多领取三次。EvalExperiment 增加 lease_epoch，结果采集、第二臂放行、实验完成及 heartbeat 都校验代次；取消也锁定实验行。

## 代价与边界

- Redis 唤醒不是可靠投递，不提供 exactly-once；可靠性来自数据库和副作用账本。
- 请求配额跨进程共享；service_concurrency 是每进程限制，不承诺集群级并发 semaphore。
- 维护任务达到上限后保留 failed 记录，需要定位原因后显式处理。
- 本次只支持同主机共享 Artifact。没有多主机文件存储、高可用 Redis 或 Kubernetes 调度。
- Linux SIGTERM 停止新领取并等待当前任务；强制终止依靠租约到期恢复，不等同于优雅退出。

## 验证

真实 PostgreSQL 的两个 Worker 进程使用相同标签完成任务；Redis 两个连接争抢同一配额；回滚不发布；限流 checkpoint 恢复；相同 Eval owner 接管后拒绝旧 epoch。命令和证据见[运行说明](阶段四-模块11至12验收与运行说明.md)。

参考：[Redis Pub/Sub](https://redis.io/docs/latest/develop/pubsub/)、[Redis Lua 脚本执行](https://redis.io/docs/latest/develop/programmability/eval-intro/)。
