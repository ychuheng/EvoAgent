import { useRef, useState } from "react";
import { api } from "../api/client";
import { learningError } from "../api/learning";
import type { MergeProposalBody, SkillSummary } from "../api/types";

export function SkillMergePanel({ skills, onCreated }: { skills: SkillSummary[]; onCreated: () => void }) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [draft, setDraft] = useState<MergeProposalBody | null>(null);
  const [definition, setDefinition] = useState("");
  const [reason, setReason] = useState("");
  const [references, setReferences] = useState<Array<{ name: string; content: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState("");
  const key = useRef("");

  const prepare = async () => {
    setBusy(true); setError(""); setCreated("");
    try {
      const parents = await Promise.all(chosen.map(id => api.getSkill(id)));
      const first = parents[0];
      if (!first?.workspace_id || parents.some(p => p.status !== "enabled" || p.workspace_id !== first.workspace_id || (p.project_id ?? null) !== (first.project_id ?? null))) {
        throw new Error("请选择同一工作区、相同范围的可用方法。");
      }
      const versions = parents.map(parent => {
        const version = parent.versions.find(v => v.lifecycle_status !== "rejected");
        if (!version) throw new Error("所选方法没有可用版本。");
        return { skill_id: parent.id, version_id: version.id, content_hash: version.content_hash, lock_version: parent.lock_version };
      });
      const originals = await Promise.all(versions.map(version => api.getVersion(version.version_id)));
      setReferences(originals.map((version, index) => ({ name: parents[index].name, content: JSON.stringify(version.definition, null, 2) })));
      const initial = { ...originals[0].definition, name: "combined_method" };
      key.current = crypto.randomUUID();
      setDraft({ workspace_id: first.workspace_id, project_id: first.project_id ?? null, parents: versions, definition: initial, reason: "", client_request_id: key.current });
      setDefinition(JSON.stringify(initial, null, 2)); setReason("");
    } catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  };

  const submit = async () => {
    if (!draft) return;
    setBusy(true); setError("");
    try {
      const parsed: unknown = JSON.parse(definition);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("方法内容必须是 JSON 对象。");
      const result = await api.proposeMerge({ ...draft, definition: parsed as Record<string, unknown>, reason, client_request_id: key.current });
      setCreated(result.id); setDraft(null); setChosen([]); onCreated();
    } catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  };

  return <section aria-label="合并方法候选">
    <h3>合并方法候选</h3>
    <p>选择 2 至 4 条同范围的方法，人工整理合并内容。起始草稿仅复制第一条方法，需要你补入其他方法；提交后仍须在“个人学习”审查和独立验证，旧方法继续保留。</p>
    <fieldset disabled={busy}>
      {!draft ? <>
        {skills.filter(s => s.status === "enabled").map(skill => <label key={skill.id}><input type="checkbox" aria-label={`合并 ${skill.name}`} checked={chosen.includes(skill.id)} disabled={!chosen.includes(skill.id) && chosen.length >= 4} onChange={event => { setChosen(event.target.checked ? [...chosen, skill.id] : chosen.filter(id => id !== skill.id)); setCreated(""); }} />{skill.name}</label>)}
        <button type="button" disabled={chosen.length < 2} onClick={() => void prepare()}>读取并冻结所选版本</button>
      </> : <>
        <p>范围：{draft.project_id ? "所选项目" : "当前工作区"}。父版本：</p>
        <ul>{draft.parents.map(parent => <li key={parent.skill_id}>{parent.version_id} · {parent.content_hash.slice(0, 18)}…</li>)}</ul>
        {references.map((reference, index) => <details key={index}><summary>参考方法：{reference.name}</summary><pre>{reference.content}</pre></details>)}
        <label>合并后的方法内容（JSON）<textarea aria-label="合并方法内容" rows={18} value={definition} onChange={event => { setDefinition(event.target.value); key.current = crypto.randomUUID(); }} /></label>
        <label>合并依据<input aria-label="合并依据" maxLength={1000} value={reason} onChange={event => { setReason(event.target.value); key.current = crypto.randomUUID(); }} /></label>
        <button type="button" disabled={!reason.trim()} onClick={() => void submit()}>确认创建合并候选</button>
        <button type="button" onClick={() => { setDraft(null); setError(""); }}>返回重新选择</button>
      </>}
    </fieldset>
    {error && <p role="alert">{error}</p>}
    {created && <p role="status">已创建合并候选 {created}；在“个人学习”刷新查看，尚未启用。</p>}
  </section>;
}
