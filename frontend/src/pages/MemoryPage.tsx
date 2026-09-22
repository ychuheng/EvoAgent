import { useState } from "react";
import { phase4, type Memory, type MaintenanceJob } from "../api/phase4";
import { Badge, Empty, ErrorNotice, Loading } from "../components/State";
import { Evidence, failure } from "../components/Evidence";

export function MemoryPage() {
  const [input, setInput] = useState("");
  const [session, setSession] = useState("");
  const [rows, setRows] = useState<Memory[]>([]);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [job, setJob] = useState<MaintenanceJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(failure(e)); } finally { setBusy(false); }
  }
  async function detail(version: string, scope = session) {
    const item = await phase4.memory(scope, version);
    setSelected(item); setJob(null);
    if (item.maintenance_job_id) setJob(await phase4.job(item.maintenance_job_id));
  }
  async function decide(action: "confirm" | "reject" | "revoke" | "erase") {
    if (!selected) return;
    await phase4.decide(session, selected, action);
    setNotice(action === "erase" ? "撤销已提交，内容清理是否完成请查看删除任务。" : "服务端已记录决定。");
    setRows(await phase4.memories(session));
    await detail(selected.version_id);
  }
  return <section>
    <h2>Memory 事实管理</h2><p>按 Session 查看所属作用域的事实。候选、冲突版本和失效记录均保留；确认以服务端版本为准。</p>
    <form className="form-row" onSubmit={e => { e.preventDefault(); void perform(async () => { setSelected(null); setJob(null); setSession(""); setRows([]); const scope = input.trim(); const items = await phase4.memories(scope); setSession(scope); setRows(items); }); }}>
      <label>Session ID<input required value={input} disabled={busy} onChange={e => setInput(e.target.value)} /></label><button disabled={busy}>读取记忆</button>
    </form>
    {busy && <Loading />}{error && <ErrorNotice message={error} />}{notice && <p role="status" className="notice">{notice}</p>}
    <div className="page-grid"><div className="panel"><h3>事实与版本</h3>
      {session && !rows.length && <Empty>此作用域还没有记忆。</Empty>}
      {rows.map(row => <button disabled={busy} className="skill-row" key={row.version_id} onClick={() => void perform(() => detail(row.version_id))}><span>{row.fact_key} · v{row.revision}<small>{row.scope} · {row.content ?? "内容已清理"}</small>{rows.some(other => other.entry_id === row.entry_id && other.version_id !== row.version_id) && row.status === "proposed" && <small>同事实存在其他版本，确认前核对冲突</small>}</span><Badge value={row.status} /></button>)}
    </div><div className="panel"><h3>来源与人工决定</h3>{!selected ? <Empty>选择一个版本查看来源与审计记录。</Empty> : <>
      <h4>{selected.fact_key} · v{selected.revision}</h4><Badge value={selected.status} /><p>{selected.content ?? "内容已清理"}</p>
      <p>作用域：{selected.scope} · 锁版本：{selected.lock_version}</p><p>过期时间：{selected.expires_at ?? "未设置"} · 置信度：{selected.confidence}（{selected.confidence_method}）</p>
      <p className="mono">{selected.content_hash}</p><h4>事实来源</h4><Evidence value={selected.sources} /><h4>审计记录</h4><Evidence value={selected.events} />
      <div className="actions">{selected.status === "proposed" && <><button disabled={busy} onClick={() => void perform(() => decide("confirm"))}>确认事实</button><button disabled={busy} onClick={() => void perform(() => decide("reject"))}>拒绝候选</button></>}
      {["proposed", "confirmed"].includes(selected.status) && <button disabled={busy} className="danger" onClick={() => void perform(() => decide("revoke"))}>撤销记忆</button>}
      {selected.content !== null && !selected.maintenance_job_id && <button disabled={busy} className="danger" onClick={() => void perform(() => decide("erase"))}>撤销并清理内容</button>}
      <button disabled={busy} onClick={() => void perform(async () => { setRows(await phase4.memories(session)); await detail(selected.version_id); })}>刷新事实</button></div>
      {selected.maintenance_job_id && <section aria-label="删除任务"><h4>删除任务</h4><p className="mono">{selected.maintenance_job_id}</p>{job ? <><Badge value={job.status} /><p>尝试 {job.attempts} 次 · 错误：{job.error_code ?? "无"} · 下次重试：{job.next_attempt_at ?? "未计划"}</p><Evidence value={job.result} />{job.status === "failed" && <button disabled={busy} onClick={() => void perform(async () => { await phase4.retryJob(job.id); await detail(selected.version_id); })}>重试删除任务</button>}</> : <p>任务状态尚未读取，请刷新事实。</p>}</section>}
    </>}</div></div>
  </section>;
}
