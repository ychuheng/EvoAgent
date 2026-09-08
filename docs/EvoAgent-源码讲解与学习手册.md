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

## 1. 阅读说明

EvoAgent 会逐步从一个可测试的 Agent 内核，发展为支持可靠长任务和可验证 Skill 生命周期的 Agent Runtime。项目采用分模块开发方式，因此阅读时必须区分下面三种状态：

- **已实现**：仓库中已经存在代码和测试，可以实际运行。
- **接口已定义**：数据结构已经存在，但负责使用它的运行模块尚未实现。
- **计划实现**：只出现在设计文档或目标架构中，当前代码还不能完成对应功能。

本手册只把已经存在的代码描述为“已实现”。目标设计和后续模块会明确标注为“尚未实现”，避免把设计计划误认为项目现状。

### 1.1 当前进度

当前 `v0.3：可验证 Skill 生命周期` 的模块 0～12 已全部实现。

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
