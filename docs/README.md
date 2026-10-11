# 文档导航

先看[当前状态](当前状态.md)判断已经实现和实际验证到哪里，再按目的阅读。计划不是实现证据；带日期的报告只证明对应版本与环境。

| 目的 | 目录与入口 |
| --- | --- |
| 启动、使用、权限与排障 | [操作指南](guides/README.md) |
| 理解架构和源码 | [四册源码手册](manual/00-总览与目录.md)、[源码地图](源码地图.md) |
| 七天项目面试准备 | [学习指导](learning/README.md) |
| 改造要求、批次与验收合同 | [计划](plans/README.md) |
| 查一批代码改了什么、怎样验证 | [实施记录](implementation/README.md) |
| 审查、评测与人工验收结论 | [评测报告](evaluations/README.md) |
| 原始 JSON 和测量数据 | [证据数据](reports/README.md) |
| 为什么作出某项设计选择 | [架构决策 ADR](adr/README.md) |
| 当时的设计、旧章节与进度快照 | [历史档案](archive/README.md) |

## 维护规则

操作步骤放 guides，学习讲解放 manual/learning，目标合同放 plans，代码交付记录放 implementation，评测结论放 evaluations，机器证据放 reports。不要把旧报告不断追加到当前状态页。

旧阶段的重复操作说明和兼容跳转页已清理；历史引用指向其冻结 Git 版本。保留的历史材料用于核对设计和证据，不代表当前功能。`reports/` 部分 JSON 被验收脚本直接读取，路径保持稳定。

移动文档后运行 `python scripts/check_doc_links.py .`。推送后按准确提交 SHA 核对完整 CI。私有配置、简历和临时输出不入库；脚本使用方式见[脚本导航](../scripts/README.md)。
