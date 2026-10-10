import { useState } from "react";
import { SkillUsagePanel } from "./SkillUsagePanel";
import { chat, type Project } from "../api/chat";
import { learning, learningError, type LearningRequest, type SkillTrial, type TrialReadiness } from "../api/learning";

export function TrialCandidatePanel({ row }: { row: LearningRequest }) {
  const [readiness, setReadiness] = useState<TrialReadiness | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [adopted, setAdopted] = useState<SkillTrial | null>(null);
  if (row.request_kind !== "validate" || !row.candidate_version_id) return null;
  const versionId = row.candidate_version_id;
  async function check() {
    setBusy(true); setError(""); setReadiness(null);
    try { setReadiness(await learning.trialReadiness(versionId, row.id)); }
    catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  }
  async function activate() {
    if (!readiness?.ready || readiness.skill_lock_version === null || !reason.trim()) return;
    setBusy(true); setError("");
    try {
      setAdopted(await learning.activateTrial(versionId, { workspace_id: row.workspace_id, project_id: row.project_id ?? null, validation_request_id: row.id, expected_lock_version: readiness.skill_lock_version, reason }));
      setReadiness(null);
    } catch (failure) { setError(learningError(failure)); setReadiness(null); }
    finally { setBusy(false); }
  }
  return <section aria-label={`限定试用 ${row.id}`}>
    <button type="button" disabled={busy || adopted !== null} onClick={() => void check()}>检查试用就绪条件</button>
    {readiness && <p>{readiness.ready ? "独立验证已满足试用门禁；需你确认限定采用。" : `尚不能试用：${readiness.reasons.join("、")}`}</p>}
    {readiness?.ready && <>
      <label>试用依据<textarea aria-label={`试用依据 ${row.id}`} maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <p>仅用于当前工作区{row.project_id ? `的项目 ${row.project_id}` : ""}的后续任务；不会移动正式版本指针。</p>
      <button type="button" disabled={busy || !reason.trim() || readiness.skill_lock_version === null} onClick={() => void activate()}>确认限定试用</button>
    </>}
    {adopted && <p role="status">限定试用已启用：{adopted.id}。可在试用管理中挂起或回滚。</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

export function SkillTrialPanel({ workspaceId }: { workspaceId: string }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [rows, setRows] = useState<SkillTrial[] | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load() { setRows((await learning.trials(workspaceId, projectId)).items); }
  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await operation(); await load(); }
    catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  }
  return <section aria-label="限定试用管理">
    <h3>限定试用管理</h3>
    <button type="button" disabled={busy} onClick={() => void act(async () => { setProjects(await chat.projects()); })}>加载项目范围</button>
    <label>试用范围<select aria-label="试用管理项目范围" disabled={busy} value={projectId ?? ""} onChange={event => { setProjectId(event.target.value || null); setRows(null); setReasons({}); setTargets({}); }}>
      <option value="">仅工作区范围</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
    </select></label>
    <button type="button" disabled={busy} onClick={() => void act(load)}>查看或刷新试用记录</button>
    {rows?.length === 0 && <p>当前所选范围没有限定试用。</p>}
    {rows?.map(row => <article key={row.id}>
      <p>试用 {row.id} / 版本 {row.version_id} / 状态 {row.status}</p>
      <SkillUsagePanel key={row.id} trial={row} />
      {row.suspension_reason && <p>挂起原因：{row.suspension_reason}</p>}
      <label>操作依据<textarea aria-label={`试用操作依据 ${row.id}`} maxLength={1000} value={reasons[row.id] ?? ""} onChange={event => setReasons(values => ({ ...values, [row.id]: event.target.value }))} /></label>
      {row.status !== "suspended" && <button type="button" disabled={busy || !(reasons[row.id] ?? "").trim()} onClick={() => void act(() => learning.suspendTrial(row, reasons[row.id]))}>挂起试用 {row.id}</button>}
      <label>回滚目标<select aria-label={`试用回滚目标 ${row.id}`} value={targets[row.id] ?? ""} onChange={event => setTargets(values => ({ ...values, [row.id]: event.target.value }))}>
        <option value="">请选择历史已替换试用</option>{rows.filter(target => target.id !== row.id && target.skill_id === row.skill_id && target.status === "replaced" && target.project_id === row.project_id).map(target => <option key={target.id} value={target.id}>{target.id} / {target.version_id}</option>)}
      </select></label>
      <button type="button" disabled={busy || !targets[row.id] || !(reasons[row.id] ?? "").trim()} onClick={() => void act(() => learning.rollbackTrial(row, targets[row.id], reasons[row.id]))}>回滚试用 {row.id}</button>
    </article>)}
    {rows?.length === 100 && <p>已达到当前页 100 项上限，更多记录可通过 API 分页查看。</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
