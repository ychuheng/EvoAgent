import { useState } from "react";

import { api } from "../api/client";
import type { ReportEnvelope } from "../api/types";
import { Badge, Empty, ErrorNotice } from "../components/State";

export function EvalPage() {
  const [experimentId, setExperimentId] = useState("");
  const [data, setData] = useState<ReportEnvelope | null>(null);
  const [error, setError] = useState("");
  const load = async () => {
    try { setError(""); setData(await api.getReport(experimentId.trim())); }
    catch (reason) { setError((reason as Error).message); }
  };
  return <section className="panel wide">
    <h2>配对评测报告</h2>
    <div className="form-row"><input aria-label="实验 ID" placeholder="EvalExperiment ID" value={experimentId} onChange={(event) => setExperimentId(event.target.value)} /><button onClick={load}>查看报告</button></div>
    {error && <ErrorNotice message={error} />}
    {!data ? <Empty>输入实验 ID，读取已经冻结的原始 Pair 和门禁结果。</Empty> : <>
      <div className="metric-grid"><div><span>Pair</span><strong>{data.report.pair_count}</strong></div><div><span>可比较</span><strong>{data.report.comparable_pairs}</strong></div><div><span>Baseline 成功率</span><strong>{(data.report.baseline_success_rate * 100).toFixed(1)}%</strong></div><div><span>Skill 成功率</span><strong>{(data.report.skill_success_rate * 100).toFixed(1)}%</strong></div></div>
      <p>门禁：{data.gate_report ? <Badge value={data.gate_report.passed ? "passed" : "failed"} /> : "尚未执行"}　安全回退：{data.report.safety_regressions}</p>
      <p className="mono">report: {data.report_hash}</p>
      <table><thead><tr><th>Case</th><th>族</th><th>Repeat</th><th>可比较</th><th>成功 Δ</th><th>Token Δ</th><th>Tool Δ</th></tr></thead><tbody>{data.report.pairs.map((pair) => <tr key={`${pair.case_key}-${pair.repeat_index}`}><td>{pair.case_key}</td><td>{pair.task_family}</td><td>{pair.repeat_index}</td><td>{pair.comparable ? "是" : "否"}</td><td>{pair.success_delta}</td><td>{pair.token_delta ?? "unknown"}</td><td>{pair.tool_call_delta ?? "n/a"}</td></tr>)}</tbody></table>
    </>}
  </section>;
}
