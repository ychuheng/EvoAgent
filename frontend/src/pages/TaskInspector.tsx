import { useEffect, useState } from "react";

import { chat, type ChatTask, type TaskTrace } from "../api/chat";
import { ApiError } from "../api/client";
import { phase4, type RetrievalEvidence } from "../api/phase4";
import { errorLabel, taskStatusLabel } from "./taskLabels";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const TOOL_STATUS: Record<string, string> = {
  pending: "待执行", running: "执行中", succeeded: "成功", failed: "失败",
  denied: "已拒绝", unknown: "结果不确定",
};

export function TaskInspector({ taskId, onOpenVersion, onOpenContext }: { taskId: string; onOpenVersion: (id: string) => void; onOpenContext: (id: string) => void }) {
  const [task, setTask] = useState<ChatTask | null>(null);
  const [trace, setTrace] = useState<TaskTrace | null>(null);
  const [retrieval, setRetrieval] = useState<RetrievalEvidence | null>(null);
  const [responses, setResponses] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    setTask(null);
    setTrace(null);
    setRetrieval(null);
    setError("");
    setResponses({});
    const refresh = async () => {
      try {
        const current = await chat.task(taskId);
        const detail = await chat.trace(current.latest_run.id);
        if (!active) return;
        setTask(current);
        setTrace(detail);
        setError("");
        if (detail.events?.some((event) => event.event_type.startsWith("retrieval."))) {
          try { setRetrieval(await phase4.retrieval(current.latest_run.id)); }
          catch (reason) { if (!(reason instanceof ApiError && reason.status === 404)) setError(String(reason)); }
        }
        if (!TERMINAL.has(current.status)) timer = window.setTimeout(() => { void refresh(); }, 1200);
      } catch (reason) {
        if (!active) return;
        setError(String(reason));
        timer = window.setTimeout(() => { void refresh(); }, 2000);
      }
    };
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [taskId]);

  async function act(action: () => Promise<unknown>, approvalId?: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      const current = await chat.task(taskId);
      setTask(current);
      setTrace(await chat.trace(current.latest_run.id));
      if (approvalId) setResponses((values) => ({ ...values, [approvalId]: "" }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  const pendingApprovals = task && !TERMINAL.has(task.status)
    ? trace?.approvals.filter((approval) => approval.status === "pending") ?? [] : [];
  const lexicalSkills = (trace?.events ?? []).filter((event) => event.event_type === "skill.selected")
    .flatMap((event) => Array.isArray(event.payload.matches) ? event.payload.matches : [])
    .map((match) => typeof match === "object" && match !== null && "version_id" in match ? String(match.version_id) : "")
    .filter(Boolean);
  const hybridSkills = retrieval?.selections.filter((item) => !item.omission_reason && item.source_key.startsWith("skill:"))
    .map((item) => item.source_key.slice("skill:".length)) ?? [];
  const selectedSkills = [...new Set([...lexicalSkills, ...hybridSkills])];
  const selectedMemories = retrieval?.selections.filter((item) => !item.omission_reason && item.source_key.startsWith("memory:")) ?? [];
  const showError = trace?.error_code && task?.status !== "cancelled" && !(
    trace.error_code === "approval_required" && task && !TERMINAL.has(task.status)
  );
  const acceptancePassed = trace?.events.some((event) => event.event_type === "acceptance.checked" && event.payload.passed === true);
  return <section className="chat-inspector" aria-label="任务执行过程">
    <h3>执行过程</h3>
    <p>{task ? `状态：${taskStatusLabel[task.status] ?? task.status}` : "正在读取任务状态…"}{task?.cancel_requested ? " · 已请求取消" : ""}</p>
    {task && <p className="chat-meta">模型：{task.latest_run.provider} / {task.latest_run.model} · Task {task.id} · Run {task.latest_run.id}</p>}
    {task?.status === "completed" && <p className="chat-meta">{task.acceptance ? (acceptancePassed ? "已通过设定的验收条件；其他内容仍需核对。" : "验收记录缺失，结果不能视为已核验。") : "模型已给出回答；未设置独立验收条件，正确性尚未核验。"}</p>}
    {trace?.error_code === "acceptance_failed" && trace.final_answer && <details><summary>查看未通过验收的模型回答</summary><p>{trace.final_answer}</p></details>}
    {task && <button type="button" className="chat-detail-button" onClick={() => onOpenContext(task.latest_run.id)}>查看上下文与检索证据</button>}
    {task && !TERMINAL.has(task.status) && !task.cancel_requested && <button type="button" className="danger" disabled={busy} onClick={() => { void act(() => chat.cancel(taskId)); }}>取消任务</button>}
    {showError && <p role="alert" className="error">{task && TERMINAL.has(task.status) ? "失败原因" : "最近错误"}：{errorLabel(trace.error_code!)}</p>}
    {trace?.tool_calls.length ? <div className="chat-tool-list"><h4>工具调用</h4><ol>{trace.tool_calls.map((call) =>
      <li key={call.id}><strong>{call.tool_name}</strong> · {trace.approvals.some((approval) => approval.tool_call_id === call.id && approval.status === "approved") && call.status === "pending" ? "已审批，等待恢复" : TOOL_STATUS[call.status] ?? call.status}
        <details><summary>查看输入参数</summary><pre>{JSON.stringify(call.arguments, null, 2)}</pre></details>
        {call.result_summary && <p>{call.result_summary}</p>}
        {call.error_code && <p className="error">{errorLabel(call.error_code)}</p>}
      </li>
    )}</ol></div> : <p className="chat-meta">此任务尚无工具调用记录。</p>}
    <div className="chat-evidence"><h4>检索与 Skill</h4>
      {selectedSkills.length ? selectedSkills.map((id) => <button key={id} type="button" className="chat-detail-button" onClick={() => onOpenVersion(id)}>查看 Skill 版本 {id.slice(0, 8)}…</button>) :
        <p className="chat-meta">{trace?.events?.some((event) => event.event_type === "skill.none_selected" || event.event_type.startsWith("retrieval.")) ? "此任务未选中 Skill。" : "尚无 Skill 选择证据。"}</p>}
      {retrieval && <p className="chat-meta">记忆命中 {selectedMemories.length} 条 · {retrieval.degraded ? "检索已降级" : "检索未降级"} · 批次 {retrieval.batch_id}</p>}
    </div>
    {pendingApprovals.map((approval) => {
      const call = trace?.tool_calls.find((item) => item.id === approval.tool_call_id);
      const unknown = trace?.tool_effects.some((effect) => effect.tool_call_id === approval.tool_call_id && effect.status === "unknown");
      const response = responses[approval.id] ?? "";
      return <div className="chat-approval" key={approval.id}>
        <h4>需要人工决定：{call?.tool_name ?? "工具调用"}</h4>
        <p>风险 {approval.risk} · {approval.reason}</p>
        {unknown && <p>外部操作结果不确定。确认重试请输入 retry；确认已提交请输入 committed:实际结果。请先核对外部状态。</p>}
        <label>回复或确认依据<input value={response} onChange={(event) => setResponses((values) => ({ ...values, [approval.id]: event.target.value }))} placeholder={unknown ? "retry 或 committed:实际结果" : "ask_user 工具需要填写回复"} /></label>
        <div className="actions">
          <button type="button" disabled={busy || (unknown && !response.trim()) || (call?.tool_name === "ask_user" && !response.trim())} onClick={() => { void act(() => chat.decideApproval(approval.id, "approve", response), approval.id); }}>批准</button>
          <button type="button" className="danger" disabled={busy} onClick={() => { void act(() => chat.decideApproval(approval.id, "reject", response), approval.id); }}>拒绝</button>
        </div>
      </div>;
    })}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
