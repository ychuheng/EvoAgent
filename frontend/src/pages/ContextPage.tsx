import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { phase4, type ContextEvidence, type RetrievalEvidence } from "../api/phase4";
import { Badge, Empty, ErrorNotice, Loading } from "../components/State";
import { Evidence, failure } from "../components/Evidence";

export function ContextPage({ initialRunId = "" }: { initialRunId?: string }) {
  const [run, setRun] = useState(initialRunId);
  const [context, setContext] = useState<ContextEvidence | null>(null);
  const [retrieval, setRetrieval] = useState<RetrievalEvidence | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retrievalError, setRetrievalError] = useState("");
  const budget = context?.policy.budget;
  const inputLimit = budget && [budget.context_window, budget.output_tokens, budget.safety_margin].every(Number.isFinite)
    ? budget.context_window - budget.output_tokens - budget.safety_margin : null;
  async function load() {
    setBusy(true); setError(""); setRetrievalError(""); setContext(null); setRetrieval(null); setMissing(false);
    try {
      setContext(await phase4.context(run.trim()));
      try { setRetrieval(await phase4.retrieval(run.trim())); } catch (e) {
        if (e instanceof ApiError && e.status === 404 && e.code === "retrieval_batch_not_found") setMissing(true);
        else setRetrievalError(failure(e));
      }
    } catch (e) { setError(failure(e)); } finally { setBusy(false); }
  }
  useEffect(() => { if (initialRunId) void load(); }, [initialRunId]);
  return <section><h2>运行上下文证据</h2><p>查看冻结预算、上下文修订与检索选择。估算值不等于模型账单；没有记录不等于零消耗。</p>
    <form className="form-row" onSubmit={e => { e.preventDefault(); void load(); }}><label>Run ID<input required disabled={busy} value={run} onChange={e => setRun(e.target.value)} /></label><button disabled={busy}>查看上下文</button></form>
    {busy && <Loading />}{error && <ErrorNotice message={error} />}
    {context && <><div className="panel"><h3>冻结预算</h3><p className="mono">Run：{context.run_id}</p><Badge value={context.status} /><p>终止原因：{context.error_code ?? "无"}</p><p className="mono">配置哈希：{context.config_hash ?? "未冻结"}</p>{budget && <div className="metric-grid"><div><span>上下文窗口</span><strong>{budget.context_window ?? "未知"}</strong></div><div><span>输出预留</span><strong>{budget.output_tokens ?? "未知"}</strong></div><div><span>安全余量</span><strong>{budget.safety_margin ?? "未知"}</strong></div><div><span>输入上限</span><strong>{inputLimit ?? "未知"}</strong></div></div>}{Object.keys(context.policy).length ? <details><summary>冻结策略原始证据</summary><Evidence value={context.policy} /></details> : <Empty>没有受限上下文策略记录（旧策略或尚未冻结）。</Empty>}<p>请求输出上限：{context.max_output_tokens ?? "未记录"}</p></div>
      <div className="panel"><h3>上下文修订</h3>{!context.revisions.length && <Empty>没有持久化修订；不能据此判断压缩成功。</Empty>}{context.revisions.map(r => <section key={r.id}><h4>修订 {r.revision}</h4><p className="mono">输入：{r.input_hash}<br />策略：{r.policy_hash}<br />父修订：{r.parent_id ?? "无"}<br />Artifact：{r.artifact_id}</p><Evidence value={r.estimate} /></section>)}</div>
      <div className="panel"><h3>检索证据</h3>{retrievalError && <ErrorNotice message={retrievalError} />}{missing && <Empty>没有冻结检索批次，不能当作零命中。</Empty>}{retrieval && <><p>批次：{retrieval.batch_id} · 索引代次：{retrieval.generation ?? "未记录"}</p><p>检索状态：{retrieval.degraded ? "已降级" : "未降级"} · 选中数量：{retrieval.selected_count}</p><details><summary>检索配置</summary><Evidence value={retrieval.config} /></details>{!retrieval.selections.length && <Empty>批次已冻结，没有候选命中。</Empty>}{retrieval.selections.map((s, index) => <section key={`${s.source_key}:${index}`}><h4>{s.source_key} · 排名 {s.rank}</h4><p>省略原因：{s.omission_reason ?? "未省略"}</p><p className="mono">来源：{s.source_hash}<br />文本：{s.text_hash}</p><Evidence value={s.evidence} /></section>)}</>}</div>
    </>}
  </section>;
}
