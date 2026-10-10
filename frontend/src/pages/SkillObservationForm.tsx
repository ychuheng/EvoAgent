import { useEffect, useRef, useState } from "react";
import { learning, learningError, type HumanSkillObservation, type ObservationCatalog } from "../api/learning";

export function SkillObservationForm({ runId, onChange }: {
  runId: string; onChange: (claim: HumanSkillObservation | null) => void;
}) {
  const currentRun = useRef(runId);
  currentRun.current = runId;
  const [pendingIds, setPendingIds] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);
  const [catalog, setCatalog] = useState<ObservationCatalog | null>(null);
  const [error, setError] = useState("");
  const [versionId, setVersionId] = useState("");
  const [criterion, setCriterion] = useState("");
  const [outcome, setOutcome] = useState<HumanSkillObservation["outcome"]>("verified_success");
  const [attribution, setAttribution] = useState<HumanSkillObservation["attribution"]>("uncertain");
  const [steps, setSteps] = useState<string[]>([]);
  const [artifactIds, setArtifactIds] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    setCatalog(null); setError(""); setVersionId(""); setSteps([]); setArtifactIds([]); setPendingIds([]); setChecking(false);
    void learning.observationEvidence(runId).then(value => { if (active) setCatalog(value); })
      .catch(reason => { if (active) setError(learningError(reason)); });
    return () => { active = false; };
  }, [runId]);
  useEffect(() => {
    const selected = catalog?.versions.find(version => version.version_id === versionId);
    const artifacts = catalog?.artifacts.filter(item => artifactIds.includes(item.artifact_id)) ?? [];
    const valid = selected && /^[a-z][a-z0-9_-]{1,63}$/.test(criterion) && steps.length > 0
      && steps.length <= 20 && steps.every(step => selected.steps.includes(step))
      && artifacts.length > 0 && artifacts.length <= 20;
    onChange(valid ? {
      type: "skill_observation", schema_version: 1, version_id: versionId,
      criterion_id: criterion, outcome, attribution, associated_steps: steps,
      artifacts: artifacts.map(({ artifact_id, content_hash }) => ({ artifact_id, content_hash })),
    } : null);
  }, [catalog, versionId, criterion, outcome, attribution, steps, artifactIds, onChange]);
  async function checkEvidence() {
    const requestedRun = runId;
    const references = (catalog?.pending_artifacts ?? []).filter(item => pendingIds.includes(item.artifact_id))
      .map(({ artifact_id, content_hash }) => ({ artifact_id, content_hash }));
    if (references.length === 0 || references.length > 10 || checking) return;
    setChecking(true); setError("");
    try {
      const checked = await learning.checkObservationEvidence(requestedRun, references);
      if (currentRun.current === requestedRun) { setCatalog(checked); setPendingIds([]); setArtifactIds([]); }
    } catch (reason) {
      if (currentRun.current === requestedRun) setError(learningError(reason));
    } finally { if (currentRun.current === requestedRun) setChecking(false); }
  }
  function toggle(values: string[], value: string) {
    return values.includes(value) ? values.filter(item => item !== value) : [...values, value];
  }
  const selected = catalog?.versions.find(version => version.version_id === versionId);
  return <fieldset aria-label="Skill 使用判定">
    <legend>关联实际 Skill 与证据</legend>
    <p>只记录你核对过的结果；环境问题或不确定原因不会自动归因于 Skill。</p>
    {error && <p role="alert">{error}</p>}
    {catalog && catalog.versions.length === 0 && <p>本次没有可判定的实际 Skill 采用记录。</p>}
    <label>实际采用版本<select aria-label="实际采用版本" value={versionId} onChange={event => { setVersionId(event.target.value); setSteps([]); }}>
      <option value="">请选择</option>{catalog?.versions.map(version => <option key={version.version_id} value={version.version_id}>{version.version_id}（{version.origin}）</option>)}
    </select></label>
    <label>验收判据标识<input aria-label="验收判据标识" maxLength={64} value={criterion} onChange={event => setCriterion(event.target.value)} placeholder="例如 output_correct" /></label>
    <label>核对结果<select aria-label="核对结果" value={outcome} onChange={event => setOutcome(event.target.value as HumanSkillObservation["outcome"])}><option value="verified_success">已核对成功</option><option value="verified_failure">已核对失败</option></select></label>
    <label>原因归属<select aria-label="原因归属" value={attribution} onChange={event => setAttribution(event.target.value as HumanSkillObservation["attribution"])}><option value="uncertain">不确定</option><option value="skill_related">与 Skill 方法有关</option><option value="environment">环境问题</option><option value="user_request">用户需求变化</option></select></label>
    <div>关联步骤（最多 20 项）{selected?.steps.map(step => <label key={step}><input type="checkbox" aria-label={`关联步骤 ${step}`} checked={steps.includes(step)} disabled={!steps.includes(step) && steps.length >= 20} onChange={() => setSteps(toggle(steps, step))} />{step}</label>)}</div>
    <div>产物证据（最多 20 项）{catalog?.artifacts.map(item => <label key={item.artifact_id}><input type="checkbox" aria-label={`产物证据 ${item.artifact_id}`} checked={artifactIds.includes(item.artifact_id)} disabled={!artifactIds.includes(item.artifact_id) && artifactIds.length >= 20} onChange={() => setArtifactIds(toggle(artifactIds, item.artifact_id))} />{item.type} / {item.artifact_id}</label>)}</div>
    {(catalog?.pending_artifacts?.length ?? 0) > 0 && <div>
      <p>这些产物尚未完成当前安全检查。选择后检查正文与哈希；此操作不调用模型，不展示正文。检查通过后仍需你核对业务结果。</p>
      {catalog?.pending_artifacts?.map(item => <label key={item.artifact_id}><input type="checkbox" aria-label={`待检查产物 ${item.artifact_id}`} checked={pendingIds.includes(item.artifact_id)} disabled={checking || (!pendingIds.includes(item.artifact_id) && pendingIds.length >= 10)} onChange={() => setPendingIds(toggle(pendingIds, item.artifact_id))} />{item.type} / {item.artifact_id}</label>)}
      <button type="button" disabled={checking || pendingIds.length === 0} onClick={() => void checkEvidence()}>{checking ? "正在检查产物证据…" : "检查所选产物证据"}</button>
      {catalog?.pending_artifacts_truncated && <p>当前显示前 200 项待检查产物。</p>}
    </div>}
    {catalog?.artifacts_truncated && <p>当前显示前 200 项可引用产物。</p>}
  </fieldset>;
}
