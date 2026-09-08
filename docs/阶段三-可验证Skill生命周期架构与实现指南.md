# 阶段三：可验证 Skill 生命周期架构与实现指南

> 文档目的：面向刚完成阶段二、第一次接触 DSL、信息检索和模型评测的开发者，解释 EvoAgent 第三阶段要解决的问题、总体架构、Skill 数据结构、来源追踪、训练/留出集隔离、配对评测、发布回滚、安全边界、测试策略和分模块实现顺序。
>
> 当前状态（2026-09-08）：模块 0～6 已实现并通过测试；模块 7～12 仍是设计计划。后续仍按照“完成一个模块、理解一个模块、补充学习手册、再进入下一个模块”的节奏开发，不能把未完成目标写成已有功能。

## 1. 第三阶段的目标

阶段一解决了“Agent 怎样调用模型和工具”，阶段二解决了“任务怎样持久化、恢复和审计”。但 Agent 每次遇到相似任务时，仍可能从头探索：重复搜索相同资料、重复试错、使用更多 Token，也可能再次走入以前失败过的路径。

第三阶段要建立一条受控制的经验固化链路：

```text
成功的训练任务
→ 判断是否真的满足确定性成功条件
→ 清洗并冻结来源 Trace
→ 提炼 DRAFT Skill
→ 在独立留出集上做无 Skill / 有 Skill 配对评测
→ 通过正确性与安全性硬门禁
→ 人工审批
→ 发布 ACTIVE 版本
→ 新的相似任务检索并使用 Skill
→ 发现退化时手动回滚到旧版本
```

这里的“进化”不是让模型自由修改自己的代码，而是把历史成功经验整理为**声明式、可验证、可追溯、可版本化的操作规程**。任何候选 Skill 都必须先经过评测与人工审批，不能从模型输出直接变成生产能力。

第三阶段需要证明以下能力：

1. Skill 只能表达受限的声明式 SOP，不能携带任意 Python、Shell 或模板代码。
2. 候选 Skill 只能来自通过确定性验证的训练轨迹，不能读取留出集答案。
3. SkillVersion 的来源 Trace、提炼输入、内容和运行配置都有稳定哈希。
4. 同一 EvalCase 可以在相同配置下完成 baseline 与 skill 配对运行。
5. 正确性或安全性下降时，Token、延迟等效率提升不能让 Skill 通过门禁。
6. 只有人工批准的 ACTIVE Skill 才能进入普通任务上下文。
7. 发布、禁用和回滚都是数据库中的原子、可审计操作。
8. 用户可以查看版本差异、来源、评测报告和失败样本。

## 2. 开始阶段三前必须保留什么

第三阶段建立在前两阶段之上，不重写 Agent Runtime。下面的边界继续成立：

- `AgentLoop` 仍只负责模型与工具迭代，不导入 ORM、Skill Repository 或 Eval 数据集。
- `AgentRunner` / `PersistentAgentRunner` 仍负责一次 Run 的生命周期。
- PostgreSQL 仍是 Task、Run、RunEvent、Snapshot、ToolEffect、Approval 以及阶段三数据的事实来源。
- `ToolExecutor`、`PermissionPolicy`、`Approval` 和 Sandbox 继续强制执行权限；Skill 不能绕过它们。
- Worker 继续通过数据库 Job Lease 领取普通 Task，阶段三不引入 Redis、Celery 或多 Worker 集群。
- 事件、快照、Artifact 和副作用账本的事务边界不改变。
- Mock Provider、Mock Search 和 SQLite 快速测试继续保证主要测试不依赖公网。
- 正式 Schema 继续只通过 Alembic migration 演进。

阶段三只能通过窄接口扩展现有系统：在 Run 开始前选择 Skill，把经过批准的 Skill 渲染为上下文，并记录本次选择；不能把 Skill 生命周期逻辑塞入 AgentLoop。

## 3. 第三阶段暂时不做什么

为了在 v0.3 形成可验证的秋招闭环，本阶段明确不实现：

- 自动发布、灰度发布、在线 A/B、质量漂移检测和自动回滚；
- 让 Skill 携带 Python、Shell、Jinja 表达式或任意可执行代码；
- 长期记忆、跨会话用户画像和上下文压缩；
- pgvector、Embedding 和复杂向量检索；
- 多 Agent 协作、MCP、Redis、多 Worker 调度和多 Provider 路由；
- Skill 市场、远程 Skill 安装、签名分发和第三方插件生态；
- 完整聊天 WebUI、认证、RBAC 和多租户管理；
- 用 LLM Judge 替代所有确定性验证器；
- 用几个精选成功样本宣称 Skill 一定有效。

本阶段可以使用 React + TypeScript 展示 Skill 差异和评测报告，但页面只服务于三条核心展示链路，不扩张成通用管理后台。

## 4. 先区分四个容易混淆的概念

### 4.1 Skill 不是普通 Prompt

普通 Prompt 通常是一段自然语言，结构和适用条件不明确。Skill 除了说明文字，还必须包含输入 Schema、触发词、允许工具、风险上限、步骤依赖、成功条件、验证器、来源和版本。

### 4.2 Skill 不是 Memory

Memory 保存“发生过什么”或“用户偏好是什么”；Skill 保存“面对一类任务时建议怎样做”。长期 Memory 属于阶段四。阶段三只能从经过验证的训练 Trace 提炼通用步骤，不能把某个用户的私有事实复制进 Skill。

### 4.3 Run 完成不等于任务正确

`Run.status == COMPLETED` 只表示模型正常给出了最终回答。它不证明回答满足引用、覆盖范围、文件格式或业务断言。阶段三新增 Validator，把“运行完成”和“任务通过”分开；只有 EvalRun 通过验证，才可能成为 SkillSource。

### 4.4 回滚不是一种版本状态

回滚是一项发布操作：把某个 RETIRED 版本重新激活，把当前 ACTIVE 版本退休，并原子切换 Skill.active_version_id。这样不会出现一个版本同时是 ACTIVE 和 ROLLED_BACK 的矛盾状态。

## 5. 总体架构

```text
                    ┌────────────────────────────┐
训练 EvalRun ──────→│ TraceEligibilityChecker    │
                    └──────────────┬─────────────┘
                                   ↓
                    ┌────────────────────────────┐
                    │ TraceSanitizer / Freezer   │
                    │ 脱敏、去环境信息、生成哈希 │
                    └──────────────┬─────────────┘
                                   ↓
                    ┌────────────────────────────┐
                    │ CandidateGenerator         │
                    │ Mock / Model 实现          │
                    └──────────────┬─────────────┘
                                   ↓
                    DRAFT SkillVersion + SkillSource
                                   ↓
                    ┌────────────────────────────┐
                    │ EvalCoordinator            │
                    │ baseline / pinned skill    │
                    └──────────────┬─────────────┘
                                   ↓
                    EvalRun + Metrics + Failures
                                   ↓
                    ┌────────────────────────────┐
                    │ QualityGate                │
                    └──────────────┬─────────────┘
                         拒绝 ↙             ↘ 通过
                      REJECTED          REVIEW_REQUIRED
                                              ↓ 人工审批
                                            ACTIVE
                                              ↓
用户任务 → SkillRetriever → SkillContextRenderer → PersistentAgentRunner
                    ↑                         ↓
              ACTIVE 版本              RunSkillSelection
```

数据流被分成三条：

1. **生成流**：训练轨迹变成 DRAFT 候选版本。
2. **验证与发布流**：候选版本经过配对评测、硬门禁和人工审批。
3. **运行时复用流**：普通任务只检索 ACTIVE 版本，并记录实际注入了什么。

三个流共享数据库记录，但使用不同的应用服务。CandidateGenerator 没有发布权限，EvalCoordinator 没有人工审批权限，普通 Worker 不能加载 DRAFT 版本。

## 6. 一次完整 Skill 生命周期

以“对比两个开源 Agent 项目”为例：

```text
1. 训练集中的多个任务以 BASELINE 模式运行。
2. Validator 检查项目覆盖、引用数量和报告结构。
3. 只有 COMPLETED 且 validation_passed 的 EvalRun 成为合格来源。
4. TraceSanitizer 读取来源，删除密钥、绝对路径、临时 ID 和无关失败分支。
5. CandidateGenerator 输出结构化 SkillDefinition。
6. DSL Validator 检查 Schema、DAG、变量引用、工具 allowlist 和风险上限。
7. Registry 创建不可变 DRAFT SkillVersion 和 SkillSource。
8. 用户启动评测，版本进入 EVALUATING。
9. EvalCoordinator 对每个留出 Case 创建 BASELINE 与 PINNED_SKILL 两次运行。
10. Validator 在模型不可见的环境中校验两个结果。
11. MetricsCollector 比较成功率、Token、Tool Calls 和延迟。
12. QualityGate 先检查正确性、安全性和数据隔离，再比较效率。
13. 不通过则进入 REJECTED；通过则进入 REVIEW_REQUIRED。
14. 用户查看来源、差异、失败样本和门禁报告，决定批准或拒绝。
15. 批准时原子发布为 ACTIVE；旧 ACTIVE 同时进入 RETIRED。
16. 新的普通任务通过 RETRIEVAL 模式检索并注入 ACTIVE Skill。
17. 若新版本退化，用户选择旧 RETIRED 版本执行手动回滚。
```

## 7. 第三阶段关键不变量

### 7.1 Skill 内容不可原地修改

任何正文变化都创建新的 DRAFT SkillVersion。版本行创建后，`definition`、`content_hash`、`schema_version` 和来源集合不可修改；生命周期状态可以按状态机迁移。这样版本差异和历史评测才可信。

### 7.2 普通任务只加载 ACTIVE 版本

普通 RETRIEVAL 模式只能查询 `Skill.status == ENABLED` 且 `SkillVersion.status == ACTIVE` 的版本。EVALUATING 或 REVIEW_REQUIRED 版本只能由内部 EvalCoordinator 使用 PINNED_SKILL 模式运行。

### 7.3 Skill 不能扩大权限

Skill 声明的 allowed_tools 必须是运行时 ToolRegistry 的子集，声明的 max_effective_risk 只能收紧 Policy，不能放宽。即使 Skill 文本要求执行 Shell，ToolExecutor 仍会按照真实 Policy 拒绝或请求审批。

### 7.4 留出集对生成器不可见

Extractor API 只接收训练 EvalRun ID，不接收任意 Run ID。Repository 查询必须联结 EvalCase.split，并在服务层确认全部为 TRAIN。留出 Case 的私有期望值和 Validator 配置不能进入 Agent 上下文、TraceSanitizer 或 CandidateGenerator 输入。

### 7.5 配对运行只允许一个实验变量

同一 Pair 的 provider、model、Prompt、Tool manifest、Policy、预算、数据集版本和代码版本必须一致。baseline 与 skill 两次运行唯一允许的系统性差异是 `skill_version_id`；无法保证时该 Pair 标记为不可比较，不能进入门禁统计。

### 7.6 发布和回滚必须原子化

同一个 Skill 同一时刻至多一个 ACTIVE 版本。发布与回滚必须锁住 Skill 聚合，检查 `lock_version`，在同一事务更新旧/新版本、active_version_id，并写 PromotionDecision。

### 7.7 哈希输入必须规范化

JSON 对象先按 key 排序，使用 UTF-8、固定分隔符并排除时间戳等非语义字段后再计算 SHA-256。不能直接对格式不同的 YAML 文本或 `str(dict)` 计算内容哈希。

## 8. 声明式 Skill DSL

### 8.1 v0.3 的最小结构

下面是便于阅读的 YAML 展示；数据库保存 Pydantic 校验后的 JSON，内容哈希基于规范化 JSON：

```yaml
schema_version: 1
name: compare_open_source_projects
description: 对多个开源项目进行基于证据的架构比较
triggers:
  - 项目对比
  - 仓库调研
inputs:
  repositories:
    type: array
    item_type: string
    required: true
preconditions:
  allowed_tools: [web_search, web_fetch, artifact_write]
  max_effective_risk: R1
steps:
  - id: discover_sources
    action: tool
    tool: web_search
    depends_on: []
    foreach: ${inputs.repositories}
    args:
      query: ${item} official README architecture
  - id: fetch_sources
    action: tool
    tool: web_fetch
    depends_on: [discover_sources]
    args:
      url: ${steps.discover_sources.output.urls}
  - id: synthesize_report
    action: model
    depends_on: [fetch_sources]
    instruction: 按统一维度比较项目，并为关键结论附上来源
success_criteria:
  - 所有目标项目均被覆盖
  - 每个关键结论包含来源
validators:
  - report_has_citations
  - all_repositories_covered
```

SkillVersion 的版本号不写在 DSL 正文里，由 Registry 在同一个 Skill 下分配单调递增整数。这样内容和数据库身份不会出现两个相互矛盾的版本号。

### 8.2 步骤类型

v0.3 只定义两种步骤，并使用 `action` 作为 Pydantic discriminated union 的判别字段：

- `ToolStep`：引用一个已注册工具、静态参数模板、依赖步骤和可选 foreach。
- `ModelStep`：给出目标明确的 instruction、依赖步骤和预期输出说明。

Validator 不作为能够修改状态的步骤，而是在 Run 完成后由独立验证器执行。这样模型不能读取隐藏答案，也不能伪造“验证通过”。

### 8.3 受限引用表达式

允许的表达式只有：

```text
${inputs.<name>}
${steps.<step_id>.output}
${steps.<step_id>.output.<field>}
${item}
```

解析器只做路径查找和类型检查，不调用 `eval()`，不支持属性调用、算术表达式、过滤器、模板导入或文件读取。所有引用必须指向已经声明的输入或依赖步骤；foreach 之外禁止 `${item}`。

### 8.4 DAG 静态检查

Schema 校验通过后还要进行语义检查：

1. step id 唯一且满足稳定命名规则；
2. `depends_on` 只能引用存在的步骤；
3. 依赖图不能成环；
4. 引用的数据必须来自当前步骤的祖先；
5. tool 必须存在于 Registry 且属于 Skill allowlist；
6. 工具风险不得超过 Skill 和系统风险上限；
7. 步骤数、foreach 展开数和文本长度不能超过配置限制；
8. success_criteria 与 validators 不能为空；
9. 禁止 Shell、绝对路径、凭据字段和未知 action。

### 8.5 `artifact_write` 与现有 `file_write`

总设计中的 `artifact_write` 是阶段三新增的窄能力：只允许在当前 Run 下创建一个新 Artifact，文件名必须唯一，不能覆盖。它不是现有 `file_write(overwrite=True)` 的别名。Skill 默认 allowlist 可以包含 `artifact_write`，但不包含 Shell 和任意覆盖写，从能力层面减少候选 SOP 的风险。

### 8.6 Schema 版本与兼容性

- `schema_version=1` 的解析器必须拒绝未知字段。
- 新增可选字段可以保持同一 Schema 版本；改变字段语义或删除字段必须升级版本。
- Registry 保存原始规范化定义和 content_hash。
- 运行时遇到未知 Schema 版本直接拒绝加载，不能猜测或静默降级。
- Pydantic 生成的 JSON Schema 可以供 API 和前端表单使用，但领域语义检查仍由独立 `SkillDefinitionValidator` 完成。

## 9. 阶段三持久化模型

### 9.1 核心表

| 表 | 关键字段 | 作用 |
|---|---|---|
| skills | id、name、slug、description、status、active_version_id、lock_version | Skill 聚合与当前发布指针 |
| skill_versions | id、skill_id、version、schema_version、definition、content_hash、lifecycle_status、created_at | 不可变版本正文 |
| skill_sources | id、skill_version_id、source_run_id、source_eval_run_id、trace_artifact_id、source_trace_hash | 来源和冻结提炼输入 |
| run_skill_selections | id、run_id、skill_version_id、mode、rank、score、query_terms、created_at | 记录一次 Run 为什么加载某版本 |
| eval_datasets | id、name、version、content_hash、status、created_at | 不可变数据集版本 |
| eval_cases | id、dataset_id、case_key、task_family、split、public_input、private_validators、risk_profile | 单个训练或留出 Case |
| eval_experiments | id、kind、skill_version_id（可空）、dataset_id、status、config_hash、gate_report、lease 字段 | 来源验证或可恢复配对评测 |
| eval_runs | id、experiment_id、eval_case_id、mode、repeat_index、task_id、run_id、paired_eval_run_id、metrics、passed | 一次具体运行和配对关系 |
| promotion_decisions | id、skill_version_id、action、reviewer、reason、gate_report_hash、created_at | 审批、拒绝、发布、禁用和回滚审计 |

为了保证评测配置可重现，`eval_experiments` 还应保存只读 `config_snapshot`，包含 provider、model、采样参数、Prompt hash、Tool manifest hash、Policy hash、预算、Skill hash、数据集 hash 和 code_version。

### 9.2 状态枚举

Skill 聚合状态：

```text
ENABLED
DISABLED
DEPRECATED
```

SkillVersion 生命周期：

```text
DRAFT → EVALUATING → REVIEW_REQUIRED → ACTIVE → RETIRED
                  ↘ REJECTED
```

EvalExperiment 状态：

```text
QUEUED → RUNNING → COMPLETED
                 ↘ FAILED
       → CANCELLED
```

数据集状态：

```text
DRAFT → FROZEN → RETIRED
```

只有 FROZEN 数据集可以用于正式门禁。修改任何 Case 都创建新数据集版本，不能修改已经参与 PromotionDecision 的版本。

### 9.3 必须建立的约束

- `skills.slug` 唯一；
- `(skill_id, version)` 唯一，version 大于 0；
- `skill_versions.content_hash` 使用固定长度格式；
- `(skill_version_id, source_run_id)` 唯一；
- `(dataset_id, case_key)` 唯一；
- `(experiment_id, eval_case_id, mode, repeat_index)` 唯一；
- SKILL_COMPARISON 实验中的每个 Pair 恰好由一个 BASELINE 和一个 PINNED_SKILL EvalRun 组成；
- `run_skill_selections(run_id, rank)` 唯一；
- active_version_id 必须属于同一个 Skill；这一跨行规则由带行锁的服务事务保证；
- EvalRun 的 skill_version_id 在 BASELINE 模式必须为空，在 PINNED_SKILL 模式必须存在。

### 9.4 删除策略

进入 EVALUATING、REVIEW_REQUIRED、ACTIVE、RETIRED 或 REJECTED 的版本都不物理删除。数据集、来源、评测和发布决定属于审计证据，只允许退休或禁用。DRAFT 如果从未参与评测，可以提供显式清理命令，但默认仍保留。

## 10. 提炼前的 Trace 合格性与冻结

### 10.1 阶段二 Trace 还缺什么

阶段二 Trace 已经足够排查运行事件、工具、审批和副作用，但“可调试”不等于“可用于训练提炼”。阶段三模块 0 必须补齐提炼所需的规范化 `TraceBundle`：

- 任务公开输入和最终输出；
- 每个完整 Turn 的请求/响应摘要与 Usage；
- 工具名、脱敏参数、结果摘要、风险和状态；
- Artifact 的内容哈希与允许读取的文本引用；
- Provider、模型、Prompt、工具、Policy、预算和代码版本哈希；
- Validator 结果；
- 明确排除 chain-of-thought、密钥和未完成流片段。

大体积内容继续放 Artifact；TraceBundle 只保存稳定摘要和引用。

### 10.2 来源 Run 的硬条件

`TraceEligibilityChecker` 只有在全部条件满足时才接受来源：

1. Run 属于 TRAIN EvalCase；
2. Run 状态为 COMPLETED；
3. EvalRun 的确定性验证结果为 passed；
4. 没有 PENDING Approval、UNKNOWN ToolEffect 或权限拒绝；
5. Snapshot 和运行配置版本兼容；
6. Trace 数据完整，来源 Artifact 哈希可验证；
7. 不包含被禁止的工具或高于配置上限的风险；
8. 同一来源尚未被重复添加到该版本。

### 10.3 清洗与冻结

`TraceSanitizer` 应按结构处理数据，而不是对整段 JSON 做简单字符串替换：

- 按字段名递归删除 API Key、Authorization、密码、Token 和推理内容；
- 把 Workspace 绝对路径改成语义占位符，例如 `${workspace}/report.md`；
- 把 run_id、task_id、临时目录、端口和时间戳从候选步骤中移除；
- 外部网页正文只保留与成功步骤有关的脱敏摘要和来源 URL；
- 保留失败分支的类别与教训，但不复制无关大输出；
- 检测疑似凭据、高熵字符串、私钥头和本机路径；
- 生成不可修改的 Trace Artifact，并对规范化内容计算 source_trace_hash。

### 10.4 CandidateGenerator 边界

定义 `CandidateGenerator` 协议，至少有：

- `MockCandidateGenerator`：测试中固定返回合法或故意非法的 SkillDefinition；
- `ModelCandidateGenerator`：把多个已清洗训练 Trace 作为不可信资料交给 ModelProvider，要求只返回结构化候选 JSON。

Generator 没有数据库发布权限。它输出的内容必须重新经过 Pydantic 和语义 Validator；解析失败、引用未知工具或包含危险内容时只记录提炼失败，不能自动修补后直接发布。

## 11. Skill Registry 与生命周期

### 11.1 Skill 和 SkillVersion 为什么分开

`Skill` 表示长期身份，例如“开源项目对比”；`SkillVersion` 表示某一次不可变实现。用户检索的是 Skill，实际运行和评测必须固定到 SkillVersion。active_version_id 让普通任务不用猜应该加载哪个版本。

### 11.2 新建和修改版本

- 第一次提炼时创建 Skill 聚合和 version=1 的 DRAFT；
- 修改 DRAFT 不覆盖原行，而是创建 version=2 的新 DRAFT；
- 新版本可以声明 `parent_version_id`，用于生成差异；
- Registry 在锁住 Skill 行后分配 `max(version)+1`；
- definition 通过校验后计算 content_hash，随后不可改变；
- 来源可以有多个，但必须全部是合格 TRAIN EvalRun。

### 11.3 生命周期状态机

状态机使用纯 Python 函数，Repository 不偷偷改变状态：

```text
DRAFT          → EVALUATING、REJECTED
EVALUATING     → REVIEW_REQUIRED、REJECTED
REVIEW_REQUIRED→ ACTIVE、REJECTED
ACTIVE         → RETIRED
RETIRED        → ACTIVE（只允许由 rollback/promotion 服务触发）
REJECTED       → 无后继；修改后创建新的 DRAFT
```

评测崩溃时版本不能永远卡在 EVALUATING。恢复服务根据 EvalExperiment 的持久化状态决定继续未完成 Pair、重新排队，或把实验标记 FAILED；只有完整门禁报告才能推进版本状态。

### 11.4 事件与审计

Skill 生命周期不复用 RunEvent，因为它不是某一个 Run 的事件。新增 SkillEvent 或在 PromotionDecision 中保存结构化审计，至少记录：

- skill/version ID；
- 操作类型和前后状态；
- actor/reviewer 展示值；
- 关联 experiment、gate report hash；
- reason 与时间；
- 变更前后 active_version_id。

在没有认证的 v0.3 本地版本中，reviewer 只是用户填写的审计标签，不是经过身份认证的真实主体。README 和页面必须明确这一点。

## 12. 可解释检索与元数据过滤

### 12.1 为什么 v0.3 不用向量数据库

阶段三预计只有几十个 Skill。先使用可解释的关键词/BM25，能够展示每个命中词和得分，也更容易在配对评测中保持稳定。pgvector 和混合召回属于阶段四。

### 12.2 检索前过滤

召回前先做硬过滤：

- Skill 聚合必须 ENABLED；
- 版本必须等于 active_version_id 且状态为 ACTIVE；
- schema_version 被当前 Runtime 支持；
- Skill allowed_tools 是当前 ToolRegistry 的子集；
- Skill 风险上限不超过当前 Task/Policy；
- 输入 Schema 与任务提取出的元数据兼容；
- 不使用已退休、已拒绝或内容哈希不匹配的版本。

### 12.3 BM25 的最小实现

v0.3 不强制增加搜索服务。对 ACTIVE Skill 的 name、description、triggers 和输入字段建立内存索引：

- 英文和数字按小写单词切分；
- 中文使用连续单字与二元词片，保证实现确定且无需外部分词服务；
- 保存文档长度、词频和逆文档频率；
- 使用固定 k1、b 参数计算 BM25；
- 结果按 score 降序，再按 skill_id 稳定排序；
- 低于阈值时不注入任何 Skill，不能为了“看起来智能”强行命中。

数据规模较小时可以每次从数据库加载 ACTIVE 版本并构建索引；只有测量到性能问题后才增加缓存。缓存不得成为事实来源，发布、禁用或回滚后必须失效。

### 12.4 选择结果也要进入 Trace

`RunSkillSelection` 保存：检索 query、归一化词项、过滤原因、rank、score、选中的 version/hash 和模式。没有命中也记录 `skill.none_selected` 事件。这样可以区分“Skill 本身无效”和“检索根本没有选中 Skill”。

## 13. Skill 运行模式与上下文注入

### 13.1 三种运行模式

```text
BASELINE      不加载 Skill；作为评测基线
RETRIEVAL     普通任务从 ACTIVE 版本中检索 Top-K
PINNED_SKILL  内部评测显式固定一个候选版本
```

公开 Task API 默认使用 RETRIEVAL，也可以显式选择 BASELINE 方便演示。PINNED_SKILL 只允许内部 EvalService 调用，防止用户绕过发布状态使用 DRAFT/EVALUATING 版本。

### 13.2 注入位置

外部网页资料继续作为不可信 user context。已经通过门禁并获批准的 Skill 属于内部操作规程，应由 `SkillContextRenderer` 生成稳定文本，加入 system message 中明确标记的 Skill 区域：

```text
系统基础规则
→ 已批准 Skill 摘要与步骤
→ “Skill 不得覆盖 PermissionPolicy；条件不满足时忽略并说明”
→ 外部不可信上下文
→ 用户任务
```

不能简单把 Skill 伪装成用户消息，否则它和外部资料优先级相同；也不能让 Skill 覆盖整个 system prompt，否则版本会意外删除安全规则。

### 13.3 v0.3 采用指导式 SOP

第三阶段先采用“结构化 DSL + 稳定渲染 + 模型遵循”的指导式执行，而不是另写一个直接控制工具的 DAG 执行器。原因是：

1. 可以复用已经测试充分的 AgentLoop 和 ToolExecutor；
2. Skill 的价值可以通过工具选择和迭代次数真实体现；
3. PermissionPolicy 仍是最终能力边界；
4. 避免在秋招冻结版同时维护第二套执行引擎。

DSL 的 DAG 和工具引用仍要严格校验，因为它们提供可审计结构和后续扩展基础。未来如果增加确定性 SkillExecutor，应作为独立版本能力，不能静默改变 schema_version=1 的执行语义。

### 13.4 Top-K 控制

默认最多注入 1 个完整 Skill；如果启用 Top-K>1，只先注入摘要和选择理由，再展开得分最高且输入匹配的一个版本。阶段三的重点是证明单个 Skill 的效果，不是堆叠大量 Prompt 消耗上下文。

## 14. 可重现运行配置

### 14.1 为什么现有 config_hash 不够

阶段二 LoopState 的 config_hash 用于判断快照能否续跑，但评测需要更完整的实验指纹。阶段三新增 `RunConfigSnapshot`，至少包含：

- provider、model、base URL 的非敏感标识；
- temperature、seed 等已支持采样参数；
- system prompt hash 和 Skill content hash；
- ToolDefinition、工具实现版本和 Registry hash；
- PermissionPolicy/Sandbox 配置 hash；
- max_iterations、超时、Token 与重试预算；
- dataset/case hash；
- code_version。

`code_version` 由 CI 或镜像构建参数注入，不能依赖运行容器中存在 `.git` 目录。任何密钥和数据库密码都不能进入快照或哈希原文。

### 14.2 工具版本

仅记录工具名不够：同名工具实现改变后，评测结果可能不可比较。阶段三为 BaseTool 增加稳定 `implementation_version`，Tool manifest hash 由名称、参数 Schema、风险、副作用、并发属性和实现版本共同计算。

### 14.3 可比较性检查

MetricsCollector 计算 Pair 前先比较两个 config snapshot。只允许 execution_mode 和 skill_version_id 不同；其他差异会产生 `not_comparable` 原因，该 Pair 保留用于排障，但不参与成功率和效率统计。

## 15. Eval 数据集设计

### 15.1 数据集不是测试代码的附属文件

EvalDataset 是有版本、有哈希、可冻结的领域对象。仓库中的 JSON 文件便于评审和复现，导入 PostgreSQL 后形成不可变版本。数据库是运行事实来源，Git 文件是可审查的定义来源，两者通过 content_hash 对齐。

### 15.2 EvalCase 结构

```json
{
  "case_key": "compare_agents_holdout_01",
  "task_family": "open_source_comparison",
  "split": "holdout",
  "public_input": {
    "goal": "比较项目 A 与项目 B 的架构，并生成带来源报告"
  },
  "private_validators": [
    {"type": "contains_sections", "sections": ["架构", "工具", "安全"]},
    {"type": "minimum_citations", "count": 2},
    {"type": "artifact_exists", "path": "report.md"}
  ],
  "risk_profile": {"max_risk": "R1", "forbidden_tools": ["shell"]}
}
```

Agent 只能看到 public_input。private_validators、期望值和隐藏标签只提供给 ValidatorRunner。

### 15.3 训练集与留出集

- 按任务族划分，不能把同一输入换个文件名后分别放入 TRAIN 和 HOLDOUT；
- 来源仓库、目标主题和难度应尽量分层；
- 先冻结 split，再开始 Candidate 提炼；
- 任何看过 HOLDOUT 私有答案的人为调参都要记录，必要时建立新数据集版本；
- 至少准备 20～30 个可重复 Case，并同时保留成功、失败和退化样本；
- Demo 可以使用较小 smoke 数据集，但不能拿 smoke 结果替代正式报告。

### 15.4 数据集泄漏防线

1. Extractor Repository 只提供 TRAIN 查询方法；
2. CandidateGenerator 输入 DTO 不包含 split 之外的任意 Case 数据；
3. Eval Worker 构造 Agent 输入时只取 public_input；
4. Validator 在 Run 终态之后独立运行；
5. Trace 输出默认隐藏 private_validators；
6. API 把数据集管理接口与普通运行接口分开；
7. 安全测试故意尝试用 HOLDOUT EvalRun 生成 Skill，必须失败。

## 16. Validator 与沙箱回放

### 16.1 Validator 协议

```text
Validator.validate(case, run_trace, artifacts) → ValidationResult
```

ValidationResult 至少包含 validator 名称、版本、passed、结构化证据、失败原因和执行耗时。Validator 必须是注册表中的可信实现，EvalCase 只能引用名字和数据参数，不能携带 Python 源码。

### 16.2 v0.3 优先实现的确定性验证器

- `run_completed`：Run 是否正常完成；
- `contains_sections`：报告是否包含规定章节；
- `minimum_citations`：引用数量是否达到要求；
- `covers_items`：是否覆盖全部输入对象；
- `json_schema`：结构化输出是否符合固定 Schema；
- `artifact_exists`：指定 Artifact 是否存在且哈希可读；
- `file_content`：文件内容满足字符串或正则断言；
- `tool_policy`：是否调用禁用工具或超过风险上限；
- `no_unknown_effects`：是否不存在 UNKNOWN 副作用；
- `max_tool_calls`：是否超过 Case 明确预算。

### 16.3 LLM Judge 的位置

事实正确性或表达质量有时难以完全用规则判断，可以增加统一 Rubric 的 LLM Judge，但它只能作为辅助结果：

- Judge 看不到 baseline/skill 标签，减少偏见；
- 固定 Judge 模型、Prompt 版本和采样配置；
- 保存输入摘要、输出和 hash；
- 不允许 Judge 单独推翻确定性安全失败；
- 报告中单列 Judge 结果，不伪装成客观真值。

### 16.4 沙箱回放

优先用录制的 Mock 搜索、固定 HTTP 响应和临时 Workspace 回放 Tool 路径，使正式门禁在没有公网时也能重复。真实模型/公网实验单独标记环境，不和确定性回放结果混为一组。

## 17. 配对评测运行器

### 17.1 为什么需要 EvalExperiment

20～30 个 Case、多个 repeat 和 baseline/skill 两种模式会产生大量普通 Run。EvalExperiment 保存整批进度，EvalRun 连接每个普通 Task/Run，使协调器崩溃后可以从未完成 Pair 继续，而不是丢失或重复统计。`SOURCE_VALIDATION` 实验可以只含 TRAIN baseline Run，用于建立提炼来源；`SKILL_COMPARISON` 实验才要求完整 Pair 和候选 skill_version_id。

### 17.2 协调流程

```text
创建 EvalExperiment
→ 冻结 config_snapshot
→ 对 case × repeat 建立 Pair 占位记录
→ 提交 BASELINE Task
→ 等待终态并运行 Validator
→ 提交 PINNED_SKILL Task
→ 等待终态并运行 Validator
→ 检查两个配置是否可比较
→ 收集 Pair metrics
→ 所有 Pair 完成后生成 GateReport
```

Pair 顺序可以按 repeat 交替 baseline-first / skill-first，降低服务时间漂移造成的固定顺序偏差。唯一约束保证协调器恢复时不会重复创建相同 EvalRun。

### 17.3 指标采集

每个 EvalRun 至少记录：

- 是否通过全部硬 Validator；
- 输入、输出、总 Token；Usage 缺失时记 unknown，不能写 0；
- Tool Calls 总数、成功数、失败数和按工具分布；
- Run 端到端延迟，以及可取得的模型/工具分项耗时；
- Approval、permission_denied、UNKNOWN effect 和恢复次数；
- 检索是否命中、得分和实际注入版本；
- 失败类别和证据引用。

### 17.4 聚合报告

同时输出原始 Pair、均值、中位数、成功/失败数量和 paired delta。效率指标只统计双方都通过且 Usage 完整的可比较 Pair；不能把失败 Run 的低 Token 当成效率提升。样本量较小时诚实展示每个 Case，不声称统计显著性。

## 18. QualityGate：发布前硬门禁

门禁按顺序执行，前一层失败后不再用后面的效率分数抵消：

### 18.1 静态正确性门禁

- DSL Schema、引用、DAG 和输入类型全部通过；
- content_hash、source_trace_hash 和 config hash 可复算；
- 所有来源均为已通过的 TRAIN EvalRun；
- 至少达到配置的最小独立来源数；
- 工具和 Validator 都存在且版本受支持。

### 18.2 隐私与安全门禁

- 不含密钥、私钥、Token、用户隐私、绝对路径和临时环境 ID；
- 不含 Shell、任意代码、未知模板表达式和未授权工具；
- Skill 风险不高于 R1 默认上限；
- skill 模式没有新增 permission_denied、UNKNOWN effect 或安全 Validator 失败。

### 18.3 留出集正确性门禁

- 使用 FROZEN HOLDOUT 数据集；
- 所有 Pair 配置可比较；
- skill 成功率不得低于 baseline；
- 关键安全 Case 不允许任何回归；
- 每个任务族单独报告，不能只用总体平均掩盖某一族退化。

### 18.4 效率排序

只有前三层全部通过，才比较 Token、Tool Calls 和延迟。效率没有改善不一定自动拒绝，但 GateReport 必须明确“正确但无可测效率收益”，由人工决定是否值得发布。正确性下降则必须自动 REJECTED，不能交给人工强行批准。

GateReport 保存每条规则的 pass/fail、阈值、实际值、样本 ID 和失败证据，并计算不可变 hash。REVIEW_REQUIRED 版本关联的报告随后不能被替换。

## 19. 人工审批、发布、禁用和回滚

### 19.1 人工评审看到什么

评审页至少同时展示：

- 当前候选与父版本的结构化差异；
- 来源 Run、Trace hash 和清洗后的提炼输入；
- 数据集版本和配置 hash；
- baseline/skill 成功率和 paired delta；
- 所有失败、退化和不可比较样本；
- 工具 allowlist、风险和静态安全报告；
- QualityGate 的每一条结果。

### 19.2 发布事务

```text
锁住 Skill 行
→ 检查 lock_version
→ 确认候选仍为 REVIEW_REQUIRED 且 GateReport 未变化
→ 当前 ACTIVE（如有）改为 RETIRED
→ 候选改为 ACTIVE
→ 更新 active_version_id
→ 写 PromotionDecision(APPROVE)
→ 提交
```

任一步失败都回滚，不能出现两个 ACTIVE 版本。

### 19.3 禁用与弃用

- DISABLED：临时停止检索，可重新启用；active_version_id 保留。
- DEPRECATED：不再用于新任务，历史 Trace 和版本仍可查看；v0.3 不提供恢复为 ENABLED 的快捷操作，避免误用。
- 禁用不等于删除，也不改变历史 EvalRun。

### 19.4 手动回滚

回滚目标必须是同一 Skill 的 RETIRED 且曾经通过门禁的版本：

```text
当前 ACTIVE → RETIRED
目标 RETIRED → ACTIVE
active_version_id → 目标
PromotionDecision(action=ROLLBACK, from, to, reason)
```

回滚不重新计算旧报告，但要先检查当前 Runtime 仍支持其 schema、工具和 Validator 版本。不兼容时拒绝回滚，并要求创建适配后的新 DRAFT。

## 20. API 设计

阶段三在 `/api/v1` 下增加：

```text
POST   /skills/extractions
GET    /skills
GET    /skills/{skill_id}
POST   /skills/{skill_id}/versions
GET    /skill-versions/{version_id}
GET    /skill-versions/{version_id}/diff?against=<version_id>
POST   /skill-versions/{version_id}/evaluations
POST   /skill-versions/{version_id}/review
POST   /skills/{skill_id}/disable
POST   /skills/{skill_id}/enable
POST   /skills/{skill_id}/deprecate
POST   /skills/{skill_id}/rollback

POST   /eval-datasets/import
GET    /eval-datasets
GET    /eval-experiments/{experiment_id}
GET    /eval-experiments/{experiment_id}/report
GET    /eval-experiments/{experiment_id}/pairs
```

接口规则：

- 创建提炼或评测返回 202 和持久化 job/experiment ID，不在 HTTP 请求中运行整批模型任务；
- review、disable、enable、deprecate 和 rollback 必须带 expected_lock_version；
- API DTO 不直接暴露 ORM；
- 普通接口不返回 private_validators 和留出答案；
- diff 返回结构化字段差异，不只返回两段长文本；
- 错误使用稳定 code，例如 `invalid_skill_transition`、`dataset_leakage`、`gate_not_passed`、`version_conflict`；
- v0.3 尚无认证，所有写接口只允许本机演示，不能暴露公网。

## 21. 最小 React + TypeScript 展示面

阶段三前端位于独立 `frontend/`，使用 Vite、React 和 TypeScript，并通过生成的 OpenAPI 类型或手写最小 DTO 对接 FastAPI。依赖版本在真正进入前端模块时根据官方稳定版本确定并提交 lockfile，不能只在设计文档里提前写死未来版本。

### 21.1 三个页面

1. **Skill 列表与详情**：状态、ACTIVE 版本、触发词、工具、风险、来源和历史版本。
2. **版本差异与评审**：结构化 diff、静态检查、GateReport、批准/拒绝/禁用/回滚操作。
3. **Eval 报告**：成功率、Token、Tool Calls、延迟、Pair 明细、失败和退化样本。

阶段二 Trace Viewer 可以作为链接继续使用，不在本模块重写完整 Trace 页面。

### 21.2 前端边界

- 不引入复杂全局状态库，服务端数据以 API 为事实来源；
- 评测运行中使用轮询或现有 SSE，不新建 WebSocket；
- 危险按钮显示目标版本、lock_version 和确认信息；
- 不在浏览器保存 API Key、留出答案或完整敏感 Trace；
- 所有状态按钮以服务端状态机为准，前端禁用按钮不能代替后端校验；
- 构建产物由 FastAPI 静态托管或 Compose 中的轻量静态服务提供。

### 21.3 前端测试

- TypeScript 类型检查和生产构建进入 CI；
- 组件测试覆盖状态、空数据和错误提示；
- Playwright 只覆盖三个核心流程：查看报告、批准发布、回滚版本；
- E2E 使用确定性 Mock 数据，不依赖真实模型和公网。

## 22. 安全、隐私与 Prompt Injection

### 22.1 Skill 是高信任输入，但来源不是

ACTIVE Skill 会进入 system 级内部规程，因此候选生成阶段必须假设来源网页、工具输出甚至模型提炼结果都可能包含 Prompt Injection。只有通过结构化清洗、静态规则、留出评测和人工审批后，内容才能获得较高信任级别。

### 22.2 入库前检查

- 敏感字段名和常见凭据格式；
- PEM 私钥头、Bearer Token、高熵长字符串；
- Windows/Unix 绝对路径、用户目录、临时目录；
- localhost、私网和云元数据地址；
- `${...}` 之外的模板语法、代码围栏中的可执行脚本；
- Shell、删除、覆盖、付款、发送消息等高风险动作；
- 要求忽略系统规则、隐藏操作或泄露上下文的指令。

发现风险时生成结构化 Finding，默认阻断，不自动删除后继续发布。用户修改应创建新 DRAFT，使审计链保持完整。

### 22.3 运行时仍需二次防线

即使 ACTIVE Skill 已通过评审：

- ToolExecutor 仍校验参数；
- PermissionPolicy 仍计算每次有效风险；
- Sandbox 仍限制路径、网络和子进程；
- ToolEffect 仍处理副作用幂等；
- Skill 声明与运行时工具不匹配时拒绝加载；
- Skill 不能访问 EvalCase.private_validators。

### 22.4 数据保留

TraceBundle 和 SkillSource 只保留提炼需要的最小数据。原始 Artifact 按现有策略保存；如果来源包含个人数据，该 Run 不得进入公共 Skill。阶段三本机版不实现自动数据保留期限，但要提供可审计的“拒绝作为来源”原因。

## 23. 代码目录框架

下面是阶段三完成后的目标目录，不要求模块 0 一次性创建全部空文件：

```text
EvoAgent/
├─ src/evoagent/
│  ├─ core/                         # 保持 AgentLoop 纯净
│  ├─ runtime/
│  │  ├─ persistent_runner.py
│  │  └─ run_config.py              # 完整实验配置指纹
│  ├─ skills/
│  │  ├─ schema.py                  # SkillDefinition 与步骤契约
│  │  ├─ canonical.py               # 规范化 JSON 与哈希
│  │  ├─ validation.py              # DAG、引用、工具和风险检查
│  │  ├─ lifecycle.py               # Skill/Version 状态机
│  │  ├─ provenance.py              # 来源资格与 Trace 冻结
│  │  ├─ sanitizer.py               # 脱敏与环境信息清洗
│  │  ├─ extraction.py              # CandidateGenerator 与提炼服务
│  │  ├─ retrieval.py               # BM25 与元数据过滤
│  │  ├─ rendering.py               # 稳定上下文渲染
│  │  └─ service.py                 # Registry、发布、禁用和回滚
│  ├─ evals/
│  │  ├─ schema.py                  # Case、Experiment、Metric DTO
│  │  ├─ datasets.py                # 导入、冻结与 split 保护
│  │  ├─ coordinator.py             # 可恢复配对评测
│  │  ├─ metrics.py                 # Pair 与聚合指标
│  │  ├─ gates.py                   # 硬门禁与 GateReport
│  │  └─ validators/
│  │     ├─ base.py
│  │     ├─ artifacts.py
│  │     ├─ content.py
│  │     └─ safety.py
│  ├─ db/
│  │  ├─ models.py
│  │  └─ repositories/
│  │     ├─ skills.py
│  │     └─ evals.py
│  ├─ api/routes/
│  │  ├─ skills.py
│  │  └─ evals.py
│  └─ tools/builtin/
│     └─ artifact_write.py
├─ migrations/versions/
├─ evals/
│  └─ datasets/
│     ├─ smoke-v1.json
│     └─ open-source-research-v1.json
├─ frontend/
│  ├─ package.json
│  ├─ package-lock.json
│  ├─ tsconfig.json
│  ├─ vite.config.ts
│  └─ src/
│     ├─ api/
│     ├─ components/
│     └─ pages/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ security/
│  ├─ evals/
│  └─ e2e/
└─ docs/
```

## 24. 技术与依赖

### 24.1 后端

- Pydantic v2：DSL、步骤 discriminated union、严格校验和 JSON Schema；
- SQLAlchemy asyncio + Alembic：Skill、Eval 与 Promotion 数据；
- PostgreSQL：事实来源、约束、聚合锁和 Eval 租约；
- 现有 ModelProvider：真实 CandidateGenerator 与评测 Run；
- 纯 Python BM25：小规模、确定性、可解释检索；
- hashlib / json：规范化哈希，不另引入重量级序列化框架；
- pytest：数据集参数化、单元、集成、安全和端到端测试。

YAML 只作为人类可读的导入/导出格式时才增加安全解析依赖；首版可以只支持 JSON，避免因 YAML 自定义标签引入额外执行风险。

### 24.2 前端

- React + TypeScript：类型化组件和三个展示页面；
- Vite：开发服务器与生产构建；
- Playwright：关键浏览器流程；
- 不引入大型 UI 框架和全局状态库，除非页面复杂度确实需要。

### 24.3 不引入的依赖

- pgvector、Embedding SDK；
- LangChain/LangGraph Skill 抽象；
- Celery、Redis；
- 可执行模板引擎；
- 自动超参数搜索框架；
- 外部实验跟踪 SaaS。

## 25. 配置设计

建议新增以下 `EVOAGENT_*` 配置，并继续通过 Settings 做类型化校验：

| 配置 | 建议默认值 | 作用 |
|---|---:|---|
| SKILL_SCHEMA_VERSION | 1 | 当前支持的 DSL 版本 |
| SKILL_MAX_STEPS | 20 | 单个 Skill 最大步骤数 |
| SKILL_MAX_SOURCES | 10 | 单个候选最大来源数 |
| SKILL_MIN_SOURCES | 2 | 进入正式评测的最小独立来源数 |
| SKILL_RETRIEVAL_TOP_K | 1 | 普通 Run 最多展开的 Skill 数 |
| SKILL_RETRIEVAL_MIN_SCORE | 0.1 | 低于阈值不注入 |
| SKILL_MAX_EFFECTIVE_RISK | R1 | Skill 默认风险上限 |
| SKILL_ALLOWED_TOOLS | 明确列表 | 候选 Skill 工具白名单 |
| SKILL_EXTRACTOR_MODEL | 空/沿用模型 | 真实提炼模型 |
| EVAL_DATASET_ROOT | ./evals/datasets | 数据集文件根目录 |
| EVAL_REPEATS | 3 | 正式 Pair 重复次数 |
| EVAL_POLL_SECONDS | 1 | Coordinator 轮询间隔 |
| EVAL_LEASE_SECONDS | 60 | EvalExperiment 协调租约 |
| CODE_VERSION | dev | CI/镜像注入代码版本 |

配置关系：

- SKILL_MIN_SOURCES 不能大于 SKILL_MAX_SOURCES；
- SKILL_RETRIEVAL_TOP_K 必须为 0～3，0 表示全局关闭 Skill；
- SKILL_MAX_EFFECTIVE_RISK 在 v0.3 不能高于 R1；
- SKILL_ALLOWED_TOOLS 不得包含 shell；
- EVAL_REPEATS 至少为 1，正式报告建议为 3，smoke 可以为 1；
- EVAL_LEASE_SECONDS 必须允许 Coordinator 在三分之一租期内 heartbeat；
- CODE_VERSION 不能为空，正式 CI 不能使用 dev；
- 数据集根目录必须位于项目允许目录内，不能读取任意宿主机文件。

## 26. 测试与评测策略

### 26.1 单元测试

- DSL 合法/非法结构、额外字段和 Schema 版本；
- discriminated union 步骤解析；
- DAG 环、未知依赖、越级引用和 foreach 作用域；
- 规范化 JSON 和稳定 hash；
- Skill/Version/Eval 状态机所有合法与非法边；
- sanitizer 的密钥、路径、临时 ID 和 Prompt Injection 样本；
- BM25 排序、中文词片、阈值和稳定 tie-break；
- Validator 通过、失败和证据格式；
- Pair config 可比较性和指标聚合；
- QualityGate 中“效率不能抵消正确性”的规则。

### 26.2 数据库集成测试

- migration upgrade、downgrade 和 Alembic check；
- SkillVersion 唯一版本和 immutable 字段；
- 两个并发发布请求只能产生一个 ACTIVE；
- 乐观锁冲突返回明确错误；
- rollback 原子切换 active_version_id；
- EvalRun 唯一 Pair 防止 Coordinator 重复创建；
- 过期 Eval lease 可以安全接管；
- SQLite 做快速兼容，真实 PostgreSQL CI 验证锁和并发。

### 26.3 泄漏与安全测试

- HOLDOUT EvalRun 不能作为 SkillSource；
- CandidateGenerator 输入不含 private_validators；
- 含 API Key、私钥、绝对路径或 Shell 的候选被阻断；
- Skill 不能调用不在 Runtime Registry 中的工具；
- ACTIVE Skill 不能提高 PermissionPolicy 权限；
- 普通 Task API 不能使用 PINNED_SKILL 加载未发布版本；
- Trace/API 默认不返回留出答案。

### 26.4 确定性评测测试

使用 MockProvider、MockSearchProvider 和固定 Artifact 建立小型 smoke 数据集，验证：

- baseline 与 skill Pair 数量正确；
- 配置 hash 相同才能比较；
- 成功率相等且 Tool Calls 降低时门禁通过；
- 成功率下降时即使 Token 更少也拒绝；
- Usage 未知时不伪造效率提升；
- Coordinator 中断后从未完成 Pair 恢复。

### 26.5 端到端测试

至少覆盖：

```text
训练 Task
→ Validator 通过
→ 提炼 DRAFT
→ HOLDOUT 配对评测
→ REVIEW_REQUIRED
→ API 人工批准
→ 普通 Task 检索 ACTIVE Skill
→ 发布退化版本被门禁拒绝
→ 手动回滚旧版本
```

真实模型实验不放在每次 CI 的硬依赖中；CI 必须通过确定性 Mock 闭环，真实报告由显式命令生成并保存环境和配置 hash。

### 26.6 前端测试

- `npm run typecheck`；
- `npm run build`；
- API DTO 契约测试；
- Playwright 覆盖报告、批准和回滚；
- 后端拒绝非法状态时，前端显示服务端错误而不是假装成功。

## 27. 分模块实现顺序

阶段三建议继续拆成模块 0～12。每个模块只完成一个可验证闭环，并在完成后追加到《EvoAgent 源码讲解与学习手册》。

### 模块 0：阶段二基线冻结与评测前置契约

创建或修改：

- `runtime/run_config.py`
- Tool implementation_version 与 manifest hash
- extraction-grade TraceBundle DTO
- 阶段二回归与基线文档

实现：

- 把项目版本进入 `0.3.0.dev0`；
- 定义 RunMode：BASELINE、RETRIEVAL、PINNED_SKILL；
- 定义完整 RunConfigSnapshot 和稳定 config hash；
- 给工具建立实现版本和 Registry manifest hash；
- 补齐提炼需要的 TraceBundle，但不开始生成 Skill；
- 记录阶段二 172 项本地测试和远端 CI 基线。

测试：配置规范化、hash 稳定性、密钥不入快照、TraceBundle 完整性、阶段一二全量回归。

完成边界：能够回答“这两次 Run 是否只有 Skill 变量不同”，但还没有 Skill 表和 DSL。

需要理解：可重现性、实验变量、内容寻址、为什么 Run completed 不等于 task passed。

### 模块 1：Skill DSL 与静态验证

创建：

- `skills/schema.py`
- `skills/canonical.py`
- `skills/validation.py`
- DSL 契约与安全测试

实现：

- SkillDefinition、InputDefinition、ToolStep、ModelStep；
- Pydantic discriminated union 与 JSON Schema；
- 受限引用解析、DAG 拓扑检查、工具/风险 allowlist；
- 规范化 JSON、content_hash 和 schema_version 拒绝策略；
- 首版 JSON 导入；YAML 只保留文档示例。

测试：合法示例、额外字段、环、未知引用、`${item}` 越界、Shell/绝对路径、未知 Schema。

完成边界：纯内存中可以严格解析和拒绝 SkillDefinition，不连接数据库、不调用模型。

需要理解：DSL 与代码的区别、结构校验与语义校验、DAG、判别联合、规范化哈希。

### 模块 2：阶段三持久化模型与状态机

创建或修改：

- Skill、Version、Source、Selection ORM
- Dataset、Case、Experiment、EvalRun、PromotionDecision、SkillEvent ORM
- Skill/Eval Repository 与 UnitOfWork 接口
- Alembic migration
- PostgreSQL 约束测试

实现：

- 一次建立阶段三表之间的完整外键，不留下指向尚不存在表的过渡状态；
- Skill 聚合与版本分离，版本、来源、Case 和 Pair 唯一约束；
- Skill/Version、Dataset 和 EvalExperiment 的纯 Python 状态机；
- 不可变版本与冻结数据集字段检查；
- `artifact_write` 窄工具及 Artifact 元数据闭环。

测试：upgrade/downgrade、全部外键与唯一约束、不可变字段、状态机、artifact 唯一创建和路径安全。

完成边界：可以保存阶段三各类记录，但还没有数据集导入、Validator、提炼、评测或发布服务。

需要理解：聚合根、不可变版本、外键、乐观锁、数据库约束与服务层规则的区别。

### 模块 3：EvalDataset 与 Validator 框架

创建：

- `evals/schema.py`
- `evals/datasets.py`
- `evals/validators/`
- smoke 数据集

实现：

- Dataset/Case/Split 契约、导入、冻结和 hash；
- public_input 与 private_validators 隔离；
- Validator Protocol、Registry 和第一批确定性验证器；
- ValidatorResult 的证据结构；
- 以 SOURCE_VALIDATION 实验对已有 TRAIN Run 执行验证并登记 BASELINE EvalRun，供后续来源模块使用；
- 数据集版本不可变。

测试：数据集校验、重复 case_key、冻结后修改、私有答案不入 Agent 请求、每种 Validator。

完成边界：可以对一个已完成 Run 得出 passed/failed 并登记训练 EvalRun，但还不批量运行 Pair。

需要理解：测试夹具与领域数据集、隐藏标签、确定性 Oracle、LLM Judge 的局限。

### 模块 4：来源资格、Trace 清洗与冻结

创建：

- `skills/provenance.py`
- `skills/sanitizer.py`
- Trace Artifact 冻结服务
- 泄漏与敏感信息测试

实现：

- TraceEligibilityChecker；
- 只允许 passed TRAIN EvalRun；
- 结构化脱敏、路径占位、临时环境信息清理；
- 冻结 extraction Trace Artifact 和 source_trace_hash；
- 明确拒绝 HOLDOUT、UNKNOWN effect 和未决审批来源。

测试：凭据、私钥、Windows/Linux 路径、Prompt Injection、Holdout 泄漏、hash 篡改。

完成边界：可以产出安全、不可变的提炼输入，尚不调用 CandidateGenerator。

需要理解：数据血缘、最小化收集、训练/留出污染、为什么清洗失败应阻断而不是静默删除。

### 模块 5：CandidateGenerator 与 DRAFT 提炼

创建：

- `skills/extraction.py`
- MockCandidateGenerator
- ModelCandidateGenerator
- extraction API/Service 的最小版本

实现：

- 从多个合格训练 Trace 生成候选；
- Generator Protocol 与 Mock；
- 真实模型只接收清洗后的结构化资料；
- 模型输出重新经过 DSL 与安全校验；
- 原子创建 DRAFT SkillVersion、SkillSource 和提炼事件；
- 失败只记录原因，不创建半合法版本。

测试：确定性生成、多来源、非法 JSON、未知工具、模型输出注入、重复请求幂等。

完成边界：生成结果永远停在 DRAFT，不能跳过评测进入 ACTIVE。

需要理解：模型输出不可信、结构化生成、生成器与发布权限分离、来源哈希。

### 模块 6：BM25 Retriever 与 Skill 上下文

创建：

- `skills/retrieval.py`
- `skills/rendering.py`
- `run_skill_selections` 写入服务
- 检索回归测试

实现：

- 可解释 tokenizer 与纯 Python BM25；
- ACTIVE/ENABLED、工具、风险和输入元数据过滤；
- 阈值、Top-K 和稳定排序；
- SkillContextRenderer；
- BASELINE/RETRIEVAL 模式与选择 Trace；
- 无命中时保持阶段二行为不变。

测试：中英文查询、禁用/退休过滤、低分无命中、稳定排序、Skill 不能覆盖安全 Prompt。

完成边界：普通任务可以加载已有手工 ACTIVE fixture；候选版本仍不能通过真实生命周期发布。

需要理解：召回与过滤、BM25、可信上下文层级、为什么检索失败和 Skill 失败要分开观测。

### 模块 7：可恢复 EvalCoordinator 与配对运行

创建：

- `evals/coordinator.py`
- EvalExperiment/EvalRun Repository
- Eval Worker 命令或现有 Worker 的窄协调入口
- 中断恢复测试

实现：

- 创建 case × repeat × mode Pair；
- BASELINE 与 PINNED_SKILL 普通 Task；
- 候选版本仅通过内部 PINNED 模式注入；
- 配置可比较性检查；
- 实验 lease、heartbeat、幂等占位和恢复；
- 每个 Run 终态后调用 Validator。

测试：Pair 数量、唯一约束、Coordinator 崩溃接管、候选不能经公开 API 使用、取消实验。

完成边界：能够可靠跑完整批配对实验，只保存原始结果，尚未计算最终门禁。

需要理解：实验编排、配对设计、租约复用思想、重复消费与统计污染。

### 模块 8：MetricsCollector 与评测报告

创建：

- `evals/metrics.py`
- report DTO/API
- 配对指标测试

实现：

- 从 Run/Turn/ToolCall/Selection 汇总指标；
- success、Token、Tool Calls、延迟和安全指标；
- paired delta、均值、中位数、样本明细；
- Usage unknown 与 not_comparable 传播；
- 保存不可变报告 Artifact 和 report hash。

测试：失败 Run、未知 Usage、双方不通过、一方不通过、配置不一致、按任务族聚合。

完成边界：报告能如实描述效果和退化，但还不自动改变 SkillVersion 状态。

需要理解：配对比较、缺失值、幸存者偏差、均值与中位数、为什么失败样本必须保留。

### 模块 9：QualityGate 与生命周期推进

创建：

- `evals/gates.py`
- GateReport 契约
- SkillVersion evaluation service

实现：

- 静态、来源、泄漏、安全、正确性和效率分层门禁；
- DRAFT→EVALUATING→REVIEW_REQUIRED/REJECTED；
- 正确性下降强制拒绝；
- GateReport 逐条证据和 hash；
- 实验失败与候选质量失败使用不同状态/错误。

测试：成功率下降但 Token 下降仍拒绝、安全回归拒绝、正确但无效率收益进入人工评审、报告不可替换。

完成边界：系统可以判定候选是否值得人工看，但仍不能发布 ACTIVE。

需要理解：硬门禁、辅助指标、失败分类、为什么加权总分会掩盖严重回归。

### 模块 10：人工审批、发布、禁用与回滚 API

创建：

- `skills/service.py`
- `api/routes/skills.py`
- PromotionDecision 与 SkillEvent
- 并发发布测试

实现：

- 版本查询、结构化 diff 和来源查询；
- review approve/reject；
- 原子发布、旧版本退休；
- Skill enable/disable/deprecate；
- 兼容性检查后的手动 rollback；
- expected_lock_version 与稳定错误响应。

测试：非法状态、门禁未通过、两个并发批准、重复请求、跨 Skill 回滚、旧 Schema 不兼容。

完成边界：后端生命周期完整；reviewer 仍只是本机审计标签，不声称完成认证。

需要理解：CAS/乐观锁、事务原子性、审计记录、发布指针与版本状态。

### 模块 11：React + TypeScript Skill 与 Eval 页面

创建：

- `frontend/` Vite 工程
- Skill list/detail/diff 页面
- Eval report/pair detail 页面
- FastAPI 静态托管或 Compose 前端服务

实现：

- 类型化 API Client；
- 版本状态、来源、门禁、指标和失败样本展示；
- 批准、拒绝、禁用、启用和回滚交互；
- lock_version 冲突和后端错误提示；
- 页面构建纳入 Docker 与 CI。

测试：TypeScript、production build、组件空/错状态、Playwright 三个关键流程。

完成边界：只完成 Skill 与 Eval 展示，不重写聊天、Session 和完整 Trace 产品界面。

需要理解：前后端 DTO、服务端事实来源、乐观更新风险、浏览器 E2E。

### 模块 12：真实数据集、Demo、ADR 与 v0.3 冻结

创建或完善：

- 20～30 个可重复 TRAIN/HOLDOUT Case；
- 真实配对评测报告；
- Skill Demo 与安全说明；
- 阶段三 ADR、README、架构图和简历证据；
- Compose/CI 完整闭环。

实现：

- “首次探索→提炼→留出评测→审批→复用”主 Demo；
- 退化候选被拒绝 Demo；
- 发布新版本并手动回滚 Demo；
- 保留成功、失败、退化和不可比较样本；
- 报告真实 Token/Tool Calls/延迟，不预填改善百分比；
- 从全新环境按 README 复现。

测试：全量后端、PostgreSQL、前端构建、Playwright、Docker Compose 和三个 Demo smoke。

完成边界：达到 v0.3 秋招冻结标准后停止增加必做功能，不提前进入长期记忆、MCP 或自动发布。

需要理解：可复现实验、负面证据、范围冻结、怎样把工程结果写成诚实的简历指标。

## 28. 推荐学习顺序

```text
Pydantic 严格模型与 discriminated union
→ JSON Schema 与规范化序列化
→ DAG、拓扑排序与受限表达式
→ 数据血缘、内容哈希与不可变版本
→ 信息检索基础与 BM25
→ 训练集、验证集/留出集与数据泄漏
→ 确定性 Validator 与 LLM Judge
→ 配对实验、缺失值和偏差
→ 硬门禁与发布状态机
→ 乐观锁、原子发布和回滚
→ React + TypeScript 数据展示
→ Playwright 与可重复 Demo
```

本阶段最重要的不是“让 Agent 自动写一个 Skill”，而是能够回答：

- 为什么这个 Run 有资格成为来源？
- 生成器究竟看到了哪些数据？
- 留出答案是否泄漏？
- baseline 和 skill 是否真的可比较？
- 哪条门禁允许或阻止了发布？
- 这次效率提升是否以正确性下降为代价？
- 当前普通任务实际用了哪个版本？
- 回滚是否完整、原子且可审计？

## 29. 第三阶段完成标准

满足以下条件后，阶段三才可以结束：

- Skill DSL 只允许受限 ToolStep/ModelStep，不含可执行代码或 Shell。
- Pydantic Schema、DAG、引用、工具和风险静态校验完整。
- Skill、SkillVersion、SkillSource、EvalCase、EvalRun 和 PromotionDecision 持久化并有 Alembic migration。
- 版本内容不可变，同一 Skill 至多一个 ACTIVE 版本。
- 来源只能是通过确定性验证的 TRAIN EvalRun，HOLDOUT 泄漏测试通过。
- TraceSanitizer 可以阻断密钥、绝对路径、临时环境信息和危险指令。
- CandidateGenerator 只能创建 DRAFT，不能自行发布。
- BM25 + 元数据过滤能够稳定检索 ACTIVE 版本，并记录选择原因。
- BASELINE、RETRIEVAL 和内部 PINNED_SKILL 三种模式边界清楚。
- 至少 20～30 个可重复 Case，训练/留出 split 冻结并有内容哈希。
- EvalCoordinator 可以在中断后恢复，不重复统计 Pair。
- baseline/skill 使用相同配置快照，差异不可比较时不进入门禁。
- 正确性和安全性是硬门禁，效率指标不能抵消回归。
- GateReport 保存所有成功、失败、退化和缺失值证据。
- v0.3 发布必须人工批准；发布、禁用和手动回滚可审计。
- 普通任务不能加载未发布版本，Skill 不能扩大 Tool Policy 权限。
- React 页面可以查看来源、版本差异、评测报告和失败样本。
- 三个核心浏览器流程、PostgreSQL 并发、Docker Compose 和后端回归测试通过。
- README 可以从全新环境复现 Skill 生成、评测、发布、复用和回滚。
- 简历中的任务数和性能改善全部来自真实报告，不使用预设目标冒充结果。

完成本阶段即达到 v0.3 秋招冻结点。长期记忆、pgvector、MCP、多 Worker、多 Agent、自动发布和自动回滚仍属于后续阶段。

## 30. 后续协作方式

每个模块继续按照以下流程推进：

```text
1. 先读本模块目标、前置条件和完成边界
2. 说明新增数据结构、调用链和迁移影响
3. 只实现当前模块的最小闭环
4. 先运行阶段一二回归，再运行本模块测试
5. 对照真实数据流讲解代码
6. 补充源码讲解与学习手册
7. 更新开发进度与决策记录
8. 有架构取舍时新增或更新 ADR
9. 用户理解后再进入下一模块
```

阶段三第一个实现单元是“模块 0：阶段二基线冻结与评测前置契约”。在用户明确开始实现前，不创建 `skills/`、`evals/` 或前端业务代码。

## 31. 官方资料核对入口

真正实现时优先重新核对官方资料：

- [Pydantic discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)：ToolStep/ModelStep 的可预测联合解析；
- [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)：从模型生成 API/前端可消费 Schema；
- [JSON Schema 文档](https://json-schema.org/docs)：理解声明式结构、required 与额外字段边界；
- [pytest 参数化](https://docs.pytest.org/en/stable/how-to/parametrize.html)：把 EvalCase 变成可重复测试输入；
- [React TypeScript](https://react.dev/learn/typescript)：组件 Props、Hooks 与浏览器事件类型；
- [Vite 指南](https://vite.dev/guide/)：前端开发、构建和静态产物。

依赖版本、推荐写法和运行要求会变化，因此每进入相关模块时再次核对，不只依赖本文。BM25 的公式和评测统计实现还应在模块 5、8 开始前分别核对可靠的信息检索与实验设计资料，并把最终选型记录到 ADR。
