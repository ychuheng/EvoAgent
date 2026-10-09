# EvoAgent 改造与 Skill 自进化全面审查（2026-10-09）

## 1. 结论与版本

**尚未达到完整修改计划，也尚未形成用户要求的个人 Skill 自进化闭环。当前是已有个人 Agent 执行能力与正式 Skill 生命周期的工程，正在补 S0 安全基础。** 这不是对全部既有功能的否定，也不能把后续批次尚未实施都记成当前批次的代码缺陷。

本次新增改动主要完成共享脱敏、artifact 注入检查、工具视图、敏感编辑保护、隔离复核、Memory 检查与命令可观测性。原有 PostgreSQL、心跳、epoch/fencing、恢复与 UNKNOWN 处理仍保留，本轮真实数据库专项通过。但 S0 的再注入和资料写入路径仍有遗漏，不能宣布 S0 整体验收完成；S1、P0、P6a、P1～P4 的新增个人学习链路尚未交付。

用户目标的判断标准是：普通任务 → 可核验结果与用户纠正 → 提炼或修订方法 → 独立正例/反例验证 → 用户确认限定试用 → 新任务正确复用 → 观察失败自动挂起 → v2 与回退。当前只具备其中若干旧能力，连接这些能力的个人入口、证据合同与后台流程不存在。**不能把“有 Skill 表、提炼模型、评测和回滚 API”当成这一闭环已成立。**

- 定时启动：2026-10-09 06:00，Asia/Shanghai。
- 审查开始与测试后 HEAD 均为 `cbeacc47383dd2472a5596e78c837b0428af00cb`，分支 `c3`；最近产品提交 `7204045`。
- 对照：[完整改造方案](../EvoAgent-个人通用Agent与Skill自进化完整改造方案.md)、[当前状态](../当前状态.md)、[上一轮 S0 复核](S0改造实施复核-2026-10-08.md)。本轮重点增量为 `730dd98..cbeacc4`，同时检查相关现有执行与 Skill 路径。
- 开始工作区已有旧计划、docs/README.md 的修改，以及新计划、教学计划、尺寸抽样和 output/ 未跟踪内容；产品目录没有未提交修改。审查中途复查 HEAD 与产品 diff 未发现变动。最后状态见第 9 节。
- 未修改产品代码、既有测试或计划；未提交 Git；零付费模型调用。额外复现只使用合成假凭据、临时目录和临时 SQLite。数据库专项使用单独创建的 PostgreSQL 库和 Redis 容器，执行后已清理，没有对现有业务库做迁移、清空或测试。

## 2. 按重要性排序的问题

### A1 / P1：旧检查点中的工具正文仍可绕过当前策略进入模型

位置：[ContextStore.prepare（第76行）](../../src/evoagent/runtime/context_store.py)、[PersistentCheckpointStore.load_latest（第77行）](../../src/evoagent/runtime/checkpoints.py)、[AgentLoop.run（第111行）](../../src/evoagent/core/loop.py)、[PersistentAgentRunner 恢复入口（第210行）](../../src/evoagent/runtime/persistent_runner.py)。

`prepare()` 只在 `state.context_revision_id` 非空时扫描相关 context_source artifact；无压缩修订的旧检查点不会经过这道正文检查。`load_latest()` 校验版本、修订引用后返回完整 `LoopState`，`AgentLoop` 直接采用其中的 messages。工具消息不会重新调用 ToolExecutor，因此新的输出投影也不覆盖这条路径。`check_sources()` 检查来源引用，不检测快照中的工具正文。

**已复现**：临时 SQLite 中领取真实租约，保存并加载 schema_version=2、context_revision_id=None 的检查点；消息为 user → assistant(file_read 调用) → tool(合成凭据 DSN)，config_hash 与当前 Loop 匹配。启用真实 `ContextStore` 和合法 `LegacyContextPolicy`，恢复运行后 Mock Provider 收到 1 次请求，其中仍包含该旧 DSN；运行最终 completed。不是用户 goal 原文的检测争议，泄漏载体明确是旧工具结果。

影响：方案 §2.1 第 9 条“恢复必须检查旧派生摘要/工具结果”未完成，规则升级后新请求仍可能发送旧策略漏掉的秘密。bounded 模式的无修订、低占用路径也没有此检查，从源码看同样需要补覆盖；本轮完整运行复现的是 legacy + schema v2。

建议：对恢复后即将发送的派生消息增加当前策略复验，覆盖无修订和有修订两种状态；不要脱敏或改写原始用户 Message/历史证据 hash。必需恢复内容不合格时明确停止并提示重新准备上下文，不能静默发送。测试必须断言 Provider 请求次数/内容，而不只是 Guard 方法单测通过。

### A2 / P1：旧 Skill 来源快照的复用分支不重新运行敏感检查

位置：[ProvenanceService.freeze（第132行）](../../src/evoagent/skills/provenance.py)，特别是 141～152 行的现有快照返回分支；[ModelCandidateGenerator（第89行）](../../src/evoagent/skills/extraction.py)。

首次构建来源经过新增共享规则的 TraceSanitizer；但复用既有 `application/vnd.evoagent.skill-source+json` artifact 时，只检查内容 hash，直接 json.loads 后返回。策略版本、erased 状态和当前敏感检查未在该分支处理。生成器随后把 payload 放入模型请求。

**已复现**：使用真实 ArtifactService 建立 hash 一致、eval_run_id 匹配的旧来源 JSON，含合成 DSN；仅将 eligibility.check 模拟为已经通过资格检查。freeze 返回的 payload 仍包含假凭据。此隔离复现证明缓存分支绕过 sanitizer；不宣称已跑完整真实 TRAIN 任务或已向远端发送内容。

建议：资格检查通过不等于旧正文通过当前隐私策略。复用来源同样检查擦除/撤销、完整性与当前规则；首版可按当前 sanitizer 阻断，不原地修改旧 bytes/hash。需要重建时产生新的安全来源及明确身份，保留旧证据。不要把 Skill 来源加入 artifact_read 白名单来图方便。

### A3 / P1：候选正文的现有校验器仍接受共享规则可识别的秘密

位置：[SkillDefinitionValidator.validate（第48行）](../../src/evoagent/skills/validation.py)、[_validate_text（第123行）](../../src/evoagent/skills/validation.py)、[SkillExtractionService.extract（第203行）](../../src/evoagent/skills/extraction.py)、[SkillContextRenderer（第7行）](../../src/evoagent/skills/rendering.py)。

现有领域校验处理 schema、工具、风险、模板引用、路径及提示覆盖，但没有对完整 definition 使用共享敏感检测。description、success_criteria 等字段没有统一正文门禁，步骤文本也没有凭据检查。候选模型或人工新版本可以带入新秘密，即使来源已清洗也不能替代输出检查。

**已复现**：构造合法 SkillDefinition，description 放合成 DSN，model step 放合成裸 JWT；allowed_tools 为 calculator、风险 R0，含合法停止条件和反例。Validator.validate 正常返回，共享 detect_sensitive 对这两处均命中，Renderer 同时输出这两段。注意：带 DSN 的 step 可能被现有路径正则误判为绝对路径，因此本复现没有靠这一被拒绝的形态证明问题。

建议：在候选保存/人工 create_version 的共同校验入口检查完整语义正文与嵌套参数；命中则拒绝或转待审新候选，不能替换后继续沿用旧 hash。补所有字段及模型生成/人工创建的参数化测试。当前虽未实现新 P1/P2 资料载体，旧 Skill 候选载体已经存在，不能将其全部推迟到后续批次。

### A4 / P1：扫描仍缺实际有界读取、可终止执行器与队列上限

位置：[ArtifactInjectionGuard.read_verified_text（第192行）](../../src/evoagent/privacy/artifact_access.py)、[_rescan（第324行）](../../src/evoagent/privacy/artifact_access.py)、[clear_quarantine 读取（第492行）](../../src/evoagent/privacy/artifact_access.py)、[LocalArtifactStore._read（第108行）](../../src/evoagent/trace/artifacts.py)。

登记 size_bytes 预判和 to_thread + wait_for 是进步：拒绝常见超大件、减少同步阻塞、超时不返回结果。但是 Store 仍 read_bytes 整读；磁盘内容超过登记尺寸时，全部 bytes 已读入才会因 hash 不同被拒。人工复核路径甚至没有登记尺寸的读前预算判断。扫描 timeout 只结束等待，后台线程不可终止，没有独立 CPU 限额、有界扫描队列和用户单件离线扫描入口。

**已复现**：登记 3 B artifact 后，将临时磁盘内容换成 2,048 B；配置 max_scan_bytes=512，计数存储确认读入了全部 2,048 B，之后才以 hash mismatch 拒绝。合成扫描器运行100 ms、预算10 ms，调用约19.7 ms返回 scan_timeout，返回时工作线程尚未完成，稍后确实完成。后者只证明不可终止语义，19.7 ms不是性能百分位或真实规则吞吐。

影响：结果 fail-closed，但资源预算不满足方案 §2.5；多个超时检测仍可能占用 CPU/线程，不能据此宣布 F3 全部修复。代码注释已承认不可强杀限制，这个登记是正确的。

参数也存在未收口差异：方案是8 MiB、CPU250 ms、含读取排队wall1000 ms、并发2/队列16；实现为6 MiB/扫描等待500 ms常量。合成吞吐测量可以支持改阈值，但没有替代完整预算和配置合同。应将实测参数明确登记成实施调整，而非同时保留两套验收口径。

### A5 / P2：新引入的 Memory quarantined 状态无法撤销或擦除

位置：[IndexService._quarantine_nonconforming（第89行）](../../src/evoagent/retrieval/indexing.py)、[memory.lifecycle.next_status（第6行）](../../src/evoagent/memory/lifecycle.py)、[MemoryService.decide（第145行）](../../src/evoagent/memory/service.py)。

索引重建将旧不合格 confirmed 版本改为 quarantined；状态机的 revoke/erase 都没有允许该状态。Entry 的 confirmed 状态和 current_version_id 也未同步清理，容易出现页面聚合仍像已确认、正文版本却不可用的状态。

**已复现**：旧版本提议→确认→queue_rebuild 隔离后，调用现有 erase 返回 invalid_memory_transition，不创建擦除任务。这阻断的恰好是需要清理的敏感正文。

建议：定义 Memory 隔离状态的用户可见含义与撤销/擦除路径，保持检测不自动解封；同步 Entry 指针/状态及索引失效合同，允许用户删除隔离秘密，并覆盖清理失败重试、来源引用与旧 Run 恢复。不要借此放开重新 confirm。

### A6 / P2：旧 proposed Memory 确认入口没有复查当前规则

位置：[MemoryService.decide 确认分支（第156行）](../../src/evoagent/memory/service.py)，对比 [verify_version（第68行）](../../src/evoagent/memory/repository.py)。

新增检查在 propose 与 verify_version，但 decide(confirm) 未调用当前敏感检测。已有专项测试甚至在恢复新规则后直接确认旧 DSN，再靠检索阻断；这不能证明“确认前复查”已实现。

**已复现**：临时模拟旧策略创建 proposed DSN，恢复当前检测后 confirm 仍返回 confirmed；随后 verify_version 返回 context_source_revoked。这不会绕过当前检索检查，但确认结果与可使用状态矛盾，并将不合格正文送入后续索引流程。

建议：在确认事务写状态/指针之前复查正文，不改变原来源 quote 与 hash；明确拒绝或隔离结果。保留当前发送前 verify_version，不能拿确认检查代替持续复验。

### A7 / P2：artifact 复核回放仍跳过当前权限与请求内容冲突检查

位置：[clear_quarantine（第464行）](../../src/evoagent/privacy/artifact_access.py)、[_find_review（第575行）](../../src/evoagent/privacy/artifact_access.py)。

跨 artifact 串结果和成功 checked_hash 已修。但是回放先于 `_require_reviewable()`，同 ID 改请求没有规范 body hash；事件查询仍是同 Run 最近1,000条，不是长期稳定的唯一身份。第一次事务释放锁后执行扫描，最终提交前也没有再次查询同请求的结算结果，行锁不能完整覆盖两阶段并发。

**已复现**：完成一次 clean artifact 人工解封，随后将数据库 erased=true；同 ID 请求仍返回 cleared/replayed=true。再用同 ID 换 expected_content_hash 与 reason，同样返回旧成功。这里是错误回放/权限合同问题，不等于 erased 正文会通过 read_verified_text；该注入入口仍有自己的复验。

建议：回放前复验现行访问权；历史 outcome 与当前状态明确区分。用 `(artifact_id, client_request_id)` 和规范请求 hash 收口，改正文409；结算提交前锁内再次查重，增加持久唯一约束/独立请求记录。本轮未做 PostgreSQL 同请求并发复现，因此并发风险是代码审查结论，不能伪装成已测失败。

### A8 / P2：阻断事件的幂等仅覆盖最近200条

位置：[_ensure_block_event（第633行）](../../src/evoagent/privacy/artifact_access.py)，查询limit200；对应方案 §2.1 第8条。

**已复现**：先记录一个阻断key，再追加201条不同来源的阻断事件，再对原key调用，最终同key事件数为2。即使没有并发，也已违反“同来源/hash/策略只写一次”的合同。查重在 Run 事件序号锁之前还存在并发风险，但本轮没有单独测该并发。

建议：数据库可约束的去重身份与锁内查询；不再靠增大lookback窗口。历史重复事件的清理另行处理，不在本次审查改写。

### A9 / P2：事件/Trace 接线的等价证据未覆盖各自旧路径

位置：[现有oracle测试（第89行）](../../tests/unit/test_redaction_compatibility.py)、[事件值级接线（第42行）](../../src/evoagent/core/events.py)、[TraceSanitizer 接线（第139行）](../../src/evoagent/skills/sanitizer.py)。

现有逐字节oracle与expected_changes主要验证 memory.policy/redaction；新增 events/Trace 单测验证命中、正常对照及路径，但没有各自旧路径对同一黄金语料的完整比较。

**已复现**：加载 `583059a` 的 core.events.sanitize_payload 与当前实现，对正常键下的 `password: FAKE_REVIEW_ONLY` 比较，旧路径保持值、当前路径替换；这是既有 credential 类别的事件形态变化，不能只用DSN/JWT扩容登记来说明。

建议：不撤回必要隐私修复；补各入口的黄金比较，明确登记“修复历史漏脱敏”造成的预期变化、截断变化与安全输出hash。不能同时宣称“非扩展类别逐字节不变”和实际引入未登记变化。此项是证据/合同缺口，不主张重新恢复旧泄漏行为。

### A10 / P2（P0待实施）：Skill 边界字段仍没有进入运行上下文

位置：[SkillContextRenderer.render（第7行）](../../src/evoagent/skills/rendering.py)。

Renderer只输出名称、用途、步骤和成功标准，没有 triggers、preconditions、stop_conditions、approval_points、counterexamples。**已复现**：合法 definition 中独立标记的停止条件、反例、触发词均未出现在render结果。

影响：模型拿不到方法的不适用边界，项目目前无法用schema存在来证明运行时会正确使用停止/反例条件。系统工具审批仍独立有效，缺approval_points渲染不等于权限被绕过。

建议按P0先做版本化Renderer修正与旧Run兼容，不改历史上下文hash。此项明确在后续P0，不能单凭它认定本次S0提交违规；但它阻止完整目标验收。

## 3. 上次发现的修复情况

| 上次项目 | 本次判断 | 证据与剩余边界 |
| --- | --- | --- |
| F1 敏感片段部分覆盖编辑 | 主问题已修 | 原文坐标detect_sensitive_spans + spans_intersect；全部相关回归随全量通过。外部并发写入仍按K3另验收 |
| F2 读盘期间擦除/隔离仍返回 | 主问题已修 | 两个返回分支均_reverify，mark_verified不覆盖quarantine；不外推到A1旧快照或A7复核回放 |
| F3 同步检测后才判超时/整读 | 部分修复 | 登记尺寸预判、线程等待超时已实现；实际bytes有界读、可终止CPU/队列仍缺，见A4 |
| F4 跨artifact复核ID串结果 | 主问题已修，幂等仍部分 | 匹配artifact_id、成功事件checked_hash已补；回放权限/请求hash/窗口/两阶段并发见A7 |
| 带旧view_metadata的输出复用 | 已修主要边界 | _project只认当前policy_version；旧版本重新投影。不能覆盖绕过executor的旧LoopState |
| Memory敏感检查 | 部分完成 | propose拒绝、检索verify、重建隔离已做；confirm和隔离删除见A5/A6 |
| TraceSanitizer/core.events共享规则 | 接线已做，证据未全 | 功能测试通过；各自黄金对照见A9 |
| artifact复核页面/元数据 | 未完成 | GET详情仍未返回三项检查列，frontend无quarantine-review入口；只有人工API并非用户UI闭环 |
| K1配置陷阱 | 已修且回归通过 | 启动拒绝真实Provider + bounded strict，不把估算伪装verified |
| K2命令可观测性 | 代码与部分环境证据存在 | 本轮Windows模拟回归通过；真实/proc及Linux隔离本轮未跑，历史容器证据保留，不等于内核硬配额 |
| K3删除/项目大文件 | 仍未修，已有登记 | 删除expected_sha256仍可空；项目读文件仍整读。不得作为超大文件或并发安全删除承诺 |

## 4. 全部实施批次覆盖矩阵

| 批次 | 当前状态 | 源码判断与未完成要求 |
| --- | --- | --- |
| S0a | 大部分实现，验收未全 | 共享原语、兼容包装、oracle、K1/命令重放合同已有；events/Trace各自等价证据需A9 |
| S0b | 部分实现，不能整体放行 | 0020迁移、artifact Guard、工具视图、编辑门禁、人工API、命令预检已有；A1～A8及有界扫描/UI/配置影响报告仍需关闭 |
| S1 | 新合同未实施 | 无LearningRepository/RunFeedbackRecord与两类source_key；无学习revision原子收口、个人data_role与machine/user判据的新数据合同 |
| P0 | 未实施主要修正 | Renderer仍旧版；尚无本轮固定任务家族的SQL/事务/时延基线 |
| P6a | O1～O5未实施 | emit逐事件提交；心跳续租与取消分查询；领取前恢复扫描；原轮询；无对应独立开关、关闭等价、取消P95/积压报告 |
| P1 | 未实施 | 无普通Run/反馈的LearningService、个人来源冻结、学习请求API/UI；Skill抽取API仍只收source_eval_run_ids |
| P2 | 未实施 | 无学习job handler、EvolutionPlanner、反馈驱动同Skill修订和重启去重的新流程；旧正式候选提炼不能替代 |
| P3 | 未实施 | 无PersonalValidationService/SkillTrialService/SkillUsageService、personal_validation分派、trial健康策略和auto_suspend；正式预算仍未冻结 |
| P4 | 未实施 | BM25与ContextResolver仍双决策；Skill无workspace/project归属，active_versions全局；无个人trial统一选择/冻结 |
| P5（可延后） | 未实施 | 没有纠正后自动修订发现、合并提议、受限自动候选与观察报告 |
| P6b（按热点） | 多数未实施 | 正则编译与字面量预筛已有，可视为局部减负；无ensure_lease/save_if_changed/渲染有界缓存/所选热点测量证据。O14/O15本来只待评估，不要求现在实现 |
| P7 | 尚未达到 | 有历史dev任务与部署证据；本轮缺三类任务“任务—学习—复用—修订—回退”的真实链路。M6/M7正式放行仍独立未完成 |

新表/迁移核对：模型仍为原Skill/Memory/Task/Eval结构，迁移head为 `20261008_0020`；没有本轮M-A学习基础或M-B试用/观察迁移。SkillSourceRecord.source_eval_run_id仍非空。SkillRecord没有新增workspace/project scope。不存在learning目录及trials/selection/usage等目标模块；此结论结合现有实际入口判断，不仅按类名搜索。

## 5. 代表性链路与真实目标

### 5.1 现有任务执行链

TaskService创建任务 → PostgreSQL队列 → JobLeaseManager领取并生成epoch → JobWorker后台心跳 → PersistentAgentRunner冻结配置/Skill选择 → AgentLoop模型工具循环 → ToolExecutor/审批/ToolEffect → 检查点与产物 → 可选acceptance校验 → finalize和会话终态投影。

代码、现有全量测试与本次真实数据库专项支持这条链路存在。进程崩溃的readonly、committed、unknown三类接管测试已在单独PostgreSQL库通过；不是“重数据库无恢复收益”的实现。它仍不能保证任意外部副作用exactly-once，未知副作用需要人工处理。

### 5.2 现有正式 Skill 链

TRAIN EvalRun并通过校验 → TraceEligibilityChecker（拒绝Skill辅助来源、超风险/未知effect）→ ProvenanceService冻结 → CandidateGenerator生成 → SkillDefinitionValidator与annotations → DRAFT → HOLDOUT正式对照/质量Gate → REVIEW_REQUIRED → 人工approve → ACTIVE → 检索/冻结 → 人工停用或回滚。

上述是确实存在的受控方法库生命周期。完整评测、正式收益和发布集仍受预算、样本、人评门槛限制，本轮不运行付费评测。提炼模型由API装配，202入口内部仍await提炼，尚非新的持久化后台学习请求。

### 5.3 个人自进化路径在哪里断

例如“修好一个项目的失败测试，用户指出不要删除边界断言，记住这次方法”：

1. 任务读改测能力已有；普通Run可以留下结果和工具证据。
2. 用户运行中补充instructions已有，但它是执行约束，不是RunFeedback修订或长期学习意图。
3. 没有把这个普通Run与纠正提交到个人学习请求的入口；既有extract只接收EvalRun，正式资格还拒绝Skill辅助来源。
4. 没有可指定target_skill/base_version的反馈修订后台链路；现有提炼按模型生成slug找Skill，用max+1分配版本。
5. 没有独立新输入/反例的个人验证和范围试用；正式Gate不能用一次开发成功代替。
6. 没有trial观察归因、连续失败阈值自动挂起或个人绑定回退。

因此目前可以描述为“可恢复个人Agent + 受控Skill提炼/评测/发布基础”，不能描述为“从日常纠正持续学习并安全复用的个人Agent已完成”。最小下一目标仍是P2候选审查版，再做到P3/P4的安全验证试用，不需要推翻PostgreSQL基础。

## 6. 数据库、内存与性能检查

- [PersistentEventSink.emit（第45行）](../../src/evoagent/trace/persistent_sink.py)每次emit打开UoW、fencing/引用校验、分配序号并提交；每个模型delta仍走该路径。
- [LeaseHeartbeat.run（第24行）](../../src/evoagent/workers/heartbeat.py)默认每10秒分别heartbeat与cancellation_requested；没有heartbeat_and_status。
- [JobWorker.run_once（第97行）](../../src/evoagent/workers/main.py)领取前仍promote/recover_expired/recover_pending；没有独立有界recovery loop与公平积压扫描。
- [SseEventService.stream（第37行）](../../src/evoagent/trace/sse.py)每连接按配置轮询查run和事件；默认0.5秒，没有共享通知/page200/已提交游标批量补读的新协议。
- [PersistentCheckpointStore.save（第44行）](../../src/evoagent/runtime/checkpoints.py)始终写快照/事件；无save_if_changed与逐字段覆盖测试。
- Worker低优先维护lane仍原固定轮询；没有新独立开关及off=reference测试。
- 内存当然已经在用：AgentLoop保存messages/usage/迭代态，事件sink保留本进程事件；并非“留数据库就不能用内存”。只是本轮设计的持久化批次和冗余访问复用尚未落地。
- 正则为模块级预编译，DSN/JWT字面量预筛已有；这是检测局部优化，不能冒充运行事务数下降。

取消P95≤11秒、无积压恢复≤3秒、N=500/1000积压预算、撤销≤500ms、lane≤2秒等均没有本轮固定环境的统计报告。现有正确性测试通过不等于达到这些时延要求。本轮没有进行≥50样本统计或SQL数量基线，所以不报改善百分比。

## 7. 验证结果与范围

| 检查 | 本轮结果 | 适用范围 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | **924 passed / 47 skipped，173.61秒** | Windows本地全量；默认未配置PG/Redis，SQLite集成不代表PG锁语义 |
| 独立PG/Redis专项（见下列文件） | **27 passed / 1 skipped，79.98秒** | 真PG/Redis、迁移、fencing、多Worker/崩溃恢复；1项为测试明确跳过SQLite行锁分支，不是PG失败 |
| `pnpm test` | **29 passed，4文件** | 前端组件，31.14秒 |
| `pnpm typecheck` | **通过** | TypeScript静态检查 |
| `pnpm e2e` | **15 passed，18.8秒** | 真浏览器、接口响应由page.route模拟；部分未mock请求报本地API8000连接拒绝，测试仍通过；不算全栈部署或真实模型验收 |
| `ruff check .` | **通过** | Ruff0.16.5，静态规则 |
| `ruff format --check .` | **未通过：154文件** | 逐个以stdin格式化比对，154项均仅CRLF→LF，无其它格式差异；没有修改文件，不当作154个逻辑缺陷 |
| `check_state_drift.py` | **通过，无产品漂移** | 状态页基准0270a57到HEAD无产品变化；不证明全部设计实现 |
| `check_doc_links.py .`（报告写入前） | **993引用、0断链** | 文档链接 |
| `git diff --check` | **通过** | 现有tracked diff空白检查，不自动覆盖未跟踪计划/本报告 |
| 补充隔离复现 | A1～A9各自证据见正文 | 临时SQLite/Mock Provider/合成正文；不是新增已提交测试，不是生产或付费模型运行 |

真实依赖专项文件：test_postgres_persistence.py、test_migrations.py、test_lease_fencing.py、test_multiworker.py、test_pgvector.py、test_phase4_demos.py、test_worker_redis.py、tests/e2e/test_worker_process_recovery.py。只跑这些环境专项来补47个skip中的相关覆盖，没有再重复全量。新库与Redis容器名称含本轮随机audit身份，资源清理返回成功；密码未写入报告或输出。

默认`python`指向没有pytest/ruff的另一解释器，第一次命令未执行测试；随后改用项目`.venv`完成验证。初版复现脚本两次输入不满足Skill schema/路径规则、一次Message错误提供name字段，这些脚本构造错误均纠正后再运行，不记成产品测试失败。

未验证：当前真实模型任务、人评/引用支持、M6净收益、M7发布集、新装Windows部署、Linux隔离和/proc专项、PG上的新增复核/阻断并发竞态、扫描器病态输入的真实CPU硬隔离、新优化时延与资源统计。部分历史记录有相关环境证据，但不冒充本轮重测。

## 8. 建议实施顺序

1. **先补S0遗漏**：优先A1恢复旧工具消息、A2复用Skill来源、A3候选正文门禁；随后A4资源上限与受限扫描。将当前门禁覆盖清单按“实际进入模型的载体”列全。
2. 补Memory确认/隔离删除、复核身份/权限/两阶段幂等、阻断事件唯一去重，以及共享接线的黄金对照。完成用户可见复核元数据/UI，明确扫描参数和迁移影响报告。
3. 按S1/P0冻结反馈身份、角色和判据合同，修Renderer，并采集可比较的SQL/时延基线。
4. 做P6a O1～O5；用独立开关、off等价、取消时延、真实PG故障和有限积压验收；不通过拉长心跳或删掉恢复证据降低SQL。
5. 完成P1/P2：普通Run + 显式“记住方法” + 用户纠正 → 可查询后台请求 → 可审查v1/v2候选，先不开放trial。
6. 完成P3/P4后才验证“个人Skill自进化闭环”：不同输入正例、反例不适用、冻结范围选择、失败自动挂起、纠正修订、回退/重启不换版本。真实模型验证额度须已批准，正式M6/M7仍分开。

不要继续扩大框架或要求一次实现O1～O15。保留已经通过的执行/持久化骨架，用一个真实任务家族把上述闭环做实，再拓展代码、资料、调研三类任务。

## 9. 审查结束状态

审查结束约06:13（Asia/Shanghai）。报告新增后文档链接检查为1,027引用、0断链；报告无行尾空白。最后核对：HEAD仍`cbeacc47383dd2472a5596e78c837b0428af00cb`；src、tests、migrations、frontend、scripts、pyproject.toml未见tracked diff。浏览器测试产生的临时报告/构建缓存不作为产品代码修改。新增本报告是本次唯一交付文件，原有未提交文档保留。

本次结论对应上述工作树与已检查版本。通过的测试只能支持其实际场景；没有跨越证据范围宣布S0/个人自进化/正式发布完成。
