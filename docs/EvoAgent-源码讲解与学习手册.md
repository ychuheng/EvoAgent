# EvoAgent 源码讲解与学习手册

> 文档定位：本手册面向第一次接触 Agent 工程的学习者，用来解释 EvoAgent 已经实现的代码、模块之间的关系以及关键设计原因。后续每完成一个模块，都在本手册中继续追加对应章节。

## 目录

1. 阅读说明
2. 项目要解决什么问题
3. 总体架构
4. 工程结构
5. 模块 0：工程初始化与配置
6. 模块 1：核心数据契约
7. 模块 1：运行事件系统
8. 模块 2：工具抽象与注册表
9. 模块 2：安全计算器
10. 模块 3：ToolExecutor
11. 模块 4：ModelProvider 与 MockProvider
12. 模块 5：ContextBuilder
13. 模块 6：AgentLoop
14. 模块 7：AgentRunner
15. 模块 8：OpenAICompatibleProvider 与 CLI
16. 模块 9：安全只读工具与阶段一收尾
17. 当前代码如何协作
18. 测试体系
19. 当前不能完成的功能
20. 后续阶段路线
21. 推荐阅读源码的顺序
22. 当前阶段应掌握的核心思想
23. 文档后续维护规则
24. 本阶段总结
25. 阶段二模块 0：工程配置
26. 阶段二模块 1：数据库与 Alembic
27. 阶段二模块 2：持久化模型与状态机
28. 阶段二模块 3：Repository、Unit of Work 与 Trace
29. 阶段二模块 4：Task Service 与最小 FastAPI
30. 阶段二模块 5：Job Lease 与单 Worker
31. 阶段二模块 6：Artifact、Snapshot 与 LoopState
32. 阶段二模块 7：PersistentAgentRunner 与恢复
33. 阶段二模块 8：分类重试与预算
34. 阶段二模块 9：Permission Policy、Approval 与 ToolEffect
35. 阶段二模块 10：Sandbox 与新增工具
36. 阶段二模块 11：SSE、完整 Trace 与 Viewer
37. 阶段二模块 12：容器装配、故障注入与阶段验收
38. 阶段三模块 0：可重现运行契约
39. 阶段三模块 1：声明式 Skill DSL
40. 阶段三模块 2：持久化模型、状态机与 ArtifactWrite
41. 阶段三模块 3：数据集与确定性 Validator
42. 阶段三模块 4：来源资格、清洗与冻结
43. 阶段三模块 5：候选生成与 DRAFT 提炼
44. 阶段三模块 6：BM25 检索与 Skill 上下文
45. 阶段三模块 0～6 的总调用链
46. 阶段三当前测试地图
47. 阶段三前七个模块的学习验收
48. 阶段三模块 7：可恢复 EvalCoordinator 与配对运行
49. 阶段三模块 8：MetricsCollector 与不可变评测报告
50. 阶段三模块 9：QualityGate 与评测生命周期
51. 阶段三模块 10：人工审批、发布、禁用与回滚 API
52. 阶段三模块 11：React + TypeScript 管理页面
53. 阶段三模块 12：真实数据集、完整 Demo 与阶段冻结
54. 阶段三完整调用链
55. 阶段三最终测试地图
56. 阶段三学习验收
57. 阶段四模块 0：v0.3 基线冻结与恢复装配
58. 阶段四模块 1：租约代次与统一写入保护
59. 阶段四模块 2：上下文契约、TokenCounter 与确定性裁剪
60. 阶段四模块 0～2 完整调用链
61. 阶段四模块 0～2 测试地图
62. 阶段四模块 0～2 学习验收
63. 阶段四模块 3：可恢复压缩与完整输出归档
64. 阶段四模块 4：Workspace、消息投影与版本化记忆
65. 阶段四模块 5：事实提议、人工确认与遗忘
66. 阶段四模块 3～5 完整调用链
67. 阶段四模块 3～5 测试地图
68. 阶段四模块 3～5 学习验收
69. 阶段四模块 6：Embedding 契约、派生索引与代次切换
70. 阶段四模块 7：混合检索、分区预算与选择冻结
71. 阶段四模块 6～7 完整调用链
72. 阶段四模块 6～7 测试地图与故障定位
73. 阶段四模块 6～7 学习验收
74. 阶段四模块 8：MCP 连接、发现与目录版本
75. 阶段四模块 8 完整调用链
76. 阶段四模块 8 测试地图与故障定位
77. 阶段四模块 8 学习验收
78. 阶段四模块 9：MCP 工具适配、目录冻结与执行身份
79. 阶段四模块 9 完整调用、恢复与卸载链
80. 阶段四模块 9 测试地图与故障定位
81. 阶段四模块 9 学习验收
82. 阶段四模块 10：独立控制器、固定规格与权限边界
83. 阶段四模块 10 完整执行、文件发布与故障恢复
84. 阶段四模块 10 网络出口、MCP 通道与测试地图
85. 阶段四模块 10 学习验收
86. 阶段四模块 11：持久队列、唤醒与服务配额
87. 阶段四模块 11：限流恢复、维护任务与 Eval fencing
88. 阶段四模块 11：测试地图与故障定位
89. 阶段四模块 11 学习验收
90. 阶段四模块 12：独立实验契约与数据隔离
91. 阶段四模块 12：执行链、成本与报告
92. 阶段四模块 12：数据集、测试与验收边界
93. 阶段四模块 12 学习验收
94. 阶段四模块 13：页面契约与 Memory 人工决定
95. 阶段四模块 13：MCP 目录审核与卸载
96. 阶段四模块 13：上下文证据、错误语义与测试地图
97. 阶段四模块 13 学习验收

## 1. 阅读说明

EvoAgent 会逐步从一个可测试的 Agent 内核，发展为支持可靠长任务和可验证 Skill 生命周期的 Agent Runtime。项目采用分模块开发方式，因此阅读时必须区分下面三种状态：

- **已实现**：仓库中已经存在代码和测试，可以实际运行。
- **接口已定义**：数据结构已经存在，但负责使用它的运行模块尚未实现。
- **计划实现**：只出现在设计文档或目标架构中，当前代码还不能完成对应功能。

本手册只把已经存在的代码描述为“已实现”。目标设计和后续模块会明确标注为“尚未实现”，避免把设计计划误认为项目现状。

### 1.1 当前进度

`v0.3：可验证 Skill 生命周期` 的模块 0～12 已全部实现。当前版本为 `0.4.0.dev0`，阶段四模块 0～13 已实现工程代码；PostgreSQL/Redis、双进程调度和 Runtime Mock 报告已验收，真实模型效果证据仍待补。最新页面增量见第 94～97 章；较早章节的测试数字保留对应交付时间点，模块 14 尚未完成。

已经完成：

- 模块 0～2：工程、数据契约、事件和基础工具系统
- 模块 3：ToolExecutor
- 模块 4：ModelProvider 与 MockProvider
- 模块 5：ContextBuilder
- 模块 6：AgentLoop
- 模块 7：AgentRunner
- 模块 8：OpenAICompatibleProvider 与 CLI
- 模块 9：安全只读工具、重复调用保护与安全并发

阶段一已经闭环。现在既可以使用 MockProvider 确定性运行和测试，也可以通过 CLI 连接 OpenAI-compatible 模型服务，调用计算、文件读取和网页读取工具，最后得到包含完整事件的 RunResult。

阶段二模块 0～12 已全部完成。阶段三已具备严格 DSL、来源验证与冻结、DRAFT 提炼、BM25 检索、可恢复配对评测、不可变报告、硬门禁、人工发布与回滚，以及 Skill/Eval 管理页面。尚未完成的生产能力包括认证、RBAC、多租户、强沙箱和高可用部署，不能把本地管理面直接当作公网产品。

### 1.2 相关文档的职责

项目中的文档各有不同用途：

| 文档 | 用途 |
|---|---|
| `EvoAgent-项目设计与分阶段实现计划.md` | 说明项目最终要做什么以及五个阶段如何演进 |
| `阶段一-可测试Agent内核架构与实现指南.md` | 说明第一阶段的目标架构、实现顺序和完成标准 |
| `阶段二-可靠可追踪任务执行架构与实现指南.md` | 说明第二阶段的持久化、Worker、恢复、安全和分模块路线 |
| `阶段三-可验证Skill生命周期架构与实现指南.md` | 说明第三阶段的 DSL、来源、评测门禁、发布回滚和模块路线；模块 0～12 已落地 |
| `阶段三-演示与安全边界.md` | 说明怎样复现完整生命周期，以及哪些生产能力不在 v0.3 范围内 |
| `开发进度与决策记录.md` | 记录当前真正完成到哪里，以及已经固定的接口决策 |
| `EvoAgent-源码讲解与学习手册.md` | 解释已经写出的代码及其原理，也就是本手册 |

开始一次新的开发前，应先查看“开发进度与决策记录”；学习已有代码时，以本手册为入口，再进入具体源码。

---

## 2. 项目要解决什么问题

### 2.1 普通聊天程序和 Agent 的区别

最简单的大模型程序只有一次请求：

```text
用户输入 → 调用大模型 → 输出回答
```

它可以回答问题，但无法可靠地操作外部世界。

Agent 在模型之外增加了工具和运行控制：

```text
用户输入
  → 模型判断下一步
  → 调用工具
  → 把工具结果交还模型
  → 模型继续判断
  → 完成任务
```

例如，用户要求比较三个开源项目并生成报告时，Agent 需要读取网页、整理证据、处理文件，并经过多轮模型和工具交互才能完成任务。

### 2.2 为什么不能让模型直接执行函数

模型生成的内容是不可信输入。它可能出现：

- 工具名称不存在；
- 参数缺失或类型错误；
- 连续重复同一个调用；
- 请求访问工作目录之外的文件；
- 请求执行危险命令；
- 工具运行超时或返回过大内容；
- 模型接口中断或返回不完整数据。

因此，模型不能绕过运行框架直接执行 Python 函数。EvoAgent 会在模型和工具之间加入参数校验、工具查找、超时、权限、安全策略、结果规范化和事件记录。

### 2.3 EvoAgent 的核心目标

EvoAgent 最终要提供以下能力：

1. 让模型通过 Agent Loop 持续调用工具完成多步骤任务。
2. 让长任务可以暂停、恢复、取消和重试。
3. 记录可审计的执行轨迹，知道每一步发生了什么。
4. 从成功轨迹中提炼候选 Skill。
5. 对 Skill 进行测试、评分、审批、发布和回滚。
6. 用评测数据证明 Skill 是否真的提高成功率和效率。

项目最重要的特色不是接入大量模型，而是：

> 在通用 Agent Runtime 上建立可验证、可版本化、可回滚的 Skill 生命周期。

---

## 3. 总体架构

### 3.1 最终执行链路

下面是第一阶段完成后应当形成的核心链路：

```text
CLI 接收用户任务
        ↓
AgentRunner 管理一次 Run
        ↓
ContextBuilder 构造初始消息
        ↓
AgentLoop 控制模型—工具循环
        ├──────────────→ ModelProvider 调用模型
        │                       ↓
        │                  ModelResponse
        │                       ↓
        └─────────────── ToolCall
                                ↓
                         ToolExecutor
                                ↓
                         ToolRegistry
                                ↓
                    Calculator / FileRead / WebFetch
                                ↓
                           ToolResult
                                ↓
                     回填 AgentLoop，再次调用模型

整个过程同时向 RuntimeEventSink 写入 RuntimeEvent。
```

图中的 `Settings` 和核心数据模型会被多个模块共同使用，因此没有画成单独的一步。

### 3.2 当前已经落地的部分

当前代码实际形成的是：

```text
CLI ──→ Settings ──→ AgentRunner
                       ├── ContextBuilder
                       ├── AgentLoop
                       │   ├── MockProvider / OpenAICompatibleProvider
                       │   └── ToolExecutor
                       │       └── ToolRegistry
                       │           ├── CalculatorTool
                       │           ├── FileReadTool → WorkspaceGuard
                       │           └── WebFetchTool → URLGuard
                       └── InMemoryEventSink ──→ RunResult
```

这条链路既有单元测试，也有不访问真实模型和公网的完整集成测试。真实服务的行为仍受具体厂商兼容程度影响，所以 Provider 会严格检查协议并返回明确错误。

### 3.3 关键职责边界

| 模块 | 只负责什么 | 不负责什么 |
|---|---|---|
| Settings | 加载和校验配置 | 不执行 Agent 业务逻辑 |
| 核心数据模型 | 规定模块间传递的数据格式 | 不发请求、不执行工具 |
| RuntimeEventSink | 接收和保存运行事件 | 不决定 Agent 下一步行为 |
| ToolRegistry | 注册、查找工具并提供 Schema | 不执行工具 |
| ToolExecutor | 校验、超时、调用和规范化结果 | 不决定调用哪个工具 |
| ModelProvider | 适配具体模型接口 | 不执行工具 |
| ContextBuilder | 构造初始模型上下文 | 不维护整轮循环 |
| AgentLoop | 控制模型和工具之间的迭代 | 不管理整个任务的总超时 |
| AgentRunner | 管理一次 Run 的生命周期 | 不处理具体厂商协议 |

这些边界的目的，是让每个模块都可以单独替换和测试。

---

## 4. 工程结构

当前主要目录如下：

```text
EvoAgent/
├── pyproject.toml
├── .env.example
├── README.md
├── docs/
│   ├── EvoAgent-项目设计与分阶段实现计划.md
│   ├── 阶段一-可测试Agent内核架构与实现指南.md
│   ├── 开发进度与决策记录.md
│   ├── ADR-001-阶段一运行时边界与安全策略.md
│   └── EvoAgent-源码讲解与学习手册.md
│
├── src/
│   └── evoagent/
│       ├── config.py
│       ├── cli.py
│       ├── core/
│       │   ├── models.py
│       │   ├── events.py
│       │   ├── context.py
│       │   ├── loop.py
│       │   └── runner.py
│       ├── providers/
│       │   ├── base.py
│       │   ├── mock.py
│       │   └── openai_compatible.py
│       └── tools/
│           ├── base.py
│           ├── registry.py
│           ├── executor.py
│           ├── guards.py
│           └── builtin/
│               ├── calculator.py
│               ├── file_read.py
│               └── web_fetch.py
│
└── tests/
    ├── unit/
    └── integration/
```

### 4.1 为什么使用 `src` 布局

项目源码放在 `src/evoagent`，而不是直接放在仓库根目录。这种结构称为 `src layout`。

它的主要作用是：

- 明确区分源码、测试、文档和配置；
- 防止测试意外导入根目录中的临时代码；
- 让本地开发环境更接近打包安装后的真实环境；
- 方便以后把 `evoagent` 构建成标准 Python 包。

### 4.2 包和模块

在当前项目中：

- `evoagent` 是顶层 Python 包；
- `core`、`tools`、`providers` 是子包；
- `config.py`、`models.py` 等是模块；
- `__init__.py` 用来标记和说明包。

推荐从项目根目录安装开发版本，而不是手动修改 Python 搜索路径：

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

`-e` 表示可编辑安装。修改 `src/evoagent` 中的代码后，不需要反复重新安装。

---

## 5. 模块 0：工程初始化与配置

### 5.1 模块目标

模块 0 解决的是“项目怎样被安装、配置、检查和测试”的问题。它不实现 Agent 业务，但为后续所有模块提供统一运行环境。

主要文件：

```text
pyproject.toml
.env.example
.gitignore
.pre-commit-config.yaml
.github/workflows/ci.yml
src/evoagent/config.py
tests/unit/test_config.py
```

### 5.2 `pyproject.toml`

`pyproject.toml` 是 Python 项目的统一配置文件，作用类似 Java 项目的 `pom.xml`。当前文件定义了：

- 项目名称和版本；
- 支持的 Python 版本；
- 运行依赖和开发依赖；
- 构建工具 Hatchling；
- pytest 的测试发现规则；
- Ruff 的检查和格式化规则。

项目支持：

```text
Python >= 3.12 且 < 3.14
```

主要运行依赖：

| 依赖 | 用途 |
|---|---|
| Pydantic | 定义和校验数据模型 |
| pydantic-settings | 从环境变量和 `.env` 加载类型化配置 |
| python-dotenv | 支持本地 `.env` 文件 |

主要开发依赖：

| 依赖 | 用途 |
|---|---|
| pytest | 编写和运行测试 |
| pytest-asyncio | 测试异步代码 |
| Ruff | 代码检查和格式化 |
| pre-commit | 提交前自动执行检查 |

第一阶段保持依赖较少，暂不引入数据库和 Web 框架。

### 5.3 环境变量和 `.env`

`.env.example` 只提供配置字段和非敏感示例。开发者可以复制出本地 `.env`：

```powershell
Copy-Item .env.example .env
```

`.env` 可能包含 API Key，因此已经被 `.gitignore` 忽略，不能提交到公开仓库。

所有 EvoAgent 配置统一使用 `EVOAGENT_` 前缀。例如：

```text
EVOAGENT_PROVIDER=mock
EVOAGENT_MAX_ITERATIONS=8
EVOAGENT_TOOL_TIMEOUT_SECONDS=30
```

### 5.4 `ProviderName` 和 `LogLevel`

`src/evoagent/config.py` 中的 `ProviderName` 和 `LogLevel` 都继承自 `StrEnum`。

`StrEnum` 同时具有枚举和字符串的特征。使用枚举的好处是可选值被限制在明确集合中，避免代码中到处出现拼写不一致的字符串。

当前 Provider 可选值是：

```text
mock
openai_compatible
```

当前日志级别可选值是：

```text
DEBUG
INFO
WARNING
ERROR
```

### 5.5 `Settings`

`Settings` 继承自 Pydantic Settings 的 `BaseSettings`，负责把外部配置转换成经过校验的 Python 对象。

配置来源包括：

1. 操作系统环境变量；
2. 项目根目录的 `.env`；
3. 代码声明的默认值。

例如：

```text
EVOAGENT_MAX_ITERATIONS=8
```

加载后成为：

```python
settings.max_iterations
```

字段上的 `Field` 同时定义默认值和限制。例如最大迭代次数必须位于 1～100，超时时间必须大于 0。非法配置会在程序启动时失败，而不是在任务执行到一半时才暴露。

### 5.6 Mock 模式和真实模型模式

默认配置使用 `mock` Provider，不要求 API Key、Base URL 或模型名称。这样可以：

- 在没有网络时运行测试；
- 避免测试消耗真实模型费用；
- 让测试结果保持确定；
- 降低第一次运行项目的门槛。

只有选择 `openai_compatible` 时，`validate_provider_requirements()` 才要求下面三个字段同时存在：

```text
EVOAGENT_API_KEY
EVOAGENT_BASE_URL
EVOAGENT_MODEL
```

这里使用的是条件校验：不同运行模式需要不同配置。

### 5.7 Workspace

`workspace` 表示以后文件工具允许工作的根目录。配置加载时会把相对路径转换成规范化的绝对路径。

这一步还没有实现完整安全检查，但先统一路径表示，可以避免不同模块各自解析相对路径。后续 `file_read` 和路径 Guard 会以这个绝对目录作为安全边界。

### 5.8 本模块应掌握的知识

- Python 虚拟环境和可编辑安装；
- `pyproject.toml` 的作用；
- 环境变量和 `.env` 的区别；
- Pydantic Settings 如何完成类型转换与校验；
- 为什么配置与业务逻辑必须分离。

---

## 6. 模块 1：核心数据契约

### 6.1 什么是数据契约

数据契约规定模块之间传递什么数据，以及这些数据必须满足什么条件。

如果没有统一契约，不同模块可能使用不同字段表达同一个意思：

```python
{"tool": "calculator"}
{"tool_name": "calculator"}
{"name": "calculator"}
```

随着模块增多，这种自由字典会产生大量隐蔽错误。EvoAgent 使用 Pydantic 模型统一字段、类型和业务规则。

核心数据契约位于：

```text
src/evoagent/core/models.py
```

### 6.2 `ContractModel`

大部分领域模型都继承自 `ContractModel`：

```python
class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
```

其中：

- `extra="forbid"`：拒绝模型中没有声明的额外字段；
- `frozen=True`：对象创建后不能重新给顶层字段赋值。

严格校验可以尽早发现 Provider、工具或其他模块传入的错误数据。冻结对象可以减少同一个数据在不同模块之间被意外修改的问题。

需要注意：`frozen=True` 保护的是顶层字段，嵌套在字段内部的 `dict` 和 `list` 仍应由调用方当作只读数据使用。

### 6.3 枚举类型

当前定义了多组枚举：

| 枚举 | 表示什么 |
|---|---|
| `MessageRole` | system、user、assistant、tool 四种消息角色 |
| `FinishReason` | 模型为什么结束本次响应 |
| `ToolRisk` | 工具风险等级 R0～R3 |
| `ToolResultStatus` | 工具成功、失败或权限拒绝 |
| `ProviderEventType` | Provider 流式事件类型 |
| `RunStatus` | 一次 Run 的最终状态 |
| `EventType` | 运行过程中可观察的事件类型 |

使用枚举比散落的字符串更安全，也方便 IDE 提示和类型检查。

### 6.4 `ToolDefinition`

`ToolDefinition` 是提供给模型的工具说明，包含：

```text
name          工具公开名称
description   工具用途
parameters    参数的 JSON Schema
```

模型不会直接看到 Python 工具对象，而是根据这份描述决定是否调用工具以及怎样生成参数。

工具名受到正则表达式限制，必须以英文字母开头，只能继续使用字母、数字、下划线或连字符，最长 64 个字符。

### 6.5 `ToolCall`

`ToolCall` 表示模型已经组装完成的一次工具调用请求：

```json
{
  "call_id": "call_001",
  "name": "calculator",
  "arguments": {
    "expression": "12 * 3"
  }
}
```

`call_id` 是一次调用的唯一关联标识。一条模型响应可能包含多个 Tool Call，后续每个 Tool Result 都必须通过这个 ID 找到原始调用。

### 6.6 `ToolResult`

`ToolResult` 表示工具执行后的统一结果：

```text
tool_call_id   对应的调用 ID
name           工具名称
status         执行状态
content        返回给模型的文本内容
error_code     机器可识别的错误码
```

模型校验器保证：

- 成功结果不能携带错误码；
- 非成功结果必须携带错误码。

这样 AgentLoop 不需要根据自然语言猜测工具是否成功。

### 6.7 `Message`

`Message` 是与具体模型厂商无关的聊天消息。角色和字段的对应关系是：

| 角色 | 允许的数据 |
|---|---|
| system | 必须有非空文本 |
| user | 必须有非空文本 |
| assistant | 可以有文本、Tool Call，或者同时存在 |
| tool | 必须有工具结果文本和 `tool_call_id` |

校验器会阻止不合理组合。例如：

- user 消息不能带 Tool Call；
- tool 消息不能再带 Tool Call；
- tool 消息必须指出自己对应哪个调用；
- assistant 既没有文本也没有 Tool Call 时无效。

### 6.8 `TokenUsage`

`TokenUsage` 记录输入、输出和总 Token：

```text
total_tokens = input_tokens + output_tokens
```

校验器会保证这个等式成立。它还实现了加法，因此多轮模型请求的 Token 可以安全累加。

第一阶段中的 Token 限制是软预算：必须等一次完整模型请求返回 Usage 后才能更新累计值，不能精确中断已经发出的请求。

### 6.9 `ModelRequest` 和 `ModelResponse`

`ModelRequest` 是 EvoAgent 发给 Provider 的统一请求，包含：

- 消息历史；
- 工具定义；
- 模型名称；
- temperature；
- 最大输出 Token。

`ModelResponse` 是 Provider 返回的统一响应，包含：

- assistant 消息；
- 结束原因；
- 可选的 Token Usage。

校验器会保证响应内部一致。例如，有 Tool Call 时，结束原因必须是 `tool_calls`；结束原因是 `tool_calls` 时，也必须真的存在至少一个 Tool Call。

这两个模型隔离了 EvoAgent 内核和具体厂商协议。以后更换 OpenAI 兼容服务时，不应修改 AgentLoop 的数据结构。

### 6.10 `ProviderEvent`

真实模型可能通过流式接口分片返回文本和工具参数。`ProviderEvent` 把厂商事件规范成四类：

```text
text_delta        文本增量
tool_call_delta   工具调用增量
usage             Token 用量
completed         完整响应
```

每种事件只能携带与自己类型对应的字段。例如 `usage` 事件不能同时夹带文本片段。这样可以避免模糊事件进入 AgentLoop。

目前只是定义了 ProviderEvent 的数据格式，实际产生这些事件的 Provider 将在后续模块实现。

### 6.11 `RuntimeEvent` 和 `RunResult`

`RuntimeEvent` 表示一次 Run 中发生的可观测动作，包含：

```text
run_id
sequence
type
timestamp
payload
schema_version
```

`RunResult` 表示 AgentRunner 最终返回的任务摘要，包含最终状态、回答、Token 用量、全部事件和错误信息。模型服务未报告完整 Usage 时，该字段显式为 `None`，不能使用零值冒充已知用量。

它会检查：

- 完成状态必须有非空答案；
- 完成状态不能有错误信息；
- 非完成状态必须有错误码；
- 所有事件必须属于同一个 run_id；
- 事件序号必须从 1 开始连续排列。

`RunResult` 在模块 1 先定义为跨模块契约，并在模块 7 由 AgentRunner 正式生成。先固定数据形状，使后续 Runner 不需要反过来修改 Loop、事件和 CLI 的接口。

---

## 7. 模块 1：运行事件系统

### 7.1 为什么 Agent 需要事件

一次 Agent 任务不是一个普通函数调用。它可能经历多次模型请求、多个工具、失败、重试和用户取消。只有最终答案无法解释任务是怎样完成的。

事件系统相当于 Agent 的行车记录仪：

```text
1  run.started
2  model.requested
3  model.completed
4  tool.started
5  tool.completed
6  model.requested
7  model.completed
8  run.completed
```

这些事件以后会用于：

- 展示实时执行进度；
- 排查任务失败原因；
- 将 Trace 保存到数据库；
- 在检查点恢复长任务；
- 从成功轨迹中提炼 Skill；
- 统计成功率、延迟和 Token。

### 7.2 `RuntimeEventSink` 协议

`RuntimeEventSink` 是事件接收目标的抽象协议。它要求实现：

```text
events 属性
emit() 方法
```

当前使用内存实现，以后可以增加数据库实现。发送事件的模块只依赖这个协议，不需要知道事件最终保存在哪里。

这体现了依赖倒置：高层运行逻辑依赖抽象，而不是依赖具体数据库代码。

### 7.3 `sanitize_payload()`

事件载荷在保存前必须先清理。当前实现会：

- 递归处理字典、列表和元组；
- 根据字段名隐藏 API Key、Authorization、Token、密码和 Secret；
- 隐藏隐藏推理相关字段；
- 截断过长字符串；
- 拒绝无法表示为 JSON 的对象；
- 拒绝 NaN 和正负无穷等非有限浮点数。

例如：

```python
{"api_key": "sk-example"}
```

会被保存成：

```python
{"api_key": "[REDACTED]"}
```

事件记录应该服务于审计和调试，但不能成为泄露密钥或隐藏推理的通道。

### 7.4 `InMemoryEventSink`

当前事件接收器将事件存放在进程内存中。每次 `emit()` 会：

1. 清理事件 payload；
2. 获取异步锁；
3. 根据已有事件数量生成下一个 sequence；
4. 生成带 UTC 时区的时间戳；
5. 创建并保存 RuntimeEvent。

异步锁保证多个协程同时发送事件时不会得到重复序号。

当前进程结束后，内存事件也会消失。数据库持久化属于第二阶段，不是当前模块的缺陷。

---

## 8. 模块 2：工具抽象与注册表

### 8.1 工具系统由什么组成

当前工具系统有三部分：

```text
BaseTool        所有工具必须遵守的接口
ToolRegistry    管理有哪些工具
CalculatorTool  第一个具体工具
```

未来的 `ToolExecutor` 会连接 ToolCall、ToolRegistry 和具体工具。

### 8.2 `BaseTool`

每个工具必须声明：

| 成员 | 含义 |
|---|---|
| `name` | 模型调用时使用的公开名称 |
| `description` | 告诉模型工具能做什么 |
| `arguments_model` | 参数的 Pydantic 模型 |
| `risk` | 基础风险等级 |
| `has_side_effects` | 是否会改变文件或外部系统 |
| `parallel_safe` | 是否允许安全并行 |
| `invoke()` | 工具真正执行的异步方法 |

可以把 BaseTool 理解为插座标准。只要 Calculator、FileRead 和 WebFetch 都遵守相同接口，ToolExecutor 就可以用统一方式执行它们。

### 8.3 泛型参数 `ArgumentsT`

`BaseTool[ArgumentsT]` 使用泛型表示每种工具拥有自己的参数模型。例如：

```text
CalculatorTool → CalculatorArguments
FileReadTool   → FileReadArguments
WebFetchTool   → WebFetchArguments
```

这样 IDE 和类型检查器能够知道某个工具的 `invoke()` 应该接收哪一种参数，减少把错误参数对象传给工具的机会。

### 8.4 `definition()`

`definition()` 根据工具自身的名称、描述和参数模型生成 `ToolDefinition`。

其中参数 Schema 由 Pydantic 的 `model_json_schema()` 自动生成。这保证“程序真正校验的参数格式”和“告诉模型的参数格式”来自同一个模型，减少两份定义不一致的问题。

### 8.5 `validate_arguments()`

模型生成的 arguments 是不可信的普通 JSON 数据。`validate_arguments()` 调用 Pydantic，把原始参数转换成该工具的类型化参数模型。

目前 BaseTool 提供了这个能力；从模块 3 开始，所有模型生成的 Tool Call 都必须由 ToolExecutor 调用它，不能直接把原始字典交给工具。

### 8.6 为什么 `invoke()` 是异步方法

计算器本身几乎立即完成，但以后工具可能需要等待网页、文件、数据库或外部 API。统一采用异步接口，可以让运行时支持超时、取消和安全并发。

### 8.7 工具错误

工具层当前定义了：

```text
ToolError
└── ToolExecutionError
```

`ToolExecutionError` 表示工具本身选择正确、参数也可能合法，但执行时无法产生结果，例如计算器遇到除零。

模块 3 的 ToolExecutor 会捕获这种预期错误，并把它转换成统一 ToolResult，而不是让异常直接击穿整个 Agent。

### 8.8 `ToolRegistry`

ToolRegistry 内部使用字典按名称保存工具：

```python
{
    "calculator": CalculatorTool对象,
    "file_read": FileReadTool对象,
}
```

它负责：

1. 注册工具；
2. 拒绝重复名称；
3. 按名称查找工具；
4. 列出工具名称；
5. 生成所有模型可见的 ToolDefinition。

它不会执行工具。

### 8.9 为什么工具名称要排序

`names` 和 `definitions()` 都按照工具名排序，而不是依赖注册顺序。

确定性顺序可以：

- 让测试结果稳定；
- 让发送给模型的工具顺序保持一致；
- 让日志和问题排查更容易比较；
- 避免无意义的顺序变化影响缓存或快照。

### 8.10 Registry 的错误类型

| 错误 | 出现场景 |
|---|---|
| `InvalidToolError` | 注册的对象没有继承 BaseTool |
| `DuplicateToolError` | 同名工具被注册两次 |
| `ToolNotFoundError` | 请求的工具不存在 |

使用明确错误类型，可以让后续 ToolExecutor 根据失败原因生成不同错误码。

### 8.11 为什么 Registry 不能增加 `execute()`

Registry 是目录，Executor 是执行者。两者分开后：

```text
ToolRegistry
  ├── 有哪些工具？
  ├── 某个工具在哪里？
  └── 它的 Schema 是什么？

ToolExecutor
  ├── 参数是否合法？
  ├── 是否超时？
  ├── 怎样调用？
  ├── 异常怎样转换？
  └── 结果是否需要截断？
```

如果 Registry 同时负责执行，它会混合存储、验证、调度和错误处理，后续难以扩展权限与沙箱。因此，项目已经固定：ToolRegistry 永远不添加 `execute()`。

---

## 9. 模块 2：安全计算器

### 9.1 参数模型

`CalculatorArguments` 只有一个字段：

```text
expression: str
```

它要求表达式：

- 不能为空；
- 最长 200 个字符；
- 校验时去掉两侧空格。

`CalculatorArguments` 继承 ContractModel，因此同样拒绝额外字段且顶层不可重新赋值。

### 9.2 工具元数据

CalculatorTool 声明：

```text
name = calculator
risk = R0
has_side_effects = False
parallel_safe = True
```

含义是：

- 它是最低风险的只读计算工具；
- 它不会修改文件或外部系统；
- 多个互不依赖的计算以后可以并发执行。

模块 2 只声明这些元数据。并发调度要到模块 9 才实现。

### 9.3 为什么不能使用 `eval()`

下面的实现虽然短，但不安全：

```python
eval(expression)
```

`eval()` 可以执行任意 Python 表达式，模型有机会构造访问文件、导入模块或执行系统操作的输入。

CalculatorTool 使用 Python AST 解析表达式，但只解释白名单中的语法节点。它不会把用户输入当作 Python 代码直接执行。

### 9.4 AST 是什么

AST 是抽象语法树。表达式：

```text
1 + 2 * 3
```

可以理解成：

```text
      加法
     /    \
    1     乘法
         /    \
        2      3
```

计算器递归遍历这棵树，只允许：

- 整数和浮点数字面量；
- 加、减、乘、除、整除、取余、乘方；
- 一元正号和负号。

变量、函数调用、属性访问、容器和其他语法节点默认拒绝。

### 9.5 资源限制

除了语法白名单，计算器还设置了限制：

| 限制 | 目的 |
|---|---|
| 表达式最长 200 字符 | 限制输入大小 |
| AST 最多 64 个节点 | 防止极其复杂的表达式 |
| 指数绝对值不超过 10 | 防止乘方迅速生成巨型结果 |
| 结果绝对值不超过 `10**100` | 限制计算资源和输出大小 |
| 拒绝非有限浮点数 | 防止 NaN 和无穷值进入后续系统 |
| 捕获除零 | 转换成明确的工具执行错误 |

这体现了安全设计中的白名单原则：只允许明确支持的能力，而不是先执行再尝试判断是否危险。

---

## 10. 模块 3：ToolExecutor

### 10.1 模块目标

ToolExecutor 是模型和 Python 工具之间唯一的执行入口。模型只能生成 ToolCall，不能直接获得工具对象或调用 `invoke()`。

主要文件：

```text
src/evoagent/tools/executor.py
tests/unit/test_tool_executor.py
```

它把模块 1 定义的 ToolCall、模块 2 的 ToolRegistry 和 BaseTool 连接成：

```text
ToolCall
  → 记录 tool.started
  → Registry 查找工具
  → 工具参数模型校验 arguments
  → 在超时控制下调用 invoke()
  → 限制结果长度
  → 返回 ToolResult
  → 记录 tool.completed 或 tool.failed
```

### 10.2 构造参数

ToolExecutor 需要：

| 参数 | 作用 |
|---|---|
| `registry` | 查找模型请求的具体工具 |
| `event_sink` | 记录工具开始、完成和失败 |
| `timeout_seconds` | 单个工具的最长执行时间 |
| `max_result_chars` | 单个 ToolResult 的最大字符数 |

超时和结果长度来自外部配置，而不是写死在执行代码中，未来 AgentRunner 会使用 Settings 组装这些依赖。

### 10.3 `execute()` 的执行过程

`execute(call)` 首先记录调用 ID、工具名和参数，然后根据工具名到 Registry 查找对象。找到后，调用工具自己的 `validate_arguments()`，把模型生成的 JSON 字典转换成类型化参数。

只有校验成功的参数才能进入：

```python
tool.invoke(arguments)
```

调用被放入 `asyncio.wait_for()`，因此单个工具不会无限占用 Agent。

### 10.4 可恢复错误如何转换

下面这些错误不会直接终止 Agent，而是转换成失败 ToolResult：

| 场景 | error_code |
|---|---|
| 工具不存在 | `tool_not_found` |
| 参数未通过 Pydantic 校验 | `invalid_arguments` |
| 超过单工具时间限制 | `tool_timeout` |
| 工具主动抛出 ToolExecutionError | `tool_execution_error` |

每个失败结果仍然保留原始 `call_id`。AgentLoop 会把错误结果反馈给模型，让模型有机会修改参数或更换方案。

### 10.5 为什么不捕获所有异常

工具主动声明的预期业务失败和工具实现自身的程序错误不是一回事。

例如，除零是可预期的 ToolExecutionError，可以反馈给模型；如果工具内部错误地访问了不存在的对象并抛出 RuntimeError，这通常代表代码缺陷，不应该伪装成普通工具结果。

因此 ToolExecutor 会记录 `internal_tool_error` 事件，但继续抛出非预期异常，让更高层明确终止 Run。

`asyncio.CancelledError` 也必须继续传播。取消最终由 AgentRunner 统一处理，任何中间层都不能把它吞掉。

### 10.6 结果截断

工具结果可能非常大。如果完整结果进入消息历史，会迅速占满模型上下文。ToolExecutor 保证最终 content 不超过 `max_result_chars`。

空间允许时，结果末尾会附加：

```text
…[结果已截断]
```

完成事件还会记录 `truncated`，使运行轨迹能够区分原始短结果和被截断结果。

### 10.7 `execute_many()`

当前版本按模型给出的原始顺序逐个调用 `execute()`，返回顺序也完全一致。

先实现顺序执行，可以固定 ToolCall 和 ToolResult 的对应关系。有副作用工具的顺序不能随意改变。只有到模块 9，在确认整批工具都无副作用且声明为 `parallel_safe` 后，才会加入安全并发。

### 10.8 本模块的测试重点

模块 3 测试覆盖：

- 正常参数校验与执行；
- 未知工具；
- 参数缺失；
- Calculator 除零；
- 工具超时；
- 长结果截断；
- 多调用结果顺序；
- 非预期异常传播；
- 取消传播；
- 非法超时和长度配置。

### 10.9 推荐阅读顺序

阅读模块 3 时，建议沿着一次 ToolCall 的数据流前进：

```text
1. 回顾 core/models.py 中的 ToolCall 和 ToolResult
2. 回顾 tools/base.py 中的 validate_arguments() 和 invoke()
3. 阅读 executor.py 的构造函数
4. 阅读 execute() 的正常路径
5. 阅读四类可恢复错误的转换分支
6. 阅读非预期异常和 CancelledError 的传播分支
7. 阅读 _truncate() 和 execute_many()
8. 对照 test_tool_executor.py 验证每条分支
```

阅读完成后，应能独立回答：一条模型生成的 ToolCall 怎样变成 ToolResult，以及哪些错误可以交还模型继续处理。

### 10.10 面试与复习要点

**问题一：为什么模型不能直接调用工具？**

模型输出属于不可信输入，必须经过工具白名单、参数校验、超时和结果限制。ToolExecutor 正是统一执行边界。

**问题二：为什么 ToolRegistry 和 ToolExecutor 要分开？**

Registry 负责发现和索引，Executor 负责执行策略。分离后可以单独测试，并且方便以后在 Executor 前后增加权限、Sandbox 和幂等控制。

**问题三：为什么 ToolExecutionError 会转成 ToolResult，而 RuntimeError 会继续抛出？**

前者是工具声明的预期业务失败，模型可能修正；后者通常表示实现缺陷，系统不应假装可以安全恢复。

**问题四：为什么取消不能转换成普通失败？**

取消是上层控制流信号。如果中间层吞掉 CancelledError，Agent 可能在用户取消后继续运行。

---

## 11. 模块 4：ModelProvider 与 MockProvider

### 11.1 模块目标

不同模型服务拥有不同的 HTTP 地址、鉴权方式和流式格式。AgentLoop 不应该知道这些厂商差异，所以模型访问被隔离在 Provider 层。

主要文件：

```text
src/evoagent/providers/base.py
src/evoagent/providers/mock.py
tests/unit/test_mock_provider.py
```

### 11.2 `ModelProvider` 协议

ModelProvider 使用 Protocol 描述统一接口：

```text
输入：ModelRequest
输出：AsyncIterator[ProviderEvent]
```

AgentLoop 只调用 `stream(request)`，不依赖 MockProvider 或未来 OpenAICompatibleProvider 的具体类。

这就是依赖倒置：高层 AgentLoop 依赖稳定抽象，底层模型适配器实现该抽象。

### 11.3 ProviderEvent 和 RuntimeEvent 的区别

两者容易混淆：

| 类型 | 服务范围 | 示例 |
|---|---|---|
| ProviderEvent | Provider 和 AgentLoop 之间的流式协议 | text_delta、usage、completed |
| RuntimeEvent | 整个 Run 的可观察记录 | model.requested、tool.completed |

ProviderEvent 表示“模型接口传回了什么分片”；RuntimeEvent 表示“Agent 运行过程中发生了什么”。AgentLoop 负责把有意义的 ProviderEvent 转换成 RuntimeEvent。

### 11.4 Provider 错误层次

当前定义了：

```text
ProviderError
├── ProviderTimeoutError
└── ProviderProtocolError
```

每个 ProviderError 都带有机器可识别的 `code`。第一阶段不自动重试；AgentLoop 记录 `model.failed` 后返回失败结果。重试需要考虑预算和幂等性，将在第二阶段统一设计。

### 11.5 MockProvider 的脚本

MockProvider 不访问网络。构造时接收一组按调用顺序消费的步骤，每个步骤可以是：

1. 一个完整 ModelResponse；
2. 一组显式 ProviderEvent；
3. 一个预设 ProviderError。

例如两轮任务可以预设为：

```text
第 1 步：返回 calculator ToolCall
第 2 步：返回最终文本答案
```

AgentLoop 每调用一次 `stream()`，MockProvider 就消费一个步骤。

### 11.6 ModelResponse 如何展开

MockProvider 会把完整响应稳定展开：

```text
可选 text_delta
→ 每个 ToolCall 对应一个 tool_call_delta
→ 可选 usage
→ completed
```

工具 arguments 使用稳定 JSON 格式输出，使测试不会因为字典格式差异而波动。

### 11.7 请求记录和脚本耗尽

MockProvider 保存收到的全部 ModelRequest。测试可以检查第二次模型请求中是否真的包含上一轮 assistant ToolCall 和 tool 结果。

如果 AgentLoop 在预设步骤用完后仍然请求模型，MockProvider 会抛出 `mock_script_exhausted`。这通常说明循环次数超出测试预期，而不是悄悄生成一个默认回答。

### 11.8 为什么核心测试必须使用 MockProvider

真实模型具有随机性、网络延迟和费用，无法稳定复现边界场景。MockProvider 可以确定性模拟：

- 直接回答；
- 多轮工具调用；
- 错误工具名称；
- Provider 超时；
- 流提前结束；
- 模型一直不停止。

因此，AgentLoop 的正确性由 MockProvider 验证；真实模型只负责通过同一 Provider 契约接入。

### 11.9 输入、输出和正常链路

MockProvider 的输入是完整 ModelRequest，输出是异步 ProviderEvent 流：

```text
ModelRequest
  → 保存到 requests 历史
  → 从脚本队列取出下一个 MockStep
  → 如果是 ModelResponse，则展开成标准事件
  → 如果是显式事件序列，则按原顺序回放
  → 如果是 ProviderError，则在迭代事件流时抛出
```

`remaining_steps` 可以帮助测试确认 AgentLoop 是否多调用或少调用了模型。

### 11.10 错误与模块边界

MockProvider 会明确报告脚本耗尽，而不会擅自生成默认回答。ProviderTimeoutError 用于模拟单次模型请求超时；ProviderProtocolError 表示事件流不符合约定。

ModelProvider 和 MockProvider 不负责：

- 构造初始上下文；
- 决定什么时候调用工具；
- 执行 ToolCall；
- 生成 RuntimeEvent；
- 自动重试模型请求；
- 管理任务总超时。

其中 ProviderEvent 到 RuntimeEvent 的转换属于 AgentLoop，任务总超时和重试策略属于更高层。

### 11.11 本模块的测试重点

`test_mock_provider.py` 覆盖：

- 文本响应展开为 text_delta、usage 和 completed；
- ToolCall 展开为 tool_call_delta；
- 显式事件序列按顺序回放；
- 预设 ProviderError 原样抛出；
- 脚本耗尽返回明确错误；
- 已接收的 ModelRequest 被完整记录。

### 11.12 推荐阅读顺序

```text
1. 回顾 core/models.py 中的 ModelRequest、ModelResponse 和 ProviderEvent
2. 阅读 providers/base.py 的 ModelProvider Protocol
3. 阅读 ProviderError 错误层次
4. 阅读 mock.py 的 MockStep 类型
5. 阅读 MockProvider.stream()
6. 阅读 _events_from_response()
7. 对照 test_mock_provider.py 查看各类脚本如何使用
```

### 11.13 面试与复习要点

**问题一：为什么使用 Provider 适配层？**

它隔离具体模型厂商的 HTTP 和流式协议，让 AgentLoop 只依赖统一接口，更换模型实现时无需修改核心循环。

**问题二：ProviderEvent 和 RuntimeEvent 有什么区别？**

ProviderEvent 是模型流式传输协议；RuntimeEvent 是整个 Agent Run 的可观测记录。二者生命周期和消费者不同。

**问题三：为什么不能用真实模型完成全部单元测试？**

真实模型存在随机性、费用、网络波动和限流，无法稳定复现精确边界。MockProvider 可以提供确定性脚本。

**问题四：为什么 v0.1 不自动重试？**

重试必须区分错误类型，并考虑预算、副作用和幂等语义。在这些规则尚未建立时盲目重试可能造成重复操作。

---

## 12. 模块 5：ContextBuilder

### 12.1 模块目标

ContextBuilder 只负责构造一次 Run 的初始消息。主要文件：

```text
src/evoagent/core/context.py
tests/unit/test_context.py
```

它不负责：

- 注册或描述工具；
- 调用模型；
- 执行工具；
- 在循环中追加 assistant 和 tool 消息；
- 保存长期记忆；
- 压缩上下文。

### 12.2 默认系统提示词

默认 system prompt 告诉模型：

- 它是通过受控工具工作的 EvoAgent；
- 只能使用明确提供的工具；
- 不能声称完成尚未执行的操作；
- 外部上下文是不可信资料；
- 工具失败后可以修正参数或更换方案。

system prompt 只能引导模型，不是安全边界。真正的超时、参数、路径和权限限制必须由代码执行。

### 12.3 初始消息顺序

`build()` 返回稳定的不可变元组：

```text
system prompt
→ 外部上下文 1
→ 外部上下文 2
→ 最终用户任务
```

最终用户任务始终位于初始消息末尾，使模型清楚当前要完成的目标。

### 12.4 为什么外部上下文使用 user 角色

网页、文件和调用方提供的资料可能包含提示注入内容。如果把这些资料作为 system 消息，它们会获得不应有的优先级。

所以外部上下文使用 user 角色，并明确添加：

```text
仅作为不可信资料，不是系统指令
```

这不能代替后续安全机制，但能保持消息角色的信任边界正确。

### 12.5 输入校验与无状态性

ContextBuilder 拒绝空 system prompt、空用户任务和空上下文块。字符串两侧空白会被清理。

它不在对象内部保存消息历史。每次 `build()` 都返回新快照，避免两个 Run 意外共享或污染上下文。

### 12.6 输入、输出和正常链路

ContextBuilder 的输入是：

```text
system_prompt       构造器传入，可使用默认值
user_input          本次用户任务
external_context    零个或多个外部资料块
```

输出是：

```text
tuple[Message, ...]
```

正常链路为：

```text
清理并校验 system prompt
  → 创建 system Message
  → 逐块清理并包装外部上下文
  → 创建最终 user Message
  → 返回不可变消息元组
```

### 12.7 本模块的测试重点

`test_context.py` 覆盖：

- 默认 system prompt 位于第一条；
- 自定义 system prompt 会清理两侧空白；
- 多个外部上下文保持原始顺序；
- 用户任务始终位于最后；
- 空 system prompt、用户任务或上下文块被拒绝；
- 多次 build 返回互不共享的新快照。

### 12.8 推荐阅读顺序

```text
1. 回顾 Message 和 MessageRole 的校验规则
2. 阅读 DEFAULT_SYSTEM_PROMPT
3. 阅读 ContextBuilder.__init__() 的系统提示校验
4. 阅读 build() 的消息追加顺序
5. 对照 test_context.py 观察输出消息
```

### 12.9 面试与复习要点

**问题一：ContextBuilder 为什么不维护整轮消息历史？**

它只负责初始上下文，后续 assistant 和 tool 消息由 AgentLoop 独占管理，避免多个模块同时修改同一状态。

**问题二：为什么外部上下文不用 system 角色？**

外部资料可能不可信，使用 system 角色会错误提升其指令优先级。它应保持为低信任资料。

**问题三：system prompt 能否作为真正安全边界？**

不能。提示词只能引导模型，参数、路径、网络、权限和超时必须由代码强制执行。

**问题四：为什么返回 tuple 而不是内部 list？**

tuple 表达初始快照不应被原地修改，有助于减少跨 Run 状态污染。

---

## 13. 模块 6：AgentLoop

### 13.1 模块目标

AgentLoop 是第一阶段的核心算法，负责不断进行：

```text
调用模型 → 判断响应 → 执行工具 → 回填结果 → 再次调用模型
```

主要文件：

```text
src/evoagent/core/loop.py
tests/unit/test_agent_loop.py
```

### 13.2 AgentLoop 的依赖

AgentLoop 需要：

| 依赖 | 用途 |
|---|---|
| ModelProvider | 获取标准化模型事件流 |
| ToolRegistry | 生成随 ModelRequest 发送的工具 Schema |
| ToolExecutor | 执行模型产生的 ToolCall |
| RuntimeEventSink | 记录模型请求、增量、完成和失败 |
| model | ModelRequest 中使用的模型标识 |
| max_iterations | 最大模型请求次数 |
| max_total_tokens | 累计 Token 软预算 |

AgentLoop 不直接依赖 CalculatorTool，也不知道 ToolExecutor 内部如何调用某个工具。

### 13.3 `AgentLoopResult`

模块 6 在核心数据模型中增加 AgentLoopResult，作为未来 AgentRunner 接收的循环结果：

```text
status          completed、failed 或 limit_reached
messages        循环结束时的完整消息快照
iterations      已执行的模型请求次数
usage           完整 Token Usage，未知时为 None
final_answer    成功时的答案
error_code      未成功时的错误码
error_message   未成功时的错误说明
```

完成结果必须有答案且不能带错误；非完成结果必须有错误码且不能带最终答案。

### 13.4 一次循环的算法

每个 iteration 执行：

```text
1. 使用当前消息和 Registry definitions 构造 ModelRequest
2. 记录 model.requested
3. 消费 ProviderEvent 流
4. 把文本和工具增量转换成 model.delta
5. 获得且校验唯一 completed 响应
6. 累计本轮 Usage
7. 记录 model.completed
8. 把完整 assistant 消息加入历史
9. 如果存在 ToolCall：
     - 交给 ToolExecutor
     - 按原顺序追加 tool 消息
     - 检查 Token 软预算
     - 进入下一轮
10. 如果没有 ToolCall：
     - 只有 finish_reason=stop 且回答非空才成功
     - 其他情况返回失败
```

### 13.5 消息历史的不变量

工具调用必须按照下面的顺序进入消息历史：

```text
assistant（包含 ToolCall call-1）
tool（tool_call_id=call-1）
```

即使 assistant 同时带有一段文本和 ToolCall，也必须先执行工具，不能把该文本当成最终答案。

一批 ToolCall 中的每个调用都必须得到一个同 ID 的 tool 消息。未知工具和参数错误也不能漏掉结果，否则下一轮模型请求会违反工具调用协议。

### 13.6 Provider 流协议检查

AgentLoop 会拒绝：

- 没有 completed 就提前结束的流；
- completed 后继续出现事件；
- 同一请求出现多个 Usage 事件；
- 流式 Usage 与 completed 响应 Usage 不一致。

这些情况统一变成 `provider_protocol_error`，并记录 `model.failed`。

### 13.7 Usage 为什么允许为 None

部分模型服务不返回 Token Usage。如果任意一轮缺少 Usage，AgentLoop 无法知道精确累计值，因此最终 `usage` 设为 `None`。

不能用：

```text
input=0, output=0, total=0
```

冒充未知值，因为“没有消耗 Token”和“服务端没有报告”是两个完全不同的事实。

只有 Usage 完整时才执行累计 Token 预算判断。

### 13.8 终止条件

| 条件 | AgentLoopResult |
|---|---|
| 无 ToolCall、finish_reason=stop、答案非空 | completed |
| ProviderError | failed |
| Provider 流协议不完整 | failed |
| length、content_filter 或 error 结束 | failed |
| stop 但答案为空 | failed |
| 达到最大模型请求次数 | limit_reached |
| 完整累计 Usage 达到 Token 预算且还需下一轮 | limit_reached |

Token 是软预算：当前模型响应已经发生，AgentLoop只能阻止下一次请求。如果本轮已经给出正常最终答案，即使累计值到达预算，也保留本轮成功结果。

### 13.9 取消与 Run 终态事件

AgentLoop 显式传播 `asyncio.CancelledError`，不把取消转换成普通失败。任务总超时、用户取消以及 `run.completed`、`run.failed` 等唯一终态事件属于 AgentRunner。

这种边界可以避免 Runner、Loop 和 Executor 同时产生终态事件，导致一次 Run 被重复记账。

### 13.10 后续收尾加入的循环保护

模块 9 在 AgentLoop 中补入重复调用检测。它对一批工具的名称、参数和执行结果生成稳定指纹；只有连续多轮完全相同并达到 `max_repeated_tool_calls` 阈值才停止。这样既阻止无进展死循环，也不会误伤参数或结果已经变化的正常重试。

任务总超时已经由 AgentRunner 实现；只读安全并发属于 ToolExecutor。Provider 自动重试、上下文压缩和长期记忆仍属于后续阶段。

### 13.11 本模块的测试重点

`test_agent_loop.py` 覆盖：

- 模型不调用工具而直接回答；
- 调用 Calculator 后进入下一轮并回答；
- assistant 同时包含文本和 ToolCall 时仍先执行工具；
- 未知工具错误反馈模型后继续；
- 工具执行失败反馈模型后继续；
- length 等非正常结束不能伪装成成功；
- ProviderError 转换成循环失败；
- Provider 流提前结束；
- 达到最大迭代次数；
- 达到 Token 软预算后不再请求模型；
- Usage 缺失时明确返回 None；
- CancelledError 继续向上传播；
- 连续重复相同工具调用和结果时达到限制；
- 非法循环配置在构造时被拒绝。

### 13.12 推荐阅读顺序

AgentLoop 依赖前面多个模块，建议按执行顺序阅读：

```text
1. 阅读 AgentLoopResult 的字段和校验器
2. 阅读 AgentLoop.__init__() 的依赖与限制
3. 阅读 run() 中 ModelRequest 的构造
4. 阅读 _consume_response() 的 Provider 流检查
5. 回到 run() 阅读 assistant 消息追加
6. 阅读 ToolExecutor 调用和 tool 消息回填
7. 阅读正常完成、失败和限制三个出口
8. 阅读 _tool_message() 与 _failure()
9. 对照 test_agent_loop.py 逐个运行场景
```

不要第一次就逐行追踪所有异常分支。先掌握正常的两轮计算流程，再阅读错误和限制处理。

### 13.13 面试与复习要点

**问题一：AgentLoop 的本质是什么？**

它是一个带终止条件的状态循环：模型产生下一步动作，Runtime 执行动作并把观察结果反馈模型，直到得到最终答案或达到限制。

**问题二：为什么 assistant ToolCall 必须先写入消息历史？**

下一条 tool 消息需要引用前面的调用 ID。缺少 assistant 调用消息会破坏 OpenAI-compatible 工具调用协议。

**问题三：为什么带有文本的 ToolCall 不能直接作为最终答案？**

只要响应包含 ToolCall，就表示模型仍要求外部执行。那段文本只是伴随说明，不代表任务已经完成。

**问题四：为什么 Token 预算是软限制？**

只有一次模型请求完成后才能获得 Usage，因此系统只能阻止下一次请求，无法撤销已经发生的 Token 消耗。

**问题五：为什么 Usage 缺失时使用 None？**

零表示确认没有消耗，None 表示无法得知。混淆两者会让预算和评测数据失真。

**问题六：AgentLoop 和 AgentRunner 的区别是什么？**

AgentLoop 管模型—工具迭代；AgentRunner 管整个 Run 的 run_id、总超时、取消、唯一终态事件和结果汇总。

---

## 14. 模块 7：AgentRunner

### 14.1 模块目标

AgentLoop 只知道怎样循环，却不知道“一次任务”从哪里开始、怎样超时以及最后应返回什么。AgentRunner 是最外层运行控制器，它把已有组件组装成一次完整 Run。

主要文件：

```text
src/evoagent/core/runner.py
tests/unit/test_runner.py
```

### 14.2 它在架构中的位置

```text
CLI
  ↓
AgentRunner
  ├── 创建 run_id 与 EventSink
  ├── ContextBuilder.build()
  ├── 创建 ToolExecutor 和 AgentLoop
  ├── 施加任务总超时
  └── 汇总 RunResult
```

Runner 依赖抽象的 ModelProvider 和 ToolRegistry，因此它既能使用 MockProvider，也能使用真实 Provider。

### 14.3 构造函数和 run()

构造函数接收四个长期依赖：Settings、ContextBuilder、ModelProvider 和 ToolRegistry。`run()` 接收本次用户任务与可选外部上下文，并返回 RunResult。

每次调用 `run()` 都重新创建：

- 唯一 UUID 类型的 run_id；
- 只属于本次运行的 InMemoryEventSink；
- 使用本次 EventSink 的 ToolExecutor；
- 使用同一 EventSink 和 Executor 的 AgentLoop。

因此连续运行两个任务时，它们不会共享事件序号或消息历史。

### 14.4 正常执行链路

```text
1. 生成 run_id
2. 写入 run.started
3. ContextBuilder 构造初始消息
4. 在 task_timeout_seconds 内运行 AgentLoop
5. 将 AgentLoopResult 转换为 RunResult
6. 写入唯一终态事件 run.completed
7. 返回答案、Usage 和完整事件快照
```

如果循环返回 `limit_reached`，Runner 会写 `run.limit_reached`；普通循环失败则写 `run.failed`。

### 14.5 总超时和取消的区别

工具超时只限制一个工具，模型超时只限制一次模型请求。任务总超时包住完整 AgentLoop，防止多轮模型和工具调用累计运行过久。

取消不是普通异常。用户或上层任务取消协程时，Runner 生成 `cancelled` RunResult 和 `run.cancelled`；任务总超时则生成 `timeout` RunResult 和 `run.timeout`。两者语义不同，后续持久化和界面展示不能混为一谈。

### 14.6 唯一终态事件

一次 Run 的终态只能是：

```text
completed / failed / limit_reached / timeout / cancelled
```

Loop 只返回 AgentLoopResult，Executor 只写工具级事件，最终由 Runner 统一选择一个 Run 终态。集中所有权比在多个模块中“见错就写终态”更容易保证一致性。

### 14.7 错误边界

- 用户输入不合法：`invalid_input`；
- 循环正常失败：保留循环给出的错误码；
- 达到轮次、Token 或重复调用限制：`limit_reached`；
- 超过任务总时间：`run_timeout`；
- 协程被取消：`run_cancelled`；
- 未预料的运行时异常：`internal_runtime_error`，不把异常详情和敏感数据直接暴露出去。

### 14.8 测试和阅读顺序

`test_runner.py` 覆盖成功、输入错误、循环失败、限制、任务超时与取消，并检查每种情况只有一个终态事件。

推荐顺序：先读构造函数，接着读正常完成分支，再对照 RunStatus 阅读限制、超时和取消分支，最后运行测试观察 events。

### 14.9 面试与复习要点

**为什么还需要 Runner，不能直接调用 Loop？**

Loop 是可复用的迭代算法；Runner 才是一次业务任务的生命周期边界。run_id、总超时、取消和最终结果都属于后者。

**为什么每次 Run 新建 EventSink？**

这样事件序号从 1 开始、run_id 完全一致，也不会把两个任务的轨迹混在一起。

---

## 15. 模块 8：OpenAICompatibleProvider 与 CLI

### 15.1 模块目标

MockProvider 适合测试，但不会访问模型服务。模块 8 增加真实 HTTP 适配器和命令行入口，让用户可以启动一个完整 Agent。

主要文件：

```text
src/evoagent/providers/openai_compatible.py
src/evoagent/cli.py
tests/unit/test_openai_compatible_provider.py
tests/unit/test_cli.py
```

`pyproject.toml` 同时增加 HTTPX、RESPX 开发测试依赖，以及 `evoagent` 命令入口。

### 15.2 为什么 Provider 是适配器

AgentLoop 只认识 ModelRequest 和 ProviderEvent，真实服务却认识 HTTP JSON 与 SSE。OpenAICompatibleProvider 负责翻译：

```text
ModelRequest
  → HTTP POST /chat/completions
  → SSE data 分片
  → ProviderEvent
  → AgentLoop
```

这样将来接入其他厂商时，不需要修改 AgentLoop 和工具系统。

### 15.3 请求转换

Provider 会把统一消息转换成兼容接口需要的 `messages`。assistant 消息中的 ToolCall 会转换为 function 调用；tool 消息携带 `tool_call_id`，让模型知道它对应哪个调用。

请求固定启用流式输出和 Usage：

```text
stream = true
stream_options.include_usage = true
```

模型名、温度、最大输出 Token 和工具 Schema 都来自 ModelRequest，不从全局变量偷偷读取。

### 15.4 SSE 和 ToolCall 分片

SSE 可以把一个 ToolCall 拆成多段，甚至交错发送多个调用。例如：

```text
index 0: name="calcu", arguments="{...前半段"
index 1: name="file_", arguments="{...前半段"
index 0: name="lator", arguments="后半段...}"
index 1: name="read", arguments="后半段...}"
```

Provider 使用 index 为每个调用建立缓冲区，分别拼接 ID、函数名和参数字符串。收到 `[DONE]` 后，它按 index 排序，解析完整 JSON，并构造 ToolCall。缺 ID、名称、连续 index 或合法 JSON 中任意一项都会变成协议错误。

### 15.5 ProviderEvent 输出

适配器可能依次产生：

- `text_delta`：一小段回答文本；
- `tool_call_delta`：一段工具调用；
- `usage`：本次请求的 Token 统计；
- `completed`：已经重组并校验的 ModelResponse。

流必须出现完成原因和 `[DONE]`。不能因为网络连接正常关闭，就假定模型已经完整回答。

### 15.6 错误分类和资源管理

HTTP 401/403、429、5xx、其他 HTTP 错误、网络错误、超时和协议错误都有不同错误码。v0.1 不自动重试，因为重试还需要同时考虑幂等性、预算和退避策略。

Provider 可以接收外部 AsyncClient，便于测试和复用连接；只有它自己创建 Client 时，`aclose()` 才负责关闭，避免误关调用方拥有的资源。

### 15.7 CLI 怎样组装应用

CLI 负责应用最外层装配，而不是实现业务规则：

```text
解析参数与 Settings
  → 选择 Mock 或真实 Provider
  → 注册 calculator、file_read、web_fetch
  → 创建 ContextBuilder 和 AgentRunner
  → 执行任务
  → 输出最终答案或错误
  → 关闭网络资源
```

常用命令：

```powershell
# 无需 API Key，演示完整工具循环
.\.venv\Scripts\evoagent --demo --show-events

# 使用当前配置运行任务
.\.venv\Scripts\evoagent "请计算 12 * (3 + 4)"

# 补充一段不可信外部上下文
.\.venv\Scripts\evoagent "总结资料" --context "资料正文"
```

`--demo` 使用确定性的 MockProvider，不是在假装访问真实模型；它的作用是验证本地安装、Runner、循环、工具和输出链路。

### 15.8 测试和阅读顺序

Provider 测试使用 RESPX 模拟 HTTP，不访问公网，覆盖文本、Usage、单个与多个交错 ToolCall、HTTP 错误、超时、非法 JSON 和缺少 `[DONE]`。CLI 测试覆盖参数解析、Mock 任务和演示模式。

推荐先读 `_build_payload()` 看请求，再读 `stream()` 的 SSE 主流程，然后读 ToolCall 缓冲与错误分类，最后读 CLI 如何装配组件。

### 15.9 当前兼容边界

这里的“OpenAI-compatible”指项目实际使用的 Chat Completions 流式子集，不代表兼容所有厂商扩展。Responses API、多模态输入、音频、结构化输出和厂商私有字段尚未实现。

---

## 16. 模块 9：安全只读工具与阶段一收尾

### 16.1 模块目标

模块 9 让 Agent 获得受限文件与网页读取能力，同时补齐权限结果、安全并发和防重复循环。目标不是宣称“绝对安全”，而是建立清晰、可测试的最低边界。

主要文件：

```text
src/evoagent/tools/guards.py
src/evoagent/tools/builtin/file_read.py
src/evoagent/tools/builtin/web_fetch.py
src/evoagent/tools/executor.py
src/evoagent/core/loop.py
tests/unit/test_tool_guards.py
tests/unit/test_readonly_tools.py
tests/integration/test_agent_run.py
docs/ADR-001-阶段一运行时边界与安全策略.md
```

### 16.2 WorkspaceGuard 与 file_read

file_read 的输入只有 path，但它不能直接调用 `Path.read_text()`。WorkspaceGuard 会：

```text
用户路径
  → 相对路径拼到 Workspace
  → 解析普通路径
  → 检查仍在 Workspace
  → 解析现有目标和符号链接
  → 再次检查仍在 Workspace
  → 确认是普通文件
```

两次检查分别防止 `../` 路径穿越和符号链接逃逸。通过后，FileReadTool 仍限制读取字节数，只接受 UTF-8，避免一个文件耗尽上下文或返回不可解释的二进制数据。

### 16.3 URLGuard 与 web_fetch

web_fetch 面临 SSRF：模型可能请求云元数据、路由器后台或本机服务。URLGuard 因此只允许 HTTP/HTTPS，拒绝 URL 凭据、localhost，以及解析到回环、私网、链路本地等非公网 IP 的地址。

重定向也属于新请求，必须逐跳重新校验：

```text
公网 URL
  → 302 Location: http://127.0.0.1/admin
  → URLGuard 再校验
  → permission_denied
```

WebFetchTool 还限制超时、重定向次数、Content-Type、Content-Length 和实际流式读取字节数。只检查响应头不够，因为服务端可能不提供或伪造 Content-Length。

### 16.4 已知网络安全限制

当前实现先用 DNS 解析并校验 IP，再让 HTTP 客户端自行连接。两者之间存在时间差，恶意 DNS 可能改变结果，这称为 DNS 重绑定竞态。

因此这里应准确描述为“最低 SSRF 防护”，不能描述为完整网络沙箱。后续要用受控解析、固定连接目标、代理或网络出口策略把已校验地址与真实连接绑定起来。

### 16.5 permission_denied

ToolPermissionError 表示“调用在技术上可以执行，但策略不允许”。ToolExecutor 将它转换为：

```text
ToolResult.status = permission_denied
error_code = permission_denied
```

这与参数错误、普通执行失败和内部缺陷不同。模型可以根据这个稳定结果换一种合法方法，上层也能审计被拒绝的访问。

### 16.6 安全并发

多个只读工具可以同时等待文件或网络 I/O，但有副作用的工具必须保持顺序。`execute_many()` 只有确认整批工具都满足：

```text
has_side_effects == false
parallel_safe == true
```

才使用并发。只要有一个不满足，整批就顺序执行。即使并发，返回结果仍按输入顺序排列，因为后续 tool 消息必须与原 ToolCall 一一对应。

### 16.7 重复 ToolCall 保护

只比较调用参数仍然不够：同一请求第一次可能失败、第二次可能成功。AgentLoop 会把工具名、参数和结果状态、内容、错误码一起组成指纹。

只有连续多轮指纹完全相同并达到阈值时，才返回：

```text
status = limit_reached
error_code = repeated_tool_calls
```

参数变化、结果变化或中间出现其他调用都会重置连续计数。

### 16.8 完整集成测试

`test_agent_run.py` 不访问真实模型和公网，但会从 AgentRunner 出发，依次经过 ContextBuilder、AgentLoop、MockProvider、ToolExecutor 和三个内置工具，再得到最终 RunResult。它还检查多个工具结果回填模型时保持 calculate、read、fetch 的原始顺序。

这个测试证明模块能协作，不代表真实模型服务和任意网站都一定兼容；外部系统仍需单独的端到端验证。

### 16.9 推荐阅读顺序

```text
1. guards.py 的 WorkspaceGuard
2. file_read.py
3. guards.py 的 URLGuard
4. web_fetch.py 的重定向循环
5. executor.py 的权限转换与 execute_many()
6. loop.py 的重复调用指纹
7. test_agent_run.py 的完整链路
8. ADR-001 的设计取舍和已知限制
```

### 16.10 面试与复习要点

**为什么只读工具也需要安全策略？**

读取源码外的密钥文件或访问内网接口同样可能泄露数据。“不写入”不等于“无风险”。

**为什么并发后仍按原顺序返回？**

并发只优化等待时间，不能改变 ToolCall 与 ToolResult 的协议关联。

**为什么要把安全限制写入 ADR？**

安全边界不仅是代码细节，也是系统承诺。明确记录已做和未做的部分，能避免后续把最低保护误当成完整沙箱。

---

## 17. 当前代码如何协作

以 CLI 演示中的请求“计算 12 × (3 + 4)”为例，当前项目可以实际执行：

```text
1. CLI 读取配置并注册三个内置工具

2. AgentRunner 创建 run_id、EventSink 和初始消息

3. AgentLoop 构造 ModelRequest
   - messages：用户问题
   - tool_definitions：registry.definitions()

4. MockProvider 返回预设模型响应

5. 模型返回 ToolCall
   - call_id：call_001
   - name：calculator
   - arguments：{"expression": "12 * (3 + 4)"}

6. ToolExecutor 调用 registry.get("calculator")

7. ToolExecutor 调用 tool.validate_arguments(...)
   得到 CalculatorArguments

8. ToolExecutor 在超时控制下调用 tool.invoke(...)

9. CalculatorTool 返回字符串 "84"

10. ToolExecutor 生成 ToolResult
   - status：success
   - content："84"

11. AgentLoop 把 ToolResult 转成 tool 消息交还模型

12. 模型回答“计算结果是 84。”

13. AgentLoop 返回 AgentLoopResult

14. AgentRunner 写入 run.completed 并返回 RunResult

15. CLI 输出最终答案，可选输出事件列表
```

这条流程已经由单元测试和集成测试完整打通。Mock 演示的意义是确定性验证框架；把配置切换到 `openai_compatible` 后，步骤 4 会由真实 HTTP 流式请求替代，其余 Runtime 结构保持不变。

---

## 18. 测试体系

### 18.1 为什么测试和模块同时编写

Agent 系统中有大量异步、流式和外部依赖。如果只依赖人工运行真实模型，很难复现同一个错误。

本项目要求每完成一个模块，同时完成对应确定性测试。测试的作用不仅是判断当前代码是否正确，也是在后续重构时保护已经固定的接口行为。

### 18.2 阶段二收尾时的测试文件

| 文件 | 主要覆盖内容 |
|---|---|
| `test_config.py` | 默认配置、环境变量、真实 Provider 条件校验、Workspace 路径 |
| `test_models.py` | 消息、工具调用、Provider 事件、Usage 和 RunResult 契约 |
| `test_events.py` | 事件排序、并发序号、脱敏、截断和非法 payload |
| `test_tool_registry.py` | 注册、重名、未知工具、定义顺序和错误对象 |
| `test_calculator.py` | 正常算术、参数校验、危险语法和资源限制 |
| `test_tool_executor.py` | 参数校验、超时、截断、错误转换、顺序和取消 |
| `test_mock_provider.py` | 标准事件流、工具增量、预设错误和脚本耗尽 |
| `test_context.py` | 初始消息顺序、输入校验和无状态性 |
| `test_agent_loop.py` | 模型—工具循环、终止条件、Usage、协议错误和重复调用 |
| `test_runner.py` | Run 生命周期、唯一终态、总超时和取消 |
| `test_openai_compatible_provider.py` | SSE、ToolCall 重组、Usage 和 Provider 错误分类 |
| `test_cli.py` | CLI 参数、Mock 任务和演示模式 |
| `test_tool_guards.py` | Workspace 边界、符号链接和公网 URL 判断 |
| `test_readonly_tools.py` | 文件/网页读取、重定向、超时、类型与大小限制 |
| `test_agent_run.py` | 从 Runner 到三个内置工具再到最终答案的集成链路 |
| `test_state_machine.py` | Task/Run 合法迁移和终态保护 |
| `test_persistence.py` | UnitOfWork、乐观锁、持久化事件和 Trace |
| `test_migrations.py` | Alembic upgrade、downgrade 和 PostgreSQL Schema 检查 |
| `test_postgres_persistence.py` | PostgreSQL 跨连接事件序号 |

阶段二收尾时的测试结果：

```text
142 passed, 3 skipped
```

本机跳过真实 PostgreSQL 的两个用例和一个无符号链接权限的用例；CI 提供 PostgreSQL Service 和 Linux 符号链接环境继续执行它们。

### 18.3 常用检查命令

在项目根目录运行：

```powershell
.\.venv\Scripts\ruff check .
.\.venv\Scripts\ruff format --check .
.\.venv\Scripts\pytest -q
```

三个命令分别检查：

1. 代码质量和常见错误；
2. 代码格式是否统一；
3. 功能测试是否通过。

---

## 19. 阶段二收尾时不能完成的功能

截至阶段二模块 12，项目仍不能：

- 在多个 Provider 间自动路由和熔断；
- 向不可信公网用户提供生产级认证、RBAC 与多租户隔离；
- 提供操作系统级强进程沙箱和网络出口隔离；
- 完全防御 DNS 重绑定；
- 压缩长期上下文或维护长期记忆；
- 编排多个 Agent；
- 生成、评测或发布 Skill（该项已由阶段三补齐）。

这是阶段二结束时的边界快照。阶段三已经补齐 Skill 生命周期；其余 Provider 路由、生产认证、强隔离、长期记忆和多 Agent 仍未实现。阶段二实现的 PermissionPolicy、路径边界和 argv allowlist 是应用级防线，不能描述成生产级隔离。

---

## 20. 后续阶段路线

### 20.1 第一阶段已经完成

```text
模块 0～6  基础契约与核心循环
模块 7    AgentRunner
模块 8    OpenAICompatibleProvider 与 CLI
模块 9    安全只读工具与第一阶段收尾
```

### 20.2 第二阶段已经完成：持久化与可恢复执行

模块 0～12 已经在阶段一内核外建立可靠执行层：

```text
Task API → PostgreSQL → Job Lease → Worker
AgentLoop → Snapshot / RunEvent / ToolEffect / Artifact
Approval + Sandbox → 受控工具执行
RunEvent → SSE，全部事实表 → Trace Viewer
```

阶段一、二测试仍是回归基线；阶段三模块 0～12 也已经完成并范围冻结。当前优先学习和验证完整生命周期，是否进入阶段四应单独决定。

---

## 21. 推荐阅读源码的顺序

第一次阅读当前代码时，建议按下面顺序：

```text
1. pyproject.toml
   ↓
2. src/evoagent/config.py
   ↓
3. src/evoagent/core/models.py
   ↓
4. src/evoagent/core/events.py
   ↓
5. src/evoagent/tools/base.py
   ↓
6. src/evoagent/tools/registry.py
   ↓
7. src/evoagent/tools/builtin/calculator.py
   ↓
8. src/evoagent/tools/executor.py
   ↓
9. src/evoagent/providers/base.py
   ↓
10. src/evoagent/providers/mock.py
   ↓
11. src/evoagent/core/context.py
   ↓
12. src/evoagent/core/loop.py
   ↓
13. src/evoagent/core/runner.py
   ↓
14. src/evoagent/tools/guards.py
   ↓
15. src/evoagent/tools/builtin/file_read.py
   ↓
16. src/evoagent/tools/builtin/web_fetch.py
   ↓
17. src/evoagent/providers/openai_compatible.py
   ↓
18. src/evoagent/cli.py
   ↓
19. 对应 tests/unit 测试
   ↓
20. tests/integration/test_agent_run.py
```

阅读每个文件时依次回答四个问题：

1. 这个模块接收什么输入？
2. 它输出什么结果？
3. 它负责什么？
4. 它明确不负责什么？

如果能回答这四个问题，就基本理解了模块边界。

---

## 22. 当前阶段应掌握的核心思想

### 22.1 先定义契约，再连接模块

Model、Tool 和 Runtime 先使用统一数据结构沟通，后续模块才不需要相互猜测字段含义。

### 22.2 模型输出是不可信输入

Tool Call 必须经过工具查找、参数校验、权限检查和执行控制，不能直接调用函数。

### 22.3 Registry 和 Executor 分离

Registry 管“有什么”，Executor 管“怎样执行”。职责拆分使测试、权限和沙箱更容易扩展。

### 22.4 使用抽象隔离外部实现

AgentLoop 依赖 Provider 和 EventSink 的抽象，而不是绑定某个模型厂商或数据库。

### 22.5 可观测性应从第一天开始设计

事件不是最后才添加的日志，而是长任务恢复、Trace、评测和 Skill 进化的基础数据。

### 22.6 安全采用白名单和最小权限

计算器只解释允许的 AST 节点；文件和网络工具限制工作目录、目标地址和结果大小。安全边界必须同时说明已知限制。

### 22.7 测试必须确定

核心逻辑使用 MockProvider 和 Mock Tool 测试，避免把随机模型行为和外部网络引入基础测试。

---

## 23. 文档后续维护规则

本手册将持续保存后续模块讲解。每完成一个模块，应按以下结构追加，而不是重写前面已经稳定的内容：

```text
模块目标
→ 新增或修改的文件
→ 模块在总体架构中的位置
→ 关键类和函数
→ 输入与输出数据
→ 正常执行链路
→ 错误和边界情况
→ 为什么采用当前设计
→ 对应测试
→ 当前仍未实现的内容
→ 推荐阅读顺序
→ 面试与复习要点
```

更新手册时还应遵守：

1. 只把仓库中已有代码描述为已实现。
2. 接口名称与真实源码保持一致。
3. 修改既有接口后，同时更新旧章节，不能只在新章节补充。
4. 示例应突出数据如何流动，不堆放大段源码。
5. 技术术语第一次出现时用通俗语言解释。
6. 每章说明模块负责什么，也说明它不负责什么。
7. 测试数量以当时实际执行结果为准。

---

## 24. 本阶段总结

当前 EvoAgent 已经完成了 Agent Runtime 的基础骨架：

```text
配置系统
  + 核心数据契约
  + 运行事件系统
  + 工具抽象
  + 工具注册表
  + 安全计算器
  + ToolExecutor
  + ModelProvider 抽象与 MockProvider
  + ContextBuilder
  + AgentLoop
  + AgentRunner
  + OpenAICompatibleProvider
  + CLI
  + WorkspaceGuard 与 URLGuard
  + FileReadTool 与 WebFetchTool
  + 重复调用保护与安全并发
  + 异步数据库与 Alembic
  + Task/Run 持久化模型和状态机
  + Repository 与 UnitOfWork
  + PersistentEventSink 与基础 Trace
```

当前已经具备可确定性测试的核心循环：

```text
CLI → AgentRunner → 模型决策 → 工具执行 → 结果回填 → 最终 RunResult
```

当前已经打通从 API 提交到 Worker 持久化执行、恢复和重试的后端主链路。后续链路是：

```text
SSE 观察 → 权限审批与副作用事实 → 受控工具 → 可验证 Skill 生命周期
```

---

## 25. 阶段二模块 0：工程配置

### 25.1 模块目标

模块 0 只建立阶段二运行环境，不执行持久化任务。项目版本进入 `0.2.0.dev0`，新增 FastAPI、Uvicorn、SQLAlchemy asyncio、Alembic 和 asyncpg；aiosqlite 只服务于本地快速测试。

新增或修改：

```text
pyproject.toml
src/evoagent/config.py
.env.example
docker-compose.yml
.github/workflows/ci.yml
```

### 25.2 为什么同时需要 asyncpg 和 aiosqlite

asyncpg 连接正式 PostgreSQL，能够验证事务、行锁和数据库并发。aiosqlite 让没有 Docker 的开发环境快速测试 ORM 和 Repository，但不能证明 PostgreSQL 的 `SKIP LOCKED` 等语义。

因此测试通过必须准确区分：SQLite 通过表示接口和基础 SQL 可运行；PostgreSQL CI 通过才表示 PostgreSQL 特性成立。

### 25.3 新配置的边界

数据库 URL 使用 SecretStr，配置对象被打印时不会直接显示密码。Artifact 必须位于 Workspace 的子目录。heartbeat 的三倍不能大于 lease，确保 Worker 有足够时间续租。

模块 0 当时的 Docker Compose 只提供 PostgreSQL；模块 12 已把 migration、API 和单 Worker 补入 Compose，端口仍只绑定本机回环地址。

### 25.4 阅读与测试重点

先读 Settings 新字段和交叉校验，再读 Compose 的端口、健康检查和 volume，最后读 CI 怎样注入独立测试数据库。对应测试覆盖异步 URL、SecretStr、Artifact 边界和租约参数关系。

---

## 26. 阶段二模块 1：数据库与 Alembic

### 26.1 模块目标

模块 1 解决三个问题：怎样建立异步连接、怎样为每个操作创建独立 Session、怎样让数据库结构可升级和回退。

主要文件：

```text
src/evoagent/db/base.py
src/evoagent/db/session.py
alembic.ini
migrations/env.py
migrations/versions/20260906_0001_initial_persistence.py
```

### 26.2 Engine、Session 和事务

```text
AsyncEngine：管理连接池
async_sessionmaker：生产 AsyncSession
AsyncSession：一次工作单元使用的数据库会话
Transaction：决定一组写入一起提交或一起回滚
```

Database 对象拥有 Engine；调用方每次从 session_factory 创建新 Session。一个 AsyncSession 不能同时给多个并发协程共享。

### 26.3 Alembic 为什么不能由 create_all 代替

`create_all()` 适合测试中从零建表，却不会记录 Schema 如何从旧版本演进。Alembic revision 是可审查的数据库版本：upgrade 正向创建结构，downgrade 按外键反序回退。

正式应用只执行 Alembic migration，不能在启动时根据当前 ORM 静默修改数据库。

### 26.4 异步迁移链路

```text
alembic 命令
→ migrations/env.py
→ AsyncEngine 建立连接
→ connection.run_sync()
→ Alembic 同步迁移上下文
→ upgrade/downgrade
```

迁移测试会在 SQLite 验证完整建表和回退，在 CI 的真实 PostgreSQL 中额外执行 `alembic check`，检查 ORM metadata 与 migration 是否发生漂移。

---

## 27. 阶段二模块 2：持久化模型与状态机

### 27.1 模块目标

Pydantic 模型规定 Runtime 数据契约，SQLAlchemy Record 规定数据库中的表、外键和约束。两者名称可能相似，但不能混用。

主要文件：

```text
src/evoagent/db/models.py
src/evoagent/tasks/state_machine.py
tests/unit/test_state_machine.py
```

### 27.2 当前记录的三组职责

| 分组 | 记录 | 作用 |
|---|---|---|
| 任务 | Session、Message、Task、Run | 保存用户目标和执行尝试 |
| Trace | Turn、ToolCall、RunEvent、RunSnapshot、Artifact | 保存过程、恢复点和大内容引用 |
| 安全 | ToolEffect、ToolApproval | 保存副作用事实与人工决定 |

这些表在模块 2 先固定结构，不代表 Snapshot 恢复、审批和副作用执行已经实现。

### 27.3 数据库约束是最后防线

模型加入 `(run_id, sequence)`、`(effect_scope, semantic_key)` 等唯一约束，非负数检查、外键和领取索引。即使应用代码存在竞争，数据库也不能接受重复事件序号或重复语义副作用记录。

时间使用带时区 UTC；Task 和 Run 保存 lock_version，为后续乐观锁更新提供依据。

### 27.4 状态机为什么独立于 ORM

`ensure_task_transition()` 和 `ensure_run_transition()` 是纯 Python 规则，可以不启动数据库直接测试。Repository 在更新前调用状态机，数据库只保存结果。

终态没有任何后继状态。例如 completed 不能重新 queued；需要再次执行时应创建新的 Run，而不是篡改旧 Run 历史。

---

## 28. 阶段二模块 3：Repository、Unit of Work 与 Trace

### 28.1 模块目标

模块 3 在 ORM 与应用层之间建立稳定访问入口：Repository 管“怎样查询某类记录”，UnitOfWork 管“哪些操作属于同一事务”，PersistentEventSink 把阶段一事件协议接到数据库。

主要文件：

```text
src/evoagent/db/repositories/
src/evoagent/db/unit_of_work.py
src/evoagent/trace/persistent_sink.py
src/evoagent/trace/service.py
tests/integration/test_persistence.py
tests/integration/test_postgres_persistence.py
```

### 28.2 Repository 与 UnitOfWork

Repository 不是简单隐藏所有 SQL。它集中表达聚合相关操作，例如 `TaskRepository.transition()` 同时执行状态机判断和带 lock_version 的原子更新。

UnitOfWork 让 Task、Run、Event、Snapshot 和 Effect Repository 共享同一个 AsyncSession：

```text
进入 UnitOfWork
→ 创建一个 AsyncSession
→ 通过多个 Repository 读写
→ 调用方显式 commit()
→ 异常时 rollback()
→ 关闭 Session
```

没有自动提交是有意设计：调用方必须清楚指出事务真正生效的位置。

### 28.3 乐观锁

更新 Task/Run 时，SQL 的条件同时包含 id 和调用方看到的 expected_version。成功后 lock_version 加一；如果更新不到记录，说明另一个事务已经先修改，抛出 ConcurrentUpdateError，不能用旧数据覆盖新状态。

### 28.4 PersistentEventSink

它实现与 InMemoryEventSink 相同的 `emit()` 形状，因此 Agent 核心不需要认识 SQLAlchemy。

```text
emit(EventType, payload)
→ 复用阶段一 sanitize_payload
→ 原子增加 Run.next_event_sequence
→ 得到本事件 sequence
→ 插入 RunEvent
→ 提交短事务
→ 返回 RuntimeEvent
```

数据库唯一约束是第二层保护。单个 Sink 还用 asyncio.Lock 保证本进程事件列表顺序。

### 28.5 TraceService

模块 3 此时的 TraceService 只按 run_id 返回 Run 状态和排序后的事件。模块 11 已在这个基础投影上聚合 Turn、ToolCall、ToolEffect、Approval、Snapshot 和 Artifact。

### 28.6 当前边界与测试

本阶段已经证明：工作单元提交/回滚、状态机与乐观锁、事件脱敏、事件有序落库、Trace 查询和 migration 可运行。真实 PostgreSQL CI 还会用两个 PersistentEventSink 并发追加 20 个事件，验证数据库原子序号没有重复。

这是模块 3 完成时的边界；Task Service、FastAPI、Worker、Job Lease 和恢复执行已经分别在后续模块 4～12 完成。保留本段是为了说明实现顺序，而不是描述仓库当前状态。

### 28.7 推荐阅读顺序

```text
1. tasks/state_machine.py
2. db/models.py
3. db/session.py
4. db/repositories/tasks.py
5. db/repositories/events.py
6. db/unit_of_work.py
7. trace/persistent_sink.py
8. trace/service.py
9. 对应 integration 测试
```

复习时重点回答：为什么 AgentLoop 不依赖 ORM、为什么 UnitOfWork 显式提交、为什么 ToolCall ID 不能代替语义幂等键，以及 SQLite 测试为什么不能证明 PostgreSQL 并发语义。

---

## 29. 阶段二模块 4：Task Service 与最小 FastAPI

### 29.1 模块目标

模块 4 给持久化底座增加应用入口。调用者可以创建 Session、提交 Task、查询状态，以及取消、暂停和恢复任务。提交 Task 返回 HTTP 202，表示请求已经被系统接受，但任务尚未执行完成。

主要文件：

```text
src/evoagent/tasks/service.py
src/evoagent/api/app.py
src/evoagent/api/dependencies.py
src/evoagent/api/schemas.py
src/evoagent/api/routes/sessions.py
src/evoagent/api/routes/tasks.py
tests/integration/test_task_api.py
```

### 29.2 一次任务提交发生了什么

```text
POST /api/v1/tasks
→ Pydantic 校验 TaskCreateRequest
→ FastAPI 注入 TaskService
→ 打开 UnitOfWork
→ 检查 Session 存在
→ 创建 QUEUED Task
→ 创建首个 QUEUED Run
→ 追加 task.queued 事件
→ 同一事务 commit
→ 返回 202 TaskResponse
```

三个数据库记录必须一起成功或一起失败。如果只写入 Task 就崩溃，Worker 会看到一个没有 Run 的残缺任务；因此事务边界不能拆开。

### 29.3 DTO 为什么不能直接返回 ORM

ORM Record 代表数据库中的可变对象，API Schema 代表对外承诺。`TaskResponse` 主动选择公开哪些字段，并把最新 Run 组装为嵌套响应。这样以后增加数据库内部列时，不会意外把租约所有者等内部信息暴露出去。

### 29.4 暂停、恢复和取消

QUEUED Task 可以直接暂停、恢复或取消。运行中的取消不同：API 只写入 `cancel_requested=True` 和事件，真正持有租约的 Worker 观察请求后提交 CANCELLED 终态。这样 API 不会越过租约所有者与执行协程竞争。

### 29.5 应用生命周期与错误格式

`create_app()` 允许测试注入 Database；正式启动时由应用创建并在 lifespan 结束时释放连接池。`/health/live` 只证明进程存活，`/health/ready` 执行 `SELECT 1` 证明数据库可用。业务冲突、找不到资源和请求校验都返回统一的 `{"error": ...}` 结构。

本模块仍不在 HTTP 请求中运行 Agent，也没有 SSE。可执行入口是 `evoagent-api`。

---

## 30. 阶段二模块 5：Job Lease 与单 Worker

### 30.1 锁与租约不是一回事

数据库行锁只在领取任务的短事务中存在。如果在模型调用的几分钟内一直占用事务，会长期占用连接和锁。Job Lease 是写在 Task 上的有期限所有权：

```text
短事务：SELECT ... FOR UPDATE SKIP LOCKED
→ 写 lease_owner、lease_expires_at、heartbeat_at
→ Task/Run 进入 RUNNING
→ commit 并释放行锁

执行期间：定期 heartbeat 延长 lease_expires_at
```

### 30.2 为什么使用 SKIP LOCKED

两个 Worker 同时查找队首任务时，第一个事务锁住该行，第二个事务跳过已锁行，而不是等待后又重复领取。应用层仍检查 lease_owner；最终提交还要求数据库中的所有者相同且租约未过期。

### 30.3 心跳、取消和崩溃

`LeaseHeartbeat` 定时续租并检查 `cancel_requested`。取消请求会中止 Handler，再由 Worker 提交 CANCELLED。Worker 崩溃时不会主动释放租约；到期后 `recover_expired()` 把 Task 和 Run 变为 RECOVERING，并记录原 Worker。

这是一种 at-least-once 执行基础：任务可能被再次处理，所以后续副作用不能只依赖“这次应该不会重复”。模块 9 将用 ToolEffect 处理副作用事实。

### 30.4 Worker 边界

`JobWorker` 依赖 `TaskHandler` 协议，不依赖具体 Agent。模块 5 可以放入 Fake Handler 测试领取与收尾；模块 7 再把 PersistentAgentRunner 作为真实 Handler 注入。单 Worker 版本仍测试两个领取者竞争，因为将来扩容不能改变正确性。

真实 PostgreSQL CI 负责证明 `SKIP LOCKED` 竞争结果；SQLite 测试只证明领取接口、续租、错误所有者拒绝和状态变化。

---

## 31. 阶段二模块 6：Artifact、Snapshot 与 LoopState

### 31.1 Event、Snapshot 与 Artifact 的区别

| 概念 | 回答的问题 | 内容 |
|---|---|---|
| Event | 发生过什么 | 小型、追加式审计事实 |
| Snapshot | 从哪里继续 | 某个合法边界的完整 LoopState |
| Artifact | 大内容在哪里 | 文件 URI、哈希、大小和元数据 |

事件不应塞入完整上下文，快照也不取代审计历史。Artifact 数据保存在受控存储中，数据库只保存可验证引用。

### 31.2 LoopState 显式保存什么

`LoopState` 包含消息历史、已经完整结束的 iteration、累计 Usage 是否完整、重复工具调用指纹与计数，以及运行配置哈希。恢复时不能只保存 messages，否则 Token 预算和重复调用保护会被重置。

AgentLoop 只在模型响应完整、全部 ToolResult 已经回填消息之后调用 `LoopCheckpointWriter.save()`。它不保存半段模型流，也不保存正在执行一半的工具。

### 31.3 版本和配置哈希

Snapshot 有数据库 `schema_version`；加载器只接受当前支持版本。LoopState 还保存不含密钥的配置哈希，其中包括模型、循环上限和工具定义。改变这些运行语义后直接续跑旧快照可能得到不可解释结果，因此默认拒绝。

### 31.4 Artifact 安全边界

`LocalArtifactStore` 只接受普通文件名，在 `artifact_root/<run_id>/` 下原子替换文件，拒绝 `../` 路径穿越，并记录 `sha256:` 哈希与字节数。读取时再次解析并检查路径仍位于根目录内。

---

## 32. 阶段二模块 7：PersistentAgentRunner 与恢复

### 32.1 如何复用阶段一内核

```text
JobWorker 领取 JobLease
→ PersistentAgentRunner 检查租约记录
→ 加载最新 LoopState（若存在）
→ 组装 PersistentEventSink
→ 组装 PersistentCheckpointStore
→ 组装 ToolExecutor 与 AgentLoop
→ 从初始消息或快照运行
→ 返回 TaskExecutionResult
→ JobLeaseManager 检查所有权并提交终态
```

SQLAlchemy 只存在于 runtime 适配层。AgentLoop 依赖 `RuntimeEventSink` 和 `LoopCheckpointWriter` 协议，因此阶段一内存测试仍然成立。

### 32.2 RecoveryService 怎样决策

租约到期只是发现崩溃，不能直接假设“重新跑就行”。RecoveryService 先检查未决 ToolEffect，再检查 Snapshot：

```text
存在 PREPARED / EXECUTING / UNKNOWN 副作用
→ FAIL_UNSAFE，等待未来人工处理机制

快照版本不兼容
→ FAIL_INCOMPATIBLE

存在合法快照
→ RESUME，从快照后的下一 iteration 继续

没有快照且没有不安全副作用
→ RESTART，从初始上下文开始
```

`recovery.started`、`recovery.decided`、`recovery.completed` 或 `recovery.failed` 都进入事件时间线。快照后的不完整模型事件不会伪装成已完成状态；恢复从上一个合法边界重试该步骤。

### 32.3 当前恢复边界

当前自动恢复面向无副作用或可安全重复的任务。ToolEffect 的完整 COMMITTED 结果复用、UNKNOWN 人工确认和审批流属于模块 9。当前实现宁可明确失败，也不会对未知副作用做乐观猜测。

---

## 33. 阶段二模块 8：分类重试与预算

### 33.1 为什么不能遇错就重试

`RetryPolicy` 先把错误分成瞬时错误、限流、永久错误、需要用户、不确定副作用和预算耗尽。只有已知瞬时错误及限流可以自动重试。协议错误、参数错误和未知错误默认是永久错误；不确定副作用必须等待人工判断。

### 33.2 退避和抖动

普通瞬时错误使用：

```text
delay = min(base × 2^(attempt-1), max)
实际等待 = delay × 0.5～1.5 的随机抖动
```

指数退避减少服务故障时的请求风暴，抖动避免大量 Worker 同时醒来。服务明确给出 Retry-After 时尊重该等待值，并受最大延迟上限保护。

### 33.3 三种预算

- 次数预算：`attempt_count` 达到上限后停止。
- 累计时间预算：从 Task 创建时间计算，下一次延迟不能越过上限。
- Token 预算：LoopState 保留累计 Usage，达到上限后不再请求模型。

重试结果由租约所有者在同一事务写为 RETRYING、设置 `next_attempt_at` 并追加 `retry.scheduled`。退避到期后 `promote_due_retries()` 写入 `retry.ready`，再把 Task/Run 放回 QUEUED。重试不是在原调用栈里 `sleep` 后死循环，因此不会长期占住 Worker。

### 33.4 推荐阅读顺序

```text
1. api/schemas.py 与 api/routes/tasks.py
2. tasks/service.py
3. tasks/lease.py
4. workers/heartbeat.py 与 workers/main.py
5. core/models.py 中的 LoopState
6. core/loop.py 的 resume_state 与 checkpoint_writer
7. runtime/checkpoints.py 与 trace/artifacts.py
8. runtime/recovery.py
9. runtime/persistent_runner.py
10. runtime/retry.py
11. 对应 unit / integration / PostgreSQL 测试
```

读完后应能解释：为什么 API 返回 202、为什么不能用长事务代替租约、为什么快照只能落在完整边界、为什么恢复不是从头盲跑，以及为什么重试必须同时受分类与预算约束。

## 34. 阶段二模块 9：Permission Policy、Approval 与 ToolEffect

### 34.1 这一模块解决什么问题

阶段一的 ToolExecutor 只判断“参数是否合法、工具能否执行”，却不知道一次调用是否需要用户授权，也无法证明副作用有没有执行过。模块 9 在 ToolExecutor 与具体工具之间加入 `PersistentToolMiddleware`，让每次持久化执行都遵循同一条链路：

```text
ToolCall
  → 参数校验
  → PermissionPolicy 计算有效风险
  → ALLOW / DENY / REQUIRE_APPROVAL
  → 记录 ToolCall
  → 有副作用时检查 ToolEffect
  → 实际调用工具
  → 持久化成功结果或 UNKNOWN
```

`PermissionPolicy` 是规则，不负责弹窗；`ApprovalService` 是审批状态的应用服务，不执行工具；`ToolEffect` 是副作用账本，不等同于普通运行日志。职责分开后，策略、人工决定和执行事实都可以单独测试。

### 34.2 动态风险与三种决定

工具类声明基础风险，但一次具体调用可以通过 `effective_risk()` 提升风险。例如 `file_write` 新建文件是 R1，覆盖已有文件会提升为 R2。默认策略允许 R0/R1，R2/R3 要求审批；明确列入拒绝名单的工具直接 DENY。

审批发生时，Runner 不在 Worker 里等待键盘输入，而是把 Task 和 Run 置为 `WAITING_USER`。用户通过审批 API 作出决定后，它们重新进入 `QUEUED`，由正常租约流程继续执行。这使 API 与 Worker 可以是两个独立进程。续跑时 Provider 可能生成新的 call_id，因此决定按“同一 Task、同一工具、同一组规范化参数”复用；call_id 仍只负责关联单次 Trace。

### 34.3 ToolEffect 为什么不能只记一个调用 ID

Worker 可能在外部动作成功后、数据库提交前崩溃。此时数据库只知道调用开始了，不知道外部世界是否已经改变。因此 ToolEffect 使用规范化的“工具名 + 参数”计算语义键，并保留以下关键状态：

- `PREPARED`：已经登记，尚无开始执行的证据；
- `EXECUTING`：外部调用已经开始，结果尚未确认；
- `COMMITTED`：结果和哈希已经持久化，后续直接复用；
- `UNKNOWN`：无法判断外部动作是否成功，必须由人确认。

用户对 UNKNOWN 选择 `retry`，表示确认外部动作没有提交，可以再执行；选择 `committed:<结果>`，表示确认已经提交，系统保存该结果而不再调用工具。它提供的是可审计的保守恢复，不宣称所有外部系统都具备通用 exactly-once。

### 34.4 推荐阅读顺序

```text
tools/policy.py
→ tools/execution.py
→ tools/executor.py
→ tools/effects.py
→ tools/approvals.py
→ api/routes/approvals.py
→ tests/integration/test_policy_effects_approvals.py
```

## 35. 阶段二模块 10：Sandbox 与新增工具

### 35.1 Policy 和 Sandbox 的区别

Policy 回答“是否允许这次调用”，Sandbox 回答“即使允许，代码实际上最多能碰到哪里”。只有 Policy，没有 Sandbox，工具实现中的缺陷仍可能越界；只有 Sandbox，没有 Policy，高风险动作会在没有用户知情的情况下执行。

`RunSandbox` 把写入限制在当前 Run 的 Artifact 目录。路径解析后必须仍在根目录中；写入先落到同目录临时文件，再用原子替换提交，避免读到半个文件。`file_write` 覆盖已有文件会提升风险并进入审批。

### 35.2 四个新增能力

- `file_write`：只写当前 Run 沙箱，覆盖需要更高权限；
- `web_search`：依赖 SearchProvider 协议，测试使用 Mock，真实实现可以选择 Brave；
- `ask_user`：不直接读标准输入，而是复用持久化 Approval 流返回用户文本；
- `shell`：默认关闭；显式启用后只接收 argv 和可执行文件白名单。

Shell 使用 `asyncio.create_subprocess_exec()`，不使用 `shell=True`，因此不会解释管道、重定向和命令替换。它还限制工作目录、环境变量、运行时间和输出长度。不过白名单不等于操作系统隔离，所以阶段二仍不把它称为生产级沙箱。

### 35.3 搜索为什么要再抽象一层

Agent 只依赖 `web_search` 工具，工具只依赖 `SearchProvider`。这样单元测试不会访问公网，也不需要 API Key；替换搜索厂商时不需要修改 AgentLoop。Brave 的密钥用 `SecretStr` 保存，只放在请求头中，不能写入事件和 Trace。

### 35.4 推荐阅读顺序

```text
tools/sandbox.py
→ tools/builtin/file_write.py
→ tools/builtin/web_search.py
→ tools/builtin/ask_user.py
→ tools/builtin/shell.py
→ tests/unit/test_new_tools.py
```

## 36. 阶段二模块 11：SSE、完整 Trace 与 Viewer

### 36.1 SSE 推送的是什么

SSE 不直接转发 Worker 内存里的事件，而是轮询 PostgreSQL 中已经提交的 RunEvent。数据库因此仍是唯一事实来源：客户端断线不会影响任务，API 重启也不会丢掉已提交历史。

每条消息使用 RunEvent.sequence 作为 SSE `id`。客户端重连时把 `Last-Event-ID` 传回来，服务只查询更大 sequence 的记录：

```text
首次连接：after_sequence = 0 → 发送 1, 2, 3
连接断开：客户端保存 id = 3
再次连接：Last-Event-ID = 3 → 发送 4, 5, ...
```

空闲时发送注释型 heartbeat；Run 进入终态且历史已经发送完毕后关闭流。关闭 SSE 连接只代表停止观看，不代表取消 Task。

### 36.2 Trace 为什么比事件列表更完整

`TraceService` 以 Run 为主键聚合状态、事件、Turn、ToolCall、ToolEffect、Approval、Snapshot 和 Artifact。事件适合回答“按时间发生了什么”，其他表适合回答“当前事实是什么、对象之间怎样关联”。Trace API 同时返回两者，才能定位一次审批、恢复或副作用复用。

`/viewer` 是刻意保持简单的本地页面：输入 run_id 后读取 Trace 并展示时间线。它用于学习和排障，不承担登录、多会话聊天或复杂前端状态管理。

### 36.3 推荐阅读顺序

```text
trace/sse.py
→ api/routes/events.py
→ trace/service.py
→ api/routes/traces.py
→ web/viewer.py
→ tests/integration/test_sse_and_trace_api.py
```

## 37. 阶段二模块 12：容器装配、故障注入与阶段验收

### 37.1 为什么还需要进程级装配

前面的类都能单独测试，但只有把 PostgreSQL、migration、API 和 Worker 装成独立进程，才能验证“请求进程不执行 Agent”和“Worker 崩溃后可被另一个进程接管”。`ConfiguredTaskHandler` 根据配置为每个 Run 创建独立 Provider、工具注册表和 PersistentAgentRunner，避免跨 Run 复用有状态 Provider。

Compose 的启动关系是：

```text
PostgreSQL healthcheck 成功
  → migrate 一次性升级成功
    → API 与单 Worker 启动
```

镜像使用非 root 用户，API 只映射到本机回环地址，Workspace 使用命名卷。Shell 白名单默认为空。完整演示命令和数据清理方式见《阶段二演示与故障注入》，能力边界见《阶段二安全边界》。

### 37.2 故障测试证明什么

普通成功测试无法证明恢复安全。故障注入测试专门制造“ToolEffect 已进入 EXECUTING，但结果尚未提交”的中间状态，验证恢复服务会把它标为 UNKNOWN、创建审批并进入 WAITING_USER；审批为 `retry` 后才重新排队。端到端测试则从 Task API 创建任务，经过真实 JobWorker 和 PersistentAgentRunner，最终查询到 COMPLETED Task 与 Trace。

这些测试证明的是本项目声明的边界，不证明进程永不崩溃，也不证明任意第三方 API 可以 exactly-once。

### 37.3 阶段二完成后的总调用链

```text
POST Task
→ PostgreSQL 中的 QUEUED Task/Run
→ Worker 领取 Job Lease
→ PersistentAgentRunner 恢复 Snapshot 或创建初始状态
→ AgentLoop 请求 Provider
→ ToolExecutor + Policy + Approval + ToolEffect + Sandbox
→ Snapshot / RunEvent / Artifact 持久化
→ SSE 与 Trace 从数据库投影给客户端
→ 正常终态，或崩溃后由 RecoveryService 决策
```

### 37.4 阶段二的学习验收

读完并运行测试后，应能独立解释：

1. 为什么数据库而不是 API/Worker 内存是事实来源；
2. 为什么行锁只用于短事务，而长任务所有权使用租约；
3. Event、Snapshot、Artifact 和 ToolEffect 各保存什么；
4. 为什么 COMMITTED 可以复用，而 UNKNOWN 必须停下来；
5. 为什么 Policy 不能替代 Sandbox；
6. 为什么 Last-Event-ID 可以完成 SSE 断点续传；
7. 为什么故障注入比只测成功路径更有说服力。

推荐最后阅读：`workers/bootstrap.py`、`docker-compose.yml`、`tests/fault_injection/`、`tests/e2e/`，再按照调用链回看模块 0～11。至此 v0.2 范围冻结，阶段三的 Skill 生命周期需要单独设计和实现。

## 38. 阶段三模块 0：可重现运行契约

### 38.1 模块目标

阶段二已经可以回答“这次 Run 发生了什么”，但还不能严格回答“为什么这次结果比另一次好”。模块 0 先建立可重现实验所需的三份契约：

```text
RunConfigSnapshot：这次运行使用了什么配置
Tool manifest：这次运行实际向模型开放了哪些工具能力
TraceBundle：评测和提炼可以读取哪些运行事实
```

主要文件：

```text
src/evoagent/runtime/run_config.py
src/evoagent/tools/base.py
src/evoagent/tools/registry.py
src/evoagent/trace/bundle.py
src/evoagent/config.py
src/evoagent/runtime/persistent_runner.py
tests/unit/test_run_config_and_manifest.py
```

本模块不会生成 Skill，也不会执行评测。它先把以后做对照实验所需的“尺子”固定下来。

### 38.2 为什么阶段二的 `config_hash` 还不够

阶段二的配置哈希主要用于判断快照能否安全恢复。阶段三需要比较两次独立 Run，如果下列任意条件不同，结果差异就不一定来自 Skill：

- 模型或 Provider 不同；
- 系统提示词不同；
- 工具参数 Schema 或工具实现不同；
- 权限策略不同；
- 最大循环数、Token 预算或超时不同；
- 检索参数不同；
- 代码版本不同。

例如，实验组既加入 Skill，又把模型从 A 换成 B。即使实验组更好，也无法证明是 Skill 带来的提升。这就是“控制变量”的工程版本。

### 38.3 `RunMode` 表示什么

一次 Run 有三种 Skill 使用方式：

| 模式 | 普通任务能否使用 | 含义 |
|---|---|---|
| `baseline` | 可以 | 明确不加载 Skill，作为基线 |
| `retrieval` | 可以，也是默认值 | 根据任务目标检索已经发布的 Skill |
| `pinned_skill` | 不可以直接创建 | 固定使用某个版本，只服务于后续配对评测 |

`TaskService.create_task()` 会拒绝普通 API 创建 `pinned_skill` Run。否则用户可以绕过评测协调器，制造一条看似是正式实验、实际上没有配对条件的记录。

### 38.4 `RunConfigSnapshot` 保存什么

`RunConfigSnapshot` 是冻结的 Pydantic 模型，`extra="forbid"` 会拒绝未知字段。它保存的是影响运行语义、但不包含密码的配置：

| 分类 | 代表字段 | 作用 |
|---|---|---|
| 模型 | `provider`、`model`、`temperature` | 固定模型行为来源 |
| Prompt | `system_prompt_hash` | 检测基础系统提示词是否变化 |
| 工具与策略 | `tool_manifest_hash`、`policy_hash` | 固定能力和权限边界 |
| 预算 | `max_iterations`、`max_total_tokens` | 保证两组资源上限相同 |
| 超时 | 三种 `*_timeout_seconds` | 保证失败条件相同 |
| 代码 | `code_version` | 标识实现版本 |
| Skill | `run_mode` 和三个 Skill 字段 | 保存唯一实验变量和实际注入内容 |

这里保存 Prompt、工具和策略的哈希而不是正文，是为了既能比较身份，又避免把敏感配置复制到每条 Run 中。

三个 Skill 字段必须一起出现：版本 ID 表示“选中了谁”，内容哈希表示“它的定义是什么”，上下文哈希表示“最终注入了什么组合”。`baseline` 模式则禁止出现这些字段。

### 38.5 规范化 JSON 与内容哈希

普通 JSON 对象的键顺序和空格可以不同：

```json
{"model":"demo","provider":"mock"}
{"provider": "mock", "model": "demo"}
```

它们语义相同，但原始字符串不同。代码在计算 SHA-256 前会：

```text
Pydantic 模型
→ 转成 JSON 可表示的 dict
→ 键名排序
→ 去掉无意义空格
→ UTF-8 编码
→ SHA-256
→ sha256:<64 位十六进制>
```

因此哈希代表结构化内容，而不是某一次偶然的序列化格式。注意：哈希用于身份和完整性检查，不是加密，也不能保护被哈希的低熵秘密。

### 38.6 `comparable_with()` 为什么要先抹去 Skill 字段

配对实验本来就要求一组不用 Skill，另一组固定使用 Skill。如果直接比较完整配置哈希，两组必然不同。`canonical_dict(comparison=True)` 会把以下字段统一成占位值：

```text
run_mode
skill_version_id
skill_content_hash
skill_context_hash
```

然后再比较其余配置哈希。相等表示“除 Skill 变量以外，已记录的条件一致”，不代表两个模型输出一定相同。

### 38.7 工具清单为什么必须包含实现版本

`ToolRegistry.manifest()` 不只保存工具名称，还保存：

```text
名称 + 描述 + 参数 JSON Schema + 风险等级
+ 是否有副作用 + 是否允许并发 + implementation_version
```

如果 `calculator` 名称不变，但实现修复了计算规则，只记录名称就会把两种不同能力误认为相同。因此每个工具都有 `implementation_version`；工具逻辑或关键语义变化时必须主动升级版本。

清单按工具名称排序，再计算哈希，所以注册顺序不会让实验配置产生无意义变化。

### 38.8 `TraceBundle` 是什么

阶段二的 Trace 面向排障，内容较丰富。阶段三不能让评测器和提炼器随意读取全部数据库对象，因此增加 `TraceBundle` 作为稳定 DTO：

```text
任务目标、最终回答、Run 状态
+ Turn 摘要
+ ToolCall 与 ToolEffect
+ Artifact 元数据
+ 验证结果
+ RunConfigSnapshot
```

它明确不保存模型的隐藏思维过程。`TraceBundleService` 通过 `TraceService` 取得运行投影，再从 Task 和 Run 补齐目标与配置。这使 Validator、清洗器和候选生成器面对同一种输入结构，而不是直接依赖 ORM。

### 38.9 配置在运行链路中的落点

`PersistentAgentRunner` 在真正调用模型前完成以下工作：

```text
加载 Task 和 Run
→ 选择 Skill（或明确无命中）
→ 计算 Skill 上下文
→ 构造 RunConfigSnapshot
→ 写入 runs.config_snapshot 和 runs.config_hash
→ 才开始 AgentLoop
```

如果 Run 已经保存过配置，而恢复时重新计算出的哈希不同，代码会拒绝继续。这样新发布的 Skill、工具变化或配置变化不会悄悄污染旧 Run。

### 38.10 阶段三配置项怎样分组

`Settings` 在模块 0 先加入整阶段会使用的配置。模块 0～6 完成时其中一部分还是预留项；模块 7～12 已正式消费评测租约、重复次数、数据集根目录和前端静态目录等配置：

| 配置 | 默认值 | 当前用途 |
|---|---:|---|
| `code_version` | `0.3.0.dev0` | 写入可重现配置 |
| `skill_schema_version` | `1` | DSL Validator 接受的版本 |
| `skill_max_steps` | `20` | 限制候选步骤数量 |
| `skill_max_sources` | `10` | 限制一次提炼来源数量 |
| `skill_min_sources` | `2` | 已校验与 max 的关系，但提炼服务尚未执行此下限 |
| `skill_retrieval_top_k` | `1` | 普通 Run 最多注入几个 Skill |
| `skill_retrieval_min_score` | `0.1` | BM25 最低命中分数 |
| `skill_max_effective_risk` | `R1` | DSL 与检索的风险上限 |
| `skill_allowed_tools` | 五个低风险工具 | 提炼和运行兼容性白名单 |
| `eval_dataset_root` | `./evals/datasets` | 为后续文件数据集加载预留 |
| `skill_extractor_model` | `None` | 未配置时不启用真实模型提炼 |
| `eval_repeats`、`eval_poll_seconds`、`eval_lease_seconds` | 见配置文件 | 为模块 7 的评测协调器预留 |

交叉校验会拒绝重复工具、`shell`、高于 R1 的风险，以及 `skill_min_sources > skill_max_sources`。配置对象允许先声明后续字段，但手册会明确区分“配置已存在”和“消费该配置的服务已实现”。

### 38.11 本模块边界、测试与阅读顺序

本模块的直接测试已经证明：配置哈希稳定、Skill 字段组合会被校验、只有 Skill 不同的配置可以比较、工具实现版本变化会改变清单哈希。工具名称的确定性排序由阶段一 `ToolRegistry` 行为保证；若要把它作为独立实验契约，还可以补一条“不同注册顺序得到相同 manifest_hash”的专门测试。

在模块 0 的完成边界内，它还没有证明实验效果；模块 7、8 现已用配对运行和指标汇总补齐这部分能力。

推荐阅读顺序：

```text
1. runtime/run_config.py
2. tools/base.py 中的工具元数据
3. tools/registry.py 的 manifest()
4. trace/bundle.py
5. runtime/persistent_runner.py 中配置落库的位置
6. tests/unit/test_run_config_and_manifest.py
```

读完后应能解释：为什么“同一个模型名称”仍不足以证明实验可比较，以及为什么 Skill ID、正文哈希和上下文哈希要同时保存。

## 39. 阶段三模块 1：声明式 Skill DSL

### 39.1 模块目标

模块 1 定义“什么样的数据才配叫 Skill”。EvoAgent 当前采用指导式 SOP：Skill 向模型提供一套经过验证的操作参考，但不是 Python 插件，也不会自己执行步骤。

主要文件：

```text
src/evoagent/skills/schema.py
src/evoagent/skills/validation.py
src/evoagent/skills/canonical.py
tests/unit/test_skill_schema.py
```

选择声明式 DSL 的核心原因是：可以校验、计算哈希、保存版本、展示差异和限制权限。如果让提炼模型直接生成 Python，后续安全审查和稳定评测都会困难得多。

### 39.2 `SkillDefinition` 的整体结构

一份 Skill 定义包含：

| 字段 | 回答的问题 |
|---|---|
| `schema_version` | 应该用哪一版解析规则 |
| `name`、`description` | Skill 是谁、解决什么问题 |
| `triggers` | 哪类用户目标可能匹配它 |
| `inputs` | 执行建议需要哪些输入 |
| `preconditions` | 最多允许哪些工具和风险 |
| `steps` | 建议按什么依赖顺序工作 |
| `success_criteria` | 怎样判断任务做完 |
| `validators` | 应该使用哪些可信验证器 |

`ContractModel` 会拒绝未声明字段，因此模型不能偷偷在 JSON 中塞入 `python_code`、`admin_override` 一类扩展能力。

### 39.3 输入定义为什么刻意简单

`InputDefinition` 当前支持字符串、整数、数字、布尔和一维数组。数组必须声明 `item_type`，非数组不能声明它，并且不支持嵌套数组。

这不是因为复杂 JSON 无法实现，而是 v0.3 先把模板引用和验证规则控制在容易理解的范围内。复杂嵌套结构以后需要同时设计路径表达式、类型传播和错误提示，不能只加一个枚举值。

### 39.4 `preconditions` 不是授权结果

`SkillPreconditions` 声明 Skill 预计使用哪些工具，以及最高有效风险。它表达的是候选定义的能力上限，不是“已经获得用户授权”。

运行时仍然经过阶段二的：

```text
ToolExecutor
→ PermissionPolicy
→ Approval
→ ToolEffect
→ Sandbox
```

因此 Skill 最多缩小可用能力，不能绕过现有安全链路扩大能力。阶段三当前把声明式 Skill 的最高风险限制在 R1，并且无条件排除 `shell`。

### 39.5 两种步骤和判别联合

步骤根据 `action` 分成两类：

```text
ToolStep(action="tool")：建议调用一个受控工具
ModelStep(action="model")：建议模型完成一段推理或整理
```

Pydantic 使用 `action` 作为 discriminator。读取 JSON 时，不需要猜一个对象属于哪种步骤；`tool` 和 `model` 会进入各自明确的数据模型。

当前步骤只是被 `SkillContextRenderer` 渲染成参考文本，并没有一个 DSL 解释器逐条强制执行。这是必须记住的能力边界。

### 39.6 变量引用规则

DSL 只接受三类引用：

```text
${inputs.topic}                 读取已声明输入
${steps.fetch.output}          读取祖先步骤输出
${steps.fetch.output.some_key} 读取祖先输出的子字段
${item}                        只允许在 foreach 步骤中使用
```

一个步骤不能读取尚未依赖的步骤，也不能只因为某一步“写在前面”就读取它。合法性由依赖图决定，而不是由 JSON 数组顺序决定。

### 39.7 `foreach` 的限制

`foreach` 必须是一个完整引用，不能混在长字符串里。若它指向输入，该输入必须声明为数组。只有启用 `foreach` 的工具步骤才能使用 `${item}`。

这些规则避免出现“模板看起来合法，但运行时不知道循环对象是什么”的模糊状态。当前只是验证和渲染该信息，尚未实现自动循环解释器。

### 39.8 为什么需要 DAG 检查

多个步骤可以形成有向无环图 DAG：

```text
collect ──→ summarize ──→ write
   └───────────────────→ verify
```

`depends_on` 给出边。Validator 会检查：

- 步骤 ID 唯一；
- 依赖项存在且不重复；
- 步骤不能依赖自己；
- 引用的步骤必须是当前步骤的直接或间接祖先；
- 整张图不能有环。

拓扑排序每次选择当前没有依赖的步骤，并按 ID 排序保证结果稳定。如果还有节点却找不到可执行节点，说明图中存在环。

### 39.9 Pydantic 校验与领域校验为什么分开

Pydantic 适合判断单个字段的形状，例如名称格式、长度和枚举值；`SkillDefinitionValidator` 负责需要外部上下文或跨字段推理的规则：

```text
工具是否真的注册
工具是否同时位于系统白名单和 Skill 白名单
风险是否超限
依赖图是否有环
引用是否指向祖先
是否出现绝对路径或危险指令
Schema 版本是否受支持
```

这样数据模型保持纯粹，Validator 可以由部署配置和当前 ToolRegistry 组装。

### 39.10 静态安全检查能防什么

Validator 会拒绝宿主机绝对路径、`shell`、超风险工具和明显的“忽略系统指令”文本。其作用是让高风险内容在入库前尽早失败。

它不是完整的自然语言安全证明。正则无法理解所有 Prompt Injection，因此来源清洗、运行时权限策略和 Sandbox 仍然必须保留。这是多层防线，不是某一层包办安全。

### 39.11 规范化与内容身份

`canonical_json()` 固定键顺序和分隔符；`content_hash()` 对结果计算 SHA-256。相同 SkillDefinition 无论字典原始键顺序如何，都得到相同内容哈希。

后续 `SkillVersionRecord` 保存定义和哈希，数据库监听器禁止原地修改正文。修改 Skill 的正确方式是创建新版本。

### 39.12 本模块边界、测试与阅读顺序

测试重点包括：严格字段、数组输入规则、重复 ID、无效依赖、环、非祖先引用、绝对路径、危险指令、工具白名单和确定性哈希。

推荐阅读顺序：

```text
1. skills/schema.py
2. tests/unit/test_skill_schema.py 中的合法示例
3. skills/validation.py 的 validate()
4. _validate_tool_step() 与 _validate_text()
5. _ancestors() 与 _topological_order()
6. skills/canonical.py
```

读完后应能区分“字段结构合法”和“领域语义安全”，并能说明为什么一份 Skill 是数据而不是代码。

## 40. 阶段三模块 2：持久化模型、状态机与 ArtifactWrite

### 40.1 模块目标

模块 2 把阶段三对象从“内存里的数据结构”变成“数据库中的可审计事实”，同时补上 Skill 需要的不可覆盖 Artifact 写入能力。

主要文件：

```text
src/evoagent/db/models.py
src/evoagent/db/repositories/skills.py
src/evoagent/db/repositories/evals.py
src/evoagent/db/unit_of_work.py
src/evoagent/skills/lifecycle.py
src/evoagent/evals/lifecycle.py
src/evoagent/tools/builtin/artifact_write.py
src/evoagent/trace/artifacts.py
migrations/versions/20260908_0003_stage_three_foundations.py
```

### 40.2 为什么 `Skill` 和 `SkillVersion` 必须分开

`SkillRecord` 是长期存在的聚合根，保存名称、slug、启用状态和当前活动版本；`SkillVersionRecord` 保存某一版不可变定义。

```text
Skill：report_research
├─ Version 1：RETIRED
├─ Version 2：ACTIVE  ← active_version_id
└─ Version 3：DRAFT
```

如果把正文直接放在 Skill 表中，每次修改都会覆盖旧内容，历史 Run 就无法证明当时加载的是哪一版。分开后，聚合状态可以变化，但每一版正文、哈希和来源身份保持不变。

### 40.3 阶段三表按职责怎样分组

| 分组 | 表 | 作用 |
|---|---|---|
| Skill 目录 | `skills`、`skill_versions` | 保存聚合与不可变版本 |
| 来源血缘 | `skill_sources` | 连接版本、来源 Run、EvalRun 和冻结 Artifact |
| 运行选择 | `run_skill_selections` | 保存某个 Run 为什么选中哪些版本 |
| 评测 | `eval_datasets`、`eval_cases`、`eval_experiments`、`eval_runs` | 保存数据、实验和结果 |
| 发布审计 | `promotion_decisions`、`skill_events` | 保存人工决定和事件序列 |

模块 2 建表时不等于所有业务服务已经完成；当时模块 0～6 只使用其中一部分。现在模块 7～10 已为配对实验、质量门禁和发布表补齐正式服务。

### 40.4 关键数据库约束

数据库负责最后一道一致性保护：

- `(skill_id, version)` 唯一，不能有两个“第 2 版”；
- `content_hash` 必须是 `sha256:` 加 64 位摘要；
- `(dataset name, version)` 唯一；
- 同一数据集中的 `case_key` 唯一；
- 同一 Run 中选择结果的 `rank` 唯一且从 1 开始；
- EvalRun 的 `baseline` 必须没有 Skill，`pinned_skill` 必须有版本 ID；
- 同一 Skill 事件的 sequence 唯一。

应用校验可以提供友好错误，数据库约束负责在并发和程序缺陷下拒绝非法事实。

### 40.5 ORM 不可变监听器

SQLAlchemy 的 `before_update` 监听器保护三类历史：

```text
SkillVersion：只允许改变 lifecycle_status
EvalDataset：只允许改变 status
EvalCase：任何原地更新都拒绝
SkillSource：任何原地更新都拒绝
```

这表示“修改内容”必须创建新版本，不能拿到 ORM 对象后直接覆盖 JSON。需要注意，监听器是应用 ORM 层保护；数据库权限和审计仍应作为生产部署的额外边界。

### 40.6 Skill 生命周期状态机

Skill 聚合状态：

```text
ENABLED ⇄ DISABLED → DEPRECATED
ENABLED ───────────→ DEPRECATED
```

SkillVersion 状态：

```text
DRAFT → EVALUATING → REVIEW_REQUIRED → ACTIVE ⇄ RETIRED
  └────────→ REJECTED       └────────→ REJECTED
```

状态机是纯 Python 函数，先判断转换是否合法。`DRAFT → ACTIVE` 被明确拒绝，因为未经评测和人工复核的版本不能直接发布。当前只落地状态规则，推进这些状态的完整服务在模块 9、10 实现。

### 40.7 Eval 生命周期状态机

数据集只允许：

```text
DRAFT → FROZEN → RETIRED
```

实验只允许从 QUEUED 进入 RUNNING 或取消，再从 RUNNING 进入终态。终态不能重新启动；重新评测应该创建新实验，保留旧结果。

把状态机从 ORM 拆出来，可以用单元测试验证全部合法和非法路径，也避免 API、Worker 各自写出一套不同规则。

### 40.8 迁移为什么要可升级也可回退

`20260908_0003_stage_three_foundations.py` 先给 `runs` 增加运行模式和 Skill 固定版本字段，再按外键依赖顺序创建阶段三表。`downgrade()` 反向删除外键、索引、表和新增列。

迁移测试验证：

```text
旧版本 → upgrade 到 head → ORM 与 Schema 一致
head → downgrade → 阶段三对象被正确移除
```

这比只用 `Base.metadata.create_all()` 更接近真实项目升级过程。

### 40.9 `artifact_write` 和 `file_write` 的区别

| 工具 | 主要用途 | 是否允许覆盖 |
|---|---|---|
| `file_write` | 修改当前 Run 工作文件 | 在审批后可以覆盖 |
| `artifact_write` | 产出可追踪结果或训练资料 | 不允许覆盖 |

`ArtifactWriteTool` 绑定当前 `run_id`，模型不能通过参数选择其他 Run。它把内容交给 `ArtifactService.create_unique()`；本地存储用排他创建保证并发时同名文件只有一个成功，然后在数据库登记 URI、内容类型、大小、哈希和 `created_by`。

当前实现没有为“文件已经创建、数据库登记却失败”增加补偿删除，因此极端失败下可能留下数据库不知道的孤儿文件。它不会覆盖已有 Artifact，但生产化前还需要增加补偿清理或孤儿扫描；这是当前实现边界。

### 40.10 本模块边界、测试与阅读顺序

模块 2 的完成边界只有 Schema、Repository 入口、状态规则、迁移和窄写入工具；发布事务与 Eval Worker 后来分别由模块 10 和模块 7 实现。

推荐阅读顺序：

```text
1. db/models.py 的阶段三 Record
2. skills/lifecycle.py 与 evals/lifecycle.py
3. db/repositories/skills.py 与 evals.py
4. migration 0003
5. trace/artifacts.py 的 create_unique()
6. tools/builtin/artifact_write.py
7. migration、状态机与 Artifact 测试
```

读完后应能解释：为什么 ACTIVE 是版本状态、ENABLED 是聚合状态，以及为什么“Artifact 不可覆盖”需要文件系统和数据库共同参与。

## 41. 阶段三模块 3：数据集与确定性 Validator

### 41.1 模块目标

模块 3 给“回答得好”一个可执行定义。它建立版本化数据集、公开输入与私有断言的隔离、可信 Validator 注册表，以及把已有 Run 绑定到训练 Case 的来源验证流程。

主要文件：

```text
src/evoagent/evals/schema.py
src/evoagent/evals/datasets.py
src/evoagent/evals/validators/base.py
src/evoagent/evals/validators/builtin.py
src/evoagent/evals/service.py
src/evoagent/trace/bundle.py
tests/unit/test_phase_three_services.py
tests/integration/test_phase_three_pipeline.py
```

### 41.2 为什么 `Run=COMPLETED` 不代表任务正确

AgentLoop 正常返回只说明程序没有以错误结束。例如用户要求“生成含三个章节和两个来源的报告”，模型只回答一句“完成了”，Run 仍可能是 COMPLETED。

阶段三把两个结论分开：

```text
runs.status = completed
    说明：执行流程正常结束

eval_runs.passed = true
    说明：该输出通过此 Case 指定的验证规则
```

只有后者才有资格成为 Skill 提炼来源或进入质量门禁。

### 41.3 数据集和 Case 的数据契约

`EvalDatasetDefinition` 包含名称、整数版本和至少一个 Case。Case 包含：

| 字段 | 用途 |
|---|---|
| `case_key` | 数据集内稳定标识 |
| `task_family` | 对相似任务分组 |
| `split` | TRAIN 或 HOLDOUT |
| `public_input` | 可以交给任务执行端的输入 |
| `private_validators` | 只供评测系统读取的断言 |
| `risk_profile` | 对风险条件的结构化描述 |

同一数据集中的 `case_key` 必须唯一，否则结果无法稳定定位到测试题。

### 41.4 TRAIN 与 HOLDOUT 为什么必须分开

TRAIN Case 可以用于：

- 验证已有优秀 Run；
- 清洗和冻结来源；
- 提炼候选 Skill。

HOLDOUT Case 只能用于后续发布门禁，不能进入生成器。若生成器提前看过答案要求，它可能只记住评测题，而不是真正学会可迁移的流程，这就是数据泄漏。

当前 `SourceValidationService` 和 `TraceEligibilityChecker` 都会检查 split，而不是只依赖调用方“自觉不传 HOLDOUT”。后续 EvalCoordinator 还需要继续保持这条边界。

### 41.5 导入、幂等与冻结

数据集导入链路：

```text
EvalDatasetDefinition
→ 规范化 JSON 并计算 content_hash
→ 查询同名同版本
   ├─ 哈希相同：返回已有记录
   └─ 哈希不同：抛出 DatasetConflictError
→ 同一事务写 Dataset 和全部 Case
→ 状态为 DRAFT
```

冻结时只允许 `DRAFT → FROZEN`。如果题目或验证器需要修改，必须把数据集版本加一并重新导入，不能改写旧实验使用过的 Case。

### 41.6 Validator 为什么是可信代码而不是数据集中的 Python

`ValidatorSpec` 只包含名称、版本和参数。真正的函数由 `ValidatorRegistry` 在应用代码中注册：

```text
Case: {name: "contains_sections", version: "1", parameters: {...}}
→ ValidatorRegistry 查找 contains_sections@1
→ 调用项目内受信任函数
→ 返回统一 ValidationResult
```

这样数据集作者只能选择已经审查过的能力，不能在 JSON 中附带任意 Python 并让 Worker 执行。

### 41.7 `ValidationResult` 保存哪些证据

每个验证器统一返回：

```text
validator + version + passed
+ evidence + failure_reason + duration_ms
```

版本很重要：同名验证器的算法变更会改变评测含义。证据让失败可解释，例如缺少哪些章节、实际引用数量或出现哪些禁止工具，而不是只留下一个布尔值。

### 41.8 当前内置的确定性 Validator

| Validator | 检查内容 |
|---|---|
| `run_completed` | Run 是否完成 |
| `contains_sections` | 最终回答是否包含指定章节文本 |
| `minimum_citations` | URL 引用数量是否达到下限 |
| `covers_items` | 指定内容项是否都出现 |
| `artifact_exists` | 是否生成指定类型 Artifact |
| `tool_policy` | 是否使用了允许集合外的工具 |
| `no_unknown_effects` | 是否存在 UNKNOWN 副作用 |
| `max_tool_calls` | 工具调用数量是否超限 |

这些检查可重复、便于调试，但不等于完整语义评价。例如字符串出现不必然代表论述正确；后续可以增加新的可信确定性 Validator，再谨慎考虑 LLM Judge。

### 41.9 `SourceValidationService` 的完整链路

```text
输入 run_id + eval_case_id
→ 若该 Run 已有 EvalRun，则验证绑定关系并幂等返回
→ 检查 Run 必须 COMPLETED
→ 检查 Case 必须是 TRAIN
→ 检查 Dataset 必须 FROZEN
→ 构造 TraceBundle
→ 逐个运行私有 Validator
→ 所有结果均通过才 passed=true
→ 同一事务写 SOURCE_VALIDATION Experiment 和 EvalRun
```

Validator 执行放在数据库事务之外，避免验证耗时期间长期占用事务。最终结果重新开启短事务落库。

### 41.10 当前简化与后续演进

当前来源验证会为一个 Run 创建一个已完成的 SOURCE_VALIDATION Experiment，适合建立提炼来源。它还不是模块 7 的可恢复批量 EvalCoordinator，也不会自动运行 baseline/pinned 配对任务。

引用数量目前按 `http://` 和 `https://` 字符串计数，是确定性启蒙实现，不代表已经校验来源真实性。文档必须如实区分“格式检查”和“事实核验”。

### 41.11 本模块测试与阅读顺序

推荐阅读顺序：

```text
1. evals/schema.py
2. evals/lifecycle.py
3. evals/datasets.py
4. evals/validators/base.py
5. evals/validators/builtin.py
6. evals/service.py
7. tests/integration/test_phase_three_pipeline.py 的来源验证部分
```

读完后应能解释：为什么验证器参数属于数据、验证器实现属于可信代码，以及为什么 HOLDOUT 不能成为候选生成资料。

## 42. 阶段三模块 4：来源资格、清洗与冻结

### 42.1 模块目标

模块 4 回答一个关键问题：哪些成功 Run 可以安全地交给候选生成器学习？它把过程拆成三道门：

```text
资格检查 → 内容清洗 → 不可变冻结
```

主要文件：

```text
src/evoagent/skills/provenance.py
src/evoagent/skills/sanitizer.py
src/evoagent/trace/bundle.py
src/evoagent/trace/artifacts.py
tests/unit/test_phase_three_services.py
tests/integration/test_phase_three_pipeline.py
```

不能因为一个 Run “效果不错”就直接把整条 Trace 发给模型。Trace 可能包含私有断言、凭据、宿主机路径、未知副作用或恶意网页指令。

### 42.2 资格检查的硬条件

`TraceEligibilityChecker` 要求同时满足：

1. EvalRun 对应 TRAIN Case；
2. `eval_run.passed` 为真；
3. 原 Run 状态为 COMPLETED；
4. Run 已保存 `RunConfigSnapshot`；
5. `config_hash` 与快照重新计算的哈希一致；
6. 来源 Run 没有使用任何 Skill；
7. 没有 DENIED ToolCall；
8. 工具均在提炼白名单内且风险不超过 R1；
9. 没有 UNKNOWN ToolEffect；
10. 没有 PENDING Approval。

任一条件失败都会抛出 `IneligibleSkillSourceError`。这是硬门，不会通过“降低一点分数”放行。

### 42.3 为什么 Skill 辅助 Run 不能继续当来源

如果一个 Skill 生成的结果又被用来提炼自己，系统会形成反馈环：

```text
Skill A → 产生 Run → Run 又提炼成 Skill A 的新版本
```

错误步骤和偏见可能被不断放大，也无法区分新版本来自原始成功经验还是旧 Skill。当前版本因此只接受 `skill_version_id is None` 的 baseline 来源。

### 42.4 为什么 UNKNOWN Effect 和 PENDING Approval 会阻断

UNKNOWN 表示系统不知道外部副作用是否真正发生；PENDING 表示执行仍在等待人的决定。这类 Trace 不是一条完整、可信的成功路径。

如果生成器从中总结 SOP，可能学到“调用工具后不确认结果也算完成”或“绕过审批继续执行”。所以资格检查必须读取结构化 ToolEffect 和 Approval，而不是只看最终文本。

### 42.5 `TraceSanitizer` 怎样递归处理数据

Sanitizer 从根对象开始递归访问字典、列表和字符串，为每个发现记录 JSON 风格路径，例如 `$.tool_calls[0].arguments.api_key`。

处理结果分两类：

| 类型 | 行为 | 例子 |
|---|---|---|
| 非阻断发现 | 安全转换或删除后继续 | 临时 ID、时间戳、隐藏推理字段、Workspace 路径 |
| 阻断发现 | 整次清洗失败 | 敏感键、私钥、疑似凭据、外部绝对路径、Prompt Injection |

返回结果同时包含清洗后的 payload、内容哈希和 findings，便于以后审计清洗发生了什么。

### 42.6 为什么临时字段要删除

`run_id`、`task_id`、时间戳和端口等字段通常不表达可迁移的解决方法。保留它们会：

- 让同一逻辑的两条 Trace 得到不同哈希；
- 诱导生成器写死临时标识；
- 泄露内部环境细节。

删除后，冻结哈希更接近“可复用过程”的身份，而不是“某次运行的全部偶然值”。

### 42.7 Workspace 路径与外部绝对路径

位于配置 Workspace 下的绝对路径会被改写：

```text
D:\EvoAgent\workspace\report.md
→ ${workspace}/report.md
```

这样 Skill 不依赖某台机器的目录。无法证明属于 Workspace 的 Windows 或 Unix 绝对路径会阻断，因为它既可能泄露宿主机信息，也可能让候选学习越界访问。

### 42.8 凭据与 Prompt Injection 检测

Sanitizer 检查敏感键名、私钥头、Bearer/`sk-` 样式值和高熵短字符串。高熵检测排除了哈希、URL 和正常含空格文本，降低中文说明被误判为 Secret 的概率。

它还检测明显的“忽略之前/系统指令”文本。来源内容被视为不可信数据；发现此类指令时直接阻断，不把它原样交给候选模型。

正则检测存在边界，不能识别所有泄漏和注入表达。因此模型生成器仍有独立系统提示词，生成结果还必须通过 DSL Validator。

### 42.9 冻结流程和完整性复查

`ProvenanceService.freeze()` 的完整过程：

```text
检查 EvalRun 资格
→ 查找是否已有该 EvalRun 的冻结 Artifact
   ├─ 有：读取文件并复算哈希，匹配后幂等返回
   └─ 无：继续
→ 构造只含最小验证结论的 TraceBundle
→ 读取来源 Artifact 并逐个复核内容哈希
→ TraceSanitizer 清洗
→ 规范化 JSON
→ create_unique() 写不可覆盖 Artifact
→ 返回 FrozenSkillSource
```

只把 Validator 名称、版本和通过状态传给生成器，不传 `private_validators` 的参数或详细证据，减少答案泄漏。

### 42.10 冻结 Artifact 的身份

冻结文件使用专用媒体类型：

```text
application/vnd.evoagent.skill-source+json
```

元数据保存 `eval_run_id` 和 `immutable=true`。`SkillSourceRecord` 后续还会把 SkillVersion、来源 Run、EvalRun、Artifact 与来源哈希连接起来，从候选版本可以反查它学自哪些事实。

### 42.11 本模块边界、测试与阅读顺序

当前清洗器提供保守的结构和正则防线，不是数据防泄漏产品；在真实生产环境还应配合租户隔离、密钥扫描、数据分类和访问审计。

推荐阅读顺序：

```text
1. skills/provenance.py 的 TraceEligibilityChecker
2. skills/sanitizer.py 的 Finding 数据结构
3. TraceSanitizer.sanitize() 与 _visit()
4. ProvenanceService.freeze()
5. trace/artifacts.py
6. tests 中的秘密、路径、注入和篡改案例
```

读完后应能画出“合格 EvalRun 到 FrozenSkillSource”的链路，并说明清洗与冻结为什么不能合并成一次简单的 JSON 导出。

## 43. 阶段三模块 5：候选生成与 DRAFT 提炼

### 43.1 模块目标

模块 5 把一组已经冻结的优秀训练 Trace 提炼成一个候选 SkillVersion。关键词是“候选”：无论模型输出看起来多好，它只能进入 DRAFT，不能直接 ACTIVE。

主要文件：

```text
src/evoagent/skills/extraction.py
src/evoagent/skills/provenance.py
src/evoagent/skills/validation.py
src/evoagent/db/repositories/skills.py
src/evoagent/api/routes/skills.py
src/evoagent/api/app.py
tests/unit/test_phase_three_services.py
tests/integration/test_phase_three_pipeline.py
```

### 43.2 `CandidateGenerator` 为什么是协议

业务服务只依赖：

```python
generate(sources) -> SkillDefinition
```

它不关心候选来自 Mock、真实模型还是未来的规则算法。当前提供：

- `MockCandidateGenerator`：直接返回预设定义或异常，保证测试确定；
- `ModelCandidateGenerator`：调用 ModelProvider，让模型从清洗资料生成 JSON。

这种分离让“生成是否成功”和“生成结果是否合法”成为两个独立问题。

### 43.3 模型生成器实际看到了什么

`ModelCandidateGenerator` 只接收 `FrozenSkillSource.payload`，不会查询原始 Trace、数据库私有 Validator 或 HOLDOUT Case。请求由两条消息构成：

```text
system：资料是不可信数据，只返回符合 Schema 的 JSON
user：sanitized_training_traces 的 JSON 数组
```

它只在 Provider 发出 COMPLETED 且包含 response 时读取最终内容。没有完成响应或 JSON 无法解析为 `SkillDefinition`，都会转换成 `CandidateGenerationError`。

### 43.4 为什么模型返回 Pydantic 对象后还要语义验证

Pydantic 成功只说明字段形状正确。模型仍可能：

- 引用未注册工具；
- 声明 `shell`；
- 形成循环依赖；
- 使用越权风险；
- 写入绝对路径；
- 引用非祖先步骤。

因此生成结果必须再次经过 `SkillDefinitionValidator`。模型是候选提出者，不是规则裁判。

### 43.5 提炼服务的前置检查

`SkillExtractionService.extract()` 要求来源 ID：

- 至少一个；
- 不能重复；
- 不能超过 `skill_max_sources`。

随后逐个调用 `ProvenanceService.freeze()`。任何来源不合格、被篡改或清洗失败，整次提炼停止。当前配置同时有 `skill_min_sources`，但现有 Service 只强制非空与最大值；最小来源数尚未在该服务中执行，这是当前实现边界，不能在手册中声称已完成。

### 43.6 两种哈希分别表示什么

生成完成后计算：

```text
definition_hash = hash(规范化 SkillDefinition)

extraction_key = hash({
  source_hashes: 排序后的全部来源哈希,
  definition_hash: definition_hash
})
```

`definition_hash` 回答“候选正文是什么”；`extraction_key` 回答“这份正文是否由同一组来源提炼而来”。来源先排序，所以调用方传入 ID 的顺序不会制造重复版本。

### 43.7 幂等返回与版本号

事务开始后先按 `extraction_key` 查询。若已存在，返回原版本并令 `created=false`，不会再创建来源或事件。

若不存在：

```text
按 definition.name 查 Skill
├─ 不存在：创建 ENABLED Skill 聚合
└─ 已存在：复用聚合
→ max(version) + 1 得到新版本号
→ 创建 DRAFT SkillVersion
```

数据库中的 `extraction_key` 唯一约束是并发情况下的最终防线。当前 `max(version)+1` 在高并发创建同一 Skill 新版本时可能由唯一约束拒绝其中一个请求，后续可用行锁和重试增强；它不会静默写出重复版本号。

### 43.8 为什么所有记录必须在一个事务中

一次成功提炼要同时写入：

```text
Skill（需要时）
+ SkillVersion(DRAFT)
+ 每条 SkillSource
+ skill.version_drafted 事件
```

UnitOfWork 最后才 commit。若写第二条来源时失败，版本和事件一起回滚，不会出现“候选存在但不知道来源”的半成品。

`SkillEvent.sequence` 在当前事务中根据已有最大序号加一。完整并发事件追加服务属于后续生命周期模块；数据库唯一约束仍会阻止重复 sequence。

### 43.9 为什么只能创建 DRAFT

候选定义虽然通过了静态 Validator，但还没有证明：

- 在 HOLDOUT 上比 baseline 更好；
- 没有安全回退；
- 资源消耗在预算内；
- 人工评审接受它。

因此代码把 `lifecycle_status` 固定为 DRAFT。生成器没有接收目标状态的参数，HTTP API 也不能要求“直接发布”。

### 43.10 HTTP 入口怎样组装服务

`POST /api/v1/skills/extract` 接收来源 EvalRun ID，路由从 `app.state` 获取 CandidateGenerator 和 Skill ToolRegistry，再根据 Settings 组装 ArtifactService、ProvenanceService、Validator 和 ExtractionService。

```text
没有配置 generator 或 registry → 503
来源或候选不合法           → 422
成功创建候选               → 201 + DRAFT 信息
```

生成器默认不自动启用，避免一启动 API 就意外调用真实模型或产生费用。当前接口面向最小开发闭环，尚未提供列表、详情和发布 API。

### 43.11 日志为什么不直接打印原始错误内容

生成或验证失败时只记录异常类型，而不是把原始模型输出和来源 Trace 全部写入日志。这样可以减少清洗资料或可疑模型输出通过日志再次泄漏的风险。调用方仍收到经过业务边界处理的错误。

### 43.12 本模块测试与阅读顺序

集成测试验证同一来源和定义重复提炼只产生一个版本与一组来源，候选初始状态为 DRAFT，并能在后续人工模拟为 ACTIVE 后被检索。

推荐阅读顺序：

```text
1. extraction.py 的 CandidateGenerator 与两个实现
2. SkillExtractionService.extract()
3. db/repositories/skills.py
4. api/routes/skills.py
5. api/app.py 中 app.state 的可选依赖
6. tests/integration/test_phase_three_pipeline.py
```

读完后应能解释：为什么“模型返回合法 JSON”仍不能发布，以及 `content_hash` 与 `extraction_key` 为什么不能合成一个概念。

## 44. 阶段三模块 6：BM25 检索与 Skill 上下文

### 44.1 模块目标

模块 6 把已发布 Skill 接入普通 Run：先从数据库取出符合条件的活动版本，再按任务目标检索，保存选择证据，渲染为受控上下文，最后由 `PersistentAgentRunner` 注入 AgentLoop。

主要文件：

```text
src/evoagent/skills/retrieval.py
src/evoagent/skills/rendering.py
src/evoagent/core/context.py
src/evoagent/runtime/persistent_runner.py
src/evoagent/tasks/service.py
src/evoagent/db/repositories/skills.py
tests/unit/test_phase_three_services.py
tests/integration/test_phase_three_pipeline.py
```

### 44.2 为什么先用 BM25 而不是向量数据库

当前阶段 Skill 数量很少，BM25 有三点适合学习项目：

- 不需要外部数据库和 Embedding API；
- 同样输入得到确定性结果；
- 可以展示命中的词和分数，便于解释选择原因。

它的弱点是主要依赖词面重合，对同义表达和复杂语义不如向量检索。以后替换检索器时，选择留痕、过滤、安全和配置冻结仍应保留。

### 44.3 中英文如何分词

`tokenize()` 对英文、数字和下划线按词提取并转小写；对连续中文同时加入单字和二元词片。例如：

```text
"研究报告"
→ 研、究、报、告、研究、究报、报告
```

单字提高召回，二元词片保留一部分局部语义。这不是专业中文分词器，但没有外部依赖，规则透明且输出稳定。

### 44.4 `SkillDocument` 检索哪些字段

每个文档携带 Skill ID、版本 ID、严格解析后的定义和内容哈希。参与 BM25 的文本只有：

```text
name + description + triggers
```

步骤正文没有进入检索，避免很长的操作细节淹没真正的触发信息。也因此编写 Skill 时，description 和 triggers 不是装饰字段，而是检索质量的重要输入。

### 44.5 BM25 分数在直觉上表示什么

BM25 会提高“查询词在当前文档中出现”的得分，同时考虑：

- 一个词在全部文档中越少见，区分能力越强；
- 同一词出现次数增加会提升分数，但收益逐渐饱和；
- 很长文档会做长度归一化，避免只因词多就占优势。

代码使用 `k1=1.5`、`b=0.75`。所有结果按分数降序；同分时按版本 UUID 字符串排序，保证测试和重放稳定。

### 44.6 检索前为什么必须过滤

`SkillRepository.active_versions()` 只返回：

```text
Skill.status == ENABLED
并且 SkillVersion.lifecycle_status == ACTIVE
并且 Skill.active_version_id 指向该版本
```

`SkillRetrievalService._compatible()` 再检查定义声明的工具全部存在、没有 `shell`、风险不超过运行配置上限。只有生命周期和当前运行环境都兼容的版本才能参加打分。

过滤必须发生在 Top-K 之前。若先把禁用或越权 Skill 排进前 K，再过滤，可能把真正合法的候选挤掉。

### 44.7 三种运行模式怎样选择

```text
baseline
→ 不检索，记录 skill.none_selected

retrieval
→ 查询 ENABLED + ACTIVE + compatible 版本
→ BM25 打分
→ 过滤 minimum_score
→ 取 top_k

pinned_skill
→ 只加载 Run 指定版本
→ 固定匹配分数 1.0，标记 matched term 为 pinned
```

普通 Task API 只允许前两种；固定版本模式只留给模块 7 已实现的 EvalCoordinator。pinned 分支按 ID 读取候选版本，配对创建与评测资格由内部评测服务约束。

### 44.8 选择为什么要落库

每个命中写入 `run_skill_selections`：

```text
run_id + skill_version_id + mode
+ rank + score + query_terms
```

同时追加 `skill.selected` 或 `skill.none_selected` RunEvent。前者保存当前事实，后者进入统一时间线。以后查看 Trace 时，不只能看到模型收到了一段文本，还能回答“选中了哪一版、排第几、为什么匹配”。

### 44.9 恢复时为什么不能重新检索

假设 Run 第一次执行时 Skill A 是 ACTIVE，执行中 Worker 崩溃，随后管理员发布了 Skill B。如果恢复时重新检索，消息上下文会悄悄改变，旧 Snapshot 和新配置不再表示同一次运行。

所以 `select()` 的优先级是：

```text
已有 RunSkillSelectionRecord → 恢复原选择
没有选择，但已有 config_snapshot → 恢复“首次无命中”
两者都没有 → 才执行第一次选择
```

“无命中”也是一种必须冻结的决定，否则新发布版本会在重试时突然进入旧 Run。

### 44.10 `SkillContextRenderer` 输出什么

Renderer 把结构化定义变成模型容易阅读的参考文本：

```text
安全边界声明
Skill 名称与用途
按序号列出的工具/模型建议步骤
成功标准
```

文本开头明确说明它不能扩大工具权限、绕过审批、改变安全规则或要求泄密。工具步骤只展示受控工具名和参数模板；最终是否执行仍由模型决定，实际调用仍经过阶段二安全链路。

### 44.11 ContextBuilder 的注入位置

有 Skill 时，`ContextBuilder` 把渲染文本附加在基础 system 消息中的专用边界后：

```text
基础系统规则
→ “受控 Skill 参考边界”
→ SkillContextRenderer 输出
→ 外部上下文 user 消息
→ 最终任务 user 消息
```

无命中时 `skill_context=None`，消息结构与阶段二保持一致。空白 Skill 上下文会被拒绝，避免出现形式上“使用了 Skill”但内容为空的配置。

### 44.12 `PersistentAgentRunner` 怎样把各模块串起来

当前完整接入链路是：

```text
Worker 把 JobLease 交给 PersistentAgentRunner
→ 加载有租约所有权的 Task/Run
→ SkillRetrievalService.select()
→ SkillContextRenderer.render()
→ 计算 skill_context_hash
→ 构造并持久化 RunConfigSnapshot
→ 加载 Snapshot
   ├─ 首次运行：ContextBuilder 注入 Skill
   └─ 恢复运行：直接复用 Snapshot.messages
→ AgentLoop 按原有安全链路执行
```

`skill_context_hash` 当前根据所有命中版本的内容哈希列表计算；`RunConfigSnapshot` 的单个 `skill_version_id` 和 `skill_content_hash` 记录第一名。默认 `top_k=1`，因此两者一致。若未来真正启用多个 Skill，需要把配置契约扩展为完整版本列表，不能只依赖第一名字段。

### 44.13 当前边界和容易误解的地方

- BM25 命中只表示文本相关，不表示 Skill 一定有效；
- 模块 6 最初的集成测试用人工 fixture 准备 ACTIVE；模块 9、10 现在已经实现真实 QualityGate 与发布事务；
- Skill 是指导式 SOP，不会逐步解释执行或强制模型遵循 DAG；
- Top-K 配置允许最多 3，但当前默认 1，多 Skill 冲突处理尚未专门实现；
- 检索不改变 ToolRegistry，Skill 声明的工具仍必须由运行环境真实提供。

### 44.14 本模块测试与阅读顺序

当前测试直接覆盖中英文分词与 BM25 同分稳定排序，并通过集成链路证明 ENABLED/ACTIVE 版本可以被检索。最低分过滤、禁用版本过滤、选择落库、重复选择恢复，以及 Skill 上下文进入 PersistentAgentRunner 的路径已经实现，但还缺少逐项独立回归测试；后续继续阶段三时应补齐这些测试。

推荐阅读顺序：

```text
1. skills/retrieval.py 的 tokenize() 和 BM25Retriever
2. SkillRepository.active_versions()
3. SkillRetrievalService.select()
4. skills/rendering.py
5. core/context.py
6. runtime/persistent_runner.py 的 handle() 前半段
7. tests/integration/test_phase_three_pipeline.py
```

读完后应能解释：为什么“无命中”也要保存、为什么先过滤再 Top-K，以及 Skill 上下文为何不能代替 PermissionPolicy。

---

## 45. 阶段三模块 0～6 的总调用链

前七个模块不是七组互不相关的类，而是从成功经验到候选和运行时复用的前半段生命周期；后半段见第 48～54 章：

```text
普通 baseline Run 完成
→ FROZEN TRAIN Case 的 Validator 验证通过
→ TraceEligibilityChecker 检查来源资格
→ TraceSanitizer 删除临时信息并阻断风险内容
→ ProvenanceService 冻结不可变来源 Artifact
→ CandidateGenerator 提出 SkillDefinition
→ SkillDefinitionValidator 再做静态与安全校验
→ UnitOfWork 写入 DRAFT SkillVersion、来源和事件
→ 配对评测、硬门禁与人工审批（模块 7～10，已实现）
→ ENABLED Skill 的 ACTIVE 版本参与 BM25
→ 选择结果和配置快照落库
→ SkillContextRenderer 注入 PersistentAgentRunner
→ AgentLoop 仍通过阶段二权限与 Sandbox 执行
```

当前集成测试为了验证后半段检索，会在测试代码中手动把 DRAFT 版本设为 ACTIVE。那是测试夹具，不代表生产发布流程已经完成。

## 46. 阶段三模块 0～6 测试地图

| 测试文件 | 主要证明 |
|---|---|
| `test_run_config_and_manifest.py` | 配置、工具清单和比较哈希稳定 |
| `test_skill_schema.py` | DSL 字段、引用、DAG 和安全规则 |
| `test_phase_three_services.py` | 数据集契约、清洗、BM25、生命周期和模型生成器 |
| `test_phase_three_pipeline.py` | 来源验证→冻结→DRAFT→选择的数据库主链路 |
| `test_migrations.py` | 阶段三 Schema 可以升级和回退 |
| `test_postgres_persistence.py` | PostgreSQL 特有并发与迁移语义 |

学习时不要只看测试数量。应先判断某条测试证明的是纯函数、SQLite 下的应用契约，还是 PostgreSQL 的真实约束与并发行为。

## 47. 阶段三模块 0～6 的学习验收

读完源码和本章后，建议不看手册独立回答：

1. `RunConfigSnapshot` 为什么不能保存 API Key，又为什么必须保存工具实现版本的哈希？
2. Skill 为什么选择声明式 DSL，而不是让模型生成 Python？
3. Pydantic 校验与 `SkillDefinitionValidator` 各负责什么？
4. `SkillRecord` 和 `SkillVersionRecord` 为什么不能合并？
5. 为什么 Run 完成和 Eval 通过是两个结论？
6. TRAIN 与 HOLDOUT 如何阻止数据泄漏？
7. 来源资格、清洗和冻结分别解决什么风险？
8. 为什么候选生成器永远只能得到 DRAFT？
9. `content_hash` 与 `extraction_key` 有什么区别？
10. 为什么恢复时要复用旧选择，连“无命中”也不能重新判断？
11. BM25 的可解释性体现在哪里？
12. 为什么 Skill 注入以后仍然不能绕过 Policy、Approval、ToolEffect 和 Sandbox？

如果这些问题能结合具体文件和调用链回答清楚，就已经掌握阶段三模块 0～6，可以继续阅读下面的评测、门禁和发布闭环。

---

## 48. 阶段三模块 7：可恢复 EvalCoordinator 与配对运行

### 48.1 本模块解决什么问题

模块 6 只能证明 ACTIVE Skill 可以被检索，不能证明一个 DRAFT 候选比“不使用 Skill”更好。模块 7 新增实验协调层，对每条 HOLDOUT Case 创建一组受控对照：一边是 BASELINE，一边是固定候选版本的 PINNED_SKILL。

这里最重要的文件是：

- `evals/coordinator.py`：创建实验、领取租约、释放 Pair、验证终态 Run；
- `evals/worker.py`：独立 Eval Worker 进程入口；
- `db/repositories/evals.py`：读取 Experiment、Case 和 EvalRun；
- `tests/integration/test_eval_lifecycle.py`：验证 Pair、接管、取消和收尾。

### 48.2 为什么不是直接循环调用 Agent

一个实验可能有很多 Case 和 repeat，运行中 API、Worker 或机器都可能重启。如果只在 Python 内存中写 `for case in cases`，进程一崩溃就不知道哪些样本已经算过，重新运行还可能把同一结果重复计入。

所以 `create_experiment()` 先在一个事务中创建全部占位：

```text
HOLDOUT Case × repeats × {baseline, pinned_skill}
→ SessionRecord
→ TaskRecord
→ RunRecord
→ EvalRunRecord
```

数据库唯一约束和 `metrics.state` 共同表达“这个样本是否已经完成”，恢复时继续消费既有记录，不重新发明一批实验行。

### 48.3 Pair 为什么要一前一后运行

同一 Pair 中只有第一条 Task 进入 QUEUED，第二条先处于 PAUSED。第一条结束并完成 Validator 后，Coordinator 才写入 `eval.pair.released` 并释放第二条。repeat 为偶数时 baseline 先跑，奇数时 pinned 先跑。

这样做有两个目的：

1. 避免两边同时争抢 CPU、网络或 Provider 限流配额；
2. 避免所有实验永远 baseline 先跑造成固定顺序偏差。

它不能消除真实服务随时间波动，但比无控制并发更容易解释。

### 48.4 为什么有两种 Worker

普通 `evoagent-worker` 仍负责真正执行 Task，它不知道这条任务是不是实验样本。`evoagent-eval-worker` 只负责实验编排：

```text
claim_next() 领取 Experiment lease
→ 普通 Worker 执行已 QUEUED 的 Run
→ run_once() 找到终态但未验证的 EvalRun
→ TraceBundleService 构造完整事实
→ ValidatorRegistry 执行 Case 的私有规则
→ MetricsCollector 保存单次原始指标
→ 释放同 Pair 的第二条任务
→ 全部完成后 Experiment = COMPLETED
```

把两种职责分开后，评测不会复制 Agent Runtime，也不会让协调器直接绕过阶段二的权限、审批、Artifact 和恢复链路。

### 48.5 实验租约怎样恢复

Experiment 保存 `lease_owner`、`heartbeat_at` 和 `lease_expires_at`。正常 Eval Worker 周期性 heartbeat；进程消失后，另一 Worker 只能领取已经过期的 RUNNING 实验。旧 owner 再心跳或收尾会得到 `EvalLeaseLostError`。

SQLite 可能丢掉时间戳时区，因此 `_is_expired()` 只在测试兼容层统一 naive/aware 时间；正式并发和 `FOR UPDATE SKIP LOCKED` 语义仍以 PostgreSQL 为准。

### 48.6 可比较性检查

Pair 两边结束后，Coordinator 读取各自 `RunConfigSnapshot` 并调用 `comparable_with()`。Provider、模型、基础 Prompt、工具清单、代码版本等公共字段必须一致；Skill 版本和 Skill 上下文哈希是实验变量，可以不同。

缺少快照或公共配置不同不会强行报一个改善数字，而是把 `EvalRun.comparable` 设为 false。后续 Metrics 和 QualityGate 会继续传播这个事实。

### 48.7 取消语义

尚未执行的 QUEUED/PAUSED/WAITING_USER/RETRYING/RECOVERING 任务可以直接进入 CANCELLED；正在运行或等待工具的任务只能设置 `cancel_requested`，由持有普通 Job lease 的 Worker 安全提交终态。Experiment 会清除自己的租约，但不能越权替另一个 Worker 假装执行已经停止。

### 48.8 本模块边界与阅读顺序

模块 7 只保存每次运行是否通过和原始指标，不决定候选能否发布。推荐阅读：

```text
1. EvalExperimentConfig 与 EvalLease
2. create_experiment() 和 _ensure_pairs()
3. claim_next() / heartbeat()
4. run_once() / _validate_and_collect()
5. _release_second_runs() / _mark_pair_comparability()
6. evals/worker.py
7. test_eval_lease_takeover_and_cancel()
```

读完应能解释：为什么恢复不能重建 Pair，为什么第二条先暂停，以及 Eval Worker 为什么不能代替普通 Worker。

---

## 49. 阶段三模块 8：MetricsCollector 与不可变评测报告

### 49.1 原始运行和评测指标的区别

Run、Turn、ToolCall、Approval、ToolEffect 与 SkillSelection 是运行事实；`RunMetrics` 是从这些事实得到的投影。`MetricsCollector.collect_run()` 读取数据库并汇总：终态、Validator 结果、Token、工具次数与状态分布、延迟、审批、权限拒绝、UNKNOWN Effect、恢复事件和检索选择。

投影可以重新计算，事实不能为了得到好看的指标而修改。

### 49.2 `None` 为什么比 `0` 正确

某些 Provider 不返回 Usage。此时 `total_tokens=None` 表示“不知道”，而零表示“确定没有消耗”。把未知写成零会让候选看起来获得虚假的 100% Token 改善。

延迟也只有在首尾时间事实都存在时才计算；缺失值会一直传播到 Pair 和报告，不在中途偷偷填默认数。

### 49.3 什么时候能比较效率

`PairMetrics.efficiency_comparable` 同时要求：

```text
公共运行配置可比较
AND baseline Validator 通过
AND skill Validator 通过
AND 两边 Usage 已知
```

只有满足这些条件才计算 Token delta。Tool Calls 和延迟至少要求配置可比较且两边通过。这样可以避免“Skill 很快失败了，所以比成功完成的 baseline 省很多 Token”这种幸存者偏差。

### 49.4 失败样本为什么仍然保留

效率比较可以排除失败对，但正确性统计不能。报告保存每一组 Pair 的 baseline/skill 结果与 `success_delta`，并同时计算总体成功率和每个 `task_family` 的成功率。

例如总体成功率持平，但架构分析族从 100% 降到 50%，任务族统计会保留这条负面证据，模块 9 也会拒绝它。

### 49.5 均值、中位数和明细各自负责什么

- 均值能反映总体资源变化，但容易受极端样本影响；
- 中位数描述一个更典型的样本；
- Pair 明细让人能回到具体 Case 判断异常原因。

当前数据规模较小，所以报告只做描述统计，不输出 p-value，也不宣称统计显著性。

### 49.6 报告怎样冻结

`EvaluationReportService.freeze()` 先用规范 JSON 构造报告并计算 SHA-256，再通过 `ArtifactService.create_unique()` 写入不可覆盖的 Artifact，最后把 Artifact ID 和报告哈希绑定到 Experiment，同时把哈希写入 SkillVersion。

再次读取已冻结报告时，会重新读取 Artifact、解析 DTO 并复算哈希；内容被改动就拒绝。数据库模型的更新监听器还禁止把已经设置的报告引用或哈希替换成另一个值。

当前 Artifact 表要求 `run_id`，因此实验报告暂时挂在该 Experiment 的第一条 Eval Run 上。这是存储模型的已知边界，不代表报告只属于那条 Run；Experiment ID 才是报告的业务归属。

### 49.7 本模块阅读顺序

```text
1. RunMetrics
2. MetricsCollector.collect_run()
3. PairMetrics 与 TaskFamilyMetrics
4. EvaluationReportService.build()
5. freeze()
6. test_correctness_regression_cannot_be_offset_by_lower_token_use()
```

读完应能区分“不通过”“不可比较”和“Usage 未知”三种不同事实。

---

## 50. 阶段三模块 9：QualityGate 与评测生命周期

### 50.1 为什么不用一个总分

假设正确性下降 10 分，Token 节省 30 分，如果简单加权，候选可能仍得到高分。但一个更便宜却给错答案的 Skill 不应发布。因此 `QualityGate` 把规则分层，所有非 efficiency 层都是硬门禁，效率只提供人工阅读信息。

### 50.2 GateReport 的结构

每条 `GateCheck` 保存：规则名、层级、是否通过、阈值、实际值和证据。`GateReport` 保存候选版本、实验、总结果和全部检查，再用规范 JSON 计算内容哈希。

当前硬检查包括：

- SkillDefinition 仍通过静态语义校验；
- 当前定义内容哈希仍与版本记录一致；
- 独立来源数量达到配置下限；
- 每个来源仍对应 passed TRAIN EvalRun；
- Skill 声明的 Validator 仍被可信注册表支持；
- 所有 Pair 都可比较且数量大于零；
- 总体成功率不低于 baseline；
- 每个任务族成功率都不低于 baseline；
- 没有更多 permission denied 或 UNKNOWN Effect。

### 50.3 状态怎样推进

`SkillEvaluationService` 管理自动评测部分的状态：

```text
DRAFT
→ start()
→ EVALUATING + QUEUED Experiment
→ EvalCoordinator 完成全部 Pair
→ finalize() 冻结 EvaluationReport
→ QualityGate.evaluate()
   ├─ 硬门禁全通过 → REVIEW_REQUIRED
   └─ 任一硬门禁失败 → REJECTED
```

`REVIEW_REQUIRED` 只表示“值得人工看”，不是 ACTIVE。自动系统到这里必须停下。

### 50.4 为什么 finalize 是显式写操作

HTTP 层使用 `POST /eval-experiments/{id}/finalize` 冻结报告并推进版本。`GET .../report` 只读取已经存在或可重算的报告，不改变状态。这样刷新浏览器不会成为一次隐蔽审批动作，也符合 GET 的只读语义。

### 50.5 报告不可替换

第一次 finalize 写入 `gate_report` 和 `gate_report_hash`；再次调用会校验并返回同一结果。若 JSON 与哈希不一致，服务直接报错。ORM 更新监听器阻止已经绑定的报告被替换，这使发布时校验的不是一份可以事后编辑的普通 JSON。

### 50.6 失败分类

实验基础设施失败和候选质量失败不应该混为一谈。前者让 Experiment 进入 FAILED 或保留可恢复状态；后者是在完整证据上得到 `GateReport.passed=false`，SkillVersion 进入 REJECTED。前者回答“没有可靠结论”，后者回答“有可靠的拒绝结论”。

### 50.7 本模块阅读顺序

```text
1. GateCheck / GateReport
2. QualityGate.evaluate()
3. SkillEvaluationService.start()
4. SkillEvaluationService.finalize()
5. api/routes/evals.py 的 finalize 路由
6. 正确性回退集成测试
```

---

## 51. 阶段三模块 10：人工审批、发布、禁用与回滚 API

### 51.1 聚合状态和版本状态

`SkillRecord` 表示一个长期 Skill 聚合，保存 ENABLED/DISABLED/DEPRECATED、`active_version_id` 和 `lock_version`。`SkillVersionRecord` 表示不可混淆的具体定义，拥有 DRAFT、EVALUATING、REVIEW_REQUIRED、ACTIVE、RETIRED、REJECTED 状态。

因此“禁用 Skill”和“退休一个版本”不是同一操作：禁用保留当前发布指针，方便恢复；发布新版则把旧 ACTIVE 变为 RETIRED。

### 51.2 查询和 diff

`SkillService` 提供列表、详情、版本详情、来源、门禁报告和结构化 diff。diff 递归比较 JSON：字典按字段路径输出 added/removed/changed，列表作为一个有顺序的整体比较。

管理页面消费结构化差异，不需要从一大段文本中猜哪项改变。

### 51.3 发布事务

批准流程在同一个 UnitOfWork 中完成：

```text
读取候选版本
→ 锁定 Skill 聚合并检查 expected_lock_version
→ 验证候选处于 REVIEW_REQUIRED
→ 查找 GateReport 并复算 hash
→ 退休旧 ACTIVE（如果存在）
→ 候选变为 ACTIVE
→ 更新 active_version_id
→ lock_version + 1
→ 写 PromotionDecision
→ 写 SkillEvent
→ 一次 commit
```

其中任何一步失败都会整体回滚，不会出现两个 ACTIVE 或“版本已激活但聚合指针仍指向旧版”的半状态。

### 51.4 expected_lock_version 是什么

页面读取 Skill 时得到 `lock_version=3`，提交审批时必须带回 3。如果另一个操作者先完成修改，数据库已经是 4，旧页面会得到稳定的 `version_conflict`，必须刷新后重新判断。

PostgreSQL 行锁负责串行化，额外的 compare-and-set 条件让 SQLite 测试也能发现陈旧请求。它不是用户身份认证，只是并发写保护。

### 51.5 为什么批准还要重新验 GateReport

状态是快速索引，报告是证据。`_require_gate()` 同时检查：报告存在、passed=true、version ID 一致、experiment ID 一致、内容哈希一致。只手工把数据库状态改成 REVIEW_REQUIRED 不能绕过证据校验。

### 51.6 回滚不是改一个外键

目标版本必须：属于同一 Skill、处于 RETIRED、有已接受的 GateReport 哈希，并且它的 Schema、工具和风险在当前运行环境仍能通过 `SkillDefinitionValidator`。随后当前 ACTIVE 退休、目标重新 ACTIVE、聚合指针和审计记录在一个事务内改变。

旧版本曾经安全不代表永远兼容。例如工具已经从当前目录移除时，回滚必须拒绝。

### 51.7 HTTP 层和当前安全边界

`api/routes/skills.py` 暴露提炼、列表、详情、建版本、diff、approve/reject、enable/disable/deprecate 和 rollback。`SkillServiceError` 由应用统一转换为稳定错误码。

当前 `reviewer` 只是请求中的本地审计字符串，没有登录、签名、RBAC 或多租户语义。管理接口只适合本地学习或受信网络，不能直接暴露公网。

### 51.8 本模块阅读顺序

```text
1. skills/lifecycle.py
2. SkillService.get_skill() / get_version()
3. _locked_skill()
4. _require_gate()
5. _publish()
6. rollback()
7. api/routes/skills.py
8. test_skill_management_api.py
```

---

## 52. 阶段三模块 11：React + TypeScript 管理页面

### 52.1 前端的职责

`frontend/` 是一个独立 Vite + React + TypeScript 工程，只实现 Skill 和 Eval 管理面。它不重复实现聊天、Session 或完整 Trace Viewer。

主要目录：

```text
frontend/src/api/types.ts       后端 DTO 的 TypeScript 形状
frontend/src/api/client.ts      统一请求和错误转换
frontend/src/components/        Loading、Empty、Error、Badge
frontend/src/pages/SkillsPage   Skill 列表、状态切换与详情
frontend/src/pages/ReviewPage   版本 diff、Gate、审批与回滚
frontend/src/pages/EvalPage     报告汇总和 Pair 明细
frontend/e2e/                   浏览器关键流程
```

### 52.2 为什么先定义类型化 API Client

页面如果到处直接 `fetch()` 并猜字段，很容易把 `gate_report` 的空值、稳定错误结构或 lock_version 忘掉。`request<T>()` 统一处理 HTTP 错误，页面 Hook 只关心 `loading/data/error` 三种基本状态。

TypeScript 类型不是运行时安全替代品，真正的事实仍由 FastAPI/Pydantic 和数据库产生；它负责在前端开发阶段尽早暴露字段使用错误。

### 52.3 页面怎样处理服务端事实

页面不会乐观地假装发布成功。用户点击 approve、disable 或 rollback 后，先等待后端事务成功，再重新加载 Skill/Version。若 lock_version 已过期，页面展示后端冲突，不覆盖另一位操作者的结果。

Eval 页面展示 Pair 数、可比较数、双方成功率、安全回退、Token/Tool delta 和哈希。尚未 finalize 时门禁明确显示“尚未执行”，不会把空报告解释为通过。

### 52.4 静态托管与容器构建

前端执行 `pnpm build` 生成 `frontend/dist`。FastAPI 在该目录存在时将它挂载到 `/ui`。Dockerfile 使用 Node 构建阶段生成静态文件，再把 dist 复制进 Python 运行镜像；最终运行容器不需要 Node。

`EVOAGENT_FRONTEND_DIST` 可以改变静态目录。开发时 Vite 代理 `/api` 到本地 FastAPI，生产时浏览器请求同源 API。

### 52.5 测试分层

- `tsc --noEmit`：DTO 和组件类型；
- Vitest + Testing Library：loading、empty、error 等组件状态；
- `vite build`：生产构建是否成立；
- Playwright：报告查看、批准发布、回滚三个浏览器流程。

Playwright 当前用路由 Mock 固定 API 返回，证明浏览器交互和请求契约；后端生命周期由 Python 集成测试证明。二者组合不等于生产环境认证测试。

### 52.6 阅读顺序

```text
1. api/types.ts
2. api/client.ts
3. components/State.tsx
4. SkillsPage.tsx
5. ReviewPage.tsx
6. EvalPage.tsx
7. App.tsx
8. e2e/lifecycle.spec.ts
```

---

## 53. 阶段三模块 12：真实数据集、完整 Demo 与阶段冻结

### 53.1 数据集内容

`evals/datasets/open-source-research-v1.json` 包含 24 条公开输入：12 TRAIN、12 HOLDOUT，覆盖 architecture_review 和 evidence_report 两个任务族。每条 Case 使用确定性 Validator，不把私有答案文本放进公开输入。

数据集加载器只允许读取配置根目录内的 `.json` 文件，并在解析后使用严格 Pydantic 契约。路径穿越、根目录外绝对路径、非 JSON 文件和非法字段都会拒绝。

### 53.2 数据集 CLI

```powershell
.\.venv\Scripts\evoagent-eval-dataset open-source-research-v1.json --freeze
```

命令读取配置、导入定义、可选冻结并输出 ID、版本、内容哈希、状态和 Case 数。它不会把 private_validators 打印或经列表 API 暴露。

### 53.3 完整生命周期 Demo

`evals/demo.py` 用确定性数据跑三个场景：

1. 合格候选通过 HOLDOUT 门禁并人工发布；
2. 正确性回退候选即使 Token 更少也被拒绝；
3. 合格新版发布后，人工回滚到仍兼容且曾通过门禁的旧版。

最后创建普通 RETRIEVAL Task，证明只检索到回滚后的 ACTIVE 版本。输出中的 UUID 会变化，但门禁结论、Case 数和最终版本关系稳定。

这个 Demo 用模拟完成记录验证生命周期，不把模拟 Token 当成真实模型效果。简历中的效果数字必须来自真实 Provider、冻结配置和保存的报告。

### 53.4 Compose 和 CI 闭环

Compose 现在包含 PostgreSQL、migration、API、普通 Worker 和 Eval Worker。CI 除 Python 3.12/3.13、PostgreSQL 和镜像构建外，还执行前端类型检查、组件测试、生产构建和 Playwright。

新增 migration 0004 为 Experiment 和 SkillVersion 增加报告 Artifact/hash 引用，并维持 0003→0004 的可升级、可回退链。

### 53.5 ADR 和安全说明

`ADR-006-配对评测硬门禁与人工发布.md` 固定为什么选择配对、硬门禁、人工发布和受约束回滚。`阶段三-演示与安全边界.md` 给出复现步骤并明确没有认证、RBAC、生产强沙箱、统计显著性和自动发布。

### 53.6 v0.3 冻结意味着什么

冻结不是说项目永远不改，而是阶段三的必做目标已经闭环。此时停止顺手加入长期记忆、MCP、多 Agent 或自动发布，把已有链路讲清楚、测稳定，比继续堆功能更重要。

---

## 54. 阶段三完整调用链

```text
普通 BASELINE Task 成功
→ TRAIN Case 的私有 Validator 验证通过
→ 来源资格检查、Trace 清洗与不可变 Artifact 冻结
→ CandidateGenerator 只生成 DRAFT SkillVersion
→ FROZEN HOLDOUT Dataset 启动 Experiment
→ EvalCoordinator 创建 case × repeat × 两种 mode 的持久化 Pair
→ 普通 Worker 分别执行 BASELINE 与内部 PINNED_SKILL
→ Eval Worker 验证终态、保存指标、检查配置可比较性
→ EvaluationReport 汇总总体、任务族和 Pair 明细
→ 显式 finalize 冻结报告并运行 QualityGate
   ├─ 硬门禁失败 → REJECTED
   └─ 硬门禁通过 → REVIEW_REQUIRED
→ 人工查看来源、diff、报告和 GateReport
   ├─ reject → REJECTED
   └─ approve → 原子发布 ACTIVE，旧版 RETIRED
→ 普通 RETRIEVAL Task 只召回 ENABLED + ACTIVE 版本
→ 如需回滚，再验证旧版门禁证据和当前兼容性后原子切换
```

整条链路有三条不可混淆的信任边界：生成器只能写 DRAFT；QualityGate 只能决定是否进入人工评审；只有人工发布服务能修改 ACTIVE 指针。

---

## 55. 阶段三最终测试地图

| 测试或检查 | 主要证明 |
|---|---|
| `test_run_config_and_manifest.py` | 配置指纹、工具清单和可比较性 |
| `test_skill_schema.py` | DSL、DAG、引用、风险和安全规则 |
| `test_phase_three_services.py` | 数据集加载边界、清洗、Validator、BM25 与生成器 |
| `test_phase_three_pipeline.py` | 来源验证→冻结→DRAFT→检索前半链路 |
| `test_eval_lifecycle.py` | Pair、租约接管、指标、硬门禁、发布、冲突和回滚 |
| `test_skill_management_api.py` | 管理 API、稳定错误和私有 Validator 不泄漏 |
| `test_migrations.py` | Alembic 升级、回退和 Schema 链 |
| `test_postgres_persistence.py` | PostgreSQL 租约与并发事实 |
| `frontend/src/App.test.tsx` | 页面 loading/empty/error 状态 |
| `frontend/e2e/lifecycle.spec.ts` | 报告、批准和回滚浏览器流程 |
| `evoagent-phase3-demo` | 全新数据库上的确定性生命周期 smoke |

Windows 普通账户没有创建符号链接权限时，WorkspaceGuard 的符号链接测试会跳过；没有配置 PostgreSQL 测试 URL 时，PostgreSQL 专用测试也会跳过。跳过不等于通过，Linux/PostgreSQL CI 才负责这两类平台事实。

---

## 56. 阶段三学习验收

读完阶段三源码后，建议不看手册独立回答：

1. 为什么同一 Case 要同时跑 BASELINE 与 PINNED_SKILL？
2. 为什么每对顺序交替，且第二条先 PAUSED？
3. Experiment lease 和普通 Job lease 分别保护什么？
4. 为什么恢复时不能重新创建 EvalRun？
5. `passed=false`、`comparable=false` 与 `total_tokens=None` 分别表示什么？
6. 为什么只有两边都成功才能比较效率？
7. 为什么正确性失败不能由 Token 改善抵消？
8. EvaluationReport Artifact、report hash 和 GateReport hash 怎样互相约束？
9. 为什么 GET report 不执行 finalize？
10. `REVIEW_REQUIRED` 为什么还不是 ACTIVE？
11. 发布事务怎样避免两个 ACTIVE 版本？
12. expected_lock_version 能解决什么，不能解决什么？
13. 回滚为什么仍要重新检查当前工具兼容性？
14. 前端为什么提交成功后重新读取服务端状态？
15. 确定性 Demo 能证明什么，又不能证明什么？
16. 为什么管理 API 不能直接暴露到公网？

如果能结合具体 Record、Service、API、事件和测试回答这些问题，就已经掌握 EvoAgent v0.3 的完整可验证 Skill 生命周期。


---

## 57. 阶段四模块 0：v0.3 基线冻结与恢复装配

### 57.1 模块目标

阶段四没有直接从 Memory 或 MCP 开始。第一步是重新验证 v0.3 基线，并修复一个会影响后续所有长任务的运行缺口：任务租约过期后，Worker 能把状态改成 `RECOVERING`，但原来的主循环没有继续调用恢复决策服务。

这意味着“代码库里存在 `RecoveryService`”和“进程重启后系统会自动恢复”是两件不同的事。模块 0 的核心任务就是让真实进程路径完整闭环：

```text
RUNNING / WAITING_TOOL 租约过期
→ 标记 RECOVERING
→ 执行 RecoveryService 决策
→ QUEUED / WAITING_USER / FAILED
→ 替代 Worker 继续处理或等待人工确认
```

主要文件：

```text
src/evoagent/workers/main.py
src/evoagent/tasks/lease.py
src/evoagent/runtime/recovery.py
tests/e2e/test_worker_process_recovery.py
tests/e2e/worker_crash_fixture.py
```

### 57.2 `run_once()` 为什么要先恢复再领取

当前 `JobWorker.run_once()` 每轮按固定顺序执行：

```text
promote_due_retries()
→ recover_expired()
→ recover_pending()
→ claim_next()
→ 同时运行 Handler 和租约心跳
→ finalize()
```

四个前置步骤各自解决不同状态：

| 步骤 | 输入状态 | 输出状态或结果 |
|---|---|---|
| `promote_due_retries()` | 到期的 RETRYING | QUEUED |
| `recover_expired()` | 租约已过期的 RUNNING / WAITING_TOOL | RECOVERING |
| `recover_pending()` | RECOVERING | QUEUED、WAITING_USER 或 FAILED |
| `claim_next()` | 可执行的 QUEUED | RUNNING + JobLease |

如果跳过第三步，任务会永久停在 RECOVERING，因为 `claim_next()` 只领取 QUEUED。把恢复扫描放在领取之前，也保证替代 Worker 能先处理已有故障，再领取恢复后的同一任务。

### 57.3 `recover_expired()` 和 `recover()` 为什么分开

`recover_expired()` 只确认一个数据库事实：原执行者的租约已经过期。它短暂锁定 Task 和最新 Run，清空 owner、过期时间和心跳，写入 `recovery.started`，然后提交。

`RecoveryService.recover()` 才解释运行证据：

```text
是否存在未决 ToolEffect？
├─ 是 → 全部转 UNKNOWN，创建或重置审批，进入 WAITING_USER
└─ 否 → 读取最新 Snapshot
         ├─ 无 Snapshot → RESTART，重新排队
         ├─ 兼容 Snapshot → RESUME，重新排队
         └─ 不兼容 Snapshot → FAILED
```

分开有两个好处。第一，过期扫描只做短事务，不在持有批量任务锁时解析快照。第二，RECOVERING 本身成为可重试的持久状态；进程在两步之间再次退出，下一进程仍能通过 `recover_pending()` 接手。

### 57.4 恢复为什么必须检查所有未决副作用

旧实现只读取第一条 PREPARED、EXECUTING 或 UNKNOWN 效果。当前实现读取同一 Run 的全部未决效果，并逐条：

1. 统一改为 UNKNOWN；
2. 找到关联 ToolCall；
3. 创建待确认 Approval，或把旧决定重置为 PENDING；
4. 把全部 approval ID 写入 `recovery.decided`。

只处理第一条会产生危险窗口：用户确认第一条后任务重新排队，第二条未知效果可能未经确认便重新进入执行路径。因此 `ApprovalService.decide()` 会检查同一 Task 是否还存在其他 PENDING Approval；只有全部处理完，Task 和 Run 才从 WAITING_USER 回到 QUEUED。

旧的 APPROVED 也不能直接复用。它只说明用户曾允许执行某个动作，不能证明崩溃前动作究竟有没有提交。UNKNOWN 的确认必须重新回答：

```text
retry                 已确认没有提交，可以再执行一次
committed:<结果>      已确认已经提交，直接登记结果
reject                不允许继续
```

其中 `retry` 是一次性确认。中间件开始重试时会消费这个响应；如果再次崩溃，系统会重新要求确认，而不是无限复用旧的 retry。

### 57.5 Snapshot 恢复保留什么

Snapshot 只在完整的模型—工具边界保存，因此恢复不会从半段 Provider 流或半次工具调用中继续。兼容快照恢复时，`PersistentAgentRunner`：

```text
load_latest()
→ 取得 LoopState.messages、已完成 iteration、usage 和重复调用状态
→ 写入 recovery.completed
→ AgentLoop 从 completed_iterations + 1 开始
```

快照之后已经写入但不属于合法边界的事件被视为尾部记录，不用于重建消息状态。`recovery.decided` 会记录快照事件序号和被忽略的尾部事件数量，便于审计。

### 57.6 进程级测试和普通集成测试的区别

普通集成测试在同一 Python 进程中创建 Service，容易不小心直接调用 `RecoveryService.recover()`，从而绕过真正需要验证的 Worker 装配。

`test_worker_process_recovery.py` 从 API 创建任务，然后启动独立 Python 子进程运行真实的 `JobWorker` 和 `ConfiguredTaskHandler`。Fixture 只在明确的持久化边界写出 ready 标记，父进程随后强制终止 Worker，等待租约过期，再启动同标签替代进程。

它覆盖三个场景：

| 场景 | 终止点 | 替代进程应观察到的结果 |
|---|---|---|
| readonly | 合法 Snapshot 保存后 | 从 Snapshot 续跑，只读工具不重复产生额外记录 |
| committed | ToolEffect 已 COMMITTED 后 | 复用结果，文件实际只写一次 |
| unknown | 外部写完成、账本提交前 | Effect 进入 UNKNOWN，任务停在 WAITING_USER |

这个测试证明主进程链路已接通，也证明 COMMITTED 与 UNKNOWN 的分界。它不证明 PostgreSQL 行锁语义，也不把本地文件写入提升为通用外部 exactly-once。

### 57.7 基线验证结果与边界

实现前回归为 207 passed、4 skipped。实现后阶段三完整生命周期 Demo 在独立 SQLite 数据库重跑，仍满足：24 个 Case、合格版本通过并发布、退化版本被门禁拒绝、回滚后只检索到目标旧版。

这个 Demo 使用确定性模拟记录，只证明阶段三生命周期没有因阶段四改动回退。它不是实际模型质量证据。本机没有 PostgreSQL 和 Docker 服务，真实模型配对报告也未取得；这些验收在运行说明中保留为待完成，不能用 Mock 结果代替。

### 57.8 推荐阅读顺序

```text
1. workers/main.py 的 run_once()
2. tasks/lease.py 的 recover_expired() 与 recover_pending()
3. runtime/recovery.py 的 recover()
4. runtime/checkpoints.py 的 load_latest()
5. tools/approvals.py 的 decide()
6. tests/e2e/worker_crash_fixture.py
7. tests/e2e/test_worker_process_recovery.py
8. tests/fault_injection/test_unknown_effect_recovery.py
```

---

## 58. 阶段四模块 1：租约代次与统一写入保护

### 58.1 模块目标

阶段二已经在终态提交时检查 lease owner，但运行过程还会持续写事件、快照、ToolEffect、Approval、Skill 选择、配置和 Artifact 元数据。如果 Worker A 失去租约、Worker B 接管，而 A 的网络请求稍后返回，A 仍可能把迟到结果写进数据库。

模块 1 引入 fencing token。它解决的问题不是“怎样发现 Worker 已死”，而是“即使旧 Worker 又活过来，怎样拒绝它的所有迟到写入”。

主要文件：

```text
src/evoagent/tasks/lease_guard.py
src/evoagent/tasks/lease.py
src/evoagent/runtime/persistent_runner.py
src/evoagent/trace/persistent_sink.py
src/evoagent/runtime/checkpoints.py
src/evoagent/tools/effects.py
src/evoagent/trace/artifacts.py
src/evoagent/skills/retrieval.py
migrations/versions/20260919_0005_lease_fencing.py
tests/integration/test_lease_fencing.py
```

### 58.2 Worker 标签、实例身份和 lease epoch

这三个值职责不同：

| 值 | 示例 | 作用 |
|---|---|---|
| 配置标签 | `worker-local` | 供部署者识别 Worker 类型或位置 |
| 实例 owner | `worker-local:<uuid>` | 区分同标签的不同进程启动 |
| lease epoch | `7`、`8` | 区分同一 Task 的前后两次领取 |

`JobWorker` 构造时给配置标签追加一次 UUID，所以同标签重启不会获得相同 owner。`claim_next()` 每次成功领取都会在 Task 行锁内执行：

```text
attempt_count += 1
lease_epoch += 1
lease_owner = 当前实例 owner
lease_expires_at = 数据库当前时间 + lease duration
```

随后返回的 `JobLease` 同时携带 task_id、run_id、owner、expires_at、attempt 和 epoch。

只用 owner 不够，因为部署编排常使用稳定名称；只用 epoch 也不够，因为 owner 仍是审计和定位进程的重要信息。两者同时检查，能清楚表达“哪个实例持有第几代执行权”。

### 58.3 为什么租约判断使用数据库时间

PostgreSQL 路径通过 `clock_timestamp()` 获取数据库当前时间。租约是数据库中的共享事实，如果不同 Worker 使用各自主机时间，时钟漂移可能让一个进程认为租约有效，另一个进程认为已经过期。

这里选择 `clock_timestamp()`，而不是事务开始时固定的 `now()`，是因为租约判断需要得到调用时的实际数据库时间。SQLite 功能测试退回 Python UTC 时间，但这不构成多连接并发保证。

测试为了构造精确的过期边界仍可显式传入 `now`。生产路径不传该参数，统一使用数据库时钟。

### 58.4 `LeaseGuard.check()` 的六项检查

Guard 在调用者提供的 `AsyncSession` 中工作，依次锁定 Task 和 Run。它检查：

1. Task 存在；
2. `lease_owner` 等于 JobLease.owner；
3. `lease_epoch` 等于 JobLease.epoch；
4. 租约到期时间晚于数据库当前时间；
5. Task 仍处于 RUNNING 或 WAITING_TOOL；
6. Run 存在、属于该 Task，并处于 RUNNING。

任一条件失败都抛出 `LeaseLostError`。Guard 返回已锁定的 Task 和 Run，让终态提交等调用者直接在同一事务中更新它们。

锁顺序固定为：

```text
Task → Run → 其他运行记录
```

ApprovalService 同样先锁 Task、再锁最新 Run、最后锁 Approval。统一锁顺序减少不同路径互相等待形成死锁的风险。

### 58.5 为什么“检查”和“写入”必须在同一事务

下面的写法没有 fencing 效果：

```text
事务 A：检查租约有效 → 提交
外部发生接管
事务 B：写入旧结果 → 提交
```

当前正确结构是：

```text
开启短事务
→ LeaseGuard 锁 Task 并检查 owner、epoch、状态、到期时间
→ 锁并检查 Run
→ 写事件、快照或账本记录
→ 提交并释放锁
```

因为 Task 行锁一直持有到写入提交，接管扫描使用 `FOR UPDATE SKIP LOCKED` 时不能跨过正在提交的合法写入。旧 Worker 如果在接管完成后进入事务，会看到 owner/epoch 已变化并被拒绝。

Guard 不能在调用者外面先检查一次再把布尔值传进去；它的 API 刻意接收 session，就是为了让事务边界可见。

### 58.6 哪些运行写路径受保护

`PersistentAgentRunner` 为一次 JobLease 创建一个 `LeaseGuard`，再注入各个持久化组件：

| 组件 | 受保护的数据库事实 |
|---|---|
| `PersistentEventSink` | RunEvent 和 sequence 推进 |
| `PersistentCheckpointStore.save()` | snapshot.saved 事件与 RunSnapshot |
| `PersistentToolMiddleware.before()` | Turn、ToolCall、Approval、ToolEffect 执行前状态 |
| `after_success()` / `after_failure()` | ToolCall 结果和 Effect 的 COMMITTED / UNKNOWN |
| `SkillRetrievalService.select()` | Skill 选择、零命中冻结和检索事件 |
| `_persist_run_config()` | RunConfigSnapshot 与 config hash |
| `ArtifactService.create*()` | Artifact 元数据 |
| `heartbeat()` / `finalize()` | 租约续期、Task/Run 终态和终态事件 |

构造这些组件时还会核对 run_id 是否与租约一致，防止有效租约被错误地接到另一个 Run。

读取 Snapshot 不需要 Guard，因为只读不会污染权威事实；但从快照恢复后的所有新写入仍必须通过 Guard。

### 58.7 外部动作为什么不能放在数据库锁里

模型请求、网页访问、Shell、文件写入可能持续很久。若在调用这些外部操作期间一直持有 Task 行锁，会占住数据库连接、阻塞心跳和恢复扫描，最终把短租约设计重新变成长事务。

因此工具链采用三个短阶段：

```text
短事务：Guard + Effect=EXECUTING
→ 无数据库锁地执行外部动作
→ 短事务：Guard + Effect=COMMITTED 或 UNKNOWN
```

这能保护数据库事实，但不能撤销已发出的外部动作。Worker 在外部写完成后、COMMITTED 提交前失联时，数据库只能知道结果未知，所以进入 UNKNOWN 并要求人工确认。

Artifact 文件同样在数据库事务外写入。`ArtifactService` 在文件 I/O 前检查一次租约，在登记元数据的事务中再检查一次。若两次检查之间失租，数据库不会接受元数据，但磁盘可能留下孤立文件。清理孤立文件属于后续维护任务，不应假装数据库回滚能删除已经完成的外部写。

### 58.8 COMMITTED 复用和 retry 的 ToolCall 关联

ToolEffect 使用 Task 作用域和规范化工具参数生成语义键。同一语义效果已经 COMMITTED 时，新 ToolCall 直接复用保存的结果，不再次执行工具。

UNKNOWN 被人工确认为 retry 后，中间件把 Effect 重新置为 EXECUTING，并把 `effect.tool_call_id` 改为本次新 ToolCall。这样后续结果和审计记录关联当前执行，而不是永远指向崩溃前的旧调用。retry 响应随后清空，保证授权只使用一次。

### 58.9 为什么零命中也要冻结

阶段三使用已保存的 `RunSkillSelectionRecord` 恢复 Skill 选择。但零命中没有 Selection 行，若进程在检索事件提交后、RunConfigSnapshot 保存前退出，恢复时可能因为新 Skill 发布而重新检索出不同结果。

迁移 0005 在 Run 增加 `skill_selection_frozen`。首次选择无论是否命中，都在同一个 Guard 事务中把它设为 true。恢复时看到 true 就返回已冻结的空结果，不重新查询当前 ACTIVE Skill。

这是一个最小的负选择冻结机制。完整 RetrievalBatch、查询 hash 和算法版本将在后续检索模块实现。

### 58.10 Migration 与混跑风险

迁移 `20260919_0005` 增加：

```text
tasks.lease_epoch              NOT NULL DEFAULT 0
runs.skill_selection_frozen    NOT NULL DEFAULT false
```

升级必须先停止全部旧 Worker，再迁移，再启动同版本进程。数据库新增 epoch 字段并不会自动让旧二进制执行 Guard；新旧 Worker 混跑时，旧进程仍可能写入迟到结果。

回退迁移会删除这两个字段。执行降级前应确认没有依赖新语义的活跃任务，并保留数据库备份。

### 58.11 取消与清理

`JobWorker.run_once()` 在 finally 中：

1. 通知心跳停止；
2. 取消尚未结束的 heartbeat 和 handler task；
3. 使用 `gather(..., return_exceptions=True)` 等待二者真正退出。

只调用 `cancel()` 而不等待会留下后台协程继续写入或在事件循环关闭后产生警告。等待清理使测试和进程退出都有明确边界。

`run_forever()` 捕获 `LeaseLostError`，将本轮视为没有成功处理任务，然后继续轮询。失去某一租约不应杀死整个 Worker 进程。

### 58.12 PostgreSQL 和 SQLite 分别证明什么

SQLite 用例可以证明字段、状态转换、Guard 判断和各写路径的功能行为。它不实现与 PostgreSQL 相同的行锁和 `SKIP LOCKED` 并发语义。

PostgreSQL 专项用例验证：Guard 持有 Task 行锁直到写事务提交时，过期扫描不能同时接管；事务提交后，扫描才能获得任务。CI 配置了 PostgreSQL 17，本机没有配置测试库，因此本地的 PostgreSQL 参数会跳过。跳过表示尚未在当前机器验证，不等于通过。

### 58.13 推荐阅读顺序

```text
1. db/models.py 的 TaskRecord 与 RunRecord 新字段
2. migration 20260919_0005
3. tasks/lease.py 的 JobLease 和 claim_next()
4. tasks/lease_guard.py 的 database_now() 与 check()
5. trace/persistent_sink.py 和 runtime/checkpoints.py
6. tools/effects.py 的 before()/after_success()/after_failure()
7. runtime/persistent_runner.py 的 Guard 注入
8. trace/artifacts.py 的两次检查
9. skills/retrieval.py 的零命中冻结
10. tests/integration/test_lease_fencing.py
```

---

## 59. 阶段四模块 2：上下文契约、TokenCounter 与确定性裁剪

### 59.1 模块目标

原有 `max_total_tokens` 是整个 Run 的累计消耗软预算。它只能在模型返回 Usage 后累加，不能阻止某一轮请求本身超过模型上下文窗口。

模块 2 在每次 Provider 请求前增加纯内存策略：

```text
完整消息历史 + 本轮工具 Schema
→ 校验消息组结构
→ 估算本轮输入大小
→ 必要时按稳定优先级删除可选资料
→ 再次计数
→ 放行或返回 context_budget_exceeded
```

主要文件：

```text
src/evoagent/core/context_budget.py
src/evoagent/core/context_policy.py
src/evoagent/core/context.py
src/evoagent/core/models.py
src/evoagent/core/loop.py
src/evoagent/runtime/run_config.py
tests/unit/test_context_policy.py
```

### 59.2 三类 Token 限制不能混用

当前运行时同时存在三类限制：

| 限制 | 作用范围 | 何时可判断 | 失败结果 |
|---|---|---|---|
| `context_window_tokens` | 单次请求的总窗口 | 发请求前 | 本地裁剪或拒绝 |
| `max_output_tokens` | 单次模型输出预留 | 发请求前，并传给 Provider | Provider 输出上限 |
| `max_total_tokens` | 整个 Run 的累计消耗 | Provider 返回 Usage 后 | `token_budget_reached` |

把累计预算设为 32000，并不表示一次请求可以发送 32000 个输入 Token。模型窗口还要为输出和计数误差留空间。

### 59.3 `ContextBudget` 的公式

`ContextBudget` 保存三个正交值：

```text
W = context_window       已确认的模型上下文窗口
O = output_tokens        本轮最大输出预留
S = safety_margin        封装差异与估算误差余量
I = W - O - S            本轮允许的输入上限
```

构造时必须满足 `W > O + S`。例如：

```text
W = 32768
O = 4096
S = 1024
I = 27648
```

这组默认数值只用于 Mock 开发环境，不声明任何真实模型一定拥有 32768 Token 窗口。真实兼容服务使用 bounded 模式时，Settings 要求显式提供已核对的 `EVOAGENT_CONTEXT_WINDOW_TOKENS`。

策略不会只在计算时扣除 O；它还会把 O 写入 `ModelRequest.max_output_tokens`。否则本地为输出保留 4096，Provider 却可能使用更大的默认上限，预算公式就失去意义。

### 59.4 `TokenEstimate` 为什么记录方法和置信度

TokenCounter 返回的不只是整数，而是：

```text
TokenEstimate(
    count,
    method,
    tokenizer_version,
    confidence,
)
```

不同计数器不能被描述成同一种保证：

| Counter | 计数内容 | confidence | 能证明什么 |
|---|---|---|---|
| `MockCounter` | 定义好的 UTF-8 字节与封装规则 | `exact_mock` | 离线算法按同一规则可重复 |
| `ConservativeTokenCounter` | UTF-8 字节、消息/工具封装余量 | `estimated` | 本地采用了保守估算，不能证明服务端真实 Token 上界 |

真实模型的分词方式依赖模型和服务端协议。当前没有引入厂商 tokenizer，因此不能把字节估算包装成“精确 Token”。`context_strict=true` 时只接受 `exact`、`exact_mock` 或 `verified_upper_bound`；真实兼容服务当前会返回 `context_count_unverified`，从而在请求前拒绝。

### 59.5 计数为什么必须包含工具 Schema

Provider 请求中的工具不只是一串名称。每个定义包含 description 和完整 JSON Schema，模型会实际接收这些文本。大量工具或复杂参数 Schema 即使消息很短，也可能占满窗口。

`request_bytes()` 构造接近 OpenAI-compatible 请求形状的规范对象，计入：

- model 名称；
- 每条消息的 role 和 content；
- assistant ToolCall 的 id、name 和序列化 arguments；
- tool_call_id；
- 每个工具的 function 名称、描述和 parameters Schema；
- 消息和工具的固定封装余量。

内部的 `context_priority` 是本地裁剪元数据，不会发送给 Provider，也不计入协议负载。

### 59.6 可选资料为什么使用显式元数据

`Message` 增加了可选的 `context_priority`，并限制只有 USER 消息可以携带。`ContextBuilder` 只给 `external_context` 生成的消息标记 priority=0，最后的用户任务没有该字段。

这条边界十分重要。策略不能看到文本以“外部上下文”开头就删除，因为用户可以把自己的真实任务写成同样的字符串。裁剪资格来自可信代码构造的结构化字段，不来自不可信正文的内容匹配。

当前保护内容包括：

- system prompt 和已注入 Skill；
- 原始用户任务与显式约束；
- assistant 回复；
- ToolCall 与 ToolResult；
- 工具 Schema。

当前可删除内容只有带 `context_priority` 的外部资料。自动摘要、旧消息归档和 Memory 分区还没有实现。

### 59.7 `MessageGroupBuilder` 保护什么协议不变量

一个 assistant 可以一次发出多个 ToolCall，后面必须存在每个 call_id 对应的 ToolResult：

```text
assistant: tool_calls=[a, b]
tool: tool_call_id=b
tool: tool_call_id=a
```

结果顺序可以不同，但整个组必须完整。`MessageGroupBuilder` 会拒绝：

- 没有前置 assistant ToolCall 的孤立 tool 消息；
- 缺少某个结果的工具组；
- assistant 和 tool 之间插入其他消息；
- 重复的 call_id；
- 在后续历史中再次使用已经出现过的 call_id。

模块 2 暂时不会压缩历史工具组，但先建立分组不变量，为模块 3 的整组摘要和归档打基础。将来无论保留、替换还是移除，都不能只处理工具组的一半。

### 59.8 确定性裁剪算法

`BoundedContextPolicy.prepare()` 的处理顺序固定：

```text
1. MessageGroupBuilder 校验全部消息
2. 把预算中的 output_tokens 写进请求
3. TokenCounter 计算完整请求
4. strict 模式检查计数置信度
5. 找出全部带 context_priority 的消息
6. 按 (priority, 原始索引) 升序排序
7. 从低优先级开始逐条删除并重新计数
8. 达到 input_limit 后返回 ContextDecision
9. 可选资料删完仍超限则抛 ContextPolicyError
```

相同输入、配置和 Counter 总会得到相同的 dropped_indices 和请求内容。当前约定数值越小越先删除；同一优先级保持原消息顺序作为稳定 tie-breaker。

裁剪不会修改原始 tuple。它通过 Pydantic `model_copy()` 创建新的 ModelRequest，所以调用者仍能保留原始上下文证据。

### 59.9 `ContextDecision` 和审计事件

策略成功时返回：

```text
request             最终允许发送的 ModelRequest
estimate            使用的计数及方法
dropped_indices     被删除消息在原请求中的索引
input_limit         本次输入上限
```

AgentLoop 根据结果写入：

| 事件 | 条件 | 记录内容 |
|---|---|---|
| `context.checked` | 已检查但没有裁剪 | estimate、limit、输出上限 |
| `context.trimmed` | 删除了可选资料 | 上述字段和 dropped_indices |
| `context.rejected` | 分组、计数可信度或预算失败 | iteration 和 error_code |

事件不保存被删除资料的正文，避免为了可观测性再次扩散敏感或庞大的外部内容。

### 59.10 为什么检查必须放在每次 Provider 请求前

初始上下文合规，不代表后续轮次合规。工具可能返回大结果，assistant 也会不断追加消息。因此 AgentLoop 在每次 iteration 构造 ModelRequest 后立即调用策略：

```text
messages + registry.definitions()
→ ModelRequest
→ ContextPolicy.prepare()
→ context.checked / trimmed / rejected
→ model.requested
→ Provider.stream()
```

如果策略失败，Loop 返回 `LIMIT_REACHED` 和具体 error_code，`model.requested` 不会产生，Provider 也不会收到请求。

测试中特意让第一轮工具产生超大结果。第一次请求可以发出，第二次请求在 Provider 前被阻止，证明检查不是只在 Runner 启动时执行一次。

### 59.11 错误代码和取消语义

当前主要错误包括：

| error_code | 含义 |
|---|---|
| `invalid_context_group` | ToolCall/ToolResult 历史结构已损坏 |
| `context_count_unverified` | strict 模式下没有可信计数器 |
| `context_budget_exceeded` | 受保护内容本身超过输入上限 |

这些错误发生在本地请求前，不是 ProviderError。Runner 最终把 Loop 的 `LIMIT_REACHED` 映射为对应终态。

ContextPolicy 是同步纯函数，不吞掉 `asyncio.CancelledError`。Provider 已经开始阻塞时取消 Run，AgentRunner 仍按原有路径生成 CANCELLED 结果，并等待 Provider 异步生成器的 finally 清理。

### 59.12 `bounded` 和 `legacy` 模式

Settings 默认：

```text
context_policy=bounded
context_window_tokens=32768
max_output_tokens=4096
context_safety_margin=1024
context_strict=false
```

`legacy` 显式保留旧行为：不做本地上下文检查，也不强制设置 `ModelRequest.max_output_tokens`。它用于兼容旧 CLI 或隔离运行旧语义，不是推荐的生产默认值。

RunConfigSnapshot 保存 bounded policy 的完整 manifest，包括版本、窗口、输出、余量、counter identity 和 strict。AgentLoop 的 config hash 也包含同一语义。恢复时如果持久化配置与当前 Runtime 不一致，返回 `snapshot_incompatible`，并且不会调用 Provider。

旧 RunConfigSnapshot 没有 `context_policy` 字段。`canonical_dict()` 在字段为 None 时删除它，所以历史配置仍能计算出原来的 hash。这个兼容只保证旧数据可读取；一旦新运行启用 bounded，不能把它冒充成与 legacy 相同的执行条件。

### 59.13 当前边界

模块 2 已完成的是请求前确定性检查和删除可选资料。它尚未实现：

- 使用模型自动生成摘要；
- 把摘要和覆盖范围保存为 ContextRevision；
- 将超大 ToolResult 转存 Artifact 后只注入摘要；
- 长期事实记忆与 Session Archive；
- 按模型选择精确 tokenizer；
- 服务端 context-length 错误后的有限再压缩。

因此受保护的任务、工具组或 Schema 本身超限时，系统会明确失败。它不会删除安全规则或悄悄截断 ToolResult 来凑窗口。

### 59.14 推荐阅读顺序

```text
1. config.py 的 context_* 配置及交叉校验
2. core/models.py 的 Message.context_priority 和新 EventType
3. core/context.py 的 external_context 标记
4. core/context_budget.py 的公式、estimate 和两个 Counter
5. core/context_policy.py 的 MessageGroupBuilder
6. BoundedContextPolicy.prepare()
7. core/loop.py 的请求前 Hook
8. runtime/run_config.py 的兼容 canonical_dict()
9. runtime/persistent_runner.py 的 policy manifest 和恢复处理
10. tests/unit/test_context_policy.py
11. tests/integration/test_persistent_runtime.py 的配置漂移用例
```

---

## 60. 阶段四模块 0～2 完整调用链

### 60.1 正常执行链路

```text
API 创建 QUEUED Task + Run
→ JobWorker 生成“配置标签:启动 UUID”实例身份
→ claim_next() 使用数据库时间和 SKIP LOCKED 领取 Task
→ lease_epoch 递增，返回 JobLease
→ PersistentAgentRunner 创建 LeaseGuard
→ Guard 保护 Skill 选择与零命中冻结
→ Guard 保护 RunConfigSnapshot 与 config hash
→ 读取可兼容 Snapshot
→ ContextBuilder 构造 system、可选外部资料、原始任务
→ AgentLoop 每轮构造完整 ModelRequest
→ MessageGroupBuilder 验证 ToolCall/ToolResult 结构
→ TokenCounter 计算消息、参数、工具 Schema 和封装
→ BoundedContextPolicy 保留受保护内容，必要时裁剪可选资料
→ 写 context.checked 或 context.trimmed
→ 写 model.requested，再调用 Provider
→ 如有工具调用：Guard + ToolEffect=EXECUTING
→ 事务外执行工具
→ Guard + ToolEffect=COMMITTED/UNKNOWN + ToolCall 结果
→ 在完整工具批次后 Guard 保存 Snapshot
→ Handler 返回 TaskExecutionResult
→ finalize() 再用 Guard 原子写 Task、Run 与终态事件
```

### 60.2 Worker 崩溃与接管链路

```text
Worker A 持有 owner=A:<uuid1>, epoch=7
→ 心跳停止，租约按数据库时间过期
→ Worker B 的 recover_expired() 锁 Task/Run
→ 清空 owner，状态改为 RECOVERING，写 recovery.started
→ recover_pending() 执行恢复决策
   ├─ 有任意未决 Effect
   │  → 全部 UNKNOWN + PENDING Approval
   │  → WAITING_USER
   ├─ 无 Effect 且 Snapshot 兼容
   │  → RESUME + QUEUED
   ├─ 无 Effect 且无 Snapshot
   │  → RESTART + QUEUED
   └─ Snapshot 不兼容
      → FAILED
→ B 领取恢复任务，epoch=8
→ A 的迟到事件、快照、效果、配置、Artifact 元数据和终态写入
→ LeaseGuard 因 owner/epoch 不匹配统一拒绝
```

### 60.3 上下文超限链路

```text
构造完整请求
→ 校验完整消息组
→ 计算 estimate
→ estimate <= input_limit
   └─ 直接发送
→ estimate > input_limit
   → 按 priority 删除可选资料并逐次重算
   → 已达上限
      └─ 写 context.trimmed，发送裁剪后请求
   → 可选资料已空仍超限
      └─ 写 context.rejected
         返回 LIMIT_REACHED/context_budget_exceeded
         Provider 请求次数不增加
```

### 60.4 三条不能混淆的保证

1. LeaseGuard 保护数据库权威事实，不能撤销已经发出的外部动作。
2. ContextPolicy 保证不发送超过本地配置输入上限的请求，estimated Counter 不证明服务端真实 Token 上界。
3. Snapshot 恢复保证从合法边界继续，不保证任意外部系统具有 exactly-once 语义。

---

## 61. 阶段四模块 0～2 测试地图

| 测试或检查 | 主要证明 | 当前环境说明 |
|---|---|---|
| `test_worker_process_recovery.py` | API→独立 Worker→强制退出→替代进程的完整链路 | SQLite 进程级行为 |
| `worker_crash_fixture.py` | 在 Snapshot、COMMITTED、外部写后三个真实边界暂停 | 仅测试辅助进程 |
| `test_lease_fencing.py::test_old_epoch...` | 同标签新 epoch 接管后，旧 Worker 各写入口全部失败 | SQLite 与 PostgreSQL 参数 |
| `test_lease_fencing.py::test_recovery_requires...` | 多个 UNKNOWN 全部确认后才重排队 | SQLite 与 PostgreSQL 参数 |
| `test_lease_fencing.py::test_worker_identity...` | 启动身份唯一，取消会等待 Handler 清理 | SQLite 与 PostgreSQL 参数 |
| `test_lease_fencing.py::test_zero_hit...` | 零命中在配置提交前也已冻结 | SQLite 与 PostgreSQL 参数 |
| `test_lease_fencing.py::test_guard_and_write...` | Guard 锁持续到写事务提交 | 仅 PostgreSQL 有效 |
| `test_context_policy.py` | 中英文裁剪、Schema 计数、分组、优先级、strict、legacy、取消 | 纯内存与 Mock |
| `test_persistent_runtime.py` | 配置漂移恢复失败且 Provider 未收到请求 | SQLite 集成 |
| `test_run_config_and_manifest.py` | 旧 hash 兼容、policy 改变不可比较 | 单元测试 |
| `test_migrations.py` | 0001→0005 升级、metadata check、回退到 base | SQLite；另有 PostgreSQL 参数 |
| `evoagent --demo` | 默认 bounded 的阶段一 CLI 工具循环 | Mock smoke |
| `evoagent-phase3-demo` | 阶段三生命周期未回退 | Mock/SQLite smoke |

本次全量本地结果为 228 passed、10 skipped。跳过项包括未配置 PostgreSQL 的参数与迁移用例、SQLite 参数下主动跳过的行锁用例，以及 Windows 普通账户无符号链接权限的 WorkspaceGuard 用例。

Ruff lint、format 和 `git diff --check` 均通过。PostgreSQL、容器和真实模型报告仍是环境验收待办；不能因为测试代码存在或 CI 配置了服务，就声称本次本机已经运行成功。

---

## 62. 阶段四模块 0～2 学习验收

读完这三个模块后，建议结合源码独立回答：

1. 为什么 `recover_expired()` 成功不代表任务会自动恢复？
2. 为什么要把 RECOVERING 作为持久状态，而不是在过期扫描事务内完成所有决策？
3. 为什么恢复必须扫描全部未决 ToolEffect？
4. 过去的 APPROVED 为什么不能证明 UNKNOWN 动作可以直接重试？
5. COMMITTED 和 UNKNOWN 在进程故障测试中分别怎样产生？
6. Worker 配置标签、实例 owner 和 lease epoch 各自解决什么问题？
7. 为什么同标签重启仍不能复用旧 JobLease？
8. 为什么 lease expiry 要使用数据库时间？
9. `LeaseGuard.check()` 检查哪些字段，锁顺序是什么？
10. 为什么租约检查和实际写入必须在同一个事务和 session 中？
11. 为什么不能在模型请求或外部工具调用期间一直持有 Task 行锁？
12. 数据库 fencing 为什么不能提供外部 exactly-once？
13. Artifact 为什么可能留下未登记文件，这是否代表旧 Worker 可以提交数据库事实？
14. 为什么零命中也需要冻结？
15. 单轮上下文窗口、输出预留和 Run 累计 Token 预算有什么区别？
16. 为什么工具 JSON Schema 必须参与输入计数？
17. `exact_mock` 和 `estimated` 分别能证明什么？
18. strict 模式为什么会拒绝当前真实兼容 Provider？
19. 为什么裁剪资格使用 `context_priority`，不能匹配“外部上下文”文本前缀？
20. 一个包含两个 ToolCall 的消息组满足什么完整性条件？
21. 为什么 ContextPolicy 必须在每一轮 Provider 请求前运行？
22. `context.checked`、`context.trimmed` 和 `context.rejected` 各表示什么？
23. 为什么 bounded 的输出预留必须真正写入 `ModelRequest.max_output_tokens`？
24. 旧 RunConfigSnapshot 怎样保持 hash 兼容，为什么 bounded Run 又不能与 legacy 混为一谈？
25. 进程级 SQLite 测试、PostgreSQL 行锁测试和真实模型效果报告各自证明什么？

如果能沿着具体 Task、Run、JobLease、ToolEffect、ModelRequest、事件和测试回答这些问题，就掌握了阶段四前三个模块的核心：先让恢复链路真实可运行，再让旧执行者无法污染数据库，最后让每次模型请求在发送前具备明确、可审计的上下文边界。

以上是模块 0～2 的阶段性边界。压缩、ContextRevision 与记忆基础见第 63～68 章；向量索引和混合检索见第 69～73 章。MCP、Redis 和容器强隔离仍属后续模块。模块 0～2 的部署步骤见[运行说明](阶段四-模块0至2验收与运行说明.md)，设计决策见[ADR-007](ADR-007-租约隔离与请求上下文预算.md)。

---

## 63. 阶段四模块 3：可恢复压缩与完整输出归档

### 63.1 本模块解决什么问题

模块 2 可以回答“这一次请求能否放进窗口”，却不能让很长的工具历史一直保留在窗口内。假设一个任务先后读取五份长文，最后才综合回答：直接继续追加消息会超限，直接截断又会丢掉后面回答所需的证据。

模块 3 把问题拆成两个步骤：完整内容保存到可校验的 Artifact；模型上下文保留较短的摘录和来源引用。与此同时，压缩后的上下文必须成为可恢复状态的一部分，否则进程重启会看到与崩溃前不同的提示词。

这里的“摘要”只描述本次 Run 的工作历史。它不表示用户已经同意保存长期偏好，也不改变副作用或审批的真实状态。

### 63.2 文件职责与阅读入口

| 文件 | 关键对象/方法 | 负责的边界 |
|---|---|---|
| `memory/summarization.py` | `ExtractiveSummarizer.summarize()` | 从合法组构造有来源的受限摘录 |
| `runtime/context_store.py` | `ContextStore.prepare()` | 判断阈值、验证候选、事务提交新上下文 |
| `core/loop.py` | `AgentLoop.run()` | 每轮请求前准备上下文，再执行预算检查 |
| `core/models.py` | `LoopState` | 保存 v2 revision、历史截止点和原循环状态 |
| `runtime/checkpoints.py` | `load_latest()` | 检查快照版本及 revision 归属 |
| `tools/output_store.py` | `preserve()` / `read()` | 全量脱敏归档、预览、哈希和作用域校验 |
| `tools/builtin/artifact_read.py` | `ArtifactReadTool` | 模型可见的 ID 分页读取能力 |
| `tools/executor.py` | `execute()` | 在截断与效果结果登记之前归档 |

建议先看 Executor 的调用位置，再看 ContextStore 的事务，最后回到 AgentLoop。这样可以先分清“工具结果过大”和“整个请求过大”两个不同层次。

### 63.3 为什么先保存全文再生成预览

原来的执行顺序是工具返回字符串，然后 `_truncate()`，再把截断结果写入效果账本。那时即使工具本身成功，运行时也没有完整结果可以回读。

现在持久化 Runner 会传入 `ToolOutputStore`：

```text
tool.invoke(arguments)
→ 得到完整字符串
→ redact(content)
→ 超过结果预算？
   ├─ 否：返回脱敏字符串
   └─ 是：create_unique() 保存全文
          → 生成 artifact_id/hash/total_chars 引用
          → 追加预算内的预览
→ Executor 最终长度检查
→ middleware.after_success()
→ ToolResult
```

“完整”指脱敏后的完整正文。密钥被替换为 `[REDACTED]`，系统不会同时保存一份可恢复密钥的原始副本。

Artifact ID 与文件路径不同。模型得到的是数据库登记的 ID；目录结构和 URI 由受控存储维护。文件写入采用排他创建，已经存在的同名文件不能被重写。

### 63.4 `artifact_read` 怎样读取

工具参数是 `artifact_id`、`offset` 和 `limit`。offset 从 0 开始，单位是 Unicode 字符；limit 默认 2000，最大 8000。它不接受文件路径，也不接受“我要读取另一个 run_id”这样的授权参数。

服务端先取 ArtifactRecord，检查属于当前 Run、没有 erased 标记，再读取字节并复算 SHA-256。通过后返回当前页、总字符数和 next_offset。越界 Run 返回权限错误，内容哈希不匹配返回工具执行错误。

这一步既防止路径穿越，也避免模型把一个已知 UUID 当作跨运行读取权限。以后若要支持显式共享的 Artifact，必须增加服务端授权关系，不能仅放宽工具参数。

### 63.5 保存失败为什么不能伪造引用

如果文件写入出现预期 I/O 错误，预览包含 `artifact_store_failed: full output unavailable`，而不是一个没有对应文件的 ID。若预算连引用本身都放不下，则返回明确的预算不足提示。

工具动作已经成功与输出归档失败是两件事。一个文件写入工具完成外部动作后，不能仅因为归档失败就把动作当作未执行再重试。ToolEffect 仍记录执行事实，输出状态通过预览说明。

大结果每次归档会获得不同 ID。重复调用检测因此对受控归档结果使用内容 hash 作为稳定指纹，否则相同结果会因为 UUID 不同而绕过重复调用上限。

### 63.6 80% 和 60% 各自负责什么

ContextStore 使用模块 2 的 Counter 对完整请求计数，工具 JSON Schema 也在其中。这里的阈值相对于 `input_limit`，不是模型声明的总窗口。

```text
input_limit = context_window - output_tokens - safety_margin
trigger = input_limit × 0.8
target = input_limit × 0.6
```

低于 trigger 时不压缩。高于 trigger 时构造候选，但只有候选小于原请求且不超过 target 才提交。两个阈值之间留出余量，避免每增加一条工具消息就重复压缩。

达不到目标不会继续无界递归尝试。当前算法记录降级原因，保留原上下文，再让原有 ContextPolicy 判断能否发送。原上下文仍可容纳时任务继续；超限时得到预算错误。

### 63.7 什么消息可以被压缩

先调用 `MessageGroupBuilder.build()`，因此孤立 tool 结果、缺失结果、重复 call ID 会在压缩前失败。候选只来自旧的、以 assistant tool_calls 开头的完整组。

以下内容保留原样：

- system 消息与已经渲染的 Skill；
- 用户最初的目标及其他 user 消息中的约束；
- 最近两组消息；
- 不符合完整工具组条件的消息。

例如第五轮工具刚返回时，旧的第一至第三轮可以转为摘录，第四、第五轮仍保留原始调用和返回。只读第一个 tool 结果而遗漏同一 assistant 请求中的第二个结果，不是合法压缩。

### 63.8 为什么默认采用原文摘录

`ExtractiveSummarizer` 有固定方法版本 `extractive-v1`、输入字符上限、组数上限和单条摘录长度上限。它不调用模型，不调用工具，也不会再次进入 AgentLoop。因此这条默认压缩链路的辅助请求数与辅助 Token 消耗均为零。

结构化结果包括：

| 字段 | 含义 |
|---|---|
| `goal_ref` / `constraint_refs` | 原始目标与约束仍在原消息中；引用指向归档上下文 |
| `observations` | 工具返回的原话片段，以及 source_index、group_id |
| `covered_group_ids` | 被替换的完整组的内容哈希 |
| `source_hash` | 候选生成所用原始上下文哈希 |
| `artifact_refs` | 脱敏完整上下文的数据库 Artifact ID |
| `completed_steps` / `pending_steps` | 当前留空，不根据摘录虚构执行状态 |
| `authority` | 明确标识低可信观察，不是工具或审批权威 |

组哈希用于定位来源，不用于证明内容正确。工具返回本身也可能错误，摘录只保证可以追溯到输入。

### 63.9 为什么摘要放在 user 层

摘要内容可能包含网页、文件或工具返回中的指令。把它拼成 system 会将不可信资料提升为最高权限提示；当前实现将它包装成明确标注的低可信 user 消息，并设置可选上下文优先级。

即使摘要里出现“写入已成功”或“用户批准”，Executor 也不能据此跳过 ToolEffect/Approval 检查。后两者仍由数据库事务维护，模型文字不能修改执行权限。

### 63.10 ContextRevision 怎样成为可恢复事实

ContextRevision 保存 Run、revision 序号、parent_id、输入/策略哈希、去重键、Artifact ID、结构化摘要及前后估算值。它不是在内存里修改 messages 的日志，而是“这次运行采用了哪一版上下文”的权威记录。

提交顺序是：

```text
构造并检查候选
→ 排他写入 Artifact 文件
→ 打开 UnitOfWork
→ LeaseGuard 锁定 Task/Run 并验证租约
→ 再验证记忆引用
→ 检查当前 revision 是否仍为预期 parent
→ 登记 ArtifactRecord
→ 写 ContextRevision
→ 写 context.summarized 事件
→ 写包含新 messages/revision 的 v2 Snapshot
→ COMMIT
→ AgentLoop 才采用新的 messages
```

parent 检查解决“另一个执行者已经提交新上下文”的问题；去重键结合 parent、输入哈希与策略哈希，避免同一转换产生重复 revision。重复提交遇到已保存的同一候选时可复用快照。

### 63.11 两个故障位置有什么差别

在提交之前退出：事务回滚，新 revision 和新 Snapshot 都不存在，旧 Snapshot 仍是恢复入口。已经创建的文件可能成为孤儿，但没有 ArtifactRecord，artifact_read 无法读取它。

在提交之后退出：最新 Snapshot 已经带着新 messages、context_revision_id 和原循环计数。恢复应加载它，而不是重新摘要旧消息、重新执行完成的工具，或把 Usage 清零。

恢复引用了 ContextRevision 时，运行时还会检查 Run 归属和 Artifact 内容哈希。文件丢失、被改动或已擦除，不能静默继续使用旧摘要。

### 63.12 v1/v2 的边界

LoopState v2 新增 schema_version、context_revision_id 和 history_before_sequence，原有 completed_iterations、Usage、usage_is_complete、重复调用指纹与次数继续保存。

RunConfigSnapshot 未显式声明版本时仍解释为 v1，canonical_dict 不加入新的默认字段，从而保持旧哈希。新持久化运行默认 v2，并冻结摘要方法。活动 v1 不自动升级；版本或配置不匹配时返回 `snapshot_incompatible`。

旧终态 Trace 查询不需要继续执行，所以仍可查看。不要把“旧 Trace 可查看”误认为“旧活动任务一定可恢复”。

### 63.13 本模块阅读与验证顺序

```text
1. ToolExecutor.execute() 中 preserve 的位置
2. ToolOutputStore.preserve() / read()
3. MessageGroupBuilder.build()
4. ExtractiveSummarizer.summarize()
5. ContextStore.prepare() 的候选检查与提交事务
6. AgentLoop 的请求前准备和 checkpoint
7. test_context_failure_before_commit_keeps_old_snapshot()
8. test_context_revision_snapshot_and_restore_preserve_counters()
```

纯内存阶段一 CLI 仍只使用预算检查，不隐式创建数据库或 Artifact 服务。自动持久化压缩属于独立 Worker 的运行链路。

---

## 64. 阶段四模块 4：Workspace、消息投影与版本化记忆

### 64.1 为什么 Session 表不等于已有会话记忆

之前的 Session 能把 Task 分组，但任务目标主要在 Task.goal，答案在 Run.final_answer，事件中只保存可能截断的片段。存在一个 Session ID，并不能保证已经有按顺序可读取、可引用的对话。

模块 4 建立权威 Message 投影，为之后的历史加载和事实提取提供稳定输入。当前没有自动将全部历史灌进模型；保存历史和选择历史是两项不同职责。

### 64.2 Workspace 和工作目录的区别

WorkspaceRecord 是数据库中的逻辑归属。Session.workspace_id 指向它，MemoryEntry 的 workspace_id 决定事实可在哪个逻辑空间使用。

`Settings.workspace` 则是文件工具的工作目录。修改路径不应自动改变记忆权限；持有某个目录也不应意味着可以读取另一个 Workspace 的事实。

当前本地服务使用固定默认 Workspace。迁移把已有 Session 归入该 Workspace；测试使用 metadata.create_all 时也创建相同默认记录。没有在请求中让模型填写任意 workspace_id。

### 64.3 新消息字段分别用于什么

| 字段 | 作用 |
|---|---|
| `session_id` | 历史所属会话 |
| `task_id` / `run_id` | 指向产生该消息的执行实体 |
| `session_sequence` | 会话内稳定顺序，不依赖时间精度 |
| `kind` | goal、terminal 或 legacy |
| `role` | user/assistant 等展示与来源身份 |
| `content_hash` | 复验来源是否被改写 |
| `backfill` | 明确标记历史迁移记录，不当作完整新来源 |

数据库约束 `(session_id,session_sequence)` 不可重复；`(run_id,kind)` 使重复终态投影无法产生第二份答案。

### 64.4 为什么不能用 `max(sequence)+1`

两个并发请求都可能读到当前最大序号 7，并且都尝试写入 8。唯一约束虽然能拒绝其中一个，却不能让正常并发创建自然成功。

`append_message()` 使用数据库原子 UPDATE：将 Session.next_message_sequence 加一，并通过 RETURNING 获得更新后的值，再减一作为当前消息序号。这个更新也串行化同 Session 的分配。

终态幂等键在持有这次会话写事务时检查。重复调用可能消耗一个序号而不增加消息，因此序号允许空洞；它承诺稳定、不重复、递增，不承诺连续无空缺。

### 64.5 Task 创建为什么必须包含用户消息

普通任务的创建事务现在是：

```text
检查 Session
→ INSERT Task
→ INSERT Run
→ 原子分配 Session 序号
→ INSERT goal Message
→ 设置 Task.history_before_sequence
→ 写 task.queued 事件
→ COMMIT
```

如果中途失败，Task、Run、消息和事件一起回滚，不会出现“任务已经可执行，但找不到用户原话来源”的半成品。EvalCoordinator 创建评测 Run 时也走同一消息投影规则，之后的记忆门禁再明确排除评测来源。

### 64.6 历史截止点怎样防止未来信息泄漏

设已有消息序号 1、2，任务 A 的 goal 分配到 3，于是 A.history_before_sequence 为 3。任务 B 随后分配到 4。

`history_for_task(A)` 使用 `session_sequence < 3`，只返回 1、2。无论 B 提交得多快，或者 A 稍后恢复多少次，都不能突然看到 B 的新目标。

A 自己的目标从 Task.goal 构造，不需要再通过历史加载一遍。严格小于与小于等于不能互换，否则自己的 goal 会重复出现。

### 64.7 终态消息何时写入

Worker.finalize 在 Task/Run 状态与事件提交的同一事务里调用 `project_terminal()`。COMPLETED 投影 final_answer；FAILED、CANCELLED、TIMEOUT、LIMIT_REACHED 投影结构化状态与 error_code。

RETRYING 和 WAITING_USER 不是终态，不投影成最终答案。直接取消路径和恢复判定失败路径也补上相同投影，避免只有正常完成才进入消息历史。

幂等性依靠 Run ID 与 kind，而不是正文相等。两次不同运行即使都回答“已完成”，也是不同来源，不能按正文合并。

### 64.8 迁移为什么只整理已有消息

迁移 `20260919_0006` 先新增可空字段，再按旧 messages 的 created_at/id 确定顺序，计算哈希并标记 backfill，更新每个 Session 的下一个序号，最后加上非空、唯一和外键约束。

旧 Task.history_before_sequence 保持 0。没有足够证据确定历史边界时，返回空历史比读到未来消息更可靠。

迁移不会把 run_events 中的文本增量或截断 tool 结果拼成“完整历史”。数据不足时应该明确保留缺口，而不是让后来提取的事实建立在伪造的来源上。

### 64.9 MemoryEntry 与 MemoryVersion 为什么分开

假设用户先要求“以后用中文”，后来改为“以后用英文”。这不是两个互不相干的事实，也不能覆盖原来那行正文后假装没有发生变化。

MemoryEntry 用 workspace、scope_key、fact_key 表示同一个事实身份，例如 `response.language`。MemoryVersion 保存每次候选正文、revision、内容哈希、类别、来源类型、有效期与 supersedes_version_id。

Entry.current_version_id 指向当前有效版本。复合外键 `(entry.id,current_version_id)` 指向 `(version.entry_id,version.id)`，因此不能把另一条事实的版本挂到这个 Entry 上。

### 64.10 版本里的置信度为什么不是概率证明

当前原话来源候选保存 `confidence=0.5` 和 `confidence_method=verbatim_source_quote_v1`。这个值只是带方法说明的候选元数据，不表示“有 50% 概率为真”，更不能据此自动确认。

真正的使用资格来自当前版本、confirmed 状态、作用域、有效期、正文哈希和来源校验。模型返回一个很高的 confidence 也不会改变这些条件。

### 64.11 Source、Event 和 Job 的职责

MemorySourceRecord 用真实 Message 外键、source_hash 和 locator 指向证据。当前不接受没有类型约束的“某个 source_id”；也没有开放从任意 Artifact 或助手文字建立事实。

MemoryEventRecord 记录 proposed、confirm、reject、revoke、erase 等操作的身份、actor 和原因码，正文不放入审计事件，以免清理事实后日志又保留一份正文。

MaintenanceJobRecord 保存 kind、去重键、冻结 payload、状态、owner、epoch、有效期、尝试次数和结果。它解决的是“这项维护工作由谁在何种租约下完成”，不是事实可信度。

### 64.12 不可变性与锁版本各解决什么

Version 的 ORM 更新监听器拒绝正文与身份字段原地修改；唯一例外是明确 erase，把正文置空并标记 erased。修改偏好必须创建新的 Version。

Entry.lock_version 则用于用户界面的并发决定。两个页面同时看到锁版本 0，只有一个操作可以把它更新为 1；另一个返回冲突，不能无声覆盖已发生的确认或撤销。

这些应用层保护不等同于数据库管理员不可修改数据。读取时仍复算哈希和来源资格，不能只因为记录曾经通过校验就永久信任。

### 64.13 本模块阅读顺序

```text
1. db/models.py 的 Workspace / Session / Message / Task
2. sessions/service.py 的 append_message()、history_for_task()
3. TaskService.create_task() 和 JobLeaseManager.finalize()
4. MemoryEntry / Version / Source / Event
5. migration 0006 的旧数据回填
6. test_message_projection_is_ordered_idempotent_and_history_is_frozen()
7. test_phase_four_migration_backfills_existing_messages()
```

---

## 65. 阶段四模块 5：事实提议、人工确认与遗忘

### 65.1 本模块的输入和输出

输入是有权威 Message 记录的用户原话，以及明确的管理操作。输出是 proposed 候选、人工决定后的当前版本、可追溯归档和可查询的维护任务。

它不负责向量检索，也不在每次 Agent 请求里自动加入长期记忆。当前提供词法查询与持久引用接口，自动注入属于模块 7。

### 65.2 为什么先验证来源再调用模型

`source_message()` 要求来源属于目标 Session，role=user、kind=goal、不是 backfill，正文哈希相符，所属 Run 已 completed。随后排除 EvalRun、待处理审批和 UNKNOWN 副作用。

这意味着失败任务中的一句“请记住”不会直接成为可提取来源；HOLDOUT 更不会通过换一个 API 进入普通记忆。当前实现保守排除所有评测来源，而不只排除 HOLDOUT。

提取 API 先做这些验证，再调用候选生成器。否则即使最终拒绝候选，不合格资料也已经被发给外部模型，后置过滤无法收回那次请求。

### 65.3 内容策略检查什么

`memory/policy.py` 检查常见凭据形式、私钥标记、忽略系统指令等注入模式，以及“本次”“这次”“this time”一类一次性意图。它们不能改写长期偏好。

候选正文还必须是来源原文中的真实子串。把“本次用中文”概括成“用户始终偏好中文”既违反一次性规则，也无法通过原文引用检查。

规则是保守入口，不是自然语言安全性的形式化证明。未来扩展新来源时应重新设计来源信任边界，不能简单减少正则规则来提高命中率。

### 65.4 确定性与 Model 生成器有什么不同

默认 `extract_proposals()` 只识别“请记住”“以后都”“always prefer”等明确长期意图，返回原文候选；没有长期意图时返回空集合，不猜测隐含偏好。

可选 `ModelMemoryExtractor` 使用专门的无工具请求。它最多返回 3 条候选，只允许 fact_key/content/kind，来源 Message ID 与 Session scope 由服务端补入。模型不能返回 confirmed、workspace_id 或任意来源 ID。

该调用不进入 AgentLoop，所以不会递归压缩或调用用户工具。它有 8000 字符输入/输出限制、1000 输出 Token、10000 本地估算预算和 10 秒期限；错误或超时不会自动无限重试。

启用方式为配置 `EVOAGENT_MEMORY_EXTRACTOR_MODEL` 及兼容 Provider。默认留空即可离线工作。Mock Provider 可以验证协议和边界，但不证明真实模型提取质量。

### 65.5 `propose()` 为什么不修改当前事实

服务端从 Session 得到 workspace_id，并将 scope 规范化为 `workspace` 或具体 Session ID。首次创建同一事实时通过 Workspace 锁串行化，找到已有 Entry 后再追加 revision。

新 Version 总是 proposed，记录其生成时看到的 supersedes_version_id。若 Entry 已有 confirmed 当前版本，提出新候选不会让旧版立刻消失。

同来源、同事实和同正文的有效候选会复用已有版本，重复点击不会制造无限版本。已 erased 的同一事实正文不会从旧来源直接复活。

### 65.6 人工确认的事务

```text
读取版本和 Session
→ 锁住 MemoryEntry
→ 检查 Workspace/Session scope
→ 检查状态转换是否合法
→ CAS expected_lock_version
→ 重新校验来源
→ 检查 supersedes_version_id 仍等于当前版本
→ 旧当前版本标记 superseded
→ 新版标记 confirmed，更新 current_version_id
→ 写不含正文的 MemoryEvent
→ COMMIT
```

CAS 解决同时操作同一 Entry 的冲突；supersedes 检查解决“这个候选提出之后，当前事实已经被别的版本改变”的冲突。两者不能互相替代。

确认权限只在本地可信管理 API，不作为模型工具注册。模型即使输出“用户已经确认”，也只能得到一个待人工决定的候选。

### 65.7 状态变化怎样理解

```text
proposed ──confirm──> confirmed ──被新版确认替换──> superseded
    │                    │
    ├─reject→ rejected   └─revoke→ revoked
    └─revoke→ revoked

可删除版本 ──erase 请求──> revoked + pending Job
pending Job ──清理成功──> erased（正文为空）
```

reject 表示候选未被接受；revoke 表示停止继续使用；erase 表示另外要求清理正文。这三者不能只用一个“删除”按钮背后的状态来表示。

状态终止后不能直接把同一 Version 改回 confirmed。新的事实应通过新的提议与确认，让来源和版本关系继续可追踪。

### 65.8 查询时为什么还要再次验证

管理列表展示候选及各版本；带 query 的查询只返回当前 confirmed、未过期、内容哈希正确、来源仍可验证的条目。Workspace 事实可在同一 Workspace 的其他 Session 使用，Session 事实不能跨会话。

`verify_version()` 还会检查来源会话属于同一 Workspace，并再次检查原始 Run 的资格。正文或来源被改动，旧 confirmed 标记不能掩盖这种变化。

当前词法匹配是 casefold 后按空白分词，任一词项出现在正文即可命中。它不提供向量召回、语义相似度或 BM25 排序，中文短语按给出的连续文本匹配。

### 65.9 SessionArchive 为什么不是 MemoryVersion

归档总结一段对话中发生了什么，里面可能有助手假设、临时目标或错误结论；长期事实则需要明确类型、scope、证据与人工确认。

`enqueue_archive()` 冻结当前末尾序号、来源消息哈希和配置，使用这些信息建立去重键。Worker 只处理这个范围，之后到达的消息不会被带入旧归档。

默认归档用受限原文摘录，最多处理 1000 条消息和 100 万字符，最终摘要最多 16000 字符。它不调用 MemoryService.propose，也不自动确认任何事实。

### 65.10 为什么维护必须使用持久 Job

如果使用 API 进程里的后台协程，进程重启后就可能永远不知道归档或删除进行到哪一步。MaintenanceJobRecord 把 pending/running/completed/failed 状态写在数据库里。

Worker 领取 pending 或过期 running，分配新的 owner 和递增 epoch，并记录 attempts。执行时检查 owner、epoch、expiry，完成前再次检查期限。旧执行者即使醒来，也不能提交新执行者已经接管的任务。

失败不显示完成。管理者可以读取 error_code，再显式 POST retry；GET 只读状态，不会因为刷新页面重新调用模型或删除文件。

### 65.11 已经装进上下文的记忆怎样撤销

后续检索模块需要调用 `bind_version()`，把实际采用的 version_id/content_hash 与 Run 绑定到 RunMemoryReferenceRecord。引用是持久记录，因此进程重启不会忘记自己采用过哪条记忆。

ContextStore 在每次请求前调用 `check_run_references()`。只要引用已经 revoked、erased、过期、被替代或来源失效，就返回 `context_source_revoked`，不能继续把旧内容交给 Provider。

持久化 Runner 开始恢复前也检查引用；Checkpoint 保存、事件正文、效果结果和终态处理有相应保护，避免撤销后迟到的响应重新写回已清理正文。

这阻止后续发送，不代表能召回已经发送的网络请求。已在外部发生的工具动作仍按效果账本处理，不能因为记忆删除就将 COMMITTED 改为“没有执行”。

### 65.12 erase 删除什么，保留什么

erase API 先提交 revoked 和持久 Job，此时查询已经不再使用该版本。Worker 随后清除指定版本正文、相关归档，以及绑定 Run 的 Snapshot/revision 正文、Artifact 文件和派生 Trace/结果正文。

身份、内容哈希、事件序号、状态与来源关系保留为墓碑，便于解释“这条引用为何不可用”，而不需要恢复被删除的正文。关联原始用户 Message 保留，它属于会话记录；本操作不是整段对话删除。

对包含已擦除来源的归档请求，系统拒绝重新生成，避免一边删除摘要一边又从相同旧来源创建副本。文件删除失败会使 Job failed，之后可重试。

数据库备份、WAL、旧文件备份、远端 Provider 和已发送消息不受本地在线清理控制。这里的 erase 不是物理介质安全擦除保证。

### 65.13 API 与服务的分工

| API | 服务职责 | 是否产生持久写入 |
|---|---|---|
| GET messages | 读取有序消息 | 否 |
| POST memories | 校验来源，创建 proposed 版本 | 是 |
| GET memories | 管理列表或有效词法查询 | 否 |
| POST decision | CAS 与人工状态转换 | 是 |
| POST memory-extractions | 有界候选生成，再执行 propose | 是 |
| POST archives | 冻结范围并入队 | 是 |
| GET archives | 读取已有归档 | 否 |
| GET maintenance-jobs | 查询处理状态 | 否 |
| POST maintenance-jobs/retry | 将 failed 任务重新入队 | 是 |

路由不自行拼接不受约束的数据库 scope。Service 负责业务事务，Repository 负责来源与资格验证，MaintenanceWorker 负责可恢复的维护执行。

### 65.14 本模块阅读顺序

```text
1. MemoryProposal / MemoryDecision
2. source_message() 与 policy.validate_content()
3. extract_proposals() / ModelMemoryExtractor.generate()
4. MemoryService.propose() / decide()
5. verify_version() / bind_version() / check_run_references()
6. enqueue_archive() 与 MaintenanceWorker.claim()/execute()
7. api/routes/memory.py
8. test_memory_api_confirm_query_revoke_archive_and_erase()
```

---

## 66. 阶段四模块 3～5 完整调用链

### 66.1 一次带大结果的长任务

```text
POST Task
→ Task/Run/goal Message 原子创建，冻结历史截止点
→ Worker 领取 epoch 租约
→ PersistentAgentRunner 冻结 v2 配置
→ 加载合法 Snapshot 或构建原始目标
→ AgentLoop 每轮调用 ContextStore
   → 验证持久记忆引用
   → 必要时摘录旧完整工具组
   → 文件先写，revision/事件/Snapshot 同事务提交
→ ContextPolicy 对最终请求再次计数
→ Provider
→ ToolExecutor
   → 执行工具
   → 完整脱敏结果归档
   → 短预览与 Artifact 引用
   → ToolEffect 记录结果
→ 保存工具边界 Snapshot
→ 下一轮或 final_answer
→ finalize 同事务投影 terminal Message
```

ContextStore 负责持久化变换；ContextPolicy 负责最后是否能发；Executor 负责工具执行与输出完整性。三个对象不能互相替代。

### 66.2 从用户原话到已确认事实

```text
已完成普通 Task 的 user goal Message
→ scope / hash / run / approval / effect / eval 检查
→ 内容门禁
→ 手动提议 / 确定性提取 / 可选 Model 提取
→ 逐条原文引用检查
→ proposed Version + Source + Event
→ 用户阅读候选
→ expected_lock_version + supersedes 检查
→ confirmed + current_version_id
→ 带 query 的 GET 每次重新验证后返回
```

没有从 Model 提取器直接到 confirmed 的路径，也没有从 Archive 直接到 confirmed 的路径。

### 66.3 从撤销到物理正文清理

```text
POST decision(action=erase)
→ 当前版本失效 + revoked
→ pending erase Job
→ 新查询不再返回
→ 已绑定 Run 在请求前检查失败
→ MaintenanceWorker 领取 owner/epoch
→ 清理版本正文、归档与绑定 Run 派生内容
→ 保留哈希/状态/来源墓碑
→ 成功 completed；失败 failed
→ POST retry 可恢复未完成清理
```

查询失效与清理完成有两个不同时间点。API 客户端应展示 revoked 与 Job 状态，而不是把入队成功显示为“所有数据已永久删除”。

---

## 67. 阶段四模块 3～5 测试地图

| 测试文件/场景 | 证明的行为 | 不证明的内容 |
|---|---|---|
| `test_memory_foundations.py` 消息投影 | 原子序号、终态幂等、固定历史截止点 | PostgreSQL 实际锁等待 |
| 同 Session 并发创建 | SQLite 下并发任务得到不同序号 | 高负载部署吞吐 |
| 候选确认与冲突 | proposed 不查询、CAS 拒绝旧决定、来源改动失效 | 用户输入的客观真实性 |
| unsafe/one-shot 参数 | 常见秘密、注入、一次性需求和非原话被拒绝 | 任意自然语言攻击均可识别 |
| Workspace scope | 同 Workspace 共享、跨 Workspace 不返回 | 公网认证/RBAC |
| revoke/erase | 引用失效、持久 Job、正文清理、禁止归档复活 | 远端及备份擦除 |
| archive range/dedupe | 固定来源范围、重复入队复用、新消息不混入 | 生成式摘要质量 |
| maintenance old epoch | 过期接管后旧执行者不能完成 | PostgreSQL 真实行锁证明 |
| output archive/scope/hash | 全量先归档、秘密脱敏、跨 Run 拒绝、哈希校验 | 未登记孤儿自动清理 |
| context commit/restore | 原约束与计数保留、v2 恢复、坏 Artifact 拒绝 | 真实 Provider Token 精确计量 |
| crash before commit | revision/ArtifactRecord/Snapshot 回滚，旧快照有效 | 文件和数据库全局原子性 |
| `test_memory_api.py` | HTTP 提取→确认→查询→归档→erase 完整链路 | 外部模型效果 |
| `test_memory_extraction.py` | 无工具请求、输出预留、禁止虚构和自行确认 | 真实服务计费/召回质量 |
| `test_migrations.py` | 空库往返迁移、metadata 对齐、旧消息回填 | 未配置的 PostgreSQL 实机验收 |
| 原有 Worker 进程故障测试 | 新默认 v2 下独立进程接管仍可运行 | 外部动作 exactly-once |

当前可重复的命令与最新数字见[模块 3～5 验收说明](阶段四-模块3至5验收与运行说明.md)。历史章节中的旧测试数字不覆盖本次增量。全量回归必须包含原有 Skill/Eval 流程，不能只运行新增记忆测试就认为阶段三没有回退。

---

## 68. 阶段四模块 3～5 学习验收

建议结合具体记录和测试回答以下问题：

1. 为什么保存短 ToolResult 不能替代完整输出归档？
2. `artifact_read` 为什么接受 ID 而不接受路径和任意 Run ID？
3. 摘录的 group_id、source_index、source_hash 分别指向什么？
4. 为什么完成步骤和待办步骤不能从工具文字中自动当作执行事实？
5. 压缩 80% 触发和 60% 目标分别相对于哪一个预算？
6. 为什么原始用户约束和最近完整工具组不能随意删除？
7. 摘录达不到目标时，谁决定原请求能不能继续发？
8. 文件已写但事务未提交时留下什么，模型为什么不能通过 ID 读到它？
9. 事务已提交但进程未收到返回时，恢复如何获得同一版上下文？
10. Usage 和重复调用次数为什么必须随 Snapshot 一起保存？
11. 大结果归档使用随机 UUID，为什么重复调用指纹仍然需要稳定？
12. 为什么旧 canonical hash 保持不变仍不意味着活动 v1 可以直接升级？
13. 逻辑 Workspace 与文件系统工作目录分别限制什么？
14. `max(sequence)+1` 在两个并发创建请求中会怎样失败？
15. history_before_sequence 为什么使用严格小于？
16. 重复 terminal 投影消耗序号但不增加消息，是否违反排序保证？
17. backfill 为什么不能直接成为新的可靠事实来源？
18. Entry、Version、Source、Event 分别回答哪一个问题？
19. composite current-version 外键怎样防止挂错事实？
20. lock_version 与 supersedes_version_id 各防止哪一种冲突？
21. 模型输出的 confirmed 字段为什么必须被拒绝？
22. 先验证来源再调用提取器，与调用后过滤有什么不同？
23. confirmed 版本在查询时还可能因哪些原因失效？
24. SessionArchive 为什么不能自动转换成长久事实？
25. 归档入队之后新到达的消息为什么不进入这个任务？
26. Maintenance epoch 与状态字段怎样共同拒绝旧执行者？
27. revoke 成功和 erase Job completed 分别能向用户承诺什么？
28. 为什么 erase 保留哈希、来源和状态墓碑，但不保留事实正文？
29. 已经发送给 Provider 的内容是否能被后来的 revoke 收回？
30. 当前词法查询、未来混合检索与自动注入分别位于哪个模块？

能沿着 Task、Run、Message、ContextRevision、MemoryVersion 和 MaintenanceJob 回答这些问题，才说明理解了三个模块之间的约束：上下文变化必须可恢复，来源必须可复验，记忆必须经过明确决定并可以停止使用。

以上是模块 3～5 的历史完成边界。后续模块 6～7 已实现 Embedding/pgvector 代码、混合召回和显式启用的 Memory 注入，详见下文；MCP、Redis 和 Memory 管理前端仍未实现。

---

## 69. 阶段四模块 6：Embedding 契约、派生索引与代次切换

### 69.1 这一模块解决什么问题

模块 5 能回答“这条事实是否经过确认，当前是否还能使用”，却不能很好地处理词面不同的查询。例如资料里是“交通工具”，查询里是“代步方式”，单靠 BM25 未必能找到。

Embedding 把一段文本映射为定长数值向量，使语义接近的文本有机会在距离上接近。它新增的是召回能力，不改变确认规则。即使某条向量与查询完全相同，来源已经撤销时也不能使用。

这也是本模块把权威正文、索引文档、向量、维护任务分开的原因：模型可以换，索引可以重建，但原始事实的版本和决定不能被重建过程覆盖。

### 69.2 建议按什么顺序阅读

| 文件 | 先读的对象 | 负责什么 |
|---|---|---|
| `retrieval/embeddings.py` | `EmbeddingProfile`、`EmbeddingResult`、`validate` | 定义输入输出与模型空间身份 |
| 同上 | `MockEmbeddingProvider`、`OpenAIEmbeddingProvider` | 离线替身与真实 HTTP 适配 |
| `retrieval/sources.py` | `Source`、`load_source` | 从权威表读取当前允许的来源 |
| `db/models.py` | 四个索引模型 | 来源外键、唯一键、活动 generation |
| `retrieval/vector.py` | `PGVector`、`exact_distances` | 原生向量列和精确距离查询 |
| `retrieval/indexing.py` | `enqueue_source`、`IndexService` | 失效、入队、计算、复验、切换 |
| `memory/maintenance.py` | `claim`、`execute`、`run_once` | 维护任务租约与失败落账 |
| `workers/bootstrap.py` | Worker 装配 | 注入并关闭 EmbeddingProvider |
| `20260920_0007_embedding_and_frozen_retrieval.py` | upgrade/downgrade | 建表、扩展与测试库迁移 |

先看契约，再看数据关系，最后看 Worker。直接从 HTTP 客户端开始，容易把“接口返回向量”误当成索引系统已经完成。

### 69.3 为什么 EmbeddingProvider 独立于 Chat Provider

Chat Provider 消费消息、工具 Schema，并产生文本和工具调用流；EmbeddingProvider 消费字符串批次，返回数量对应的向量。两个接口的单位、失败方式与计费边界不同。

```python
EmbeddingProfile(
    model="mock-hash-v1",
    dimension=1536,
    preprocessing="text-v1",
    metric="cosine",
)

await provider.embed(("以后都使用中文回答",), profile)
# EmbeddingResult(vectors=(...), model="mock-hash-v1", usage=0)
```

`EmbeddingResult.usage=None` 表示服务没有报告用量。维护任务把已知批次用量相加，只要任一批未知，总用量也保持未知。Mock 返回 0，因为没有调用收费服务；不能据此估计真实模型成本。

真实实现请求 `/embeddings`，显式发送 `model`、`input`、`dimensions=1536`、`encoding_format=float`。响应根据 `index` 排序后检查完整序号，再校验整个结果。它使用独立的 Embedding Key/Base URL，不偷偷复用 Chat 凭据。

### 69.4 为什么要整批校验

假设输入 A、B、C，服务只返回两个向量。如果直接 `zip(inputs, vectors)`，程序可能把第二个向量错误绑定给 B，甚至静默丢弃 C。数量对得上也不充分：重复 index 或模型标识不符同样不能接收。

当前依次检查：

1. 输入批次 1～16 条，每条非空且不超过 12000 字符。
2. Profile 为固定 1536 维、cosine、text-v1。
3. 返回 model 与请求身份相同，数量完全相等。
4. 每个向量恰好 1536 项。
5. 数值不能是 bool、NaN、Infinity 或超出 float32 范围。
6. 至少有可表示的非零分量，避免余弦分母为零和落库后全零。

所有批次校验完成后才开始最终写事务。这样第二批失败不会留下第一批的半成品；重建也不会把不完整 generation 标成 active。

### 69.5 Mock 到底模拟了什么

Mock 对通用 tokenizer 产生的词做 SHA-256，再把词频放到确定的 1536 个位置。相同输入、相同代码得到相同向量，不需要网络。

它能验证维度、幂等写入、查询排序、预算、冻结与故障处理，但不是训练得到的语义模型。测试中用手工指定的相近向量演示“同义词无词面重合也能进入融合结果”，只证明排序算法支持这种输入，不能证明 Mock 理解同义词。

### 69.6 四张索引表各保存什么

| 表 | 身份或关键约束 | 数据职责 |
|---|---|---|
| `embedding_profiles` | model 唯一，dimension=1536 | 模型空间、预处理、距离、active/next generation |
| `index_generations` | profile_id + generation 唯一 | building/active/retired 与来源 manifest |
| `retrieval_documents` | source_key 唯一，三个来源 FK 恰好一个非空 | scope、来源 hash、输入 hash、active |
| `document_embeddings` | document/profile/generation/input_hash 唯一 | 派生向量 |

`source_key` 形如 `memory:<version UUID>`，方便统一调用，但关系完整性依靠显式外键。exactly-one 约束防止一行同时挂到 Skill 和 Memory，或完全没有权威来源。

Profile 的模型、维度、距离和预处理身份受 ORM 不可变保护。只允许活动代次等运行字段更新。数据库外部管理员直接修改表不属于应用层不可变保护的承诺范围。

### 69.7 source_hash 与 input_hash 不能合并

`source_hash` 校验权威正文；`input_hash` 校验实际送入 Embedding 的字符串。

对 Memory，检索文本是事实正文；对 Skill，是 name、description 和 triggers 的组合，最终注入的规程还包括步骤等内容；对 Archive，是摘要文本。首版每个来源一个文档，索引输入取前 12000 字符。

因此同一份来源有至少三种视角：完整权威内容、用于召回的文本、实际渲染的注入文本。模块 7 还会保存第三种文本的 hash。一个“内容哈希”不能含糊地代表这三个对象。

### 69.8 为什么不开放任意向量维度配置

迁移定义的是 `vector(1536)`。如果只把配置改成 3072，却继续写入相同列，数据库会拒绝；若把列改成不定长，又可能将不可比较的向量混在一起。

即使两个模型都是 1536 维，也可能使用完全不同的坐标空间。因此查询必须同时约束 profile 和 generation，不能只比较长度。

当前模型切换需要设置模型身份并为新 Profile 重建。以后支持其他维度，应增加明确迁移和对应数据结构；本次没有用 JSON 列规避 PostgreSQL 的维度约束。

### 69.9 PostgreSQL 与 SQLite 的两条路径

`PGVector` 是 SQLAlchemy 自定义类型，PostgreSQL DDL 为 `VECTOR(1536)`，绑定参数序列化为向量文本。迁移先执行 `CREATE EXTENSION IF NOT EXISTS vector`。

生产查询使用：

```sql
embedding <=> CAST(:query_vector AS VECTOR(1536))
```

距离越小越接近。查询还限制允许文档、Profile、generation、Document active 和当前 input_hash；它不负责扩展权限，也不为了填满 Top-K 去掉 WHERE 条件。

SQLite 路径把向量保存为 JSON，由 Python 计算 cosine distance。它验证应用分支和数据生命周期，但没有 PostgreSQL 类型、算子、扩展和真实行锁。测试报告必须把这两类证据分开。

### 69.10 为什么业务事务中只入队

确认事实时，如果先提交 confirmed，再单独入队，进程可能在两个操作之间退出，留下永远没有索引任务的事实。反过来，先入队再提交事实，Worker 可能看到还没确认的数据。

`enqueue_source` 与业务决定处于同一事务。Memory 的 confirm/revoke/erase、Skill 的 publish/rollback/disable、归档完成都会产生维护任务。替换事实还会使旧版本的索引失效。

该函数也立即把现有 Document 标为 inactive 并删除向量。撤销无需等待后台 Worker 才生效；没有向量的合法新事实仍可通过词法路径读取。

### 69.11 IndexService.execute 的三个阶段

```text
短事务 A
  验证 Job owner/epoch/expiry
  读取 Profile、generation 和合法来源
  冻结本次输入及 hash
结束事务 A

事务外
  分批调用 EmbeddingProvider
  15 秒请求上限
  校验完整返回结果

短事务 B
  重新验证 Job owner/epoch/expiry
  锁定 Profile；来源父记录加锁并刷新
  重新验证来源状态、source_hash、input_hash
  幂等写入 Document/Embedding
  必要时切换 generation
  提交前再检查租约有效期
  完成 Job 并提交
```

网络等待期间不持有数据库事务。事务 B 不能只相信事务 A 中读到的对象，因为调用期间可能发生撤销或新版本发布。

已失效的增量来源不会写入向量；重建则要求整个 manifest 仍满足条件。前者允许单项失效后完成无写入任务，后者不能把缺来源的构建误记为完整成功。

### 69.12 重复、晚返回与旧代次分别怎样处理

重复消费通过向量唯一键避免重复写同一 input；完成的 Job 不再被 claim。不同业务状态变化有不同 Job，不能按 source_key 把所有变化合并成一个永久任务。

旧 Worker 晚返回会遇到 owner/epoch/expiry 校验失败。即使它持有相同来源文本，也没有权利完成已被接管的 Job。

增量任务算向量期间，如果活动 generation 已变化，最终事务拒绝向旧代提交。显式重试会重新读取活动代。旧 rebuild 的 generation 若已经落后于当前活动代，同样拒绝倒退切换。

### 69.13 重建为什么先保存 manifest

`queue_rebuild` 在 Profile 锁下分配新代，并把当时合法来源的 key/hash 保存到 IndexGeneration。重复提交会复用同 Profile 的 pending/running rebuild。

构建开始验证 manifest，计算结束再验证来源。只有完整结果和活动指针一起提交，查询才读到新代。失败时旧代继续可用，不会先清空旧索引再等待新向量。

首版重建最多 64 个合法来源，每批 16 个。超限返回 `index_batch_limit`，需要未来实现分页重建后再扩大规模；不能取前 64 个却宣称全量完成。构建期间检测到来源集合变化时，重新排一个 rebuild，而不是修改原 manifest。

该策略面向小规模受控数据。重建后的新发布仍由同事务增量 Job 跟进；索引是最终一致的，查询层必须能处理 `partial_index`。

### 69.14 失败如何留痕与重试

MaintenanceWorker 保存 failed、error_code、attempts 和 `next_attempt_at`。后者当前是建议的重试时间，不是已经安装了自动重试调度器。

`POST /maintenance-jobs/{id}/retry` 只接受 failed，清除旧错误和建议时间后重新排队。网络恢复等临时故障可重试；`rebuild_manifest_changed` 需要重新提交 rebuild，重复旧任务不会自动更新 manifest。

取消向上传播，未完成 running 任务在租约到期后可接管。索引完成不代表普通 Run 已开启混合检索，读取策略是下一模块单独控制的开关。

---

## 70. 阶段四模块 7：混合检索、分区预算与选择冻结

### 70.1 本模块新增了哪一层

`ContextResolver` 位于持久化 Runner 与检索算法之间。它接收 Task/Run，负责获得允许候选、召回、预算选择、持久化冻结结果，并返回实际可构造上下文的文本。

算法文件 `lexical.py` 和 `hybrid.py` 不读取业务表，也不决定权限。来源层负责“允许使用什么”，算法负责“允许集合里什么更相关”，Resolver 负责“本次到底注入什么”。

### 70.2 新旧链路如何选择

| Run 与配置 | 执行路径 |
|---|---|
| retrieval，lexical，Memory/Archive 都关闭 | 原 SkillRetrievalService |
| retrieval，hybrid | ContextResolver，Skill 两路召回 |
| retrieval，Memory 开启 | ContextResolver，允许已确认事实；backend 决定是否向量召回 |
| retrieval，Archive 开启 | ContextResolver，同会话历史归档候选 |
| baseline | 不自动注入 Skill/Memory/Archive |
| pinned_skill | 原内部固定 Skill 路径，不自动加入长期记忆 |

配置默认不改变旧任务的词法 Skill 行为。新的选择冻结链路要求 Snapshot v2；纯内存 CLI 没有持久化 Batch，不承担这里的恢复保证。

### 70.3 先过滤有什么实际意义

设当前会话允许的事实向量距离为 0.2，另一会话的私有事实距离为 0.0。如果先在全库取最近一条，再检查权限，会丢掉唯一候选；开发者容易为了填满结果继续扩大无权限检索。

当前实现先得到允许的 source 集合，再把对应的 Document ID 传给向量 SQL。另一会话的私有事实不会进入召回集合，也不会作为“被过滤的高分候选”暴露在审计 API 中。

### 70.4 三类来源的硬条件

| 来源 | 初次选择必须满足 |
|---|---|
| Skill | enabled、当前 active 指针、active 版本、定义 hash、DSL 校验、工具集合和风险兼容 |
| Memory | 同 Workspace，可见 Session 或 workspace scope，confirmed、current、未到期、正文及原话来源有效 |
| Archive | active，同 Session，end_sequence 严格小于 Task.history_before_sequence，来源 hash 有效，来源不是 Eval |

缺少来源、状态不允许等记录不会进入候选。对于当前作用域下已经确认的正文 hash 损坏，检索直接失败；它不是“向量服务不可用”，不能通过 BM25 降级掩盖。

Archive 只是低可信历史，不能自动变成 confirmed Memory。Eval 来源也不能通过先归档再索引绕过普通记忆的来源限制。

### 70.5 通用 BM25 为什么仍保留旧 tokenizer

`lexical.py` 抽出原 Skill 检索的分词和 BM25，`skills/retrieval.py` 保留生命周期选择和结果包装。这样增加 Memory 候选无需复制另一套打分公式。

英文按词，中文保留单字和相邻双字特征；同分按稳定 ID 排序。`k1=1.5`、`b=0.75` 控制词频饱和与文档长度归一化。它没有新增分词模型，也不声称能进行中文语义理解。

### 70.6 RRF 如何把两路结果放到一起

BM25 分数与余弦距离单位不同，不能直接相加。RRF 使用各自名次，默认两路等权：

```text
score(d) = 1 / (60 + lexical_rank(d))
         + 1 / (60 + vector_rank(d))
```

只在某一路出现时只加该项。比如 A 的词法名次 1、向量名次 2，得分为 `1/61 + 1/62`；B 只在向量路名次 1，得分为 `1/61`。这是相对排序信号，不是 A 的事实准确概率。

每路先过门槛：默认 BM25 ≥ 0.1、向量距离 ≤ 0.35，然后各取 Top-100。没有任何一路达标就不会入选，避免低相关资料仅靠“总有一个第一名”进入上下文。

同分按来源键排序，其中包含稳定版本 ID。阈值、RRF k、Top-N 与算法版本都记录到 Batch config，不能只保存最终分数而丢掉产生它的条件。

### 70.7 相同 scope 的 BM25 降级

| degraded | 触发条件 | 后续行为 |
|---|---|---|
| `index_not_ready` | 缺 Profile 或合法索引文档 | 使用已过滤的词法候选 |
| `vector_unavailable` | Query Embedding/向量查询失败或超时 | 使用已过滤的词法候选 |
| `partial_index` | 活动代只有部分候选向量 | 已有向量参与，其他候选仍可走词法 |
| 空值 | 正常或没有候选而无需向量 | 按当前配置冻结 |

初始来源验证在降级处理之外。SQL 降级也只包围向量查询阶段，不把读取权限表失败当成“可以忽略权限”。

事件使用 `retrieval.degraded` 或 `retrieval.frozen`，记录 Batch ID、数量和原因。正式性能/效果比较必须单独识别降级运行；本模块没有交付检索效果评测器。

### 70.8 召回命中为什么不等于实际注入

Resolver 对排序结果逐条复验，再依次执行三个门槛：

1. 所属分区的 Top-K。
2. 所属分区的 Token 预算。
3. 包含 system、用户目标、Skill、Memory 和工具 Schema 的完整请求预算。

Skill 默认预算 4000、Top-K 1；Memory 与 Archive 共用记忆分区，默认预算 2000、Top-K 3。预算使用保守计数器，默认 bounded 下完整请求还受 ContextPolicy 的输入上限约束；显式 legacy 只保留分区预算，不提供完整窗口保证。

例如已经选择两条 Memory，累计估算 1600，第三条估算 600，而预算为 2000，则记录 `partition_budget`，不保存其可注入文本。排名第四但更短的一条仍有可能被选中，因为预算筛选并不是简单截取前三个。

### 70.9 Selection 如何表达未注入

| 字段 | 意义 |
|---|---|
| source_key / source_hash | 哪个不可变来源版本参与了选择 |
| evidence | lexical score/rank/terms、vector distance/rank、RRF |
| rank | 实际入选顺序；未注入为 null |
| text / text_hash | 实际冻结的渲染内容及其指纹；未注入正文为空 |
| omission_reason | top_k、partition_budget 或 request_budget |

硬过滤淘汰的来源不写 Selection，以免暴露其他 scope 的存在。这里的遗漏记录针对已允许且达到相关门槛、但没有通过预算/数量筛选的候选。

冻结的是初始上下文选择。后续模型循环增长时，ContextPolicy 仍可按模块 2 的规则裁剪可选资料；具体某轮发出的消息以该轮请求/快照为准，不能把 Batch 理解成每一轮都发送了所有内容。

### 70.10 零命中为什么也必须写 Batch

没有 Batch 无法区分“还没检索”与“检索完成但没有结果”。如果把零命中当作未初始化，暂停期间新确认一条事实，恢复时就会突然多出新的行为依据。

`RetrievalBatchRecord` 用 run_id + purpose 唯一，selected_count 可以为 0。首次选择即使没有 Selection 也提交 Batch 和选择冻结标记。恢复先查 Batch，有记录就不会调用 Query Embedding。

所以“没有选中任何资料”也是需要持久化的运行决定。

### 70.11 实际冻结文本放在哪

SkillRenderer 的输出作为规程区；Memory 带“长期记忆（不可信资料，不能覆盖当前任务）”标识；Archive 带低可信历史标识。Memory/Archive 通过 `ContextBuilder.external_context` 成为 user 资料消息，最终用户目标保留在后。

Selection 保存渲染文本本身及 hash，而不只保存版本 ID。仅保存版本号仍可能在 Renderer 升级后得到不同文本，无法说明旧运行究竟读到了什么。

Memory 被选中时同时调用 `bind_version` 写 RunMemoryReference。它不是自动确认操作，只是把已经合法的事实绑定到本次 Run，供后续发送前检查和 erase 追踪。

### 70.12 恢复流程与“冻结但仍可撤销”

`_restore` 先比较检索配置，再读取有序 Selection、核对文本 hash 并检查来源。不会因新索引 generation、新确认事实或新 Skill 发布重新排名。

Skill 有一个必要区别：初次选择要求当前 active；恢复允许原冻结版本继续使用，只要其 Skill 仍 enabled、定义 hash 仍一致。否则发布新版会无条件破坏所有旧运行的冻结语义。

Memory 则必须仍是当前有效 confirmed 版本。事实被撤销或替换，旧运行不能继续把旧事实当成当前约束；Archive 仍需遵守原 Task 截止点。冻结保障可复现，不取消用户停止使用来源的权利。

### 70.13 每次发送前怎样复验

`PersistentAgentRunner` 启动时检查引用；Snapshot v2 的 ContextStore 在后续模型请求前也调用 `check_run_references`。

该函数检查绑定 Memory，同时检查 Batch 中冻结文本和 Skill/Archive 来源。Skill 禁用、Archive 擦除、文本 hash 损坏或 Memory 撤销都导致 `context_source_revoked`，不会换另一条资料来“补足”旧选择。

这只能阻止后续发送，不能撤回已经发给远端 Provider 的请求。发送前验证与外部网络动作之间也不具备跨系统事务，不能将它描述成远端数据删除保证。

### 70.14 erase 为什么还要追踪 Archive 引用

一条事实的原始消息可能进入归档，归档又被后续 Run 注入。若只清理 RunMemoryReference，后续 Run 的 Selection/Snapshot 仍可能保存同样的文字。

维护 Worker 从直接绑定的 Run 和原始消息出发，寻找覆盖这些消息的 Archive，再寻找选中了这些 Archive 的 Run，沿引用关系扩展到没有新归档为止。所有受影响 Run 的派生正文采用保守清理。

被清理对象包括冻结文本和证据、向量、终态答案、请求/响应摘要、快照、上下文 revision 和 Artifact 正文。状态、hash、关联 ID 等墓碑保留用于解释为什么内容不可恢复。

原始用户消息、已发往外部服务的内容和外部备份仍不属于此 API 的物理擦除范围。文件清理失败保留 failed Job，可重新执行；不要将入队响应当成全部删除完成。

### 70.15 完整 Skill 列表怎样进入运行指纹

旧 RunConfigSnapshot 的单个 skill_version_id 只能表达第一条 Skill。现在新增有序 `selected_skills`，保存每个版本 ID 和 content_hash；retrieval 保存算法、预算、profile/generation、降级状态和 selection_hash。

第二条 Skill 改变或顺序改变都会改变 content_hash。原单 ID 字段保留，便于历史记录读取和既有报告显示，但不再承担完整选择的唯一身份。

新字段为 None 时从 canonical_dict 省略，历史 hash 不因升级自动变化。新 Runner 保存完整列表，旧活动配置与新语义不一致时仍返回 snapshot_incompatible，不偷偷回填一个看似兼容的列表。

原配对评测的 comparison hash 排除 Skill 实验变量，包括完整列表；其他配置仍要一致。新 retrieval 条件不被随意排除，因此混合与词法不自动成为同一实验条件。

### 70.16 只读证据接口与显式维护接口

`GET /retrieval/profiles` 查看模型身份和活动代；`GET /runs/{id}/retrieval` 查看批次、数量、排名、hash 与遗漏原因，不回传冻结正文，也不触发 Embedding 或写 Job。

`POST /retrieval/rebuild` 才会创建维护任务，返回 Job ID；任务状态和重试复用 `/maintenance-jobs`。原 `/memories?query=` 保留简单查询语义，不在 GET 请求中悄悄确认或索引事实。

接口仍用于项目现有的本地受控服务，没有在本模块新增公网认证/RBAC。检索内部 scope 过滤不能替代 API 的用户身份认证。

---

## 71. 阶段四模块 6～7 完整调用链

### 71.1 从确认事实到可检索向量

```text
MemoryService.decide(confirm)
→ CAS / 来源与版本检查
→ current_version 指向 confirmed
→ enqueue_source（同事务；替换时旧版立即失效）
→ MaintenanceWorker.claim（owner / epoch / expiry）
→ IndexService.execute：读合法正文、冻结本次输入
→ 结束读取事务
→ EmbeddingProvider.embed + 整批 validate
→ 新事务复验 Job、Profile 和来源
→ Document + Embedding 幂等写入
→ Job completed
```

如果 Provider 故障，事实仍已确认，只是向量索引未就绪。查询不能把“缺少向量”解释为“事实不存在”。

### 71.2 从任务到冻结上下文

```text
Task 固定 session / history_before_sequence
→ Worker 领取 Run 租约
→ PersistentAgentRunner 检查 run_mode 和配置
→ ContextResolver：优先读取已有 Batch
→ 初次选择：load_source 硬过滤
→ BM25 + 可选 Query Embedding / 精确向量距离
→ 各路阈值 + Top-100 + RRF
→ Guard 检查 + 来源加锁复验
→ 分区 Top-K / Token / 完整请求预算
→ Batch + Selection + Memory 引用 + Skill 选择 + Event 同事务提交
→ RunConfigSnapshot 保存完整配置指纹
→ ContextBuilder：Skill 规程，Memory/Archive 低可信 user 资料
→ 每轮 ContextStore / ContextPolicy 复验与预算
→ ModelProvider
```

### 71.3 从失败恢复到继续使用同一选择

```text
RecoveryService / 新 owner 与 epoch
→ Runner 读取旧 Run
→ 找到 Batch（包括 selected_count=0）
→ config / text hash / 来源状态复验
→ 不调用 Embedding、不重新排名
→ 校验 RunConfigSnapshot 与原 Snapshot
→ 复用冻结上下文与执行进度
```

新发布影响下一次初始选择；撤销影响当前选择能否继续使用。这两个方向不能混为一谈。

### 71.4 从显式重建到原子切换

```text
POST rebuild
→ Profile 锁下分配 generation
→ 保存来源 manifest + pending Job
→ Worker 领取，旧 generation 继续可读
→ 事务外分批生成向量
→ 检查 manifest、来源状态、租约与活动代
→ 写新代 + 改 active_generation + 完成 Job，同事务提交
```

失败时活动指针不动；构建期间已冻结的 Run 也不重新选择。索引更新改变未来召回，不改写过去的运行依据。

---

## 72. 阶段四模块 6～7 测试地图与故障定位

### 72.1 新增测试分别证明什么

| 测试文件/场景 | 证明的行为 | 证据边界 |
|---|---|---|
| `test_embeddings_and_hybrid.py` 非法向量参数 | 数量、模型、维度、有限值、零向量、float32 边界拒绝 | 没有真实服务质量结论 |
| HTTP MockTransport | 完整 index 校验、部分失败、取消传播 | 不是外网调用或真实计费 |
| 人工同义向量/RRF | 词法未命中仍可由合法向量路进入，噪声被阈值排除 | 不代表 Mock 能理解同义词 |
| 通用 tokenizer/BM25 | 中文特征和稳定同分顺序 | 不引入新的语言模型 |
| `test_hybrid_retrieval.py` 索引与恢复 | 后续恢复不再次调用 Provider | SQLite 生命周期测试 |
| 零命中后新增事实 | 负选择不会漂移 | 不能证明外部数据库行锁 |
| 撤销发生在 embed 内 | 返回的旧向量不能复活来源 | 模拟竞态交错 |
| 跨 Session 候选 | 私有事实不进入 Query Embedding 路径 | API 身份认证不在范围内 |
| 重建成功/失败 | 成功切代，失败保留旧代 | 本地事务行为 |
| 旧 maintenance epoch | 接管后旧执行者无权完成 | PostgreSQL 并发还需实机 |
| 分区预算 | 未注入候选有 omission_reason，正文不保存 | 估算 Token 不等于真实用量 |
| Runner retrieval/baseline | 真实构造的请求只在 opt-in 普通 Run 包含低可信 user 记忆 | 使用 Mock Chat Provider |
| Archive erase | 冻结文本、向量和派生快照清理，发送前复验失败 | 不覆盖远端及备份删除 |
| `test_run_config_and_manifest.py` | 第二条 Skill、顺序和完整列表影响运行身份；旧 hash 稳定 | 配置读取兼容不等于自动续跑 |
| `test_migrations.py` | SQLite upgrade/downgrade/check、旧数据迁移 | PostgreSQL 项未配置时跳过 |
| `test_pgvector.py` | 真实 vector(1536)、余弦算子、generation 过滤、错误维度拒绝 | 本机未执行；需 PostgreSQL+vector |

完整结果见[模块 6～7 验收与运行说明](阶段四-模块6至7验收与运行说明.md)。新增测试之外，还要运行原有 Skill 发布/评测、上下文恢复和 Worker 故障测试，确认公共 tokenizer 与配置指纹没有破坏旧闭环。

### 72.2 出问题先看哪张表

| 现象 | 首先检查 | 常见解释 |
|---|---|---|
| 确认成功但没有向量 | maintenance_jobs | Worker 未运行、Provider 失败、旧 epoch |
| 向量存在却没有结果 | profile/generation、来源状态 | 查的是其他模型空间，或来源已失效 |
| 命中但没有注入 | retrieval_selections | top_k/partition_budget/request_budget |
| 恢复仍是零命中 | retrieval_batches | 这是冻结结果，需新 Task 才重新选择 |
| 重建一直失败 | manifest、error_code | 来源已变，应该重新排 rebuild |
| 配置改动后不能恢复 | RunConfigSnapshot | 可复现性保护触发 snapshot_incompatible |
| 撤销后运行失败 | Memory/Archive/Skill 状态 | context_source_revoked 是预期拒绝 |
| PostgreSQL 迁移报 vector 不存在 | 镜像、扩展权限 | 普通 PostgreSQL 安装不自带此扩展 |

不要通过改 active 指针、手工删 Batch 或改来源 hash 来“修复”上述现象。这会破坏审计与恢复依据；正常操作是修复外部故障、显式重试或创建新的 Task/重建代。

---

## 73. 阶段四模块 6～7 学习验收

结合源代码与测试回答以下问题：

1. 为什么距离为零的 Memory 仍可能不能进入候选？
2. EmbeddingProvider 与 Chat Provider 的批次、输出和取消语义有什么不同？
3. 为什么返回数量正确还要校验响应 index？
4. 全零向量和 float32 下溢分别会造成什么问题？
5. 相同维度为什么不能跨模型直接比较？
6. source_hash、input_hash、text_hash 分别对应哪段内容？
7. source_key 已有类型前缀，为什么还需要显式外键和 exactly-one？
8. 为什么确认事实和 index Job 要在同一事务？
9. 为什么不能在 Embedding 网络调用期间一直持有数据库锁？
10. 撤销发生在网络等待期间，哪个检查阻止向量复活？
11. Job 唯一键、向量唯一键与 epoch 各解决哪一类重复？
12. 增量任务生成期间切换 generation，应如何处理？
13. rebuild manifest 为什么不能在重试时自动替换成最新来源？
14. 64 个来源上限为何必须失败而不是截断后成功？
15. SQLite JSON 测试不能证明 PostgreSQL 的哪些能力？
16. 普通任务在什么配置下继续走旧 SkillRetrievalService？
17. 为什么硬过滤要发生在向量 Top-N 之前？
18. Archive 为什么必须使用 Task 原来的历史截止点？
19. 为什么 BM25 分数与余弦距离不能直接相加？
20. RRF 两路都排第一，是否说明该事实更可信？
21. 阈值若放到融合后，会产生哪一种低相关入选问题？
22. Top-N、分区 Top-K 和 Token 预算分别限制哪个阶段？
23. 为什么排在后面的短资料可能比前面的长资料更适合注入？
24. selected_count=0 与不存在 Batch 有什么不同？
25. 为什么冻结文本而不仅冻结版本 ID？
26. 新 Skill 发布与 Skill 禁用对恢复分别有什么影响？
27. Memory 被选中为什么还需要 RunMemoryReference？
28. 一条事实经 Archive 被另一 Run 使用，erase 如何找到这个派生引用？
29. 为什么第二条 Skill 与列表顺序也必须进入运行指纹？
30. 历史 canonical hash 兼容为什么不等于旧活动快照能自动续跑？
31. baseline/pinned Skill 为什么不受普通记忆开关影响？
32. next_attempt_at 与自动重试调度之间有什么区别？
33. 何时可以 BM25 降级，何时必须直接拒绝？
34. Batch 为什么不能证明每一轮都发出了全部初始资料？
35. 要证明真实同义召回有改善，还缺哪些数据与执行证据？

能沿来源生命周期、索引 generation、冻结 Batch 和模型请求四个层次解释这些问题，才说明理解了两个模块的协作方式。模块 8 的 MCP 连接与目录发现现已实现，见第 74～77 章；PostgreSQL、容器和真实检索效果验收仍待补。

---

## 74. 阶段四模块 8：MCP 连接、发现与目录版本

### 74.1 为什么先实现发现，再实现调用

此前 EvoAgent 的工具由本地 Python 类提供：参数模型、风险和实现来自当前仓库。MCP Server 则可以在运行时告诉客户端“我提供了哪些工具”，工具名称、描述和 Schema 都可能由远端改变。

如果把 `tools/list` 的返回值直接放进 ToolRegistry，远端的一次目录更新就可能改变模型可调用的能力，甚至把原先审核过的同名工具换成另一种行为。

本模块先建立连接和目录证据。它能回答“连接到哪一个预设 Server、看到哪一版目录、谁审核过什么”，不提供 tools/call API，不修改 AgentLoop，也不向普通 Run 注册新工具。后续模块 9 才将审核后的目录接入统一执行链。

### 74.2 源码阅读地图

| 文件 | 重点对象 | 对应问题 |
|---|---|---|
| `mcp/schema.py` | LaunchProfile、HTTPProfile、MCPServerConfig | 部署配置与 API 配置怎样分开 |
| `mcp/transports.py` | open_transport、PinnedTransport | 实际启动什么、实际连接到哪里 |
| `mcp/connections.py` | Connection、ConnectionManager | 谁拥有 SDK 会话，何时关闭 |
| `mcp/discovery.py` | discover、check_schema、catalog_diff | 什么条件下才算完整目录 |
| `mcp/service.py` | create/update/publish/review/observe | 网络结果怎样安全落库 |
| `db/models.py` | 四个 MCP Record | 配置、证据和健康各存什么 |
| `api/routes/mcp.py` | 管理接口 | 哪些操作写入，哪些只查询 |
| `api/app.py` | lifespan | API 进程如何持有与关闭 Manager |
| `mcp/fixture.py` | build_server、serve_stdio | 如何运行真实协议的受信测试 Server |
| `20260920_0008_mcp_discovery_catalogs.py` | upgrade/downgrade | 如何增加与撤销持久化结构 |

阅读顺序建议为 schema → transports → connections → discovery → service → API。先理解连接的所有权，再看目录事务，可以避免把数据库状态误当成进程内连接对象。

### 74.3 为什么锁定 SDK 与协议两种版本

依赖固定为官方 `mcp==1.30.0`，本项目接受的协商协议固定为 `2025-11-25`。前者是 Python API 与实现版本，后者是线上双方交换消息的契约版本。

SDK 自身还支持其他历史协议；EvoAgent 不因此自动承诺所有协议都已经测试。initialize 返回版本不匹配时拒绝建立 ready 状态。

上游主线已进入 v2，本模块采用维护中的 v1 系列并按实际安装源码核对接口。不能将 v2 的 `Client` 示例直接复制到使用 v1 `ClientSession` 的连接管理器里。相关选择记录在 [ADR-010](ADR-010-MCP发现连接与目录版本.md)。

### 74.4 部署 Profile 与 ServerConfig 的职责

部署 Profile 定义“允许连什么”：stdio 的绝对可执行文件、固定 argv、可选 cwd，或 HTTP 的固定 URL。它们来自 Settings 的部署配置。

MCPServerConfig 定义“这个 Server 使用哪个预设”：展示名称、transport、profile ID、secret_ref、超时、enabled。服务通过 profile ID 查部署配置，API 请求无法临时传入 shell 命令或任意 URL。

```text
部署者配置：fixture → 绝对 Python 路径 + 固定 -m 参数
API 保存：server UUID → launch_profile_id="fixture"
连接时：配置引用 → 部署 Profile → 官方 stdio_client
```

Server UUID 是稳定身份，可编辑的展示名不是身份。修改配置需要 expected_lock_version；启用与否是持久配置，不等于某个进程此刻已经连接。

### 74.5 为什么 stdio 只允许受信 fixture

stdio 传输会在宿主机启动子进程。MCP 协议不会自动把这个进程放进容器，也不会限制它读取文件或访问网络。

当前 LaunchProfile 要求绝对可执行文件，并拒绝直接把 `.bat`、`.cmd`、`.ps1`、`.sh` 当启动文件；参数是部署者预先给定的数组，不做 shell 字符串拼接。`trusted_fixture=true` 是部署者对受信启动配置的声明，不是系统自动证明程序安全。

本地 fixture 是项目自带模块，只提供目录。任意第三方 Server、动态下载的包和运行中自动安装依赖，都不属于本阶段启动范围。模块 10 的隔离完成前不能把该入口当作任意程序平台。

### 74.6 secret_ref 怎样工作

API 只提交类似 `fixture-auth` 的别名。部署者把别名映射到一个 `EVOAGENT_MCP_SECRET_*` 环境变量名，连接时再从环境读取值。

```text
ServerConfig.secret_ref
→ Settings.mcp_secret_refs[别名]
→ 检查环境变量名白名单
→ 读取非空、限长、无换行的秘密
→ stdio: MCP_AUTH_TOKEN
   HTTP: Authorization Bearer Header
```

数据库和 Server DTO 只保存别名。没有把秘密写入 command args、URL、健康错误文本或配置 JSON。stdio 继续使用 SDK 的基础环境白名单，应用只额外注入必要 Token，不复制全部父进程环境。

秘密缺失或引用不合法时连接失败；创建配置成功并不代表密钥已经可用。密钥更新需要断开并重新连接，当前连接不会在每条消息里重新读取环境变量。

### 74.7 HTTP 端点检查为什么还不够

只做“DNS 解析后判断是公网”仍有窗口：校验时是公网地址，真正连接时再次解析可能得到内网地址。

`endpoint_address` 先解析并要求所有结果都是公网地址，再选择一个地址交给 PinnedTransport。后者实际使用固定 IP 连接，同时保留原 Host 与 TLS SNI，证书仍按原主机名校验。

每条 HTTP 请求必须与预设 endpoint 完全一致；重定向一律拒绝，关闭 httpx 的环境代理继承。远端不能用 Location 把已附带凭据的请求带到另一个目标。

### 74.8 本地 HTTP fixture 的例外有多大

默认远端 profile 必须是 HTTPS，拒绝 URL 中的用户名、密码、query 和 fragment，DNS 结果也不能是私网。

测试时可为一个明确命名的 profile 设置 `local_fixture=true`，但主机必须是字面 `127.0.0.1` 或 `::1`。这不允许 `localhost` 的任意解析结果，也不放开 `10.*` 等私网段。

HTTP 响应流累计上限 2 MiB，并请求 identity 编码；返回压缩编码会被拒绝，避免原始流大小合格但解压后无限膨胀。长期通知流达到上限可能重连或降级，这是当前有限资源策略的一部分。

### 74.9 Connection 为什么必须有专属任务

官方 Session 和 transport 内部使用 AnyIO task group/cancel scope。这些资源通常必须由进入它们的任务退出；在请求 A 创建 AsyncExitStack、请求 B 里随手 `aclose()`，可能造成跨任务退出错误。

Connection 自己创建一个长期运行的 asyncio 任务。该任务进入 transport、进入 ClientSession、initialize、分页发现、处理通知，然后在同一任务里退出全部上下文。

Manager 不把 SDK Session 交给 API。API 只等待初始化 Future 或刷新 Future，关闭时取消连接任务并等待清理完成。这样资源生命周期跟随明确的所有者，而不是碰巧最后一个访问对象的 HTTP 请求。

### 74.10 连接状态与数据库健康为什么分开

```text
connecting → ready
          ↘ degraded
ready → draining → disabled
```

这些状态描述当前进程内的连接。数据库 mcp_servers.config.enabled 则表达用户是否允许发现，两个状态不能互相替代。

例如 API 进程退出后，配置仍可能 enabled，但 Python Session 已不存在。mcp_health 保存 instance_id、配置版本、观察时间和到期时间；读取时到期或版本不一致返回 stale，不把旧 ready 记录当成活连接。

### 74.11 API lifespan 怎样装配

应用启动时创建 ConnectionManager 和 MCPService，关闭时先关闭 Manager，再释放数据库。默认没有部署 profile，不建立外部连接；即使配置记录 enabled，也要显式 POST discover 才连接。

Manager 使用每次实例化生成的 UUID 区分进程。当前调用方是管理 API 进程，普通任务 Worker 尚未接入。后续 Worker 可以各自实例化 Manager，不能跨进程共享或把 ClientSession 序列化到数据库。

首次连接及人工刷新由 Manager 串行管理，连接数量默认上限 8；每个连接的发现并发固定为 1。后台 list_changed 刷新属于连接自己的任务，不改变普通 Agent 的并发工具执行策略。

### 74.12 握手失败与受限重连

建立传输并 initialize 受 connection_timeout 限制，发现受 call_timeout 限制，默认各 10 秒。协商必须得到接受的协议版本和 tools 能力。

首次发生传输故障或超时，最多再尝试一次，中间等待 0.1 秒；错误目录、能力不支持等确定性问题不重试。SDK 自身的 HTTP 流恢复仍属于其实现，项目不会据此重试远端业务写操作。

稳定连接每 15 秒无通知时发送 ping，并刷新 60 秒健康观察；发现断线后进入 degraded。下一次显式 discover 会重新握手，当前没有无限后台重连循环。

取消向外传播并关闭当前资源。若旧任务已结束，Manager 会回收其槽位，避免长期把 degraded 连接算作可用容量。

### 74.13 为什么错误记录只留代码

SDK 异常可能包含报文、URL 或 Server 任意输出。Connection 把它映射为 mcp_transport_failed、mcp_timeout 或明确的 MCPError 代码；不会将 ExceptionGroup 的完整字符串存入数据库。

stdio stderr 当前全部丢弃，保留大小为零；SDK 原始日志，包括其少量直接使用根 logger 的报文日志，也被过滤。代价是管理接口不能浏览远端 stderr，排查时应使用受控 fixture 和稳定错误码。

不能为了“日志方便”把含 Token 的环境、原始请求 Header 或远端异常正文写进 Trace。后续若引入诊断尾部，需要单独定义大小限制、脱敏和读取权限。

### 74.14 为什么必须完整分页后提交

一个 Server 可以分多次返回工具目录。第一页成功、第二页断开时，第一页不是完整目录，也不应被标记为新版本。

`discover` 暂存在内存里累积工具，检查重复名称、游标循环和每页结果，直到 nextCursor 为 null。只有整个目录合法后才调用 publish。

游标不被当作 URL 或命令，只作为下一页参数原样交给 SDK；它有 2048 字符上限，重复、空但未终止、超过页数都失败。

### 74.15 目录大小和 Schema 边界

| 对象 | 当前限制 |
|---|---|
| 页数 | 16 |
| 工具数 | 128 |
| 工具名称 | 非空、最多 128 字符、无控制字符、跨页唯一 |
| 描述 | 最多 4096 字符 |
| 每份 Schema | 64 KiB、遍历深度不超过 16 |
| 整个工具目录 | 1 MiB |
| 初始化身份/能力元数据 | 16 KiB |

inputSchema、outputSchema（存在时）根类型必须是 object，按 Draft 2020-12 做 Schema 结构检查。禁止远程 `$ref`/`$dynamicRef`，拒绝改变引用基址的 `$id` 和未支持的方言。

这一层是目录结构检查，尚不承担 tools/call 的参数实例验证。模块 9 必须继续实现本地引用处理、动态参数校验和统一错误映射，不能把“目录存储成功”当成任意参数都能安全发送。

### 74.16 annotations 为什么只保存为提示

fixture_health 自报 `readOnlyHint=true`，测试仍要求它的本地初始审核为 approved=false、risk=R3、effect=non_idempotent_write。

远端声明只读，最多帮助审核者理解意图，不能成为降级风险或跳过审批的依据。本模块也不会因为 description 写了“安全工具”就自动批准。

本地人工审核可以在明确了解工具后记录 R0/read_only，但这是绑定特定目录版本的审核证据，尚未赋予执行权限。

### 74.17 四张新表怎样关联

| 表 | 唯一身份 | 主要内容 |
|---|---|---|
| mcp_servers | 稳定 UUID | config、lock_version、latest_revision |
| mcp_catalogs | server_id + revision | config_version、协议、Server 信息、capabilities、工具、hash、diff |
| mcp_tool_reviews | catalog_id + tool_name + lock_version | 本地批准、风险、副作用分类、审核者和原因 |
| mcp_health | server_id + instance_id | 配置版本、状态、稳定错误码、观察及到期时间 |

Catalog 与 Review 有显式外键，历史对象不在原行改写。Review 的 lock_version 是审核链上的序号，与 Server 配置的 lock_version 是两个不同计数器。

Python 的 Session、socket、子进程句柄不进入任何表。进程退出后应重新建立这些资源，而不是把一条 health.ready 当作可恢复句柄。

### 74.18 目录 hash 和 diff 包含什么

工具按原始名称排序，保存 description、input/output Schema、各 Schema hash 和 annotations。目录 hash 同时包括工具集合、Server 身份、能力和协商协议。

相同配置版本且完整身份相同，重复发现复用同一个 revision；没有变化也不重复初始化审核。配置版本改变，即使远端工具文字一样，也建立新的目录版本，避免把旧端点/凭据配置下的审核带到新的连接身份。

diff 分 added、removed、changed。例如 echo 改名为 renamed，表现为删除 echo、增加 renamed；同名工具的 Schema 或描述变化则进入 changed。

Server 元数据变化也能改变目录 hash，此时工具级 diff 可以为空。这不矛盾：目录身份不只包含工具列表。

### 74.19 list_changed 怎样避免半旧半新

每收到一次通知，Connection 的 notification_epoch 加一并唤醒刷新任务。分页开始记住 epoch，结束时比较；若期间发生变化，就丢弃刚读到的批次并从第一页重读。

最多重读三次，持续变化返回 mcp_catalog_unstable。通知只表示“可能变了”，不直接给出新目录，也不能直接替换旧表内容。

通知发生在提交附近时，新目录仍可能短暂成为一个已观察版本，然后紧接着产生下一版；由于本模块没有激活执行，这些版本都只是审核证据。未来运行注册仍须绑定明确目录和撤销规则。

### 74.20 网络等待与目录事务如何隔离

网络调用期间不持有数据库事务。MCPService 先读取 config/lock_version，连接任务完成发现后再开启短写事务。

写事务重新检查 Server 仍 enabled、配置版本与最初相同，分配下一个 revision，再一次性写 Catalog 和每个工具的初始 Review。失败则全部回滚，不留下半目录或缺少审核的工具。

如果管理员在发现期间禁用 Server，旧结果最终看到不同 lock_version 或 enabled=false，返回 mcp_config_changed；它不能把旧目录重新发布为当前配置的目录。

### 74.21 行锁之外为什么还要 CAS

PostgreSQL 行锁使同一 Server 的配置和目录事务按顺序执行；SQLite 不提供同样的 `FOR UPDATE` 语义，因此还使用带旧版本条件的 UPDATE。

配置更新必须命中 expected_lock_version；目录修订分配必须命中原 latest_revision。没有命中就返回冲突，不让两个读取相同旧值的事务都宣称成功。

审核先检查当前配置和最新目录，再用条件 UPDATE 固定此判断的写边界，最后追加审核记录；唯一键也防止相同审核版本被写入两次。测试包含两个请求同时修改同一配置版本，只有一个成功。

### 74.22 为什么审核要追加而不是覆盖

初始版本 0 表示等待本地审核，之后每个决定新增一行。这样可以看到先批准、后撤销的时间和原因，旧证据仍然存在。

POST review 必须给出 expected_lock_version、tool_name、reviewer 和 reason。写入型 effect 不允许标成 R0/R1；风险不能仅靠 Server 注解自动设置。

新目录会为所有工具重新创建未审核记录，包括文字未变化的工具。旧审核绑定旧目录，不能在不知道目录/连接条件变化的情况下自动继承。读取 reviews 接口返回完整有序历史，最后一个版本才是该目录下该工具的最新本地决定。

### 74.23 关闭到底关闭了什么

人工 disconnect 或应用 shutdown 会使连接进入 draining，取消专属任务，等待 SDK Session、HTTP 流或 stdio 子进程退出，再将本地状态置为 disabled。

真实 stdio 测试记录 fixture PID，在连接期间确认进程存在，disconnect 后确认该 PID 已退出。HTTP 测试则经过真实 loopback TCP、SDK SessionManager 和关闭流程。

disconnect 不修改持久配置的 enabled，所以之后显式 discover 可以重新建立连接。要持久停止发现，应通过配置更新设置 enabled=false；两个动作的语义需要在客户端界面区分。

### 74.24 模块 8 尚未改变什么

普通 Task 的 ToolRegistry、Skill 工具白名单、Approval、ToolEffect 和 RunConfigSnapshot 没有因为 MCP 目录出现而增加远端工具。目录 DTO 明确返回 execution_enabled=false，即使本地 review.approved=true 也一样。

这使模块 8 可以单独验收连接和发现，同时保持既有任务执行闭环。模块 9 必须完成参数契约、稳定工具名、目录冻结、目录撤销和副作用身份后才能接通 tools/call。

---

## 75. 阶段四模块 8 完整调用链

### 75.1 从部署配置到第一版目录

```text
Settings：受信 launch / HTTP profiles，secret_ref 映射
→ POST /mcp/servers：保存稳定 UUID 与配置引用
→ POST /mcp/servers/{id}/discover
→ MCPService 读取 enabled/config_version
→ ConnectionManager 检查容量、旧连接与配置身份
→ Connection 专属任务
   → 解析秘密
   → 官方 stdio 或 Streamable HTTP transport
   → ClientSession.initialize
   → 协议/tools 能力核对
   → tools/list 完整分页、结构与大小校验
→ MCPService.publish 短事务
   → 复验启用与配置版本
   → Catalog + 初始 Review 同事务提交
→ health.ready（60 秒有效期）
→ 返回目录证据，不注册 Agent 工具
```

### 75.2 从目录变更到重新审核

```text
Server notifications/tools/list_changed
→ notification_epoch + 1，合并唤醒
→ 从第一页发现完整目录
→ 发现过程中再次变更则整批重读，最多三次
→ 比较 hash/config_version
→ 相同：复用旧 revision
→ 变化：新 revision + added/removed/changed
→ 为新目录所有工具建立未批准/R3 审核版本 0
→ 人工提交带 expected_lock_version 的审核决定
→ 追加审核版本，不改变 execution_enabled=false
```

### 75.3 禁用与迟到结果

```text
发现正在等待远端
→ 管理员 PUT enabled=false，配置 CAS 成功并先提交
→ 旧发现返回
→ publish 检查失败，拒绝提交旧目录
→ Manager disconnect 等待清理
→ 后续 discover 直接拒绝 disabled
```

“先提交禁用，再等待网络关闭”很重要。若反过来先等待旧连接结束，期间返回的旧结果仍可能认为配置允许发布。

### 75.4 失败与重新连接

```text
传输初始化失败 / 超时
→ 清理本次资源
→ 只保存稳定错误码
→ 首次可恢复错误最多再尝试一次
→ 仍失败则 degraded
→ 后续显式 discover 重新读取配置、握手、发现
```

重新连接要重新确认目录身份，不是拿旧 Session ID 继续相信上一轮的能力。目录未变化时可以复用版本，但这个判断必须在新的完整发现之后做。

---

## 76. 阶段四模块 8 测试地图与故障定位

### 76.1 各类测试提供什么证据

| 测试文件/场景 | 已证明行为 | 不证明的内容 |
|---|---|---|
| `test_mcp_discovery.py` 分页 | 多页合并、游标传递、稳定排序 | 第三方目录业务语义 |
| 重复名/游标循环/页数/工具数 | 不完整或无界目录拒绝 | 无限大原始 stdio 流的宿主隔离 |
| Schema/描述限制 | 远程 ref、超大、非法方言和结构拒绝 | tools/call 参数实例验证 |
| HTTP profile 与 PinnedTransport | 私网默认拒绝、固定 IP/Host/SNI、重定向拒绝 | 公网 TLS 服务实机联调 |
| secret_ref / 本地审核默认值 | 配置不保存秘密值，远端只读提示不能自行批准 | 通用 OAuth 登录 |
| `test_mcp_connections.py` 故障传输 | 尝试次数有界，取消清理，错误码不包含原始秘密 | 真实第三方网络故障类型全集 |
| 官方 SDK 内存握手 | tools 能力缺失时拒绝 | 其他协议版本的兼容承诺 |
| `test_mcp_catalogs.py` 真实 stdio | SDK 启动子进程、两页发现、稳定目录复用 | 敌对程序隔离 |
| PID 关闭断言 | disconnect 后 fixture 进程退出 | 第三方任意进程树的容器清理 |
| list_changed | 通知触发新目录，旧批准不继承 | Run 目录冻结，属于模块 9 |
| 配置 CAS/迟到 publish | 一个旧配置版本只能成功更新一次，禁用阻止迟到目录 | 未执行的 PostgreSQL 实机并发 |
| 本地审核/不可变证据 | 旧决定拒绝重复写，Catalog 正文不能原地改写 | 审核内容的客观正确性 |
| 真实 loopback HTTP + API | SDK Streamable HTTP、管理路由、健康和 disconnect | 公网代理、OAuth、第三方服务 |
| 无 call 路由 | 管理发现 API 没有 tools/call 快捷入口 | 模块 9 的执行安全已完成 |
| `test_migrations.py` | SQLite 新旧迁移往返、metadata 对齐 | 跳过的 PostgreSQL 项 |

最新测试数字见[模块 8 验收说明](阶段四-模块8验收与运行说明.md)。本次本地 HTTP fixture 是真实 TCP/协议调用，和纯 Mock 不同；它仍不能替代公网 TLS、容器或第三方服务验收。

### 76.2 看见错误时从哪里检查

| 错误/现象 | 检查位置 | 正确处理 |
|---|---|---|
| mcp_profile_not_found | Settings profile ID 与 ServerConfig | 修复部署配置引用 |
| mcp_secret_unavailable | secret_ref 映射与环境变量 | 在部署环境补齐秘密，重新连接 |
| mcp_endpoint_forbidden | HTTP profile、DNS/IP | 核对可信端点，不全局放开私网 |
| mcp_redirect_forbidden | 真实服务最终路径 | 将审核过的最终 endpoint 明确配置 |
| mcp_tools_unsupported | initialize capabilities | 使用提供 tools 的 Server |
| mcp_protocol_unsupported | 协商协议 | 使用已支持协议或单独完成升级验收 |
| mcp_tool_name_invalid | 全部分页名称 | 修复重复、过长或控制字符名称 |
| mcp_catalog_unstable | 通知频率与分页 | 等目录稳定后重新发现 |
| mcp_config_changed | 配置 lock_version | 旧结果失效；根据新配置重新 discover |
| mcp_catalog_stale | 审核绑定目录/config_version | 读取最新目录后重新审核 |
| mcp_review_conflict | 最新审核 lock_version | 读取最新决定，不覆盖他人审核 |
| health=stale | expires_at、config_version | 发起显式发现或核对进程状态 |
| approved=true 但不能调用 | 模块边界 | 模块 9 尚未接入，不绕过 Executor |

不要通过手工修改目录 hash、重写旧审核行、临时放开任意 argv 或 URL 来处理错误。正常操作是修改受控部署配置、读取新版本、显式重连或重新审核。

---

## 77. 阶段四模块 8 学习验收

结合源码、数据库记录与测试回答：

1. SDK 版本与协商协议版本为什么要分别锁定？
2. Server 提供 tools 能力，为什么仍不能直接进入 ToolRegistry？
3. LaunchProfile 与 MCPServerConfig 分别由谁控制？
4. 为什么启动配置必须是 argv 数组而不是 shell 字符串？
5. trusted_fixture=true 能证明宿主隔离已经完成吗？
6. secret_ref、环境变量名和秘密值分别出现在哪一层？
7. HTTP 在 DNS 检查后为什么还要绑定实际连接 IP？
8. 固定 IP 连接时，Host 与 TLS SNI 为什么必须保留原主机名？
9. 明确的 loopback fixture 例外与“允许私网”有什么差别？
10. 为什么限制响应流大小后还要处理压缩编码？
11. AnyIO cancel scope 为什么影响连接任务的设计？
12. API 请求取消后，谁负责关闭 SDK Session 和子进程？
13. enabled、ready、degraded、stale 各表达哪一种事实？
14. 为什么健康状态需要 instance_id 和 expires_at？
15. 两次重连尝试为什么不能扩展成写工具的自动重试？
16. stderr 丢弃和稳定错误码带来哪些诊断取舍？
17. 第一页成功、第二页失败，为什么不能保存第一页？
18. 游标循环与目录变更期间的分页分别如何限制？
19. Schema 结构检查和参数实例验证有什么区别？
20. Server 的 readOnlyHint 为什么不会改变默认 R3？
21. 目录 hash 为什么包含 Server 信息与协议，而不只包含工具名？
22. 工具 diff 为空但目录 hash 变化，是否一定是错误？
23. 配置 lock_version 与目录 revision 为什么不能共用一个字段？
24. 为什么所有新目录工具都重新初始化未批准状态？
25. 审核记录为什么要追加，如何读取当前决定？
26. PostgreSQL 行锁之外，条件 UPDATE 对 SQLite 测试有何意义？
27. 禁用与发现同时发生时，哪个事务条件拒绝迟到结果？
28. disconnect 与 enabled=false 为什么是两种操作？
29. PID 退出测试证明了什么，又没有证明什么？
30. 模块 9 接入工具之前，还缺哪几条运行时保护链？

能够从部署 Profile、进程内 Connection、持久化 Catalog、人工 Review 四个层次回答这些问题，才说明理解了模块 8 的完成边界。下一模块继续建立 MCPToolAdapter 与统一安全执行，不能跳过参数、目录、审批和副作用身份的一致性检查。

---

## 78. 阶段四模块 9：MCP 工具适配、目录冻结与执行身份

第 74～77 章保留模块 8 交付时的边界。模块 9 开始，已审核目录可以显式激活并进入普通持久化 Run；发现本身仍不授权执行。阅读本章时，请区分连接是否启用、工具是否批准、目录是否激活，以及当前 Run 是否已经冻结选择这四件事。

### 78.1 为什么不能把 SDK call_tool 直接交给模型

模块 8 已经得到原工具名、inputSchema、outputSchema 和 annotations，但这些仍只是远端声明。模型可能产生错误参数；远端可能在审批后修改同名工具；Worker 可能在外部写入成功后失联。仅封装一次 `session.call_tool()` 无法处理这些情况。

本模块复用原有 ToolExecutor、PermissionPolicy、Approval 和 ToolEffect。MCPToolAdapter 只承担固定契约的协议翻译，不自行批准动作，不替代副作用账本，不创建第二套 AgentLoop。

推荐先读下列文件，再沿第 79 章追踪完整调用：

| 文件 | 负责的事实 | 不负责的事项 |
|---|---|---|
| `mcp/adapter.py` | 命名、参数、结果、调用和 Run 目录装配 | 审批决定、直接修改效果状态 |
| `mcp/bindings.py` | Server、目录、审核的实时有效性 | 建立连接、访问远端 |
| `mcp/connections.py` | SDK 会话所有权、请求排队、取消与关闭 | 判断业务是否允许写入 |
| `mcp/service.py` | 审核和目录管理、执行状态 CAS | 从 API 直接执行工具 |
| `tools/base.py` | 规范参数与执行绑定的公共契约 | 理解具体 MCP Schema |
| `tools/registry.py` | 模型定义和 manifest 的一致性 | 执行模型请求 |
| `tools/executor.py` | 校验、错误映射、调用顺序和输出存储 | 推断远端是否已提交事务 |
| `tools/effects.py` | 审批复用、幂等账本与受保护提交 | 把 request ID 当业务幂等键 |
| `tools/approvals.py` | 人工决定及决定时的绑定复查 | 给失效目录重新授权 |
| `runtime/persistent_runner.py` | 每个 Run 装配与关闭 Manager | 热更新已运行 Registry |

### 78.2 一种工具有两种身份

第一种是业务工具身份。Server UUID 与原始工具名决定它是谁，展示名改变不应产生一个新的外部动作。`mapped_name()` 生成：

```text
mcp_<Server UUID 前 8 位>_<安全 slug>_<16 位 hash>
```

slug 只保留字母、数字、下划线，最长 29 字符；哈希输入包含完整 UUID 和未经截断的原名。因此两个名字前 100 个字符相同，尾部不同，仍得到不同映射名。最终长度不超过 ToolDefinition 的 64 字符限制。若极端情况下仍重名，Registry 抛出重复工具错误，不覆盖已有工具。

第二种是执行契约身份。同一个业务工具可以有不同 Schema、风险审核和目录版本。`execution_binding()` 保存这些证据，审批不能跨契约复用。

理解这个区别，才能理解为什么目录 revision 必须出现在审批绑定中，却不应加入副作用语义键：修改契约不能使昨日未查清的外部写入凭空消失。

### 78.3 ValidatedMCPArguments 是内部承载对象

内置工具事先定义 Pydantic 参数类；MCP 工具的参数结构到发现阶段才能确定。适配器继续继承 BaseTool，使用：

```python
class ValidatedMCPArguments(BaseModel):
    payload: dict[str, JsonValue]
```

这不是模型看到的参数结构。`definition()` 返回冻结的 input_schema；`validate_arguments()` 深拷贝调用参数，按该 Schema 校验，通过后才创建包装对象。`canonical_arguments()` 返回其 payload 的副本。

例如模型生成 `{"text":"hello"}`，实际审批参数、ToolCall.arguments、效果语义键及 SDK arguments 都仍是这个对象。任何一层若改用 `{"payload":{"text":"hello"}}`，都会破坏 Schema 和语义身份一致性。

内置 BaseTool 的 canonical_arguments 使用 model_dump(mode="json")，保留 Pydantic 规范化后的值和默认值。Executor 同时捕获原有 ValidationError 与新增 ToolArgumentValidationError，统一返回 invalid_arguments。动态参数校验失败不会创建远端请求。

### 78.4 Schema 定义检查与实例检查分别在哪里

`discovery.check_schema()` 在保存目录前检查定义本身。根必须为 object；仅支持 Draft 2020-12，拒绝远程引用和改变引用基址。原有 Schema 大小 64 KiB、结构深度 16 的限制继续生效。

模块 9 增加本地引用展开检查：仅允许可解析的 JSON Pointer，拒绝循环、缺失目标和不支持的引用形式，展开深度最多 32，最多访问 10000 个节点。这样不能用很短的递归引用构造无限验证链。

`adapter.validate_payload()` 检查实际实例。参数最多 1 MiB，实例深度最多 32，不能含 NaN/Infinity；随后由 Draft202012Validator 验证字段类型、required、additionalProperties 等规则。失败只返回稳定说明，不把完整参数和 SDK 堆栈回显给模型。

首版是有界子集，不支持的 Schema 在发现时拒绝。拒绝某个合法但复杂的 JSON Schema 不等于整个规范无效，而是项目没有承诺支持它。

### 78.5 为什么修改 manifest 的参数来源

以前 Registry.manifest() 从 arguments_model.model_json_schema() 生成参数定义。对于适配器，这会错误地记录 payload 包装，而模型看到的是远端 inputSchema。

现在 manifest 读取 `tool.definition().parameters`，并在 execution_binding 非空时追加 binding。内置工具的空绑定不额外序列化，已有内置 manifest 哈希保持兼容。implementation_version 为 MCP 适配器实现自身提供版本身份；目录和审核变化则反映在 binding 内。

模型定义、manifest 和实际校验共同读取冻结工具定义，这是恢复一致性的基础。manifest_hash 只是一份证据摘要，不能替代每次调用前的撤销检查。

### 78.6 发现、审核、激活的三个提交点

Server 的 config.enabled 允许进程建立连接；Review.approved 允许某个固定目录中的工具被选择；Server.execution_state=active 与 active_catalog_id 明确选择候选目录。三者缺一都不能让新 Run 注册工具。

新目录工具仍默认未批准、R3、non_idempotent_write。annotations 保存在目录中供人审核，但不会自动降低风险，也不会令工具并行执行。所有 MCP 适配器 parallel_safe=False。

新增 execution_version 用于管理操作 CAS。调用 `/mcp/servers/{id}/execution` 必须提交 expected_lock_version 和 expected_execution_version。连接配置版本与执行状态版本分离：激活目录不会改变其 config_version，使刚激活目录立即过期。

激活要求目录属于当前 Server、当前配置和最新 revision。即使目录已激活，未批准工具仍被过滤；一个目录中可以只开放经过审核的那部分工具。

### 78.7 RunToolCatalog 为什么使用 Run 上的 JSON 列

迁移 `20260921_0009` 在 runs 增加 tool_catalog_snapshot，首版不另建选择明细表。每个 Run 只有一次工具目录选择，完整 JSON 保存的是该次选择的证据，不是共享的可变工具列表。

| 快照字段 | 恢复时的用途 |
|---|---|
| server_id | 稳定 Server 身份与连接查找 |
| catalog_id / revision / catalog_hash | 目录证据、漂移判断 |
| config_version | 拒绝使用旧配置的调用 |
| review_id | 锁定批准这份契约的具体审核记录 |
| risk / effect | 本地权限与副作用分类 |
| tool | 原名、描述、Schema、Schema hash 和 annotations |

NULL 表示尚未选择，[] 表示已经选择且没有 MCP 工具。这个区分避免首次无命中时在恢复阶段突然新增工具。ORM 事件拒绝覆盖已写入快照；首次写入仍在 LeaseGuard 保护下进行。

`register_run_tools()` 先读取快照。没有快照时，只为尚未保存配置的 retrieval Run 选择 active、配置匹配、仍为最新 revision 的目录；随后保存，包括空选择。已有 config_snapshot 的历史 Run 冻结空 MCP 选择，避免升级给旧运行增加权限。baseline 和 pinned_skill 路径不自动注册 MCP。

### 78.8 Worker 在哪里接入

PersistentAgentRunner.handle() 创建本次执行使用的 ConnectionManager，克隆基础 Registry，再装配该 Run 的冻结 MCP 工具。之后才进入原有 Skill 选择、RunConfigSnapshot 和 AgentLoop。

恢复时重新实例化适配器，但使用的是旧快照正文；不根据当前最新目录重新挑选。Manager 的关闭放在 finally 中，成功、失败和审批暂停都不会把本次 Run 的 SDK 会话永久留在 Worker 中。

API 进程发现连接和 Worker 执行连接属于不同进程内对象，通过数据库目录与状态关联。不能把 API 返回 ready 误认为 Worker 已经持有相同的 Python Session。

---

## 79. 阶段四模块 9 完整调用、恢复与卸载链

### 79.1 从模型调用到远端请求

以批准的只读 echo 工具为例：

```text
ModelRequest.tool_definitions 使用冻结 Schema
  → 模型产生稳定映射名 + {text: "hello"}
  → ToolExecutor 查找并校验参数
  → preflight 检查租约、启用状态、目录与审核
  → PersistentToolMiddleware 执行 PermissionPolicy
  → 必要时创建 Approval；写调用建立 Effect 占位
  → Adapter 重新发现并比较整个目录 hash
  → Connection.call 排队，由连接任务处理
  → 请求发出前再次检查绑定
  → SDK tools/call 使用原始工具名和解包参数
  → 返回类型、outputSchema 和大小检查
  → ToolOutputStore 保留大正文，生成模型预览
  → after_success 在提交事务中复查租约和绑定
```

Executor 在缺少持久化中间件时拒绝 MCP 工具，避免把独立演示执行器当成跳过审批的入口。管理路由没有 tools/call；Provider 仍只生成工具请求。

### 79.2 连接任务为什么使用请求队列

SDK transport 与 ClientSession 内部包含 AnyIO 上下文，创建和关闭必须由同一任务负责。模块 8 的 Connection.run() 已经承担这个所有权；模块 9 增加 calls 队列与结果 Future，不把会话交给路由直接使用。

调用唤醒连接任务，任务先完成目录扫描，再串行执行操作。实际 SDK 请求在子任务中等待，以便约每 100ms 检查 Future 是否取消以及数据库绑定是否撤销；子任务不进入或退出 SDK 上下文。

连接关闭时，尚未完成的等待者获得 mcp_connection_closed。Executor 超时取消 Future 后，连接任务取消 SDK 请求，避免调用者离开后队列继续无声执行。远端是否已经提交仍由效果账本处理，取消消息不具备业务回滚语义。

### 79.3 审批如何绑定目录和规范参数

ToolCallRecord 新增 execution_binding。中间件把适配器绑定与 PermissionPolicy.manifest_hash() 一同保存；Approval 通过 tool_call_id 关联它。

同 Task 的新 provider call_id 可以复用决定，但必须同时匹配工具名、规范参数和完整绑定。review_id 变化，即使风险字符串仍为 R3，也表示一次新的本地决定；旧批准不自动继续有效。相同 call_id 携带另一份绑定则直接拒绝，而不是覆盖原调用证据。

ApprovalService.decide() 也检查当前绑定。用户点批准时，如果 Server 已禁用、目录已变更或 Review 已被替代，返回 tool_manifest_changed。仅在执行入口校验而允许界面“批准失效契约”，会产生误导性的管理状态，因此决定入口同样复查。

### 79.4 写入去重为何不加入目录 hash

PersistentToolMiddleware.semantic_key() 对稳定工具名与 canonical_arguments 做哈希，effect_scope 为 Task UUID。稳定工具名又绑定完整 Server UUID 和原始工具名。

如果把 catalog_hash 加入这个键，Server 升级后同一个转账或写文件动作会生成新键，绕过旧 UNKNOWN。当前实现保留旧语义键，ToolEffect 通过关联 ToolCall 保存目录证据。

同契约 COMMITTED 返回旧结果，不再次调用。另一契约遇到 COMMITTED 返回 tool_manifest_changed，避免把旧输出伪装成符合新 Schema 的结果。EXECUTING/UNKNOWN 进入人工核查，不因目录变化自动生成第二个效果。

### 79.5 外部成功、本地未确认的时间线

```text
本地提交 Effect=EXECUTING
  → fixture 写入调用日志，模拟外部动作已发生
  → fixture 在返回前退出
  → SDK 报连接关闭
  → Executor 记录失败
  → Effect=UNKNOWN
  → 同语义请求再次出现
  → 创建或重开确认请求，不发送第二次写入
```

fixture 的调用日志是本地故障实验中的外部证据，不代表第三方系统已经具备业务查询接口。isError 同样不能证明未执行，因此写调用返回 isError 也进入 UNKNOWN。

当前所有写分类最多调用一次，包括本地声明 idempotent_write 的工具。真正启用写入自动重试，需要以后明确接入远端业务幂等键或回执查询；MCP request ID 不满足该条件。

### 79.6 只读重试与结果检查

只有本地审核为 read_only 的工具，遇到连接关闭、超时或传输失败时最多重试一次。重试先关闭旧连接，再重新握手和发现；业务错误、协议错误、目录漂移、输出不合约不重试。整个 Adapter 仍受 Executor 的总工具超时限制。

SDK 已验证 outputSchema，Adapter 还按被冻结的 output_schema 校验 structuredContent。返回格式为包含 text 数组以及可选 structuredContent 的 JSON 字符串，继续适配现有文本 ToolResult。

图片、音频、资源链接和嵌入资源返回 unsupported_mcp_content，不下载 URL，不读取 file URI。规范结果最多 2 MiB；预算内的大正文沿用 ToolOutputStore。模型预览被截断不等于完整正文丢失，Artifact 访问仍走原有受控工具。

### 79.7 成功提交前为什么还要检查一次

远端返回成功之后，输出存储或其他异步操作期间仍可能发生禁用。仅在请求前校验会留下“撤销后仍 COMMITTED”的窗口。

after_success 在同一提交事务中执行 check_binding(lock=True)，锁住 Server 并复查状态、目录与 Review。绑定失效时，将调用标记失败，未确认效果转 UNKNOWN，然后抛出稳定错误；不会发出成功完成事件。

如果是 LeaseGuard 失败，旧 Worker 无权修改任何权威状态；它不能自行写 UNKNOWN。恢复协调负责接管并处理遗留 EXECUTING，这是租约保护和业务状态修复各自的职责。

### 79.8 list_changed 后旧 Run 怎样处理

通知生成新 Catalog 和默认未批准 Review，不改 Registry，也不覆盖 Run 快照。当前项目不支持让远端按旧目录 revision 执行，所以一旦发现 latest_revision 改变，旧绑定就失败。

这是一种保守的完整目录策略：即使远端仅新增另一个工具，也可能令旧 Run 无法继续调用。它用明确拒绝换取可解释的契约边界，后续若要允许旧版本继续执行，需要 Server 提供可验证的版本契约，不能单凭原工具名称未变推断。

新 Run 也不会自动选择未激活的新目录。管理者需要重新审核并激活，随后新 Run 才能冻结新契约。

### 79.9 draining、disabled 与 disconnect 的区别

| 操作 | 新 Run / 新调用 | 已发出的调用 | 连接处理 |
|---|---|---|---|
| draining | 停止选择和调用 | 允许完成并提交 | 本进程等待结束后关闭；其他执行进程检查后关闭 |
| disabled | 禁止 | 尽可能取消，写结果未知保留 UNKNOWN | 本进程立即关闭；其他进程检查撤销 |
| disconnect | 不改变数据库执行授权 | 关闭本管理进程连接可能中断调用 | 以后仍可重连，不是全局禁用 |

execution 状态先持久化，再操作进程内连接。因此另一个 Worker 无需共享内存也能看到撤销。100ms 是轮询等待间隔，不是分布式撤销延迟承诺，真实延迟还受数据库、调度及网络影响。

---

## 80. 阶段四模块 9 测试地图与故障定位

### 80.1 每组测试证明什么

| 场景 | 断言重点 | 证据边界 |
|---|---|---|
| 真实 stdio 完整调用 | SDK tools/call、规范参数、Trace、结果 | 受信 fixture，不是第三方服务 |
| 动态参数错误 | invalid_arguments，外部日志不存在 | Schema 子集验证 |
| 缺少持久化中间件 | mcp_persistence_required | 防止普通 Executor 装配绕过 |
| 只读提示与本地 R3 | 仍创建 Approval | 不自动相信 annotations |
| Review 替换 | 旧适配器拒绝、新绑定重新审批 | 审核身份而非只比较风险字符串 |
| 待批目录失效 | ApprovalService 拒绝批准 | 决定时实时校验 |
| isError / 写后退出 | UNKNOWN，重复请求不再写 | 外部成功、本地不确定的故障模拟 |
| 成功写重复调用 | 一条调用日志、一条 Effect | 同 Task 同语义去重 |
| 只读断连 | 两次尝试后停止 | 有界重试，不承诺网络恢复 |
| 正常卸载 | 在途完成，新调用被拒，连接关闭 | 单进程真实 SDK 生命周期 |
| 紧急禁用 | 调用中断、写效果 UNKNOWN | 远端事务不承诺回滚 |
| 远端成功到提交间撤销 | 不提交成功，效果 UNKNOWN | 本地提交窗口保护 |
| 目录变更与恢复 | Registry hash 不变，调用拒绝 | 不发生热更新 |
| 空选择及 baseline | 激活后仍保持旧空快照 | 不污染既有 Run 和基线 |
| PersistentAgentRunner | 模型定义、真实工具调用、运行完成 | 模型使用 Mock 脚本 |
| 本地 ref | 非循环引用验证，缺失与递归拒绝 | 不支持全部 JSON Schema 特性 |
| 执行状态 CAS | 旧 execution_version 被拒 | SQLite 行为；PostgreSQL 实机另验 |
| migration 往返 | 新列、旧版本升级与降级 | 临时数据库，不建议生产丢弃绑定 |

主体为 `tests/integration/test_mcp_execution.py`；Schema 限制扩展在 `tests/unit/test_mcp_discovery.py`。原有审批、副作用故障恢复、API/Worker、模块 8 连接和迁移测试仍参与全量回归。最终数字与环境边界见[运行说明](阶段四-模块9验收与运行说明.md)。

### 80.2 按层排查

模型看不到工具时，先查 config.enabled、execution_state、active_catalog_id、最新目录及 approved，而不是修改 Provider；再检查该 Run 是否已经冻结空快照，或属于 baseline/pinned_skill。

出现 tool_manifest_changed 时，对照 Run 快照、ToolCall.execution_binding、Server.lock_version/latest_revision 和最新 Review.id。不要直接改旧 JSON、旧 hash 或账本状态。原来能调用但新目录未审核，是正常的拒绝状态。

出现 UNKNOWN 时，先查外部结果，再决定 retry 或 committed。不要将重启 Worker、重新激活目录、换 call_id 当成业务撤销手段。

发现成功而执行失败时，还要确认 Worker 拥有相同的部署 Profile 和秘密引用。API 进程健康记录有自己的 instance_id，不代表另一个执行进程连接正常。

### 80.3 本次边界

模块 9 没有扩大 Skill 低风险白名单，没有增加公网第三方 Server 的 OAuth 产品，也没有完成敌对可执行程序的隔离。受信 stdio fixture 是开发验收条件；独立执行容器和网络出口策略仍属于模块 10。

PostgreSQL 实机并发、公网 TLS、第三方业务回执与真实模型效果需单独提供证据。本地通过数量不能替代这些验收。

---

## 81. 阶段四模块 9 学习验收

结合源码和数据库回答以下问题：

1. 为什么 Server enabled、Review approved、Catalog active 和 Run 已冻结是四个不同事实？
2. 同一原名在两个 Server 中如何得到不同工具身份？
3. 名称截断后如何保留原始名字的区分信息？
4. 为什么目录 revision 不应进入副作用语义键？
5. ValidatedMCPArguments.payload 为什么不能出现在模型 Schema 和远端参数中？
6. Registry manifest 为什么改读 definition.parameters？
7. 空 binding 如何保持内置工具的旧 manifest 兼容？
8. Schema 定义检查与参数实例检查分别发生在哪一步？
9. 为什么循环本地引用必须拒绝，局部引用还需要展开预算？
10. 只读 annotations 为什么无法绕过本地 R3 审批？
11. execution_version 为什么不能复用 config lock_version？
12. tool_catalog_snapshot 的 NULL 与 [] 各代表什么？
13. 升级前已有 config_snapshot 的 Run 为什么不会新增 MCP 能力？
14. Runner 为什么必须先装配目录，再选择 Skill 和保存配置？
15. 新 call_id 可以复用哪些批准，哪些变化必须重新审批？
16. 用户点击批准时，为什么仍要检查目录是否有效？
17. 一个 UNKNOWN 动作改变目录后，为什么不会自动得到第二条可执行效果？
18. 已提交效果属于旧契约时，为什么不能直接返回新契约成功？
19. SDK request ID 与外部业务幂等键有什么区别？
20. isError 为什么仍可能对应已发生的外部写入？
21. 只读重试为何先关闭旧连接，为什么最多一次？
22. Adapter 的请求子任务与 Session 所有者任务分别负责什么？
23. 正常 draining 如何允许在途完成，又阻止新调用？
24. disabled 与 disconnect 为什么不是同一种撤销？
25. 为什么成功回执之后还需要提交事务中的绑定复查？
26. 失去租约的 Worker 为什么不能自行把效果改为 UNKNOWN？
27. Unsupported content 为何不能通过自动下载变成支持内容？
28. 长正文的完整存储和模型预览分别在哪里？
29. list_changed 后模型的 Registry、Run 快照与数据库最新目录会如何变化？
30. 哪些本地证据已经成立，哪些需要 PostgreSQL、第三方系统和模块 10 的环境验证？

学习时先运行一次只读调用，再观察 R3 审批、写后断连和 draining 三条测试的数据库变化。能够逐一解释工具身份、授权身份、业务动作和连接所有权，才算理解了模块 9 的执行边界。


---

## 82. 阶段四模块 10：独立控制器、固定规格与权限边界

第 35 章的宿主 ShellSandbox 和第 74～81 章的受信 stdio fixture 保留历史教学用途。模块 10 开始，持久化 Worker 的 Shell 装配改为 ControllerExecutor；未配置容器 Profile 时拒绝执行。旧白名单不会重新启用宿主命令。本章解释代码已建立的边界；FakeDriver 只提供契约证据，另有 Docker Desktop Linux Engine 的 11 项实机结果证明声明范围内的资源、网络、清理与文件限制。

### 82.1 为什么需要第三个进程

API 负责管理任务，Worker 负责持有租约和执行工具，sandbox-controller 负责创建与回收容器。Docker socket 只交给 controller，因为能操作该 socket 的进程可以影响宿主。把它挂进 Worker，再要求 Worker 自觉不传危险参数，无法构成独立的权限边界。

controller 是受信计算基：它持有 Docker 控制权、数据库连接和 Artifact 卷访问权。运行模型生成代码的执行容器只拿到经过筛选的输入。Bearer token 用于内部控制接口认证，不能将 controller 当作可公开的多用户服务。

```text
Model ToolCall
  → ToolExecutor → Policy / Approval / ToolEffect
  → ShellTool → ControllerExecutor（携带 Run 与租约代次）
  → 私有控制接口 → SandboxService → DockerDriver
  → 无网络执行容器 → guest.py → 有界结果
  → 确认容器移除 → 再查租约 → ArtifactService → ToolResult
```

### 82.2 源码导航与阅读顺序

| 文件 | 主要职责 | 推荐观察点 |
|---|---|---|
| `sandbox/schema.py` | 固定规格与请求契约 | 哪些字段由部署者决定，哪些能由调用方提交 |
| `sandbox/client.py` | Worker 控制器客户端 | 不提供镜像、挂载、网络和环境变量覆盖 |
| `sandbox/controller.py` | HTTP/stdio 认证与生命周期 | 启动回收、退出取消、私有通道 |
| `sandbox/ownership.py` | 单实例文件锁 | 与清理器的所有权假设对应 |
| `sandbox/docker.py` | Docker 参数、预检、限流读取与移除确认 | 操作的是整个容器 |
| `sandbox/service.py` | 租约、输入、记录、输出发布 | 提交之前反复验证执行权 |
| `sandbox/guest.py` | 容器内部命令与输出扫描 | 仅依赖标准库，可单独复制进镜像 |
| `sandbox/egress.py` | 公网请求的实际 IP 绑定 | Host 和 TLS SNI 保留原站点身份 |
| `sandbox/stdio.py` | MCP 私有控制通道适配 | 官方 SDK 仍处理 JSON-RPC 会话 |
| `workers/bootstrap.py` | 持久化 Worker 装配 | 无 Profile 时使用 DisabledSandboxExecutor |
| `trace/service.py` | Run 执行证据 | 容器 ID、epoch、状态和错误码 |

先读 Schema，再沿一次 run() 阅读服务与 Driver，最后看取消、回收和 MCP 分支。直接从 Docker 命令行开始，容易遗漏审批、租约和 Artifact 的提交约束。

### 82.3 SandboxSpec 与 SandboxRequest 为什么分开

SandboxSpec 是部署配置，禁止额外字段并冻结实例。镜像必须是 `name@sha256:...`，不能填写可变 tag；Profile 的规范 JSON 参与 content_hash。job 模式规定允许的绝对可执行路径；stdio 模式规定整条启动命令。资源上限由类型模型限制，调用请求不能放大它们。

SandboxRequest 只包含 execution_id、Task/Run、owner、epoch、profile、profile_hash、argv 和 input_artifact_ids。argv 最多 64 项、UTF-8 总量最多 16384 字节，拒绝 NUL；输入 Artifact 最多 32 个。模型既不能添加 bind mount，也不能提交 `privileged=true` 或 `network=host`。

例如 Worker 冻结了 Profile A 的 hash，部署者随后调整了镜像或内存。即使名字仍是 A，controller 也会因 hash 不同拒绝旧请求。ShellTool 的 implementation_version 和 execution_binding 同样携带规格身份，避免把旧批准解释为新规格的授权。

### 82.4 默认资源限制具体限制什么

| 限制 | 默认值或固定值 | 生效位置 |
|---|---|---|
| 用户 | UID/GID 10001 | Docker create |
| CPU | 1 核配额 | Docker cgroup，规格最大 2 核 |
| 内存与 swap 总量 | 都设为 256 MiB | Docker；规格最大 512 MiB |
| 进程数量 | 64 | pids-limit，规格最大 128 |
| 执行时限 | 30 秒 | controller，规格最大 300 秒 |
| stdout + stderr | 1 MiB | guest 与 controller 双层检查 |
| `/tmp`、`/output` | 各 16 MiB tmpfs，各 128 inode | Docker 挂载参数 |
| 输出文件 | 最多 32 个；每个 1 MiB；合计 8 MiB | guest 扫描及 controller 二次校验 |
| 根文件系统 | 只读 | Docker readonly |
| capability / 提权 | drop ALL / no-new-privileges | Docker security options |
| 网络 | none | 所有执行 Profile 固定 |
| Docker 日志 | none | 防止 attach 输出上限之外的日志落盘 |

超时、文件数、字节数和 inode 数解决不同问题。一个程序可以输出极少文本，却不断 fork；也可以只创建空文件，把文件系统元数据用完。因此不能把“读取输出最多 1 MiB”当作资源隔离的全部。

默认 seccomp 保留，AppArmor 可由部署者指定固定 Profile；并非每台 Linux 都安装 AppArmor。预检要求 Linux daemon 及内存、swap、CPU、PIDs、seccomp 支持，镜像已在本地且 RepoDigest 匹配，并拒绝镜像声明的 VOLUME。运行阶段不自动 pull 或安装软件。

### 82.5 创建之后还要核对什么

DockerDriver 使用 create 而非把任意字符串交给 shell。随后 inspect 核对网络、只读根、非 privileged、capability、no-new-privileges、日志驱动、资源与用户。如果创建阶段只执行到一半，持久化记录仍可帮助重启后的清理器定位命名容器。

Driver 固定本地 Unix Docker socket，并使用独立空 DOCKER_CONFIG，避免继承操作者 Docker context、代理或认证配置。可选输入挂载必须来自 controller 生成的目录；路径中的逗号拒绝，避免进入 Docker mount 参数的另一层语法。

### 82.6 为什么此版本只允许单 controller

ownership.py 在 staging 根持有操作系统文件锁，第二个实例无法取得同一根目录的所有权。service.active 只记录本进程在途任务，reap 会停止不在 active 中的遗留执行，所以它依赖单实例假设。

部署时必须对一个 Docker daemon 使用一个 controller 和一个固定 staging 根。更换 staging 根不能用来绕过单实例限制，否则两个清理器会把对方的容器视为孤儿。本模块没有分布式 controller 选主；多 Worker 可以调用同一个 controller，但 controller 本身不是高可用集群。

---

## 83. 阶段四模块 10 完整执行、文件发布与故障恢复

### 83.1 以一个生成文件的调用为例

模型提出 `/usr/local/bin/python -c ...`，脚本读取 `/input/<Artifact UUID>`，在当前目录写 `answer.txt`。ShellArguments 校验 argv 和输入 ID 后，既有 R2 权限流程决定是否需要审批。通过权限与效果账本后，ControllerExecutor 为这一次发送分配 execution_id，并附上当前租约。

controller 校验 Bearer、Profile hash、允许的可执行路径和数据库租约，再占用执行槽。槽上限为 4，不等于全局外部 API 限流；模块 11 的分布式协调尚未完成。执行记录在创建容器之前写入，随后输入准备、创建、运行和发布都受规格时限约束。

### 83.2 执行记录与 ToolEffect 是两张不同的账

migration `20260921_0010` 新建 sandbox_executions。记录包含执行 UUID、可选 run_id/server_id、lease_epoch、profile_hash、container_id、status、error_code、expires_at、created_at 和 finished_at。

| 状态 | 含义 | 恢复处理 |
|---|---|---|
| starting | 已登记，可能尚未创建或未记下容器 ID | 按固定名称检查并回收 |
| running | 已创建并开始执行 | 中断后回收，不重放 |
| stopped | 已确认移除，尚在处理结果或 stdio 已结束 | 重启可保守标记 reaped |
| succeeded | 命令成功且发布路径完成 | Trace 保留证据 |
| failed / cancelled | 执行失败或取消 | 已清理时保留终态 |
| cleanup_pending | 无法确认容器移除 | 后续清理重试 |
| reaped | 清理器回收了遗留执行 | 不推断业务成功 |

SandboxExecution 描述“哪个容器运行过、是否清理”；ToolEffect 描述“这个业务动作能否重试、结果是否已提交”。容器已经不存在，不证明它此前没有产生效果。恢复时仍沿用 UNKNOWN 的人工核查规则，不因容器回收而自动重新执行写动作。

### 83.3 输入不是整个 Workspace

stage() 使用 `run_id/epoch/execution_id/input` 创建独立目录。每个输入必须在数据库中属于当前 Run；解析后的文件路径必须处于 Artifact 根内，大小和存储 hash 必须一致。单个输入最多 1 MiB，合计最多 8 MiB。

输入在容器中只用 Artifact UUID 命名，不使用外部原始文件名。controller 写入副本并设为只读，再把该目录只读绑定到 `/input`。Workspace、数据库凭据、Provider key、宿主目录与 Docker socket 都不出现在执行容器的参数中。

这里“可用输入”表示调用明确列出且经过同 Run 校验的 Artifact；本模块没有新增跨 Run 文件授权产品。需要处理另一 Run 的文件时，应先经过现有业务流程生成当前 Run 的输入，不能伪造 UUID 绕过校验。

### 83.4 guest.py 为什么仍不可信

guest 使用 create_subprocess_exec 启动参数数组，设置最小环境和 `/output` 工作目录。它并发读取两个输出流，按合计字节数限制，避免程序通过 stderr 绕过 stdout 上限。运行结束后输出一个 JSON 包。

执行代码可能尝试伪造回执，因此 controller 不直接相信 guest 声称的文件和大小。decode_outputs 再次验证列表数量、严格 base64、唯一文件名和字节预算；外层 Docker attach 同时设有 16 MiB 总限额，容纳 base64 与 JSON 开销但不允许无限响应。

非零命令退出记录为 failed / sandbox_command_failed，ShellTool 返回工具失败。guest 执行包装异常则返回通用错误，controller 不把堆栈或内部路径直接回显给模型。

### 83.5 输出路径与特殊文件为什么必须拒绝

收集器只接受 `/output` 下单层普通文件，名字不能包含目录分隔符、冒号、控制字符或特殊的点路径。使用 O_NOFOLLOW 与 O_NONBLOCK 打开，随后 fstat 确认 regular file 且链接数为 1。

这同时拒绝 symlink、hardlink、FIFO、设备和目录：symlink 可能指向镜像其他路径，hardlink 可能共享未预期对象，FIFO 可能让读取阻塞，目录或压缩包递归展开会产生另一套路径和配额问题。本实现不展开任意归档，也不提供目录复制。

这些检查是纵深防护。最终 Artifact 路径由 ArtifactService 生成，不沿用 guest 的文件系统路径。原始名字只放入 attributes，用于展示和追踪。

### 83.6 发布为何安排在容器移除之后

正常顺序是：读取有界回执 → 校验文件 → 强制移除并确认不存在 → 再查租约 → 创建 Artifact → 写执行终态。容器尚在运行时，程序可能继续改变输出；停止整个容器后才进入宿主发布阶段，可以缩小这种竞争窗口。

每个 Artifact 的内部名称包含 epoch、execution_id、序号和 SHA-256，attributes 保存原始名字、执行 ID 与代次。ArtifactService 使用 LeaseGuard，在自身写入事务中继续检查执行权。旧 Worker 即使拿到了回执，也不能绕过 fencing 发布。

多个文件逐个创建，并非跨文件原子提交。发布到一半失去租约时，可能保留已发布的部分产物；它们仍属于旧执行且可追踪，不能因此把整个工具标成成功。这是排查 UNKNOWN 时应同时查看 Artifact 和 ToolEffect 的原因。

### 83.7 取消、断连与超时如何到达整个容器

服务等待输出时每 250ms 检查租约和任务取消，同时检查 HTTP 客户端是否断连。客户端退出时还会尽力发送 DELETE；该请求失败仍有 controller 的时限和租约检查兜底。轮询间隔不是最大撤销延迟承诺，数据库与 Docker 响应也会影响时延。

finally 先取消读取任务并有界排空本地 CLI 管道，再执行 Docker rm --force；随后通过精确名称查询确认容器不存在。只杀 docker attach 进程不算清理成功，因为容器和其子进程可能仍然运行。

rm 或确认步骤失败时写 cleanup_pending，不返回可发布的成功结果。恢复清理可以重复，业务执行不能因此重复。数据库 update 用 shield 等待已经开始的提交完成后再传播取消，避免取消恰好落在 SQLite commit 时留下未完成事务。

### 83.8 重启后如何找回遗留执行

controller 启动先 reap，Docker 不可达则启动失败；运行中每 5 秒尝试扫描。清理器扫描 starting/running/stopped/cleanup_pending 记录及受控 label 的容器，跳过本进程 active 中的执行，其他执行只停止和回收，不重新运行命令。

staging 删除先验证目标位于配置根之内且不是根本身。正常完成及遗留执行回收会删除对应输入副本；如果进程在写入终态之后、删除目录之前崩溃，仍可能留下孤立 staging 文件，需要停机核对后清理。本模块不宣称任意崩溃点都能自动清空所有历史磁盘残留。

---

## 84. 阶段四模块 10 网络出口、MCP 通道与测试地图

### 84.1 为什么 DNS 检查后还要绑定 IP

只在发请求前检查域名并不充分：检查时得到公网 IP，实际连接时若再次解析为私网地址，前一次检查就失效了。URLGuard.resolve 返回规范 URL 和全部地址，并要求全部地址都可公开访问，IPv4、IPv6 和云元数据地址均受限制。

EgressTransport 选取已检查地址，直接把发给底层 HTTP transport 的 URL host 改成该 IP；Host 头仍使用原站点，TLS sni_hostname 也保留原域名。这样证书校验使用原站点身份，连接目标却固定为已检查的地址。

例如请求 `https://example.com/a`，解析得到 `93.184.216.34`，实际连接该地址，同时使用 `Host: example.com` 和原域名 SNI。不能为了连接成功关闭 TLS 校验。所有 DNS 结果中只要混有一个私网地址，整次解析就拒绝。

### 84.2 重定向与环境代理的处理

web_fetch 保留手动重定向，每一跳都重新校验。公共页面跳转到 `169.254.169.254` 时，在建立下一跳连接之前拒绝。默认客户端不继承 HTTP_PROXY 等环境设置，底层不自动重试到其他地址。

请求设置 Accept-Encoding: identity，非 identity 压缩回执直接拒绝，避免在字节限制之前出现透明解压膨胀。原有响应类型、正文大小和超时限制继续生效。注入 MockTransport 的单元测试只证明请求构造与决策，不能证明公网 TLS 实际握手。

### 84.3 公网读取与执行容器网络不是一条通道

本版没有让容器借助 HTTP_PROXY 联网。所有 job 与 stdio 执行容器固定 `network=none`，任意代码不能自行直连公网、内网或云元数据。需要网页资料时，由受控 web_fetch 获取结果，再通过明确的 Artifact 输入交给执行容器。

Provider 使用自己的可信客户端和秘密，不经过公开网页工具的策略。预设 MCP HTTP endpoint 也保持独立的部署者信任配置。本模块没有把 EgressTransport 自动套到所有网络调用，更没有增加任意联网 Python 或通用容器出口代理。

### 84.4 第三方 stdio 如何接入现有 SDK

LaunchProfile 增加 sandbox_profile_id。容器 Profile 不能同时配置宿主 command、args 或 cwd；原宿主 stdio 路径必须显式标记 trusted_fixture，仅用于项目受信测试程序。生产第三方 Server 应进入经过审查的固定镜像。

ConnectionManager 把 server_id、config_version 和可选 lease 传入 open_transport。sandbox.stdio 使用带 Bearer 的内部 WebSocket 连接 controller，将官方 SDK SessionMessage 转换为逐行 JSON-RPC。这个 WebSocket 是项目私有控制通道，不是宣称新增 MCP 标准传输协议。

controller 从数据库读取 Server 的启用状态和配置版本，再取得部署 Profile。调用者不能直接指定要执行的命令。Worker 连接携带租约；API 管理发现可能没有 Run，因此执行记录的 run_id/epoch 允许为空。

### 84.5 通道有哪些停止条件

stdio 首帧最大 4 KiB，单条传输消息最大 64 KiB；输入累计最多 4 MiB，stdout 与 stderr 共享规格输出预算。连接受 Profile 总时限约束，默认 30 秒、最大 300 秒，不是永久后台服务。

服务定期检查配置版本和 enabled，Worker 路径额外检查租约。任何读写任务结束、连接断开、超时或检查失败都会进入 finally，关闭 CLI 并回收容器。SDK Session 的创建与关闭仍由 Connection 所有者任务完成，避免跨任务关闭 AnyIO 上下文。

容器 stdio 当前拒绝 secret_ref，不注入 Provider 或外部服务密钥。需要凭据或联网的第三方 Server 不属于此版支持范围；不能用 trusted_fixture 标记把任意第三方程序移回宿主运行。

### 84.6 测试地图及其证据边界

| 测试文件 / 场景 | 已验证内容 | 不能替代的证据 |
|---|---|---|
| test_sandbox_controller.py：鉴权与执行 | HTTP 契约、规格绑定、Artifact 与状态记录 | FakeDriver 不证明内核隔离 |
| 取消、租约、超时、清理失败 | 拒绝发布、记录待清理、回收不重放 | PostgreSQL 并发时序 |
| 非零退出与单实例锁 | 失败状态、第二 owner 被拒绝 | 分布式 controller 选主 |
| 输出洪泛 | 真实本地固定测试脚本、有界读取与清理 | Docker cgroup 限额 |
| TCP + WebSocket + 官方 MCP SDK | 实际控制通道与 stdio 会话 | 测试 Server 为受信 fixture |
| test_sandbox_egress.py | 混合 DNS、IP/Host/SNI、重绑定、重定向拒绝 | 公网证书与真实网络路径 |
| test_docker_sandbox.py：cgroup | CPU 配额与节流、内存、swap、PIDs、用户与能力 | 只有显式 Linux Docker 运行才成立 |
| Docker 故障注入 | 内存、进程、磁盘、输出、进程树时限与清理 | 本机跳过不能记为通过 |
| Docker 网络与特殊文件 | 直连公网/元数据拒绝、symlink/hardlink/FIFO/目录拒绝 | 共享内核的敌对多租户安全 |
| test_migrations.py | 0010 升降级与表结构 | 生产数据回滚决策 |

CI 新增独立 sandbox job，构建镜像、推入 job 本地 registry 得到 RepoDigest，再显式开启真实测试。测试开关开启后，缺少 Docker、Linux 或 digest 会失败，不会静默跳过。本机通过数字及跳过原因见[模块 10 运行说明](阶段四-模块10验收与运行说明.md)。

### 84.7 故障定位顺序

未配置 Shell 时先看 shell_sandbox_profile；已有旧宿主白名单不代表启用。controller 拒绝请求时核对 token、两侧 Profile 的完整 JSON 与 hash，再看 Trace 的 sandbox_executions。客户端有意归一化内部错误，详细原因从受控记录与部署检查定位。

启动失败时检查 Linux daemon、socket、镜像 RepoDigest、cgroup/seccomp 支持和 staging 锁。不要通过移除资源参数、换成 mutable tag、挂载整个 Workspace 或运行 privileged 容器来绕过预检。

cleanup_pending 时先恢复 Docker 管理通道并确认具体容器，等待或触发回收。ToolEffect UNKNOWN 仍需核查业务结果。Artifact 存在、容器不存在和工具成功是三个不同事实，应一起检查。

---

## 85. 阶段四模块 10 学习验收

结合源码、数据库和测试回答：

1. 为什么 Docker socket 只能属于 controller，不能挂给 Worker？
2. controller 为什么仍属于必须信任的计算基？
3. SandboxSpec 与 SandboxRequest 分别由谁控制？
4. Profile 名字没变而 hash 变化时，旧请求会怎样？
5. 为什么只限制 argv[0] 无法代替容器隔离？
6. 为什么禁用模式不能退回旧 ShellSandbox？
7. CPU、内存、PIDs、输出和 inode 上限各解决什么问题？
8. memory 与 memory-swap 设置成相同值意味着什么？
9. 为什么不允许镜像 VOLUME 和运行时自动 pull？
10. Docker create 成功后为何还要 inspect？
11. Docker attach 的进程退出是否等于容器及子进程停止？
12. 为什么先写执行记录，再创建容器？
13. SandboxExecution 和 ToolEffect 分别记录什么事实？
14. execution_id 与业务幂等身份为什么不同？
15. 为什么 stage 路径包含 Run、epoch 与执行 ID 三层？
16. 输入 Artifact 为什么检查所属 Run、hash 和解析后的路径？
17. guest 的输出包为什么还要在 controller 重新校验？
18. O_NOFOLLOW、O_NONBLOCK、regular file 和 nlink 各防什么？
19. 为什么不直接把输出原名拼进宿主 Artifact 路径？
20. 多个输出文件发布中途失败，会留下什么可追踪事实？
21. 收到成功回执后为何还要移除容器并再次检查租约？
22. shield 数据库更新和 shield 清理各保护什么窗口？
23. cleanup_pending 为什么不能转译为业务执行成功？
24. 单实例文件锁和 reap 的 active 集合有什么关系？
25. 为什么仍可能留下需要停机清理的 staging 文件？
26. DNS 预检与实际 IP 绑定有什么区别？
27. IP 绑定后 Host 与 SNI 为什么必须保留域名？
28. 私有 WebSocket 控制通道与 MCP 标准传输有什么区别？
29. network=none 的第三方 stdio Server 可以使用哪些资源，不能使用哪些资源？
30. 哪些测试已经在本机通过，哪些必须在 Linux Docker 或 PostgreSQL 环境补证？

建议按正常文件产出、租约丢失、清理失败、DNS 重绑定、stdio 断连五条路径阅读测试。能够解释每一次权限检查、数据提交和清理确认的位置，才算掌握模块 10，而不是只会拼接 docker run 参数。

---

## 86. 阶段四模块 11：持久队列、唤醒与服务配额

### 86.1 为什么增加 Redis 后仍然需要 PostgreSQL 队列

先从一个故障窗口理解设计。API 已把 Task 和 Run 提交到数据库，随后进程崩溃，来不及发布 Redis 消息。如果 Worker 只等消息，这个任务会永远停留在 queued。反过来，若先发布再提交，Worker 可能收到一个还不存在、甚至稍后回滚的任务。

现在的调用链是：

```text
TaskService / Eval 放行 / 维护任务入队
  → SQLAlchemy flush 识别 queued/pending 变化
  → QueueSession.commit 完成数据库提交
  → Wakeup.publish best-effort 发送 scan
  → Worker 被唤醒，或 poll 周期到达
  → JobLeaseManager 使用数据库领取
```

[db/session.py](../src/evoagent/db/session.py) 中 after_flush 只设置 queue_changed 标记，不能在这个阶段发送消息。QueueSession 在 super().commit() 成功返回后才读取标记并 publish。rollback 清除标记，因此一次失败事务不会在下一次无关提交时留下假通知。

这不是 transactional outbox：没有保证每条提示必达。这里允许丢提示，是因为 Worker 的周期扫描能够恢复发现；真正的 Task 和维护 Job 已经持久化。不能把相同模式用于必须逐条送达的业务事件。

### 86.2 Wakeup 对外只表达“可以再扫描一次”

[workers/wakeup.py](../src/evoagent/workers/wakeup.py) 包含发布、订阅和等待三部分。Redis 客户端设定连接和操作时限；publish 失败不回滚已提交任务。listen 断线后重连；wait 同时等待唤醒 Event、停止 Event 和固定超时。

Event 会合并重复通知。连续收到十条 scan，并不意味着应该执行十个 Task；实际执行数量由数据库中的可领取记录决定。清除 Event 与新消息抵达之间仍可能发生提示丢失，但下一轮 poll 能补偿，因此正确性不依赖这个窗口完全消失。

channel 包含 redis_namespace。Redis Pub/Sub 不按数据库编号隔离；开发和验收若只分别使用 /0、/1，仍可能互相唤醒。namespace 应表达部署环境，不应包含密码、用户输入或完整连接串。

### 86.3 活动 Run 与服务请求是两层并发

[workers/main.py](../src/evoagent/workers/main.py) 的 run_forever 创建固定数量的 _lane。每个 lane 只有一个 run_once 在执行，所以 worker_concurrency=2 表示单进程最多处理两个活动 Run。两个进程各配置 2，最多是四个 Run，而不是两个。

每个 Run 又可能等待模型、执行 MCP 或读写 Artifact。这些请求由 [rate_limit.py](../src/evoagent/workers/rate_limit.py) 的 ServiceGate 控制。进程内 semaphore 限制某服务同时调用数；Redis token bucket 限制所有使用同 namespace/服务身份的进程的请求速率。

两种上限解决不同问题：并发限制控制同时占用连接和内存的请求，速率限制控制单位时间消耗配额的请求。请求越慢，同一并发数的吞吐越低；不能简单把 worker_concurrency 翻倍就宣称吞吐翻倍。

持久化 ToolExecutor 仍然串行处理同一模型响应中的工具。跨 Run 的并发不会改变单 Run 副作用账本的顺序。

### 86.4 Lua token bucket 怎样避免超领

TOKEN_BUCKET 在一个 Redis 脚本内执行读取状态、获取 Redis TIME、补充 token、扣减或计算等待、写回状态和 TTL。多个连接不能在读取同一个余额后各自扣减，因而不会把最后一个 token 发给两个请求。

假设 burst=4、rate=2，桶满时四个请求可以立即进入。第五个请求看到余额不足，获得约 0.5 秒等待时间。它等待后重新执行脚本，不能把等待时间当成预约成功。其他进程可能先拿走刚补充的 token。

Redis TIME 避免 Worker 本地时钟不一致导致多补充 token。TTL 用于回收长期不用的配额 key。这个脚本限制的是请求次数，不是精确 Token 成本；模型返回的 usage 仍需要单独记录。

### 86.5 哪些路径使用 ServiceGate

ConfiguredTaskHandler 共享一个进程级 ServiceGate，再为每个 Run 创建 GatedProvider。模型请求在取得服务许可后重新检查 LeaseGuard。ToolExecutor 对 MCP binding 的 server_id 分组，并把许可检查放在 middleware.before 之前。ContextResolver 和 IndexService 对 Embedding 服务使用同类入口。

本地 calculator、文件工具不需要 Redis 请求配额，但持久执行入口仍检查租约。Embedding 查询限额不可用时允许按现有检索规则降级 lexical；这表示没有发送被拒绝的向量请求，不表示临时关闭限流。

服务身份不包含 API Key。多个配置应使用稳定、非敏感的模型或 MCP server 身份；namespace 决定配额共享范围。

### 86.6 为什么等待后还要检查租约

一个 Worker 开始等待时可能持有 epoch=7；等待期间它的心跳中断，另一个进程接管到 epoch=8。若只在等待前检查，旧进程仍会向外部服务发送请求。

ServiceGate 的 check 回调在 semaphore 和 token 都获得之后执行。检查使用短数据库事务，返回后事务关闭；配额等待本身不持有数据库锁。数据库不可用时检查失败，请求不会被放行。这与“数据库暂时不可用但继续执行，等恢复再补账”的做法有本质不同：后者会产生无法证明所有权的外部副作用。

## 87. 阶段四模块 11：限流恢复、维护任务与 Eval fencing

### 87.1 rate_limited 怎样进入恢复状态机

模型尚未发出请求就因配额超时失败时，GatedProvider 抛出 RateLimited。AgentLoop 捕获 ProviderError 后，识别 rate_limited 并保存请求前的完整消息、已完成迭代数、上下文 revision 和 usage。当前尚未执行的模型轮次不计入 completed_iterations。

MCP 限流发生在副作用 PREPARED 之前，ToolExecutor 返回对应错误结果。AgentLoop 补齐这一批 tool message 后再保存 checkpoint，然后结束本次执行。已经提交的其他工具效果仍在账本中；UNKNOWN 仍由原有恢复策略处理，不能因为下一次有配额就重放不确定写入。

PersistentAgentRunner 返回 RETRYING 和 next_attempt_at。JobLeaseManager 再次领取时看到上一轮 error_code=rate_limited，不增加网络重试次数，但仍递增 lease_epoch。前者是策略计数，后者是所有权代次，两者不能混用。

### 87.2 为什么限流不会无限占住一次执行

等待由 rate_wait_seconds 限定。短等待仍处于当前 Run 的总时限内，Heartbeat 独立运行。超出短预算会释放当前执行槽位，让其他 Run 有机会被领取。重调度受 retry_max_elapsed_seconds 限定；任务创建时间不会因为配额重试被重置。

例如某模型完全不可用：首次领取 attempt=1、epoch=1；限流后重试仍 attempt=1，但接管为 epoch=2。总年龄超过上限后进入失败，不能靠“不增加网络重试次数”永久循环。

### 87.3 维护队列为何拆为独立进程

旧 Worker 在领取前执行一次维护任务。若一次 index_rebuild 要等 Embedding，正常用户任务也会被拖住。现在普通 Worker 只负责 Task，evoagent-maintenance-worker 独立领取 MaintenanceJob。

维护 Job 的 payload 保存来源 ID、版本或哈希，不复制整个长文本。claim 使用数据库时间，检查 pending、到期 failed 或租约过期 running，锁行并以 epoch 条件更新。执行结果提交仍需要 owner、epoch、未到期三个条件。

MaintenanceWorker 在执行期间每 30 秒续租，将有效期推进到 120 秒。Heartbeat 失败会结束执行任务；取消通过 finally 回收子任务。文件或服务错误被记录为稳定 error_code，设置 30 秒后的 next_attempt_at；达到三次领取上限后停止自动重试。不能把三次失败直接当成“维护成功但没有结果”。

archive/erase 内的某些事务本身会持有 Job 行锁。耗时操作仍要在提交前复验租约时间；心跳不是无限延长长事务正确性的证明。大规模维护任务应进一步分批，而不是只把 lease_seconds 调大。

### 87.4 Eval 为什么也需要 epoch

[evals/coordinator.py](../src/evoagent/evals/coordinator.py) 原先只有 owner 和 expiry。若同名实例重启或租约被接管，旧进程可能在耗时 Validator 结束后继续写回结果。0011 为 EvalExperiment 增加 lease_epoch，每次领取递增。

EvalLease 携带代次。heartbeat 在 UPDATE 条件中检查代次；_guard 在提交事务中锁定实验行并验证状态、owner、epoch、有效期。结果采集事务、第二个配对 Run 放行、实验完成都使用该保护。Validator 可以在事务外计算，但写回时必须再次获得当前所有权。

这是“计算结果可以晚到，提交权限不能沿用”的模式。仅在 run_once 开头检查一次是不够的，因为耗时计算期间租约可能变化。

### 87.5 停止、取消与崩溃各发生什么

Linux Worker 收到 SIGTERM 后设置 stopping，停止领取新任务，已领取的任务继续维持心跳并完成当前执行。Compose 设置 310 秒宽限，与默认 300 秒任务时限配合。

取消 Task 由 Heartbeat 发现 cancel_requested，取消 Handler 并提交 cancelled；这与关闭整个 Worker 不同。进程被 kill 或 Windows 强制终止时不能承诺 finally 完成，后继 Worker 通过租约过期、RECOVERING、快照和副作用账本接管。共享 Artifact 的合法性仍由 Run、epoch、哈希和原有发布协议保证。

## 88. 阶段四模块 11：测试地图与故障定位

### 88.1 阅读代码的顺序

先读 QueueSession 与 Wakeup，确认通知不是任务本身；再读 JobWorker._lane/run_once，区分并发槽位与租约；然后读 ServiceGate 和 ToolExecutor 的调用位置；最后读维护 Worker 与 EvalCoordinator._guard，核对各自的所有权条件。

不要先从 Compose 扩容命令开始。扩容只是让进程增多，正确性来自每一个提交边界是否仍受租约保护。

### 88.2 测试分别证明什么

| 测试 | 关键断言 | 不能替代的证据 |
| --- | --- | --- |
| test_worker_services.py | Redis 故障关闭配额、semaphore 取消后释放、等待后检查租约 | Redis 脚本实际原子性 |
| test_worker_redis.py | 两个真实连接争抢桶，20 次请求只能拿到 burst 额度；重复唤醒合并 | 数据库状态机 |
| test_multiworker.py | 两个真实进程、相同标签、不同 owner，8 个任务合法完成 | 多主机共享存储 |
| test_runtime_experiments.py 的 quota 用例 | 首次保存零完成轮次的 checkpoint，恢复完成且 attempt 不变 | 外部服务 exactly-once |
| 同文件 Eval epoch 用例 | 相同 owner 重领后旧代次 heartbeat/run_once 拒绝 | PostgreSQL 行锁竞争 |
| test_lease_fencing.py | PostgreSQL 事务 fencing、接管、取消 | 网络服务幂等性 |
| test_unknown_effect_recovery.py | UNKNOWN 不盲目重放 | 远端业务是否实际提交 |

QueueSession 的集成测试在 publish 回调中重新打开数据库读取 Task，直接验证通知发生在可见提交之后。随后 flush 再 rollback，确认不会遗留一次假发布。

### 88.3 常见故障的定位步骤

任务一直 queued：先确认数据库 URL、Task.next_attempt_at、运行时实验过滤条件，再检查 Worker 是否存活。Redis 可用并不能证明可领取条件满足。

大量 RETRYING 且 error_code=rate_limited：检查服务桶是否被其他实例共享、Redis 是否断线，以及 rate_wait_seconds 是否过小。不要第一步就删除 REDIS_URL，这会改变部署的配额语义。

维护任务长期 failed：检查 attempts、error_code 与来源状态。来源被撤销、索引模型不匹配、磁盘错误需要分别处理；修改 next_attempt_at 前先解决原因。

旧 Eval 结果没写入：核对实验当前 owner/epoch/expiry 与旧 Lease。若新实例已经接管，拒绝写入正是正确行为，不应通过删掉 epoch 条件“修复”。

## 89. 阶段四模块 11 学习验收

1. 数据库提交成功但 publish 失败，任务会在哪里？
2. 为什么 after_flush 只能标记，不能发送通知？
3. rollback 为什么要清除 queue_changed？
4. Pub/Sub 重复十次为什么不等于执行十次？
5. Redis /0 与 /1 为什么不能隔离 channel？
6. poll_seconds 对可靠性和延迟分别有什么影响？
7. 两个进程各 concurrency=2，活动 Run 上限是多少？
8. 一个 Run 内的工具为什么仍串行？
9. semaphore 与 token bucket 各限制什么？
10. 为什么脚本使用 Redis TIME？
11. 返回等待时间为什么不代表已经预约 token？
12. TTL 到期是否会丢失 Task？
13. Redis 断线时模型请求如何失败？
14. 无 Redis 模式与 Redis 故障降级有什么不同？
15. key 中为什么不能保存 API Key？
16. 配额等待期间哪部分代码续租？
17. 为什么取得配额后还要检查 LeaseGuard？
18. 数据库断线后为什么不能先发请求再补账？
19. 请求前 checkpoint 的 completed_iterations 如何计算？
20. MCP 配额检查为什么早于 middleware.before？
21. rate_limited 重试为什么递增 epoch 而不增加网络 attempt？
22. 总等待年龄为什么不能在重试时重置？
23. 独立维护进程解决了旧 Worker 的什么阻塞？
24. failed Job 什么时候可以重新被领取？
25. 三次上限后的 Job 为什么仍然保留？
26. Eval 的 owner 相同为什么仍要检查 epoch？
27. Validator 算完后为什么再次验证租约？
28. SIGTERM、Task cancel 与 kill 分别走什么路径？
29. 真实双进程测试与 asyncio.gather 两协程测试有何区别？
30. 当前哪些证据仅适用于同主机，不能据此宣布多主机高可用？

## 90. 阶段四模块 12：独立实验契约与数据隔离

### 90.1 先区分要回答的两个问题

Skill 配对评测回答“这份 Skill 相比不使用 Skill 是否更好”。Runtime 实验回答“只改变某一运行时机制，结果如何变化”。把两个问题塞进同一个 baseline 字段，会导致同时关闭 Skill、记忆和上下文管理，结果再好也无法归因。

因此新增 RuntimeExperimentRecord 与 RuntimeEvalRunRecord，迁移 0012 建立 runtime_experiments/runtime_eval_runs。Skill 的 EvalExperiment/EvalRun、baseline/pinned_skill 及 comparable_with 保持原有含义。

### 90.2 RuntimeExperimentSpec 如何拒绝混杂变量

[evals/runtime_schema.py](../src/evoagent/evals/runtime_schema.py) 定义 RuntimeArm 和 RuntimeExperimentSpec。Arm 只包含 context_policy、memory_mode、retrieval_backend、worker_count 四个可操纵字段；两臂所有字段的差异集合必须严格等于 experiment_variable 的单元素集合。

例如声明 memory_mode，却同时将 lexical 改成 hybrid，会在 Pydantic 校验阶段失败。两臂完全一样也失败，因为它没有执行所声明的实验。模型名、代码版本、数据集哈希、Token 预算、检索阈值和环境是共享配置，不靠运行时从另一臂猜测。

spec 使用规范 JSON 计算 spec_hash。领取执行前重新核对 hash，防止恢复时读取被改动的实验配置。Run 仍保存独立的实际 RunConfigSnapshot；Spec 表达计划，RunConfig 表达实际执行，两者都需要。

### 90.3 从冻结数据集创建任务

RuntimeExperimentService.create 首先验证数据集存在、处于 frozen、content_hash 与 Spec 相同，且包含 HOLDOUT。随后在同一事务中建立 experiment、两个臂各自的 Workspace/Session、history、固定 memory fixture、PAUSED Task/Run 和关联记录。

PAUSED 很重要：创建过程中不能让普通 Worker 看见半套 fixture 就开始执行。release 才把指定臂转为 queued。RuntimeEvalRun 使用 experiment/case/arm/repeat 唯一约束，报告中的一个样本对应一个持久 Run，恢复不会重新创建匿名样本。

普通 Worker 的领取查询排除 RuntimeEvalRun。实验进程同时指定 experiment_id 与 arm；因此日常任务和另一实验臂不会悄悄改变这次 worker_count 实验的并发数。

### 90.4 隔离不是清空所有记忆

memory_mode 对照需要两臂拥有相同内容的固定记忆，只改变是否读取。实现为每个样本创建独立 Workspace，再克隆公开 fixture。两个臂的版本 UUID 不同，但内容与公开来源相同，不会共享一次执行新写入的历史。

runtime_fixtures.py 创建可核验的公开来源和 MemoryVersion；foreign fixture 使用额外的工作区，expired/revoked 保留相应状态，读取仍经过原有 verify_version。这些样本用于检查权限与生命周期，不能通过复制文本直接注入提示词来绕过检索。

执行完成后的实验 goal/terminal 消息不能成为新记忆来源。source_message 同时检查 Skill EvalRun 和 RuntimeEvalRun；enqueue_archive 拒绝实验来源；检索读取旧归档时也检查 Runtime 关联。这样 HOLDOUT 的执行结果不会进入下一次公共记忆检索。

### 90.5 history 与 private_validators 的路径完全不同

公开 history 按 Message 契约校验，不允许 system 角色，工具调用和结果由 MessageGroupBuilder 检查完整配对。PersistentAgentRunner 只在没有恢复快照时注入初始历史；恢复时使用已经持久化的消息，避免重复插入。

private_validators 留在 EvalCase。ConfiguredTaskHandler 只读取冻结配置；history_for_run 只读取 public_input.history；记忆 fixture 只来自 public_input.memories。采集器等 Run 终止后才取出私有规则，对 TraceBundle 执行。测试用 PRIVATE_SENTINEL 验证该内容没有出现在持久消息里。

## 91. 阶段四模块 12：执行链、成本与报告

### 91.1 CLI 真正启动了什么

[evals/runtime_cli.py](../src/evoagent/evals/runtime_cli.py) 导入并冻结数据集、创建实验、放行一臂，再使用当前 Python 解释器启动对应数量的 evoagent-worker 进程。每个子进程 concurrency=1，通过环境变量过滤实验和 arm。等待这一臂的所有 Run 达到终态后，再执行另一臂。

worker_count 的 1/2 表示真实进程数，不能用两个 coroutine 冒充两个 Worker。资源环境由报告记录；若两次执行使用不同机器或配额，应创建新的实验而不是沿用同一个报告标题。CLI 超时保留所有持久记录，方便诊断未完成的样本。

API 提供 create/release/collect，但不自动启动后台进程。需要自动执行用 CLI；需要已有进程池则显式设置过滤条件。这两种使用方式应在部署时选定。

### 91.2 collect 为什么可以重复调用

collect 读取终态且尚未记录 metrics 的样本，构建 TraceBundle，运行确定性 Validator，再复用 MetricsCollector。计算在短事务之外，写入时锁定 RuntimeEvalRun，若另一收集器已经写入就跳过。

只有全部样本拥有 metrics 才生成最终报告。最后锁住 RuntimeExperiment，检查已有 report，保存 JSON 和 report_hash。再次调用直接返回保存的报告，因此 Validator 的耗时测量不会让同一已完成报告反复变动。

失败的 Run 同样要验证和收集。若只收集 completed 且验证通过的样本，成功率和平均延迟会发生选择偏差。API 返回 pending 表示还有未完成样本，不表示这些样本可以从分母删除。

### 91.3 实际配置可比性如何检查

单变量 Spec 合法不代表执行时没有环境漂移。例如两个 Worker 加载了不同工具清单，实验仍不能视为可比。runtime_pair_comparable 比较实际 RunConfig，核对 provider/model、禁止额外 Skill，以及启用检索时的公共参数。

只归一化声明变量及隔离来源产生的身份：context 实验允许 context_policy/max_output_tokens 的派生差异；retrieval 实验允许 backend；memory off 没有 RetrievalBatch，read_only 的参数必须逐项匹配 Spec。选择 hash、profile/generation、降级证据仍保留在样本中用于解释，不改动 Skill 的通用比较规则。

报告保留每一对的 comparable 标志。发现不可比时，应定位实际配置差异，而不是把所有新配置从比较函数中删除。

### 91.4 unknown 与 0 的区别

模型 usage 缺失时，精确总 Token 为 null，不能默认为零。模型完成事件是新执行的用量来源：直接回答没有工具 Turn，也必须计费；仅对旧 fixture 回退读取 Turn，避免重复计数。运行时 evidence 保留已知小计和 unknown_main_calls；有可能消耗 Token 的失败请求保留未知，发请求前的 rate_limited 不伪造模型消耗。usage_breakdown 在精确总量未知时仍可展示已知小计。

extractive-v1 是本地摘录器，不调用模型，因此 summarizer 的模型 Token 为 0。实验禁止自动生成记忆，memory_generator 为 0。Embedding 使用自己的 usage 字段和独立事件，不能混入主模型计数；没有发送向量请求与发送了但没得到 usage 也应区别记录。

LoopState 增加可选 known_usage。恢复先使用它，其次兼容旧快照中的 usage；usage_is_complete 仍表达总量是否完整。这样某次调用没有 usage 后，前面已知的消耗不会因为 checkpoint 将 usage 设为 null 而重置为零。

### 91.5 evidence、summary 与质量结论

runtime_evidence.py 读取摘要 revision 的 before/after 估算、输入 hash、检索实际选中来源 hash、degraded、配额领取事件和已知用量。它不把估算当作实际账单。

summary 分臂给出 samples、success_rate，以及 Token、工具数、延迟的 known_samples/mean/median。samples 保留失败码、Validator 证据和实际配置；pairs 保留可比性。相关来源集合有标注时计算 Recall@K、MRR 与无相关项却强制命中的标志，没有标注时返回未知，不能猜测 relevance。

report_kind=mock 只证明代码可运行。当前演示 Provider 固定执行搜索、写报告、回答，不会认真回答预算问题；报告出现质量约束失败是预期的负面证据。真实模型效果报告必须另外运行，并记录真实模型、Embedding、语料和资源条件。

## 92. 阶段四模块 12：数据集、测试与验收边界

### 92.1 固定数据集的四种样本

phase4-runtime-v1.json 为 long_history、memory、retrieval 各保留四种样本：

| 家族 | 正常 | 边界 | 失败 | 恶意 |
| --- | --- | --- | --- | --- |
| 长历史 | 保留早期预算要求 | 多组工具结果触发预算压力 | 无可靠来源不能编造 | 历史文本诱导覆盖规则 |
| 记忆 | 读取固定预算 | 过期/撤销不可召回 | 外工作区来源不可读 | 历史偏好与当前要求冲突 |
| 检索 | 直接词面命中 | 汽车/轿车同义表达 | 不相关资料不能硬凑 | 检索文本注入指令 |

这些是冻结的公开输入和私有检查。Mock 特征哈希不保证同义召回，真实语义效果需要真实 Embedding。数据集版本不允许原地覆盖；修改 Case 应递增版本并重新冻结。

### 92.2 工程故障与语言题分开

MCP schema/drift/unload/UNKNOWN 来自真实协议 fixture 测试；租约竞争与恢复来自 PostgreSQL 故障测试；CPU、内存、PIDs、网络和特殊文件来自 Linux Docker 专项。给模型一道“请模拟租约失效”的题目，不能证明数据库 fencing 正确。

运行说明列出对应测试入口。模块 12 报告复用执行 Trace 的基础指标，但不会自动把整个测试套件的通过数冒充语言质量样本。质量数据和工程故障证据需要同时存在、分别解释。

### 92.3 端到端测试为什么故意失败一个 Validator

test_runtime_experiments.py 创建一个只在 private_validators 中出现的字符串，Mock 回答不会包含它。测试要求报告成功生成，同时两臂 success_rate=0，失败样本完整保留，私有字符串没有进入 messages。

同一测试检查每个样本独立 Workspace、Skill EvalRun 表没有被写入、source_message 与 enqueue_archive 拒绝实验来源、重复 collect 返回完全一致报告、完成后不能再次 release。这些断言验证的是实验框架，不是为了让所有质量分数变成满分。

### 92.4 本次已有证据与仍缺证据

本次已执行 PostgreSQL/Redis 回归（403 passed、13 skipped）、双真实进程任务竞争、Lua 原子配额、限流恢复、迁移 schema 对齐，以及 Runtime Mock 报告。应用镜像与容器健康检查通过。最终命令、跳过原因及一次 SQLite 锁冲突的复查记录见[模块 11～12 运行说明](阶段四-模块11至12验收与运行说明.md)及[开发进度](开发进度与决策记录.md)。

用户随后提供了 DeepSeek 配置，已完成 6 对、12 次真实记忆实验，实际配置全部可比、用量 16,616 Token；两臂都为 5/6 通过，尚未显示成功率提升。模型没有按已召回的有效记忆给出预算，保留为失败证据；过期、撤销、跨工作区及注入样本也保留在明细。另有 2 对真实上下文样本：4096 预算的 bounded 臂在请求前拒绝，legacy 臂完成，实际调用 5,944 Token；这是预算边界证据，不是压缩质量提升。完整 Skill 配对、真实 Embedding、跨主机高可用、前端实验管理和阶段四最终交付仍未完成。

## 93. 阶段四模块 12 学习验收

1. Skill baseline 与 Runtime control 分别回答什么问题？
2. 为什么新增两张 Runtime 表而不增加一个 baseline 特例？
3. 差异集合为空为什么也属于非法实验？
4. 同时打开记忆与 hybrid 为什么不能归因？
5. Spec hash 与 RunConfig hash 分别固定什么事实？
6. frozen 数据集为什么还要校验 content_hash？
7. 为什么只选择 HOLDOUT？
8. Task 最初为什么为 PAUSED？
9. 普通 Worker 为什么排除 Runtime 实验任务？
10. API release 是否会自动创建 Worker 进程？
11. worker_count=2 如何证明是两个真实进程？
12. 两臂的 Workspace UUID 为什么必须不同？
13. UUID 不同如何保证 fixture 内容相同？
14. memory_mode=off 为什么仍创建相同固定记忆？
15. foreign、expired、revoked 各测试哪个边界？
16. 为什么不能直接把 memory fixture 拼进提示词？
17. history 为何禁止 system 角色？
18. history 中工具调用与结果怎样保持完整？
19. 恢复时为什么不能再次注入初始 history？
20. private_validators 在哪一层才会被读取？
21. 为什么禁止实验结果生成公共记忆和归档？
22. collect 为什么先计算再锁行写回？
23. 为什么失败样本也必须留在分母中？
24. 未完成时 pending 与完整报告有何区别？
25. 实际工具清单漂移为何会使配对不可比？
26. 为什么不修改通用 comparable_with 来忽略所有新字段？
27. unknown、零调用、已知小计、估算分别是什么？
28. known_usage 为什么需要进入 checkpoint？
29. Mock 检索命中能否证明真实同义召回改善？
30. 在宣布阶段四完成之前，还需要哪些真实模型、容器和资源环境证据？


---

## 94. 阶段四模块 13：页面契约与 Memory 人工决定

### 94.1 本模块要解决什么问题

之前 Memory 已能由 API 提议、确认和撤销，但使用者要手工拼 UUID、锁版本和请求 JSON，容易把“HTTP 返回成功”理解为“所有后台工作已经完成”。本模块提供可核对的来源、版本与任务状态界面，让人工决定建立在服务端事实之上。

例如一条记忆执行 erase 后，数据库先把版本撤销，再由维护 Worker 清理内容。此时页面必须能同时表达“不能再用于新运行”和“物理清理还在 pending”。若点击按钮后直接从列表移除记录，使用者就失去了检查删除失败的入口。

模块只扩展管理能力。它不负责重新实现 Memory 状态机，也不在浏览器中自行确认模型生成的候选。入口是 [App.tsx](../frontend/src/App.tsx)，原 Skill、Review、Eval 页面保留，新增 Memory、MCP、Context 三个分支；切换页面会卸载当前组件，不把内容写入浏览器持久存储。

### 94.2 文件和职责

| 文件 | 职责 |
| --- | --- |
| [api/phase4.ts](../frontend/src/api/phase4.ts) | Memory、维护 Job、MCP、Context、Retrieval DTO 和窄 API 封装 |
| [api/client.ts](../frontend/src/api/client.ts) | 共用请求、错误、204 响应处理 |
| [MemoryPage.tsx](../frontend/src/pages/MemoryPage.tsx) | 作用域查询、详情、人工决定、删除任务状态 |
| [Evidence.tsx](../frontend/src/components/Evidence.tsx) | JSON 证据文本展示与 409 说明 |
| [State.tsx](../frontend/src/components/State.tsx) | Loading、Empty、ErrorNotice、Badge |
| [memory.py](../src/evoagent/api/routes/memory.py) | 原有可信管理 API，作用域和来源校验仍在服务端 |

phase4.ts 沿用 TypeScript 明确接口，不把所有响应都当作任意 JSON。只有本来属于开放证据的 locator、result、Schema、estimate 等使用 unknown。请求路径中的身份使用 encodeURIComponent，写请求只带契约允许的字段。

### 94.3 Session 输入与已加载作用域为何分开

MemoryPage 的 input 是用户正在编辑的值，session 是成功读取后固定下来的作用域。用户可以先把输入框改成另一个 Session，再决定是否查询；这不能导致当前详情按钮突然向新 Session 发送旧 memory_version_id。

读取新作用域时，先清除旧列表、详情、Job 和 session。只有列表读取成功才登记新 session。详情和决定始终使用已加载 session，不使用实时 input。这把“表单草稿”与“当前事实所属范围”分开，避免界面身份混用。

所有异步交互通过 perform 设置 busy、清理旧错误和通知，操作中禁用相关输入与按钮。finally 释放 busy，所以接口错误不会让页面永久卡在加载状态。这里没有通过修改本地对象模拟确认成功的乐观更新。

### 94.4 从列表到来源证据的完整调用链

```text
输入 Session → GET /sessions/{id}/memories
  → 显示所有版本和状态
  → 选择 version_id
  → GET /sessions/{id}/memories/{version_id}
  → 展示 sources/events/maintenance_job_id
  → 如有 Job，再 GET /maintenance-jobs/{id}
```

列表保留 proposed、confirmed、rejected、revoked、superseded 等历史状态；不能只列可检索的 confirmed，因为人工审核需要看到被替代和拒绝的证据。详情中的 scope、revision 和 lock_version 有不同含义：scope 决定可见范围，revision 是内容版本，lock_version 是该事实条目的并发修改条件。

内容哈希和来源哈希也不能互换。content_hash 标识记忆文本，source_hash 标识来源消息，locator 表示提取位置。人工确认时后台仍会重新读取来源、核对哈希和内容引用，不依赖页面已经展示过来源这一事实。

### 94.5 冲突和按钮规则

同一 entry_id 存在其他版本时，proposed 行提示先核对冲突。这只是人工提示，不是替代服务端冲突判定。是否可确认还取决于 supersedes_version_id、current_version_id、来源有效性和当前锁版本。

proposed 显示确认和拒绝；proposed/confirmed 显示撤销；仍有内容且没有清理 Job 的记录可请求清理。决定请求发送 action 和 expected_lock_version。409 统一说明“版本或状态冲突”，保留错误码并要求刷新后重新审核，不自动换成最新版本重放决定。

举例：用户读到 lock_version=3，另一个管理入口先改到 4。页面提交 3，后台拒绝。此时如果前端悄悄改成 4 重试，就把针对旧事实的人工决定套到了新事实上。当前实现没有这种自动重试。

### 94.6 删除维护任务如何展示

决定请求成功后重新读取列表和详情，再根据 maintenance_job_id 查询状态。erase 的通知仅写“撤销已提交”，后面明确要求查看内容清理任务。即使后续读取失败，也不会声称文件或索引已经清理。

Job 展示 status、attempts、error_code、next_attempt_at 和 result。只有 failed 显示显式重试入口。重试 POST 返回后再次读取详情和 Job，不在本地直接标记 completed。用户通过“刷新事实”观察 Worker 后续进展；这里采用手动刷新，不维持无限轮询或创建第二套重试计时器。

刷新后 content=null 显示“内容已清理”；审计和来源引用仍可保留。删除记录、撤销使用资格、清理正文是三个不同动作，不能从一个字段推断全部完成。

## 95. 阶段四模块 13：MCP 目录审核与卸载

### 95.1 四种状态必须分别理解

[MCPPage.tsx](../frontend/src/pages/MCPPage.tsx) 同时展示连接配置 enabled、进程健康 state、工具审核 approved、Server execution_state。这四个维度不能合并成一个绿色“可用”。

例如 enabled=true 但没有健康观察，只能说明部署配置允许连接，不能说明进程已连接。目录里有工具但 approved=false，不能交给模型调用。工具审核通过但 execution_state=disabled，仍不允许执行。即便全部满足，实际调用仍需通过运行时冻结契约、权限审批和副作用控制。

### 95.2 配置引用而不是浏览器执行命令

注册表单只接收 Server 名称、stdio/streamable_http 选择和部署 Profile ID。新对象的 enabled=false、超时 10 秒、max_concurrency=1；transport 决定填 launch_profile_id 还是 endpoint_profile_id，另一项为 null。服务端 validate_config 再核对部署配置是否存在。

页面不提供 argv、任意 URL 或 API Key 输入。新注册表单不配置 secret_ref；已有带凭据 Server 只显示“已配置引用”，不会显示引用名，更不会向浏览器请求服务端密钥值。需要凭据的配置仍由既有部署/API 工作流完成。

启用/禁用操作把已读取配置连同 expected_lock_version 交给 PUT。配置变化由后端递增版本并关闭旧连接。前端不修改目录 config_version 来强行使它重新匹配。

### 95.3 发现与目录历史

选择 Server 后并行读取 catalogs 和 health，这是互不依赖的只读查询；得到目录后再读取该目录 reviews。当前默认选最后一个 revision，但保留所有目录按钮用于历史核对。

每个目录展示 content_hash、revision、config_version、diff 和工具列表。展开工具可看 input_schema、output_schema、remote_annotations。远端 description 和 JSON 都通过 React 文本节点渲染，不使用 dangerouslySetInnerHTML；远端声明只是需要审核的内容，不自动成为本地风险配置。

页面用“目录 revision 等于 Server latest_revision，且 config_version 等于 Server lock_version”识别当前目录。历史目录仍可查看，但审核 fieldset 与激活按钮禁用。后端也会拒绝 stale，前端禁用不能替代这个检查。

### 95.4 审核版本如何选择和提交

reviews 是不可变审计记录序列，同一个 tool_name 可能有多个 lock_version。页面建立 Map，按工具保留最大版本作为当前审核事实。ReviewForm 的 key 包含 catalog.id、tool_name 和 lock_version，刷新到新审核时重建表单，避免沿用旧表单状态。

提交时显式挑选 tool_name、approved、risk、effect、reviewer、reason、expected_lock_version；不直接展开 API 返回对象。原因是返回值可能包含 created_at 等字段，而后端 extra=forbid。如果把显示 DTO 原样发送，正常审核也会因额外字段失败。

允许风险 R0～R3 和三种副作用，写入型 effect 配 R0/R1 时界面不能批准。拒绝工具需要理由和审核人，并保持当前风险/副作用；如果要重新分类后批准，必须显式填写并提交。

### 95.5 激活为什么需要两种版本

execution 请求包含 expected_lock_version、expected_execution_version、state 和 catalog_id。配置版本防止针对旧连接配置激活；执行版本防止两个人相互覆盖 active/draining/disabled。两者都由当前 Server 响应取得，不由浏览器递增推算。

激活成功后重新查询服务端列表、目录与审核，才能显示结果。返回通知仍说明逐工具审核和权限检查继续存在。按钮“激活此目录”不是绕过所有审核的快捷开关。

### 95.6 排空与卸载的边界

排空调用提交 draining，运行时拒绝新调用，并让已有调用按既有规则退出。页面说明“等待在途调用退出”，不把 draining 直接显示为全局清理完成。

卸载执行能力提交 disabled；后端提交执行状态后调用 manager.disconnect。目录和审核记录保留，旧 Run 的冻结记录也不会被重写。卸载不等于删掉 MCPServer 行，因此页面仍有审计入口。

健康观察由 API 按 expires_at 和 config_version 判断新鲜度。页面显示 stale、错误码和有效期，没有观察则明确“未知”。当前是查询时快照，过了时间需刷新，不能因为页面还开着就把一条旧 ready 观察称为实时健康。

## 96. 阶段四模块 13：上下文证据、错误语义与测试地图

### 96.1 为什么新增 Context 查询而不是直接展开快照

[traces.py](../src/evoagent/api/routes/traces.py) 新增 GET /runs/{run_id}/context。界面需要的是预算和修订事实，不需要完整消息和内部配置。直接返回 config_snapshot、summary 或 Artifact URI 会把无关正文和存储路径带到页面。

接口先读取 Run；不存在返回 run_not_found。随后只从 context_policy 取 mode/version/budget/counter/strict，返回 max_output_tokens、config_hash、status 和 error_code。修订按 revision 升序查询，返回父 ID、输入/策略哈希、Artifact ID、estimate 和时间，不返回摘要正文。无迁移，因为这些事实已经存在。

### 96.2 预算卡片的含义

[ContextPage.tsx](../frontend/src/pages/ContextPage.tsx) 展示 context_window、output_tokens、safety_margin 三个冻结值。只有三个值都是有限数字时才计算输入上限，否则显示未知。

```text
输入上限 = 上下文窗口 - 输出预留 - 安全余量
```

这是预算配置关系，不是服务端模型的精确用量。max_output_tokens 是当次冻结请求的输出上限，单独展示。修订 estimate 中的 before/after 是上下文处理的估算；如果 Run 因 context_budget_exceeded 在请求前停止，页面显示失败事实，不把安全拒绝解读为成功优化。

没有 policy 的 Run 可能使用旧策略或尚未冻结。没有 revisions 表示没有持久化修订，不能宣称压缩成功，也不能用零填补不存在的计数。

### 96.3 检索的三种“空”

Context 成功读取后，再请求已有 retrieval API。这样即使检索查询失败，预算和修订证据仍可查看。

第一种是 retrieval_batch_not_found：没有冻结批次，可能旧链路不支持或尚未执行到该阶段。第二种是有批次但 selections 为空：有明确的无候选事实。第三种是有候选但 selected_count=0：候选可能因预算等原因被全部省略。页面逐项显示 omission_reason，因此第三种不能冒充第二种。

检索配置、generation、degraded、source_hash、text_hash、rank 和 evidence 都来自已有持久记录。界面不重新跑检索，不按今天的索引替换历史候选，也不把 degraded=false 当作真实语义质量合格。

### 96.4 错误与旧数据清理

共用 request 现在正确处理 204：没有响应体就不调用 response.json。对于 FastAPI 422 的数组 detail，界面显示 HTTP 状态，不把包含用户输入的校验对象转换为字符串或直接展示。

failure 把 409 显示为版本或状态冲突，并保留 error.code 方便定位。其他异常保留可读错误。页面开始读取新 Run 时先清除旧 context、retrieval 和错误，所以失败后不会把上一 Run 的证据留在新查询标题下。

异步写操作不进行后台自动重放。若写操作已提交但随后的刷新失败，使用者应刷新确认事实；再次提交旧锁版本会由服务端拒绝。这比前端假设网络错误就等于数据库回滚更可靠。

### 96.5 测试地图

| 测试文件 | 验证的不变量 |
| --- | --- |
| [Phase4.test.tsx](../frontend/src/Phase4.test.tsx) | Memory 空/错/冲突/删除重试；MCP 空/错/历史只读/引用不回显；Context 缺批次和清旧值；204/422 |
| [phase4.spec.ts](../frontend/e2e/phase4.spec.ts) | 浏览器执行 Memory 审核清理、MCP 审核排空卸载、上下文降级省略与空命中 |
| [lifecycle.spec.ts](../frontend/e2e/lifecycle.spec.ts) | 旧查看配对报告、批准发布、回滚流程继续工作 |
| [test_context_evidence_api.py](../tests/integration/test_context_evidence_api.py) | 真数据库查询、修订顺序/父关系、预算、404、私密正文/凭据/路径不返回 |
| [test_memory_api.py](../tests/integration/test_memory_api.py) | 既有真实 API 状态机、冲突与异步清理契约 |
| [test_mcp_catalogs.py](../tests/integration/test_mcp_catalogs.py) | 既有目录版本、发现与审核后端契约 |

前端 fixture 固定 HTTP 响应便于稳定验证交互，不声称它执行了真实第三方 MCP 或付费模型。Python 集成测试补充服务端契约验证。浏览器还在 390×844 视口检查文档宽度，并保存上下文页面截图，测试输出留在 Git 忽略目录。

### 96.6 推荐阅读与故障定位顺序

先读 phase4.ts，再读页面的读取函数、perform 和写操作，最后沿 API 进入 MemoryService/MCPService。若看到 409，先检查所读 lock_version 和目录 config_version；若删除一直 pending，检查维护 Worker；若 MCP enabled 但无法调用，逐一核对当前目录、审核、execution_state 和健康；若没有 Context 修订，检查运行是否真正触发压缩，而不是首先怀疑页面漏查。

本模块运行步骤和当次测试数字见[模块 13 验收说明](阶段四-模块13验收与运行说明.md)。页面交付不修改模块 12 小样本的负面结论，也不把模块 14 的最终效果与部署验收提前标为完成。

## 97. 阶段四模块 13 学习验收

### 97.1 能画出三条请求链

请从按钮开始画出确认记忆、卸载 MCP、查询上下文三条链路，分别标注身份、版本条件、数据库提交、异步维护和后续重新读取的位置。不要只画 React → API 两个方框；要能指出哪一步失败时用户会看到什么，哪些事实可能已经提交。

### 97.2 必须解释的 30 个问题

1. 为什么输入框里的 Session ID 和已加载 Session ID 要分别保存？
2. 查询一个新作用域失败后为什么要清除旧详情？
3. Memory revision 和 entry lock_version 分别控制什么？
4. 为什么列表要保留 proposed、revoked 和 superseded？
5. 同事实多版本提示为什么不是最终冲突判定？
6. content_hash、source_hash 和 locator 各证明什么？
7. 页面已经展示来源，后端为什么还要在确认时复验？
8. 收到 409 后为什么不能自动获取最新锁版本并重试？
9. erase 成功响应究竟证明了哪一步完成？
10. 删除 Job pending、failed、completed 对应什么操作建议？
11. 为什么重试后需要再读 Job，而不是标记 completed？
12. 内容清理后为什么还可以保留审计记录？
13. MCP enabled、健康 ready、审核 approved、执行 active 为何分开？
14. 为什么浏览器只能注册部署 Profile 引用？
15. 为什么页面不回显 secret_ref 名称？
16. 配置更新后旧目录为何不能直接激活？
17. 同一工具多个审核记录如何选出当前版本？
18. 为什么不能把审核响应 DTO 原样发送给 POST？
19. 为什么写入副作用配 R0/R1 不能批准？
20. execution_version 与配置 lock_version 防止的竞争有什么不同？
21. 排空返回后为什么不能宣称所有在途调用已结束？
22. 卸载执行能力为什么不删除 Server 历史？
23. stale 健康观察与 disabled 执行状态有什么区别？
24. Context API 为什么不直接返回整个 config_snapshot？
25. 输入上限如何计算，缺少字段时为什么显示未知？
26. 修订 estimate 和 Provider usage 为什么不能互换？
27. 没有冻结批次、无候选、全部省略三种情况如何区分？
28. 422 的 detail 数组为什么不应直接展示给用户？
29. 固定 HTTP fixture 的浏览器测试证明了什么、没有证明什么？
30. 模块 13 测试通过为什么不能推出记忆效果提升或 v0.4 完整交付？

### 97.3 动手验收

准备同一事实的两个版本，在一个页面读旧锁版本，再通过 API 修改并从页面提交，观察 409 和刷新后的来源变化。接着暂停维护 Worker，请求内容清理，确认界面停留在真实 pending；启动维护 Worker 后刷新，观察完成结果。最后查看一个预算超限 Run 和一个没有冻结检索批次的旧 Run，说明两者各缺少什么证据。

上述练习应在演示数据库执行，不为了界面截图篡改真实报告。能够区分按钮反馈、数据库事实、外部执行结果和效果评测结论，才算掌握本模块。
