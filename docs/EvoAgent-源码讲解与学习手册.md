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

## 1. 阅读说明

EvoAgent 会逐步从一个可测试的 Agent 内核，发展为支持可靠长任务和可验证 Skill 生命周期的 Agent Runtime。项目采用分模块开发方式，因此阅读时必须区分下面三种状态：

- **已实现**：仓库中已经存在代码和测试，可以实际运行。
- **接口已定义**：数据结构已经存在，但负责使用它的运行模块尚未实现。
- **计划实现**：只出现在设计文档或目标架构中，当前代码还不能完成对应功能。

本手册只把已经存在的代码描述为“已实现”。目标设计和后续模块会明确标注为“尚未实现”，避免把设计计划误认为项目现状。

### 1.1 当前进度

当前处于 `v0.2：可靠、可追踪的任务执行` 阶段。

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

阶段二已经完成模块 0～8：除了持久化底座，现在还具备 Task API、Job Lease、单 Worker、版本化 LoopState、Artifact、本地快照恢复、PersistentAgentRunner，以及受预算约束的分类重试。SSE、权限审批、副作用恢复和新增高风险工具仍未实现。

### 1.2 相关文档的职责

项目中的文档各有不同用途：

| 文档 | 用途 |
|---|---|
| `EvoAgent-项目设计与分阶段实现计划.md` | 说明项目最终要做什么以及五个阶段如何演进 |
| `阶段一-可测试Agent内核架构与实现指南.md` | 说明第一阶段的目标架构、实现顺序和完成标准 |
| `阶段二-可靠可追踪任务执行架构与实现指南.md` | 说明第二阶段的持久化、Worker、恢复、安全和分模块路线 |
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

### 18.2 当前测试文件

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

当前测试结果：

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

## 19. 当前不能完成的功能

截至阶段二模块 3，项目仍不能：

- 通过 API 创建和管理持久化任务；
- 由独立 Worker 领取并执行 Task；
- 在进程重启后恢复未完成任务；
- 自动重试模型请求或在多个 Provider 间切换；
- 提供操作系统级工具沙箱和完整权限审批；
- 完全防御 DNS 重绑定；
- 压缩长期上下文或维护长期记忆；
- 编排多个 Agent；
- 生成、评测或发布 Skill。

这些能力属于后续模块和阶段，不能因为对应数据模型已经定义就描述成“已经完成”。

---

## 20. 后续阶段路线

### 20.1 第一阶段已经完成

```text
模块 0～6  基础契约与核心循环
模块 7    AgentRunner
模块 8    OpenAICompatibleProvider 与 CLI
模块 9    安全只读工具与第一阶段收尾
```

### 20.2 当前阶段：持久化与可恢复执行

模块 0～3 已经建立数据库、状态机和事件持久化底座：

```text
RuntimeEvent → PersistentEventSink → RunEvent
Task/Run → Repository + UnitOfWork
run_id → TraceService → 有序事件
```

下一模块实现 Task Service 与最小 FastAPI；Worker、检查点内容和恢复仍在后续模块。阶段一测试继续作为契约基线。

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

Docker Compose 当前只提供绑定到本机回环地址的 PostgreSQL，不包含 API 和 Worker，因为它们属于后续模块。

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

TraceService 当前按 run_id 返回 Run 状态和排序后的事件。它是基础投影，还没有聚合 Turn、ToolCall、Snapshot 和 Artifact；这些会随阶段二后续模块扩展。

### 28.6 当前边界与测试

本阶段已经证明：工作单元提交/回滚、状态机与乐观锁、事件脱敏、事件有序落库、Trace 查询和 migration 可运行。真实 PostgreSQL CI 还会用两个 PersistentEventSink 并发追加 20 个事件，验证数据库原子序号没有重复。

当前仍没有 Task Service、FastAPI、Worker、Job Lease 和恢复执行。下一模块只把 Session/Task/首个 Run 的创建与查询暴露为应用服务和最小 API。

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
