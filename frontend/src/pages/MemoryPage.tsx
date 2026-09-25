import { useEffect, useState } from "react";
import { phase4, type Memory, type MaintenanceJob, type SessionMessage } from "../api/phase4";
import { Badge, Empty, ErrorNotice, Loading } from "../components/State";
import { Evidence, failure } from "../components/Evidence";

export function MemoryPage({ initialSessionId = "" }: { initialSessionId?: string }) {
  const [input, setInput] = useState(initialSessionId);
  const [session, setSession] = useState("");
  const [rows, setRows] = useState<Memory[]>([]);
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [sourceMessageId, setSourceMessageId] = useState("");
  const [factKey, setFactKey] = useState("");
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<"session" | "workspace">("session");
  const [selected, setSelected] = useState<Memory | null>(null);
  const [job, setJob] = useState<MaintenanceJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!initialSessionId) return;
    let active = true;
    void Promise.all([phase4.memories(initialSessionId), phase4.messages(initialSessionId)]).then(([items, sourceMessages]) => {
      if (active) { setSession(initialSessionId); setRows(items); setMessages(sourceMessages.filter((item) => item.role === "user")); }
    }).catch((reason: unknown) => { if (active) setError(failure(reason)); });
    return () => { active = false; };
  }, [initialSessionId]);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(failure(e)); } finally { setBusy(false); }
  }
  async function detail(version: string, scope = session) {
    const item = await phase4.memory(scope, version);
    setSelected(item); setJob(null);
    if (item.maintenance_job_id) setJob(await phase4.job(item.maintenance_job_id));
  }
  async function propose() {
    await phase4.propose(session, sourceMessageId, factKey.trim(), content.trim(), scope);
    setNotice("候选已创建。请核对来源后确认，未确认的事实不会注入新任务。");
    setRows(await phase4.memories(session));
  }
  async function extract() {
    const created = await phase4.extract(session, sourceMessageId);
    setNotice(`提取出 ${created.length} 条候选；确认后才会被检索。`);
    setRows(await phase4.memories(session));
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
    <form className="form-row" onSubmit={e => { e.preventDefault(); void perform(async () => { setSelected(null); setJob(null); setSession(""); setRows([]); setMessages([]); const current = input.trim(); const [items, sourceMessages] = await Promise.all([phase4.memories(current), phase4.messages(current)]); setSession(current); setRows(items); setMessages(sourceMessages.filter((item) => item.role === "user")); }); }}>
      <label>Session ID<input required value={input} disabled={busy} onChange={e => setInput(e.target.value)} /></label><button disabled={busy}>读取记忆</button>
    </form>
    {busy && <Loading />}{error && <ErrorNotice message={error} />}{notice && <p role="status" className="notice">{notice}</p>}
    {session && <fieldset className="panel"><legend>从对话建立记忆候选</legend>
      <p>候选正文必须逐字包含在所选用户消息中；敏感内容、一次性命令和未经确认的候选不会进入检索。</p>
      <label>来源消息<select aria-label="记忆来源消息" disabled={busy} value={sourceMessageId} onChange={(e) => setSourceMessageId(e.target.value)}><option value="">选择用户消息</option>{messages.map((item) => <option key={item.id} value={item.id}>#{item.sequence} · {item.content.slice(0, 100)}</option>)}</select></label>
      <label>事实键<input aria-label="事实键" value={factKey} onChange={(e) => setFactKey(e.target.value)} placeholder="preference.language" /></label>
      <label>事实原文<input aria-label="事实原文" value={content} onChange={(e) => setContent(e.target.value)} placeholder="从来源消息复制需要记住的原文" /></label>
      <label>作用域<select aria-label="记忆作用域" value={scope} onChange={(e) => setScope(e.target.value as "session" | "workspace")}><option value="session">仅此 Session</option><option value="workspace">当前 Workspace 的 Session</option></select></label>
      <div className="actions"><button disabled={busy || !sourceMessageId || !factKey.trim() || !content.trim()} onClick={() => void perform(propose)}>创建候选</button><button disabled={busy || !sourceMessageId} onClick={() => void perform(extract)}>自动提取候选</button></div>
    </fieldset>}
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
