# 脚本导航

所有命令从仓库根目录运行，先用 `python scripts/<脚本名>.py --help` 核对参数。文件名带阶段号的脚本仍用于复现验收、发布门禁或被测试导入，并不因为名字旧就删除。付费脚本需要独立费用授权；整理不运行真实模型。

## 启动与个人学习

| 脚本 | 用途 |
| --- | --- |
| [host_mode.py](host_mode.py) | Run the API and worker on a trusted Windows host with isolated local data. |
| [personal_preflight.py](personal_preflight.py) | Validate the private personal-mode configuration without contacting paid services. |
| [personal_project_trial.py](personal_project_trial.py) | Run one development-only real-model project task and save a compact audit record. |
| [personal_research_eval.py](personal_research_eval.py) | Freeze and evaluate public research tasks without treating URL matching as fact checking. |
| [personal_skill_evolution_dev.py](personal_skill_evolution_dev.py) | Explicit offline dev scenarios through real services; never model efficacy. |
| [reconcile_learning_usage.py](reconcile_learning_usage.py) | 受信本机用户核对单条 unknown 费用；需使用量凭据、预留金额 CAS 和审计理由，不调用模型。 |
## 性能与隐私测量

| 脚本 | 用途 |
| --- | --- |
| [benchmark_maintenance_polling.py](benchmark_maintenance_polling.py) | 离线空闲维护轮询对照；只创建并删除临时 SQLite 库，不调用模型。 |
| [benchmark_runtime_io.py](benchmark_runtime_io.py) | Offline persistent-task I/O matrix; never load product connection settings. |
| [benchmark_safe_scan_cache.py](benchmark_safe_scan_cache.py) | Measure only safe scan computation on fixed synthetic text; no DB or model IO. |
| [compare_redaction_rules.py](compare_redaction_rules.py) | 固定文件集合、只换规则，量化一次规则扩容的真实影响。 |
| [measure_artifact_scan_budget.py](measure_artifact_scan_budget.py) | 测量工具输出归档的尺寸包络与敏感复查吞吐，作为 S0b 扫描预算的依据。 |
| [measure_redaction_false_positives.py](measure_redaction_false_positives.py) | 测量当前敏感规则在真实代码语料上的命中与误报基线。 |
| [runtime_performance_baseline.py](runtime_performance_baseline.py) | 固定只读项目任务的持久化成本基线；仅 Mock，只接受独立验收数据库。 |
| [runtime_resource_probe.py](runtime_resource_probe.py) | Benchmark-only sampling; never retain SQL parameters or payload content. |
## 检查与 CI

| 脚本 | 用途 |
| --- | --- |
| [check_answer_citations.py](check_answer_citations.py) | 对一个已保存的 RunTrace 做**逐结论引用核对**（离线，不发起任何模型或网络调用）。 |
| [check_command_readiness.py](check_command_readiness.py) | 在**当前环境**核对项目命令的可观测性预检（K2 / §13.4）。 |
| [check_doc_links.py](check_doc_links.py) | 检查仓库内 Markdown 文件的相对链接目标是否存在。 |
| [check_project_command_interpreter.py](check_project_command_interpreter.py) | 验证"应用自己装在 `/usr` 之外"时项目命令仍能执行（CI 上的真实失败场景）。 |
| [check_project_command_isolation.py](check_project_command_isolation.py) | Run inside the application Linux image to verify real kernel command boundaries. |
| [check_project_command_limits.py](check_project_command_limits.py) | Run inside the application Linux image to verify resource limits and the network switch. |
| [check_project_fixture_command.py](check_project_fixture_command.py) | Verify the development fixture's failing test runs through the real command tool. |
| [check_sample_ledger.py](check_sample_ledger.py) | M0b 样本台账检查：拒绝跨集合复用、重复正式使用与 holdout 来源。 |
| [check_state_drift.py](check_state_drift.py) | M0c 状态漂移闸门：核对基准提交与 HEAD 的产品代码是否已经漂移。 |
| [ci_docker.py](ci_docker.py) | Job-owned CI containers and official pull cache; never configure local hosts. |
| [selftest_sample_ledger.py](selftest_sample_ledger.py) | 自检：把计划 §5.5 的每条拒绝规则各喂一次，确认检查脚本真的拒绝。 |
## 数据集和验收环境

| 脚本 | 用途 |
| --- | --- |
| [acceptance_semantic.py](acceptance_semantic.py) | Compare frozen Chinese paraphrases with the actual local embedding service. |
| [acceptance_workspace.py](acceptance_workspace.py) | Exercise real worker retrieval across two logical workspaces. |
| [build_m0b_holdout.py](build_m0b_holdout.py) | 生成 M0b 的 holdout fixture、数据集与样本台账。 |
| [build_m5_fixture.py](build_m5_fixture.py) | M5 fixture：`organize-lab` —— 一个"待整理的收件箱"专用仓库。 |
| [generate_baseline_doc.py](generate_baseline_doc.py) | 把 `output/m0-baseline.json` 渲染成可提交的基线文档（计划 §5.6）。 |
## 可复现的阶段验收

| 脚本 | 用途 |
| --- | --- |
| [agent_user_eval.py](agent_user_eval.py) | Run the frozen synthetic user-task set against a dedicated local deployment. |
| [m0_baseline.py](m0_baseline.py) | 计划 §5.6：记录当前基线——Mock/真实模型、搜索、工具清单、前端交互与耗时。 |
| [m0_interaction_skill.py](m0_interaction_skill.py) | 计划 §5.1 要求的两类 dev 任务：运行中介入与 Skill 对照。 |
| [m0a_project_dev.py](m0a_project_dev.py) | Collect Mock tool evidence for the M0a project-reading development set. |
| [m1_dogfood_map.py](m1_dogfood_map.py) | M1 收尾：在只读授权下用 EvoAgent 自身做一次"模块地图"核对（计划 §6 用户流程最后一句）。 |
| [m1_project_read.py](m1_project_read.py) | 跑 M1 项目只读 dev 集，收集**证据采集器层面**的通过情况。 |
| [m2_project_edit.py](m2_project_edit.py) | 跑 M2 项目编辑 dev 集，收集编辑链路的**证据采集器层面**结果。 |
| [m3_failure_fix.py](m3_failure_fix.py) | 跑 M3 失败修正 dev 集，收集"运行→失败→修正→复测"的证据。 |
| [m5_acceptance.py](m5_acceptance.py) | M5 整体验收采集器：本机授权目录整理 + 失败样本留证。 |
| [m6_dev_mismatch.py](m6_dev_mismatch.py) | M6 第一步：用 dev 试跑的配对结果算出**失配率**，再据此定所需配对数 `N`。 |
| [m6_pair_experiment.py](m6_pair_experiment.py) | M6 S-03 冻结对照实验的命令行入口：冻结检查、生成配对计划、判定结果、登记正式使用。 |
| [m6_preregistration.py](m6_preregistration.py) | 生成 M6 预注册记录（计划 §11）：样本量、`N × K` 库存与功效曲线。 |
| [m6_skill_review.py](m6_skill_review.py) | M6 S-04 人工发布 / 禁用 / 回滚审查的命令行入口。 |
| [m7_degradation_rehearsal.py](m7_degradation_rehearsal.py) | M7 降级演练：Redis 不可用、付费闸门拒付、模型服务不可达，以及恢复。 |
| [m7_release_rehearsal.py](m7_release_rehearsal.py) | M7 干净环境发布演练：对一个**已启动的**部署做端到端检查。 |
| [m7_release_run.py](m7_release_run.py) | M7 发布集执行器：跑已登记的 24 条正常 + 8 条故障/越权任务并算发布门槛。 |
| [phase4_demos.py](phase4_demos.py) | 可重复的四条验收演示；运行真实服务代码，报告保留失败和跳过。 |
| [phase4_deployment_probe.py](phase4_deployment_probe.py) | 验证构建后 API/UI/Worker/共享 Artifact 与关闭新能力的完整链路。 |
| [phase4_migrations.py](phase4_migrations.py) | 在两个独立空验收数据库验证 fresh 和带 v0.3 消息的升级；拒绝已有表。 |
| [phase4_release_check.py](phase4_release_check.py) | 核对交付证据完整性；未知/待补项绝不自动视作 v0.4 完成。 |
| [phase4_skill_eval.py](phase4_skill_eval.py) | 显式运行真实模型 TRAIN→固定人工 Skill→HOLDOUT 配对→质量门禁；不发布。 |

[项目文档导航](../docs/README.md)。原始输出写 `output/`，不提交临时日志、库备份、Git 验证副本或凭据。
