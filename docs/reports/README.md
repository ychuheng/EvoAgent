# 证据索引（`docs/reports/`）

> 这里保存**机器可读的原始证据**：真实运行、故障注入、迁移与发布门禁导出的 JSON。它们是报告与[当前状态](../当前状态.md)里结论的来源，默认脱敏、不含密钥。
>
> 脚本约定：部分文件的路径是固定的，例如 `scripts/phase4_release_check.py` 读取 `docs/reports/phase4-final-manifest.json`、`scripts/phase4_migrations.py` 与 `scripts/phase4_deployment_probe.py` 写入本目录。**移动这些文件会破坏脚本**，因此本目录不参与归档迁移。
>
> 重新运行会把同名文件覆盖为当次结果；被覆盖前的内容只存在于 Git 历史中。历史结论引用的具体数字以 Git 中对应的提交版本为准。

## 1. 当前仍被直接引用的证据

| 文件 | 内容 | 引用处 |
| --- | --- | --- |
| [agent-core-user-v1-2026-09-26.json](agent-core-user-v1-2026-09-26.json) | 16 项冻结真实用户任务的逐样本结果（含数据集 SHA-256、通过率与失败 ID） | [真实任务质量评测](../evaluations/Agent真实任务质量评测-2026-09-26.md)、[当前状态](../当前状态.md) |
| [agent-basics-live-2026-09-26.json](agent-basics-live-2026-09-26.json) | 真实模型环境下的正常/失败验收任务、迁移、回归与长上下文记录 | [运行模式与任务验收条件](../Agent运行模式与任务验收条件-2026-09-26.md) |
| [agent-dual-worker-crash-2026-09-26.json](agent-dual-worker-crash-2026-09-26.json) | PostgreSQL 双 Worker 运行中强杀、UNKNOWN 人工处理与页面联合操作 | [整体 Agent 能力核查](../evaluations/整体Agent能力核查-2026-09-26.md)、[真实任务质量评测](../evaluations/Agent真实任务质量评测-2026-09-26.md) |
| [phase4-semantic-holdout-2026-09-25.json](phase4-semantic-holdout-2026-09-25.json) | 独立留出集的词法 6/10、混合 9/10、纯向量 10/10 与错排样本 | [前四阶段补完阶段性验收](../evaluations/前四阶段补完阶段性验收-2026-09-25.md) |
| [phase4-workspace-multiworker-2026-09-25.json](phase4-workspace-multiworker-2026-09-25.json) | 跨 Workspace 记忆隔离与双 Worker 等待审批交接 | 同上 |
| [phase4-final-negative-and-recovery-2026-09-25.json](phase4-final-negative-and-recovery-2026-09-25.json) | 浏览器负路径、后端长上下文拒绝、运行中崩溃恢复 | 同上 |

## 2. 阶段四模块 14 的交付证据（冻结快照）

这批文件属于模块 14 当次的发布清单与验证结果，**不再重跑**；`phase4-final-manifest.json` 中 `real_embedding` 仍为 `pending`，这是历史快照，不代表后来新增的证据不存在。

| 文件 | 内容 |
| --- | --- |
| [phase4-final-manifest.json](phase4-final-manifest.json) | 交付清单与检查项；被 `scripts/phase4_release_check.py` 读取 |
| [phase4-final-verification.json](phase4-final-verification.json) | 后端/前端/Docker/镜像与版本汇总 |
| [phase4-final-deployment.json](phase4-final-deployment.json) | 部署探针：就绪、UI、任务、Artifact 与 Provider |
| [phase4-final-migrations.json](phase4-final-migrations.json) | 全新库与带历史数据的旧库迁移结果 |
| [phase4-final-demos.json](phase4-final-demos.json) | 四组连续 Demo 共 15 个子场景 |
| [phase4-final-phase3-regression.json](phase4-final-phase3-regression.json) | 阶段三发布/拒绝/回滚回归 |
| [phase4-final-skill-deepseek.json](phase4-final-skill-deepseek.json) | 真实 DeepSeek 算术 Skill 配对报告（含门禁与 hash） |
| [phase4-final-skill-failures.json](phase4-final-skill-failures.json) | 上述配对的失败样本明细（Unicode 负号导致的字面验证失败，原分数不改写） |

## 3. Runtime 隔离实验报告（阶段四模块 12）

每组是独立单变量实验的报告，含 spec 哈希、可比对数、逐样本与结论。Mock 与真实模型分开记录，Mock 报告不能当作效果证据。

| 文件 | 内容 |
| --- | --- |
| [phase4-runtime-workers-mock.json](phase4-runtime-workers-mock.json) | Worker 数量对照（Mock） |
| [phase4-runtime-context-mock.json](phase4-runtime-context-mock.json) | 上下文策略对照（Mock） |
| [phase4-runtime-retrieval-mock.json](phase4-runtime-retrieval-mock.json) | 检索路径对照（Mock） |
| [phase4-runtime-context-deepseek.json](phase4-runtime-context-deepseek.json) | 上下文策略小样本（真实 DeepSeek） |
| [phase4-runtime-memory-deepseek.json](phase4-runtime-memory-deepseek.json) | 记忆读取小样本（真实 DeepSeek） |
| [four-stage-overall-2026-09-23.json](four-stage-overall-2026-09-23.json) | 四阶段整体回归的机器可读汇总 |

## 4. 新增证据的约定

按[当前实现计划](../EvoAgent-项目设计与分阶段实现计划.md)第 12～13 节的发布与文档要求，报告至少包含：范围、环境（提交、镜像 digest、迁移 head、Provider/模型、搜索/Embedding 模式）、自动化结果与跳过原因、真实任务的样本与失败 ID、安全与恢复证据、发布结论。凭据、私有正文和本机绝对路径不得写入。
