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
14. 当前代码如何协作
15. 测试体系
16. 当前不能完成的功能
17. 后续模块路线
18. 推荐阅读源码的顺序
19. 当前阶段应掌握的核心思想
20. 文档后续维护规则
21. 本阶段总结

## 1. 阅读说明

EvoAgent 会逐步从一个可测试的 Agent 内核，发展为支持可靠长任务和可验证 Skill 生命周期的 Agent Runtime。项目采用分模块开发方式，因此阅读时必须区分下面三种状态：

- **已实现**：仓库中已经存在代码和测试，可以实际运行。
- **接口已定义**：数据结构已经存在，但负责使用它的运行模块尚未实现。
- **计划实现**：只出现在设计文档或目标架构中，当前代码还不能完成对应功能。

本手册只把已经存在的代码描述为“已实现”。目标设计和后续模块会明确标注为“尚未实现”，避免把设计计划误认为项目现状。

### 1.1 当前进度

当前处于 `v0.1：可测试的 Agent 内核` 阶段。

已经完成：

- 模块 0～2：工程、数据契约、事件和基础工具系统
- 模块 3：ToolExecutor
- 模块 4：ModelProvider 与 MockProvider
- 模块 5：ContextBuilder
- 模块 6：AgentLoop

下一模块：

- 模块 7：AgentRunner

当前已经可以使用 MockProvider 确定性运行“模型决定—工具执行—模型继续”的核心循环，但还没有负责总超时、取消和最终 RunResult 汇总的 AgentRunner，也没有 CLI 和真实模型接口，因此还不能作为完整应用直接使用。

### 1.2 相关文档的职责

项目中的文档各有不同用途：

| 文档 | 用途 |
|---|---|
| `EvoAgent-项目设计与分阶段实现计划.md` | 说明项目最终要做什么以及五个阶段如何演进 |
| `阶段一-可测试Agent内核架构与实现指南.md` | 说明第一阶段的目标架构、实现顺序和完成标准 |
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
ContextBuilder ──→ 初始 Message
                       ↓
                  AgentLoop
                  ├── ToolRegistry.definitions()
                  ├── MockProvider.stream(ModelRequest)
                  ├── assistant ToolCall 写入消息历史
                  ├── ToolExecutor.execute_many()
                  │       └── ToolRegistry.get() → CalculatorTool
                  ├── ToolResult 转成 tool Message
                  └── 再次请求 MockProvider，直到完成或达到限制

AgentLoop 与 ToolExecutor ──→ InMemoryEventSink ──→ RuntimeEvent
```

这条内核链路已经可以通过确定性测试运行。`AgentRunner`、真实 Provider 和 CLI 尚未实现，所以项目还缺少任务级生命周期管理和用户入口。

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
│   └── EvoAgent-源码讲解与学习手册.md
│
├── src/
│   └── evoagent/
│       ├── config.py
│       ├── core/
│       │   ├── models.py
│       │   ├── events.py
│       │   ├── context.py
│       │   └── loop.py
│       ├── providers/
│       │   ├── base.py
│       │   └── mock.py
│       └── tools/
│           ├── base.py
│           ├── registry.py
│           ├── executor.py
│           └── builtin/
│               └── calculator.py
│
└── tests/
    └── unit/
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

`RunResult` 已经定义，但 AgentRunner 尚未实现，因此现在还没有真实任务返回它。

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

AgentLoop 显式传播 `asyncio.CancelledError`，不把取消转换成普通失败。任务总超时、用户取消以及 `run.completed`、`run.failed` 等唯一终态事件属于下一模块 AgentRunner。

这种边界可以避免 Runner、Loop 和 Executor 同时产生终态事件，导致一次 Run 被重复记账。

### 13.10 当前尚未加入的循环能力

模块 6 暂未实现：

- 重复 ToolCall 和相同结果检测；
- 只读并行工具执行；
- 任务总超时；
- Provider 自动重试；
- 上下文压缩和长期记忆。

这些能力按照计划由模块 7、模块 9或后续阶段实现。

---

## 14. 当前代码如何协作

以测试中的请求“计算 12 × 3”为例，当前内核可以实际执行：

```text
1. 用户输入“计算 12 * 3”

2. AgentLoop 构造 ModelRequest
   - messages：用户问题
   - tool_definitions：registry.definitions()

3. MockProvider 返回预设模型响应

4. 模型返回 ToolCall
   - call_id：call_001
   - name：calculator
   - arguments：{"expression": "12 * 3"}

5. ToolExecutor 调用 registry.get("calculator")

6. ToolExecutor 调用 tool.validate_arguments(...)
   得到 CalculatorArguments

7. ToolExecutor 在超时控制下调用 tool.invoke(...)

8. CalculatorTool 返回字符串 "36"

9. ToolExecutor 生成 ToolResult
   - status：success
   - content："36"

10. AgentLoop 把 ToolResult 转成 tool 消息交还模型

11. 模型回答“12 × 3 = 36”

12. AgentLoop 返回 AgentLoopResult
```

这条流程已经由 MockProvider 和单元测试完整打通。MockProvider 不是真实大模型，它按测试脚本返回确定性响应；当前还没有 AgentRunner 和 CLI，所以普通用户尚不能从命令行输入自然语言启动一次正式 Run。

---

## 15. 测试体系

### 15.1 为什么测试和模块同时编写

Agent 系统中有大量异步、流式和外部依赖。如果只依赖人工运行真实模型，很难复现同一个错误。

本项目要求每完成一个模块，同时完成对应确定性测试。测试的作用不仅是判断当前代码是否正确，也是在后续重构时保护已经固定的接口行为。

### 15.2 当前测试文件

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
| `test_agent_loop.py` | 模型—工具循环、终止条件、Usage 和协议错误 |

当前测试结果：

```text
88 passed
```

### 15.3 常用检查命令

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

## 16. 当前不能完成的功能

截至模块 6，项目不能：

- 通过 CLI 接收自然语言任务；
- 调用真实大模型；
- 由 AgentRunner 管理一次正式 Run；
- 执行文件读取或网页请求；
- 实施完整工具权限策略；
- 处理任务级超时和用户取消；
- 将 Trace 持久化到数据库；
- 生成、评测或发布 Skill。

这些能力属于后续模块和阶段，不能因为对应数据模型已经定义就描述成“已经完成”。

---

## 17. 后续模块路线

### 17.1 第一阶段剩余模块

```text
模块 7  AgentRunner
模块 8  OpenAICompatibleProvider 与 CLI
模块 9  安全只读工具与第一阶段收尾
```

### 17.2 下一模块：AgentRunner

AgentRunner 将把初始上下文和 AgentLoop 包装成一次具有完整生命周期的 Run：

```text
用户任务
  → 创建 run_id 和 EventSink
  → ContextBuilder 构造初始消息
  → 在任务总超时下启动 AgentLoop
  → 处理完成、失败、限制、超时和取消
  → 生成唯一 Run 终态事件
  → 汇总 RunResult
```

模块 7 的边界是：

- 管理任务总超时和用户取消；
- 将 AgentLoopResult 转换成 RunResult；
- 保证每个 Run 只有一个终态事件；
- 不实现真实模型 HTTP、CLI、数据库、重试或 Sandbox。

---

## 18. 推荐阅读源码的顺序

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
13. 对应 tests/unit 测试
```

阅读每个文件时依次回答四个问题：

1. 这个模块接收什么输入？
2. 它输出什么结果？
3. 它负责什么？
4. 它明确不负责什么？

如果能回答这四个问题，就基本理解了模块边界。

---

## 19. 当前阶段应掌握的核心思想

### 19.1 先定义契约，再连接模块

Model、Tool 和 Runtime 先使用统一数据结构沟通，后续模块才不需要相互猜测字段含义。

### 19.2 模型输出是不可信输入

Tool Call 必须经过工具查找、参数校验、权限检查和执行控制，不能直接调用函数。

### 19.3 Registry 和 Executor 分离

Registry 管“有什么”，Executor 管“怎样执行”。职责拆分使测试、权限和沙箱更容易扩展。

### 19.4 使用抽象隔离外部实现

AgentLoop 依赖 Provider 和 EventSink 的抽象，而不是绑定某个模型厂商或数据库。

### 19.5 可观测性应从第一天开始设计

事件不是最后才添加的日志，而是长任务恢复、Trace、评测和 Skill 进化的基础数据。

### 19.6 安全采用白名单和最小权限

计算器只解释允许的 AST 节点；未来文件和网络工具也必须限制工作目录、目标地址和结果大小。

### 19.7 测试必须确定

核心逻辑使用 MockProvider 和 Mock Tool 测试，避免把随机模型行为和外部网络引入基础测试。

---

## 20. 文档后续维护规则

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

## 21. 本阶段总结

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
```

当前已经具备可确定性测试的核心循环：

```text
初始消息 → Mock 模型决策 → 工具执行 → 结果回填 → Mock 模型最终回答
```

下一步加入 AgentRunner 后，核心循环才会获得 run_id、任务总超时、用户取消、唯一终态事件和最终 RunResult。再实现真实 Provider 与 CLI，普通用户才能从命令行启动完整任务：

```text
用户输入 → 模型决策 → 工具执行 → 模型继续决策 → 最终回答
```
