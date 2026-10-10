import { useState } from "react";
import { api } from "../api/client";
import { learning, learningError, type SkillTrial } from "../api/learning";
import type { SkillSummary, SupersessionBody } from "../api/types";

export function SkillSupersessionPanel({ skill, onChanged }: { skill: SkillSummary; onChanged: () => void }) {
  const [trials, setTrials] = useState<SkillTrial[]>([]);
  const [selected, setSelected] = useState("");
  const [frozen, setFrozen] = useState<SupersessionBody | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  async function load() {
    if (!skill.workspace_id) return;
    setBusy(true); setError(""); setFrozen(null); setSelected("");
    try {
      setTrials((await learning.trials(skill.workspace_id, skill.project_id ?? null)).items.filter(trial => trial.status === "active" && trial.skill_id !== skill.id && trial.project_id === (skill.project_id ?? null)));
    } catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  }
  async function check() {
    const trial = trials.find(row => row.id === selected); if (!trial) return;
    setBusy(true); setError(""); setFrozen(null);
    try {
      const [old, replacement, version, readiness] = await Promise.all([api.getSkill(skill.id), api.getSkill(trial.skill_id), api.getVersion(trial.version_id), learning.trialReadiness(trial.version_id, trial.validation_request_id)]);
      if (!version.merge_candidate || !readiness.ready || readiness.report_hash !== trial.report_hash || old.status !== "enabled" || replacement.status !== "enabled") throw new Error("替换方法须是已通过独立验证、正在限定试用的合并候选。");
      if (old.workspace_id !== replacement.workspace_id || (old.project_id ?? null) !== (replacement.project_id ?? null)) throw new Error("替换方法范围不一致。");
      setFrozen({ replacement_id: replacement.id, replacement_version_id: version.id, replacement_trial_id: trial.id, expected_lock_version: old.lock_version, expected_replacement_lock_version: replacement.lock_version, expected_trial_lock_version: trial.lock_version, reason: "" });
    } catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  }
  async function submit() {
    if (!frozen || !reason.trim()) return;
    setBusy(true); setError("");
    try { await api.supersede(skill.id, { ...frozen, reason }); setDone(true); setFrozen(null); onChanged(); }
    catch (failure) { setError(learningError(failure)); setFrozen(null); }
    finally { setBusy(false); }
  }
  return <section aria-label="用合并方法替换旧方法">
    <h3>用合并方法替换旧方法</h3>
    <p>人工确认后弃用当前旧方法、停止其旧试用绑定，保留历史来源。替换方法须是当前方法参与生成的合并候选；服务器还会复查来源和健康状态。</p>
    <fieldset disabled={busy || done}>
      <button type="button" disabled={!skill.workspace_id} onClick={() => void load()}>加载同范围有效试用</button>
      <label>替换试用<select aria-label="替换试用" value={selected} onChange={event => { setSelected(event.target.value); setFrozen(null); setReason(""); }}><option value="">请选择</option>{trials.map(trial => <option key={trial.id} value={trial.id}>{trial.skill_id} / {trial.version_id}</option>)}</select></label>
      <button type="button" disabled={!selected} onClick={() => void check()}>检查并冻结替换版本</button>
      {frozen && <><p>已核对独立验证报告；最终操作会复查当前健康状态和版本。弃用后，旧方法不可直接重新启用。</p><label>替换依据<textarea aria-label="替换依据" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label><button type="button" disabled={!reason.trim()} onClick={() => void submit()}>确认替换并弃用当前方法</button></>}
    </fieldset>
    {error && <p role="alert">{error}</p>}{done && <p role="status">已替换旧方法，历史版本与来源已保留。</p>}
  </section>;
}
