import { ApiError, request } from "./client";

export type LearningPolicy = {
  mode: "off" | "manual" | "suggest"; lock_version: number;
  daily_limit_micros: number | null; request_limit_micros: number | null;
  daily_candidate_limit: number; cooldown_seconds: number; max_source_risk: string;
  learning_enabled?: boolean;
};
export type LearningRequest = {
  id: string; workspace_id: string; origin_run_id: string; status: string; stage: string;
  request_kind: string; lock_version: number; candidate_version_id: string | null;
  validation_report_hash: string | null; error_code: string | null; available_actions: string[];
  source?: { id: string; status: string; revocation_epoch: number } | null;
  cost?: { known_spent_micros: number; outstanding_reserved_micros: number; unknown_usage_count: number } | null;
};
export type FeedbackResult = { id: string; revision: number; learning_revision: number; routing: string; learning_request_id: string | null };

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const learning = {
  policy: (id: string) => request<LearningPolicy>(`/workspaces/${id}/learning-policy`),
  updatePolicy: (id: string, body: unknown) => request<LearningPolicy>(`/workspaces/${id}/learning-policy`, { method: "PUT", body: JSON.stringify(body) }),
  feedback: (runId: string, body: unknown) => post<FeedbackResult>(`/runs/${runId}/feedback`, body),
  list: (workspaceId: string, cursor?: string) => request<{ items: LearningRequest[]; next_cursor: string | null }>(`/learning-requests?workspace_id=${encodeURIComponent(workspaceId)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  get: (id: string) => request<LearningRequest>(`/learning-requests/${id}`),
  cancel: (row: LearningRequest) => post<LearningRequest>(`/learning-requests/${row.id}/cancel`, { expected_lock_version: row.lock_version }),
  review: (row: LearningRequest, action: "acknowledge" | "reject", reason: string) => post<LearningRequest>(`/learning-requests/${row.id}/review`, { action, reason, expected_lock_version: row.lock_version }),
  retry: (row: LearningRequest, clientRequestId: string) => post<LearningRequest>(`/learning-requests/${row.id}/retry`, { expected_lock_version: row.lock_version, client_request_id: clientRequestId }),
  revoke: (id: string, expectedStatus: string, reason: string) => post(`/learning-sources/${id}/revoke`, { expected_status: expectedStatus, reason }),
};

export function learningError(reason: unknown): string {
  const labels: Record<string, string> = {
    learning_disabled: "方法学习已关闭，本次没有保存学习请求。可取消勾选后提交普通反馈。",
    learning_policy_off: "当前工作区未开启方法学习。请先在个人学习页设置策略。",
    source_method_consent_required: "需要明确选择方法，并允许用于改进方法。",
    source_run_not_terminal: "任务尚未结束，请完成后再提交方法学习。",
    source_effect_unresolved: "任务含有尚未确认的外部操作，核对结果后才能作为学习来源。",
    learning_request_version_conflict: "请求已变化，请刷新后重新核对。",
    learning_usage_unresolved: "上次调用费用尚未确认，暂时不能重试。",
    invalid_learning_payload: "反馈内容或引用不符合要求，可能包含敏感信息。请修改后再提交。",
  };
  return reason instanceof ApiError ? labels[reason.code] ?? reason.message : String(reason);
}
