/**
 * I-01 事件投影：把服务端已提交的 RunEvent 投影成页面进度状态。
 *
 * 契约（实施计划 §9）：
 * - 事件带 ID 与递增序号；重连时用上次看到的序号做游标（`Last-Event-ID`）；
 * - 同序号事件只应用一次，避免重连把同一工具显示两次；
 * - 先查一次 Task 终态，再开流；流在终态关闭后仍会补一次终态查询，
 *   防止「事件刚好在开流前提交」导致阶段丢失。
 */

export type StreamEvent = {
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ProgressStep = {
  sequence: number;
  type: string;
  label: string;
  detail?: string;
};

export type ProgressPlan = {
  steps: ProgressStep[];
  lastSequence: number;
  /** 是否已收到终态事件（服务端流会随之关闭）。 */
  terminal: boolean;
  /** 长任务中当前正在做什么；没有工具活动时为 null。 */
  current: string | null;
};

export const EMPTY_PROGRESS: ProgressPlan = {
  steps: [],
  lastSequence: 0,
  terminal: false,
  current: null,
};

const RUN_TERMINAL = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.timeout",
  "run.limit_reached",
  "run.authorization_revoked",
  "run.input_changed",
  // 授权拒绝事件本身也意味着本次运行必然终止（随后会写 run.authorization_revoked）。
  "authorization.revoked",
  // 输入被替换同样必然终止运行。
  "input.changed",
]);

function shorten(value: unknown, limit = 120): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
  const trimmed = text.trim().replace(/\s+/g, " ");
  return trimmed.length > limit ? `${trimmed.slice(0, limit)}…` : trimmed;
}

/** 每个事件类型一句话说明「现在在做什么」。 */
export function stepLabel(event: StreamEvent): { label: string; detail?: string } {
  const payload = event.payload ?? {};
  switch (event.type) {
    case "task.queued":
      return { label: "任务已排队" };
    case "run.started":
      return {
        label: payload.resumed === true ? "从快照恢复执行" : "开始执行",
        detail: [payload.provider, payload.model].filter(Boolean).join(" / ") || undefined,
      };
    case "context.checked":
    case "context.trimmed":
      return { label: "整理上下文", detail: shorten(payload) };
    case "context.rejected":
      return { label: "上下文超预算，已拒绝", detail: shorten(payload) };
    case "model.requested":
      return { label: "请求模型" };
    case "model.delta":
      return { label: "模型输出中", detail: shorten(payload.text_delta) };
    case "model.completed":
      return { label: "模型返回" };
    case "model.failed":
      return { label: "模型调用失败", detail: shorten(payload.error_code) };
    case "tool.started":
      return {
        label: `调用工具 ${shorten(payload.name, 60)}`,
        detail: shorten(payload.arguments),
      };
    case "tool.completed":
      return {
        label: `工具完成 ${shorten(payload.name, 60)}`,
        detail: payload.truncated === true ? "结果已截断" : undefined,
      };
    case "tool.failed":
      return {
        label: `工具失败 ${shorten(payload.name, 60)}`,
        detail: shorten(payload.error_code ?? payload.error_message),
      };
    case "approval.required":
      return { label: "等待人工审批", detail: shorten(payload.tool) };
    case "input.frozen": {
      const files = Array.isArray(payload.files) ? payload.files : [];
      return {
        label: `冻结输入 ${files.length} 个文件`,
        detail: files.map((item) => shorten((item as { path?: string }).path, 60)).join("、"),
      };
    }
    case "input.changed":
      return { label: "输入已变化，任务终止", detail: shorten(payload.message) };
    case "authorization.revoked":
      return { label: "项目授权已被撤销，本次调用被拒绝", detail: shorten(payload.message) };
    case "acceptance.checked":
      return {
        label: payload.passed === true ? "验收条件通过" : "验收条件未通过",
      };
    case "recovery.completed":
      return { label: "恢复完成", detail: shorten(payload.decision) };
    case "source.observed":
      return { label: "记录来源证据", detail: shorten(payload.level) };
    case "run.completed":
      return { label: "任务完成" };
    case "run.failed":
      return { label: "任务失败", detail: shorten(payload.error_code ?? payload.error_message) };
    case "run.cancelled":
      return { label: "任务已取消" };
    case "run.timeout":
      return { label: "任务超时" };
    case "run.limit_reached":
      return { label: "达到执行上限", detail: shorten(payload.error_code) };
    case "run.authorization_revoked":
      return { label: "授权撤销，任务终止", detail: shorten(payload.error_message) };
    default:
      return { label: event.type, detail: shorten(payload) || undefined };
  }
}

/**
 * 把一个事件合并进进度状态。
 *
 * 去重按序号：`sequence <= lastSequence` 的事件直接丢弃，因此重连补发不会重复显示。
 */
export function applyEvent(plan: ProgressPlan, event: StreamEvent): ProgressPlan {
  if (event.sequence <= plan.lastSequence) return plan;
  const { label, detail } = stepLabel(event);
  const next: ProgressPlan = {
    steps: [...plan.steps, { sequence: event.sequence, type: event.type, label, detail }],
    lastSequence: event.sequence,
    terminal: plan.terminal || RUN_TERMINAL.has(event.type),
    current: plan.current,
  };
  if (event.type === "tool.started") next.current = `正在执行 ${shorten(event.payload?.name, 60)}`;
  else if (event.type === "tool.completed" || event.type === "tool.failed") next.current = null;
  else if (event.type === "approval.required") next.current = "等待人工审批";
  else if (RUN_TERMINAL.has(event.type)) next.current = null;
  return next;
}

export function applyEvents(plan: ProgressPlan, events: StreamEvent[]): ProgressPlan {
  return events.reduce(applyEvent, plan);
}
