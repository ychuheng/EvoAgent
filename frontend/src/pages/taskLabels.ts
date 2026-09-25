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
    side_effect_unknown: "外部操作结果不确定",
    unknown_effect: "外部操作结果不确定",
  };
  const label = known[code];
  return label ? `${label}（${code}）` : code;
}
