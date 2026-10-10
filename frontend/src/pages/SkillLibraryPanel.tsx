import { useState } from "react";
import { api } from "../api/client";
import { learningError } from "../api/learning";
import type { LibrarySuggestions, SkillSummary } from "../api/types";

export function SkillLibraryPanel({ scope, skills }: { scope: SkillSummary; skills: SkillSummary[] }) {
  const [result, setResult] = useState<LibrarySuggestions | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = (id: string) => skills.find(skill => skill.id === id)?.name ?? id;
  const inspect = async () => {
    if (!scope.workspace_id) return;
    setBusy(true); setResult(null); setError("");
    try { setResult(await api.librarySuggestions(scope.workspace_id, scope.project_id ?? null)); }
    catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  };
  return <section aria-label="方法库整理建议">
    <h3>方法库整理建议</h3>
    <p>范围与所选方法相同：{scope.project_id ? "该项目" : "工作区级方法"}。相似文字仅作线索；30 天未被选择也不代表方法无效。</p>
    <button disabled={busy || !scope.workspace_id} onClick={() => void inspect()}>查看整理建议</button>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p role="status">已检查 {result.examined_count} 条方法，未修改任何方法。{result.truncated && "仅覆盖排序前 20 条，不代表整个方法库。"}</p>
      {result.unavailable_count > 0 && <p>{result.unavailable_count} 条方法未通过当前来源检查，已排除。</p>}
      <h4>可人工比较的相似方法</h4>
      {result.merge_hints.length === 0 ? <p>本次没有相似建议。</p> : <ul>{result.merge_hints.map(hint => <li key={hint.parents.map(p => p.version_id).join(":")}>
        {hint.parents.map(parent => name(parent.skill_id)).join(" 与 ")} · {hint.reason === "identical_body" ? "正文相同" : `文字相似度 ${Math.round(hint.score * 100)}%`}。请先比较内容，再在下方人工整理合并候选。
      </li>)}</ul>}
      <h4>30 天未被选择</h4>
      {result.low_usage.length === 0 ? <p>本次没有长期未用标记。</p> : <ul>{result.low_usage.map(item => <li key={item.skill_id}>{name(item.skill_id)} · 保留，由你决定是否整理。</li>)}</ul>}
    </>}
  </section>;
}
