export const taskStatusLabel: Record<string, string> = {
  created: "已创建", queued: "排队中", running: "执行中", waiting_tool: "等待工具",
  waiting_user: "等待人工处理", retrying: "准备重试", paused: "已暂停",
  recovering: "恢复中", completed: "已完成", failed: "失败", cancelled: "已取消",
};

export function errorLabel(code: string): string {
  const known: Record<string, string> = {
    provider_network_error: "模型服务连接失败",
    provider_timeout: "模型服务超时",
    provider_auth_error: "模型服务凭据无效或无权限",
    provider_rate_limit: "模型服务已限流",
    provider_server_error: "模型服务暂时不可用",
    provider_http_error: "模型服务拒绝请求",
    provider_api_error: "模型服务返回错误",
    search_auth_failed: "搜索服务凭据无效",
    search_forbidden: "搜索服务拒绝访问",
    search_rate_limited: "搜索服务已限流",
    search_service_unavailable: "搜索服务暂时不可用",
    search_http_error: "搜索服务请求失败",
    search_timeout: "搜索服务超时",
    search_network_error: "搜索服务连接失败",
    search_invalid_response: "搜索服务返回的数据无效",
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
