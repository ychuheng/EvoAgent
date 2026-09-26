export const taskStatusLabel: Record<string, string> = {
  created: "已创建", queued: "排队中", running: "执行中", waiting_tool: "等待工具",
  waiting_user: "等待人工处理", retrying: "准备重试", paused: "已暂停",
  recovering: "恢复中", completed: "已完成", failed: "失败", cancelled: "已取消",
};

export function errorLabel(code: string): string {
  const known: Record<string, string> = {
    provider_network_error: "模型服务连接失败",
    provider_timeout: "模型服务超时",
    context_budget_exceeded: "上下文超过预算",
    invalid_arguments: "工具参数无效",
    tool_timeout: "工具执行超时",
    run_timeout: "任务执行超时",
    provider_configuration_mismatch: "API 与 Worker 的模型配置不一致",
    acceptance_failed: "回答未通过设定的验收条件",
    acceptance_evidence_missing: "任务缺少验收记录",
    acceptance_check_error: "验收条件检查失败",
    side_effect_unknown: "副作用结果不确定，需要人工确认",
  };
  const label = known[code];
  return label ? `${label}（${code}）` : code;
}
