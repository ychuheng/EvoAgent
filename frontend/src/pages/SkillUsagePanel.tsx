import { useState } from "react";
import { learning, learningError, type SkillTrial, type SkillUsageEvidencePage, type SkillUsageSummary } from "../api/learning";

export function SkillUsagePanel({ trial }: { trial: SkillTrial }) {
  const [summary, setSummary] = useState<SkillUsageSummary | null>(null);
  const [page, setPage] = useState<SkillUsageEvidencePage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load(cursor: string | null = null) {
    setBusy(true); setError("");
    try {
      const [nextSummary, nextPage] = await Promise.all([learning.usageSummary(trial), learning.usageEvidence(trial, cursor)]);
      setSummary(nextSummary); setPage(nextPage);
    } catch (failure) { setError(learningError(failure)); }
    finally { setBusy(false); }
  }
  return <section aria-label={`使用证据 ${trial.id}`}>
    <button type="button" disabled={busy} onClick={() => void load()}>查看使用证据 {trial.id}</button>
    {summary && <>
      <p>近 30 天被选择 {summary.selected_count} 次；已有试用观察 {summary.projected_count} 次，未投影 {summary.unprojected_count} 次。</p>
      <p>最新结论：确认成功 {summary.outcomes.verified_success}，确认失败 {summary.outcomes.verified_failure}，未知 {summary.outcomes.unknown}；归因于技能的失败 {summary.skill_related_failures}。</p>
      <p>工具调用 {summary.tool_call_count}；已结算账本 token {summary.accounted_input_tokens + summary.accounted_output_tokens}。未结算及未记账用量不包含在内。</p>
      <p>耗时均值：{summary.elapsed_mean_seconds === null ? "暂无完整样本" : `${summary.elapsed_mean_seconds.toFixed(2)} 秒`}（{summary.elapsed_sample_count} 个完整样本{summary.elapsed_sample_truncated ? "，仅最近 1000 条运行" : ""}）。</p>
      <p>被选择仅表示进入上下文；观察目前覆盖限定试用，以上统计不能证明技能带来了因果收益。</p>
    </>}
    {page && <>
      <ul>{page.items.map(item => <li key={item.id}>运行 {item.run_id} / 反馈版本 {item.feedback_revision} / {item.outcome} / {item.attribution} / {item.verification_origin}</li>)}</ul>
      {page.items.length === 0 && <p>该版本在当前范围还没有观察记录。</p>}
      {page.next_cursor && <button type="button" disabled={busy} onClick={() => void load(page.next_cursor)}>下一页使用证据</button>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
