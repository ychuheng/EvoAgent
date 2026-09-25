import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ReportEnvelope } from "../api/types";
import { Badge, Empty, ErrorNotice } from "../components/State";

type Dataset = Awaited<ReturnType<typeof api.listDatasets>>[number];
type EvalCase = Awaited<ReturnType<typeof api.listDatasetCases>>[number];
type Pair = Awaited<ReturnType<typeof api.getPairs>>[number];

export function EvalPage({ onOpenVersion }: { onOpenVersion?: (id: string) => void }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [caseId, setCaseId] = useState("");
  const [runId, setRunId] = useState("");
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [versionId, setVersionId] = useState("");
  const [repeats, setRepeats] = useState(1);
  const [experimentId, setExperimentId] = useState("");
  const [experimentStatus, setExperimentStatus] = useState("");
  const [pairs, setPairs] = useState<Pair[]>([]);
  const [data, setData] = useState<ReportEnvelope | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { void api.listDatasets().then(setDatasets).catch((reason: Error) => setError(reason.message)); }, []);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function chooseDataset(id: string) {
    setDatasetId(id); setCaseId(""); setCases(id ? await api.listDatasetCases(id) : []);
  }
  async function loadExperiment() {
    const id = experimentId.trim();
    const [experiment, rows] = await Promise.all([api.getExperiment(id), api.getPairs(id)]);
    setExperimentStatus(experiment.status); setPairs(rows);
    if (experiment.skill_version_id) setVersionId(experiment.skill_version_id);
    setData(experiment.status === "completed" ? await api.getReport(id) : null);
  }

  return <section className="panel wide">
    <h2>Skill 来源与配对评测</h2>
    <p>用冻结数据集的 TRAIN 样本验证已完成任务，再提炼候选。HOLDOUT 配对完成后核对逐例结果，最后进入人工评审。</p>
    {error && <ErrorNotice message={error} />}{notice && <p role="status" className="notice">{notice}</p>}
    <fieldset disabled={busy} className="eval-workflow"><legend>1. 验证训练来源</legend>
      <label>冻结数据集<select aria-label="冻结数据集" value={datasetId} onChange={(event) => { void perform(() => chooseDataset(event.target.value)); }}><option value="">选择数据集</option>{datasets.filter((item) => item.status === "frozen").map((item) => <option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}</select></label>
      <label>TRAIN 样本<select aria-label="TRAIN 样本" value={caseId} onChange={(event) => setCaseId(event.target.value)}><option value="">选择训练样本</option>{cases.filter((item) => item.split.toLowerCase() === "train").map((item) => <option key={item.id} value={item.id}>{item.case_key} · {item.public_input.goal ?? ""}</option>)}</select></label>
      <label>已完成任务的 Run ID<input aria-label="训练 Run ID" value={runId} onChange={(event) => setRunId(event.target.value)} /></label>
      <button disabled={!caseId || !runId.trim()} onClick={() => { void perform(async () => { const result = await api.validateSource(runId.trim(), caseId); if (result.passed) { setSourceIds((ids) => [...new Set([...ids, result.source_eval_run_id])]); setNotice(`来源验证通过：${result.source_eval_run_id}`); } else setError(`来源验证未通过：${JSON.stringify(result.validation_results)}`); }); }}>验证来源</button>
      <p>已通过来源：{sourceIds.length ? sourceIds.join("、") : "暂无"}</p>
    </fieldset>
    <fieldset disabled={busy} className="eval-workflow"><legend>2. 候选与独立评测</legend>
      <button disabled={sourceIds.length < 2} onClick={() => { void perform(async () => { const result = await api.extractSkill(sourceIds); setVersionId(result.skill_version_id); setNotice(`候选已创建：${result.skill_version_id}`); }); }}>从来源提炼候选</button>
      <label>候选版本 ID<input aria-label="候选版本 ID" value={versionId} onChange={(event) => setVersionId(event.target.value)} /></label>
      <label>每个 HOLDOUT 样本重复次数<input aria-label="重复次数" type="number" min="1" max="100" value={repeats} onChange={(event) => setRepeats(Number(event.target.value))} /></label>
      <button disabled={!versionId.trim() || !datasetId || !Number.isInteger(repeats) || repeats < 1 || repeats > 100} onClick={() => { void perform(async () => { const result = await api.startEvaluation(versionId.trim(), datasetId, repeats); setExperimentId(result.experiment_id); setExperimentStatus(result.status); setNotice(`配对评测已创建：${result.experiment_id}`); }); }}>启动 HOLDOUT 配对</button>
      {versionId && onOpenVersion && <button onClick={() => onOpenVersion(versionId)}>查看候选定义与来源</button>}
    </fieldset>
    <fieldset disabled={busy} className="eval-workflow"><legend>3. 查看逐例结果与门禁</legend>
      <div className="form-row"><input aria-label="实验 ID" placeholder="EvalExperiment ID" value={experimentId} onChange={(event) => setExperimentId(event.target.value)} /><button disabled={!experimentId.trim()} onClick={() => { void perform(loadExperiment); }}>刷新实验</button><button disabled={!experimentId.trim() || experimentStatus !== "completed" || Boolean(data?.gate_report)} onClick={() => { void perform(async () => { await api.finalizeExperiment(experimentId.trim()); await loadExperiment(); }); }}>冻结报告与门禁</button></div>
      {experimentStatus && <p>实验状态：<Badge value={experimentStatus} /> · 逐例记录 {pairs.length} 条</p>}
      {pairs.length > 0 && <table><thead><tr><th>Case</th><th>组</th><th>通过</th><th>Run</th><th>验证结果</th></tr></thead><tbody>{pairs.map((pair) => <tr key={pair.id}><td>{pair.case_key}</td><td>{pair.mode}</td><td>{pair.passed === null ? "待定" : pair.passed ? "是" : "否"}</td><td className="mono">{pair.run_id.slice(0, 8)}…</td><td><details><summary>查看</summary><pre>{JSON.stringify(pair.validation_results, null, 2)}</pre></details></td></tr>)}</tbody></table>}
      {!data ? <Empty>实验完成并冻结后显示报告；失败样本保留在逐例记录中。</Empty> : <>
        <div className="metric-grid"><div><span>Pair</span><strong>{data.report.pair_count}</strong></div><div><span>可比较</span><strong>{data.report.comparable_pairs}</strong></div><div><span>Baseline 成功率</span><strong>{(data.report.baseline_success_rate * 100).toFixed(1)}%</strong></div><div><span>Skill 成功率</span><strong>{(data.report.skill_success_rate * 100).toFixed(1)}%</strong></div></div>
        <p>门禁：{data.gate_report ? <Badge value={data.gate_report.passed ? "passed" : "failed"} /> : "尚未执行"}　安全回退：{data.report.safety_regressions}</p>
        <p className="mono">report: {data.report_hash}</p>
        <table><thead><tr><th>Case</th><th>族</th><th>Repeat</th><th>可比较</th><th>成功 Δ</th><th>Token Δ</th><th>Tool Δ</th></tr></thead><tbody>{data.report.pairs.map((pair) => <tr key={`${pair.case_key}-${pair.repeat_index}`}><td>{pair.case_key}</td><td>{pair.task_family}</td><td>{pair.repeat_index}</td><td>{pair.comparable ? "是" : "否"}</td><td>{pair.success_delta}</td><td>{pair.token_delta ?? "unknown"}</td><td>{pair.tool_call_delta ?? "n/a"}</td></tr>)}</tbody></table>
        {versionId && onOpenVersion && <button onClick={() => onOpenVersion(versionId)}>查看门禁并人工评审</button>}
      </>}
    </fieldset>
  </section>;
}
