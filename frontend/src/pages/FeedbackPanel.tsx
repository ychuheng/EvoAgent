import { useState } from "react";
import { learning, learningError, type FeedbackResult, type LearningPolicy } from "../api/learning";

export function FeedbackPanel({ runId, workspaceId }: { runId: string; workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const [policy, setPolicy] = useState<LearningPolicy | null>(null);
  const [intent, setIntent] = useState("unsure");
  const [verdict, setVerdict] = useState("helpful");
  const [correction, setCorrection] = useState("");
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<FeedbackResult | null>(null);
  function changed() { setKey(crypto.randomUUID()); setResult(null); }
  async function expand() {
    setOpen(true);
    try { setPolicy(await learning.policy(workspaceId)); }
    catch (reason) { setError(learningError(reason)); }
  }
  async function submit() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      setResult(await learning.feedback(runId, { intent, verdict, correction, comment, learn_from_feedback: consent && intent === "method", client_request_id: key }));
    } catch (reason) { setError(learningError(reason)); }
    finally { setBusy(false); }
  }
  return <section className="chat-evidence" aria-label="任务反馈">
    {!open ? <button type="button" onClick={() => void expand()}>反馈与记成方法</button> : <>
      <h4>这次任务有什么值得保留或改进？</h4>
      <label>反馈类型<select aria-label="反馈类型" value={intent} onChange={event => { setIntent(event.target.value); setConsent(false); changed(); }}><option value="unsure">暂不确定</option><option value="method">可复用的方法</option><option value="fact">事实或偏好</option><option value="mixed">同时包含事实和方法</option></select></label>
      <label>结果判断<select aria-label="结果判断" value={verdict} onChange={event => { setVerdict(event.target.value); changed(); }}><option value="helpful">有帮助</option><option value="needs_fix">需要改进</option><option value="incorrect">结果错误</option></select></label>
      <label>纠正与方法<textarea aria-label="纠正与方法" maxLength={8000} value={correction} onChange={event => { setCorrection(event.target.value); changed(); }} /></label>
      <label>备注<textarea aria-label="反馈备注" maxLength={8000} value={comment} onChange={event => { setComment(event.target.value); changed(); }} /></label>
      {intent === "method" && <label><input type="checkbox" checked={consent} disabled={!policy?.learning_enabled || policy.mode === "off"} onChange={event => { setConsent(event.target.checked); changed(); }} />用于改进方法</label>}
      {intent === "method" && (!policy?.learning_enabled || policy.mode === "off") && <p>方法学习当前关闭；可以先提交普通反馈，再到个人学习页设置策略。</p>}
      {intent === "fact" && <p>事实或偏好请到 Memory 管理页另行提议和确认；本次只保存反馈。</p>}
      {intent === "mixed" && <p>请先拆分事实和方法；本次不会自动创建学习请求。</p>}
      <p>记成方法会生成待审候选，不会直接启用 Skill。</p>
      <button type="button" disabled={busy || result !== null} onClick={() => void submit()}>提交任务反馈</button>
      {result && <p role="status">反馈已保存{result.learning_request_id ? `，方法请求已排队：${result.learning_request_id}` : "，没有创建方法请求"}。</p>}
      {result?.routing === "clarify" && <p>已保存反馈，请拆分事实与方法，或明确要修订的 Skill；不会自动猜测目标。</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </>}
  </section>;
}
