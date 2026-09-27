# ADR-005：声明式 Skill、来源冻结与可解释检索

- 状态：已接受
- 日期：2026-09-08

## 背景

阶段三需要把成功经验变成可复用 Skill，但模型输出、网页内容和历史 Trace 都不能直接获得高信任级别。同时，早期 Skill 数量很少，没有必要引入向量数据库和不可解释的召回链路。

## 决策

1. Skill 使用不可执行的严格 DSL，工具、风险、引用和依赖图必须经过静态验证。
2. 只有通过确定性 Validator 的 TRAIN EvalRun 可以成为来源；HOLDOUT 永不进入 CandidateGenerator。
3. 来源先递归清洗，再保存为内容寻址、不可覆盖的 Trace Artifact。敏感信息或 Prompt Injection 检测失败时阻断提炼。
4. CandidateGenerator 只能创建 DRAFT，不能评测、审批或发布。
5. 运行时先用纯 Python BM25，并记录匹配词、得分、版本和稳定排序结果。
6. 运行恢复必须复用原选择；Skill 上下文不能扩大 ToolRegistry 和 PermissionPolicy 的能力。

## 影响

优点是边界清晰、结果可解释、测试无需外部服务，且可以审计“从哪条 Trace 得到哪个版本、一次 Run 为什么选中它”。代价是首版 DSL 表达能力有限，关键词检索不具备语义向量的召回能力；这些限制在真实评测证明有必要前不提前扩张。
