import { useEffect, useRef, useState } from "react";
import { chat, type ChatWorkspace } from "../api/chat";
import { api } from "../api/client";
import { learning, learningError, type LearningPolicy, type LearningRequest } from "../api/learning";
import { PersonalValidationPanel } from "./PersonalValidationPanel";
import { SkillTrialPanel, TrialCandidatePanel } from "./SkillTrialPanel";

const STAGE: Record<string, string> = { prepare: "整理经验", generate: "提炼方法", static_validate: "检查候选", review: "等待审查", reviewed: "已审查", duplicate: "发现重复" };

const STATUS: Record<string, string> = { queued: "已排队", running: "处理中", ready_for_review: "候选待审", completed: "候选已确认", rejected: "已拒绝", cancelled: "已取消", superseded: "来源失效", failed: "失败", waiting_budget: "等待学习额度", skipped: "已有相同方法" };

export function LearningPage() {
  const [workspaces, setWorkspaces] = useState<ChatWorkspace[]>([]);
  const [workspace, setWorkspace] = useState("00000000-0000-0000-0000-000000000001");
  const selectedWorkspace = useRef(workspace);
  selectedWorkspace.current = workspace;
  const [policy, setPolicy] = useState<LearningPolicy | null>(null);
  const [rows, setRows] = useState<LearningRequest[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [mode, setMode] = useState<"off" | "manual" | "suggest">("off");
  const [daily, setDaily] = useState("");
  const [perRequest, setPerRequest] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [retryKeys, setRetryKeys] = useState<Record<string, string>>({});
  const [candidateViews, setCandidateViews] = useState<Record<string, string>>({});
  const [details, setDetails] = useState<Record<string, LearningRequest>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { void chat.workspaces().then(setWorkspaces).catch(reason => setError(String(reason))); }, []);
  useEffect(() => {
    let active = true;
    setRows([]); setPolicy(null); setError(""); setReasons({}); setRetryKeys({}); setCursor(null); setDetails({}); setCandidateViews({});
    void Promise.all([learning.policy(workspace), learning.list(workspace)]).then(([nextPolicy, page]) => {
      if (!active) return;
      setPolicy(nextPolicy); setMode(nextPolicy.mode);
      setDaily(nextPolicy.daily_limit_micros === null ? "" : String(nextPolicy.daily_limit_micros / 1_000_000));
      setPerRequest(nextPolicy.request_limit_micros === null ? "" : String(nextPolicy.request_limit_micros / 1_000_000));
      setRows(page.items); setCursor(page.next_cursor);
    }).catch(reason => { if (active) setError(learningError(reason)); });
    return () => { active = false; };
  }, [workspace]);
  async function refresh() {
    const [nextPolicy, page] = await Promise.all([learning.policy(workspace), learning.list(workspace)]);
    if (selectedWorkspace.current !== workspace) return;
    setPolicy(nextPolicy); setRows(page.items); setCursor(page.next_cursor); setDetails({});
  }
  async function act(operation: () => Promise<unknown>, reload = true) {
    if (busy) return;
    setBusy(true); setError("");
    try { await operation(); if (reload) await refresh(); }
    catch (reason) { setError(learningError(reason)); }
    finally { setBusy(false); }
  }
  async function savePolicy() {
    if (!policy) return;
    const money = (value: string) => value.trim() === "" ? null : Math.round(Number(value) * 1_000_000);
    const dailyLimit = money(daily), requestLimit = money(perRequest);
    if ([dailyLimit, requestLimit].some(value => value !== null && (!Number.isSafeInteger(value) || value < 0))) { setError("预算需为非负金额。"); return; }
    await act(() => learning.updatePolicy(workspace, { mode, expected_lock_version: policy.lock_version, daily_limit_micros: dailyLimit, request_limit_micros: requestLimit, daily_candidate_limit: policy.daily_candidate_limit, cooldown_seconds: policy.cooldown_seconds, max_source_risk: policy.max_source_risk }));
  }
  async function retry(row: LearningRequest) {
    const key = retryKeys[row.id] ?? crypto.randomUUID();
    setRetryKeys(values => ({ ...values, [row.id]: key }));
    await act(() => learning.retry(row, key));
  }
  return <section aria-label="个人学习"><h2>个人方法学习</h2>
    <p>任务结束后提交反馈或记成方法，生成候选供审查。确认候选不会直接启用 Skill；验证和试用需要各自的证据与操作。</p>
    <label>学习 Workspace<select aria-label="学习 Workspace" value={workspace} disabled={busy} onChange={event => setWorkspace(event.target.value)}>{workspaces.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
    {policy && <section className="chat-evidence" aria-label="学习策略"><h3>工作区学习策略</h3>
      <p>服务学习开关：{policy.learning_enabled ? "已开启" : "已关闭"}。后台建议需要选择对应模式并具备验收证据。</p>
      <label>学习模式<select aria-label="学习模式" value={mode} onChange={event => setMode(event.target.value as "off" | "manual" | "suggest")}><option value="off">关闭</option><option value="manual">仅显式申请</option><option value="suggest">后台建议（需要明确验收证据）</option></select></label>
      <label>每日学习预算（元）<input aria-label="每日学习预算" type="number" min="0" step="0.000001" value={daily} onChange={event => setDaily(event.target.value)} /></label>
      <label>每次申请预算（元）<input aria-label="每次学习预算" type="number" min="0" step="0.000001" value={perRequest} onChange={event => setPerRequest(event.target.value)} /></label>
      <p>留空不授权付费学习；学习额度也受总预算限制。</p>
      <p>后台建议仅扫描终结的个人任务，遵守每日候选数量、冷却期和费用额度；只生成待审方法，不会自动启用。</p><button type="button" disabled={busy} onClick={() => void savePolicy()}>保存学习策略</button>
    </section>}
    <button type="button" disabled={busy} onClick={() => void act(refresh, false)}>刷新学习请求</button>
    {!rows.length && <p>当前没有学习请求。</p>}
    {rows.map(row => <article className="chat-evidence" key={row.id}><h3>{STATUS[row.status] ?? row.status}</h3>
      <p>请求 {row.id} · 阶段 {STAGE[row.stage] ?? "处理候选"} · 来源运行 {row.origin_run_id}</p>
      {row.candidate_version_id && <><p>候选版本：{row.candidate_version_id}（尚不表示已启用）</p><button type="button" disabled={busy} onClick={() => void act(async () => { const version = await api.getVersion(row.candidate_version_id!); setCandidateViews(values => ({ ...values, [row.id]: JSON.stringify(version, null, 2) })); }, false)}>查看候选内容</button>{candidateViews[row.id] && <pre>{candidateViews[row.id]}</pre>}</>}
      {row.status === "waiting_budget" && <p>当前额度不足或尚未授权。若申请时未填写额度，请保存策略后从原任务重新申请；旧申请不会自动扩大授权。</p>}
      {row.error_code && <p role="status">处理未完成：{row.error_code}</p>}
      <button type="button" disabled={busy} onClick={() => void act(async () => { const detail = await learning.get(row.id); setDetails(values => ({ ...values, [row.id]: detail })); }, false)}>查看来源与处理证据</button>
      {details[row.id] && <><pre>{JSON.stringify(details[row.id], null, 2)}</pre>{details[row.id].cost && <p>已知学习费用：{details[row.id].cost!.known_spent_micros / 1_000_000} 元 · 尚未确认的调用：{details[row.id].cost!.unknown_usage_count}</p>}{details[row.id].source?.status === "valid" && <><label>撤销学习来源的依据<textarea aria-label={`撤销依据 ${row.id}`} value={reasons[row.id] ?? ""} maxLength={2000} onChange={event => setReasons(values => ({ ...values, [row.id]: event.target.value }))} /></label><button type="button" disabled={busy || !reasons[row.id]?.trim()} onClick={() => void act(() => learning.revoke(details[row.id].source!.id, "valid", reasons[row.id]))}>撤销这份学习来源</button></>}</>}
      {row.available_actions.includes("cancel") && <button type="button" disabled={busy} onClick={() => void act(() => learning.cancel(row))}>取消学习请求</button>}
      {row.available_actions.includes("retry") && <button type="button" disabled={busy || !policy?.learning_enabled} onClick={() => void retry(row)}>重试学习请求</button>}
      {row.available_actions.includes("review") && <><label>候选审查依据<textarea aria-label={`审查依据 ${row.id}`} maxLength={2000} value={reasons[row.id] ?? ""} onChange={event => setReasons(values => ({ ...values, [row.id]: event.target.value }))} /></label><button type="button" disabled={busy || !reasons[row.id]?.trim()} onClick={() => void act(() => learning.review(row, "acknowledge", reasons[row.id]))}>确认候选（不启用）</button><button type="button" disabled={busy || !reasons[row.id]?.trim()} onClick={() => void act(() => learning.review(row, "reject", reasons[row.id]))}>拒绝候选</button></>}
      <PersonalValidationPanel row={row} enabled={policy?.personal_validation_available === true && policy.mode !== "off" && !busy} onChanged={refresh} />
      <TrialCandidatePanel row={row} />
    </article>)}
    {cursor && <button type="button" disabled={busy} onClick={() => void act(async () => { const page = await learning.list(workspace, cursor); setRows(values => [...values, ...page.items]); setCursor(page.next_cursor); }, false)}>加载更多学习请求</button>}
    <SkillTrialPanel key={workspace} workspaceId={workspace} />
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
