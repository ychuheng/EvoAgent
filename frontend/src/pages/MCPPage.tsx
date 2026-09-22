import { useEffect, useState } from "react";
import { phase4, type MCPServer, type Catalog, type ToolReview, type Health, type ServerConfig } from "../api/phase4";
import { Badge, Empty, ErrorNotice, Loading } from "../components/State";
import { Evidence, failure } from "../components/Evidence";

function ReviewForm({ review, disabled, save }: { review: ToolReview; disabled: boolean; save: (value: ToolReview) => void }) {
  const [risk, setRisk] = useState(review.risk);
  const [effect, setEffect] = useState(review.effect);
  const [reason, setReason] = useState("");
  const [reviewer, setReviewer] = useState("local-ui");
  return <form onSubmit={e => { e.preventDefault(); save({ ...review, risk, effect, reason, reviewer, approved: true }); }}>
    <p>审核版本 {review.lock_version} · {review.approved ? "已批准" : "未批准"} · {review.risk} / {review.effect}</p>
    <fieldset disabled={disabled}><legend>本地审核：{review.tool_name}</legend>
      <label>风险等级<select value={risk} onChange={e => setRisk(e.target.value as ToolReview["risk"])}>{["R0", "R1", "R2", "R3"].map(r => <option key={r}>{r}</option>)}</select></label>
      <label>副作用<select value={effect} onChange={e => setEffect(e.target.value as ToolReview["effect"])}>{["read_only", "idempotent_write", "non_idempotent_write"].map(r => <option key={r}>{r}</option>)}</select></label>
      <label>审核人<input required maxLength={128} value={reviewer} onChange={e => setReviewer(e.target.value)} /></label>
      <label>审核理由<input required maxLength={2048} value={reason} onChange={e => setReason(e.target.value)} /></label>
      <div className="actions"><button disabled={!reason.trim() || !reviewer.trim() || (effect !== "read_only" && ["R0", "R1"].includes(risk))}>批准工具</button><button type="button" className="danger" disabled={!reason.trim() || !reviewer.trim()} onClick={() => save({ ...review, reason, reviewer, approved: false })}>拒绝工具</button></div>
    </fieldset>
  </form>;
}

export function MCPPage() {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [server, setServer] = useState<MCPServer | null>(null);
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [reviews, setReviews] = useState<ToolReview[]>([]);
  const [health, setHealth] = useState<Health[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<ServerConfig["transport"]>("stdio");
  const [profile, setProfile] = useState("");
  useEffect(() => { let live = true; phase4.servers().then(rows => { if (live) setServers(rows); }).catch(e => { if (live) setError(failure(e)); }).finally(() => { if (live) setBusy(false); }); return () => { live = false; }; }, []);
  async function perform(action: () => Promise<void>) { setBusy(true); setError(""); setNotice(""); try { await action(); } catch (e) { setError(failure(e)); } finally { setBusy(false); } }
  async function selectCatalog(item: Catalog) {
    setCatalog(null); setReviews([]);
    const rows = await phase4.reviews(item.id); setCatalog(item); setReviews(rows);
  }
  async function load(item: MCPServer) {
    setServer(item); setCatalog(null); setCatalogs([]); setReviews([]); setHealth([]);
    const [cs, hs] = await Promise.all([phase4.catalogs(item.id), phase4.health(item.id)]);
    setCatalogs(cs); setHealth(hs);
    if (cs.length) await selectCatalog(cs[cs.length - 1]);
  }
  async function refresh(selectedId = server?.id) {
    const rows = await phase4.servers(); setServers(rows);
    const item = rows.find(row => row.id === selectedId); if (item) await load(item); else setServer(null);
  }
  const current = !!catalog && !!server && catalog.revision === server.latest_revision && catalog.config_version === server.lock_version;
  const latest = new Map<string, ToolReview>();
  reviews.forEach(r => { if (!latest.has(r.tool_name) || latest.get(r.tool_name)!.lock_version < r.lock_version) latest.set(r.tool_name, r); });
  return <section><h2>MCP Server 与工具目录</h2><p>连接、健康观察、目录审核和执行状态分别展示。远端描述不构成本地授权。</p>
    {error && <ErrorNotice message={error} />}{notice && <p role="status" className="notice">{notice}</p>}{busy && <Loading />}
    <div className="actions"><button disabled={busy} onClick={() => void perform(() => refresh())}>刷新 Server</button></div>
    <details className="panel"><summary>注册部署配置引用</summary><p>仅填写部署者预先配置的 profile 名称；凭据由服务端配置，页面不接收密钥。</p>
      <form onSubmit={e => { e.preventDefault(); void perform(async () => { const created = await phase4.createServer({ name: name.trim(), transport, launch_profile_id: transport === "stdio" ? profile.trim() : null, endpoint_profile_id: transport === "streamable_http" ? profile.trim() : null, secret_ref: null, connection_timeout: 10, call_timeout: 10, max_concurrency: 1, enabled: false }); setName(""); setProfile(""); await refresh(created.id); }); }}><fieldset disabled={busy}><label>Server 名称<input required maxLength={128} value={name} onChange={e => setName(e.target.value)} /></label><label>传输<select value={transport} onChange={e => setTransport(e.target.value as ServerConfig["transport"])}><option>stdio</option><option>streamable_http</option></select></label><label>部署 Profile ID<input required maxLength={128} value={profile} onChange={e => setProfile(e.target.value)} /></label><button disabled={!name.trim() || !profile.trim()}>注册 Server</button></fieldset></form>
    </details>
    <div className="page-grid"><div className="panel"><h3>Server 列表</h3>{!busy && !servers.length && <Empty>还没有 MCP Server。</Empty>}{servers.map(item => <button className="skill-row" disabled={busy} key={item.id} onClick={() => void perform(() => load(item))}><span>{item.config.name}<small>{item.config.transport} · 配置 v{item.lock_version}</small></span><Badge value={item.execution_state} /></button>)}</div>
    <div className="panel"><h3>目录审核与执行状态</h3>{!server ? <Empty>选择 Server 查看目录。</Empty> : <>
      <h4>{server.config.name}</h4><p>连接配置：{server.config.enabled ? "enabled" : "disabled"} · 执行状态：{server.execution_state} · 执行版本：{server.execution_version}</p>
      <p>部署引用：{server.config.launch_profile_id ?? server.config.endpoint_profile_id} · 凭据：{server.config.secret_ref ? "已配置引用" : "未配置"}</p>
      <div className="actions"><button disabled={busy} onClick={() => void perform(async () => { await phase4.updateServer(server, !server.config.enabled); await refresh(); })}>{server.config.enabled ? "禁用连接配置" : "启用连接配置"}</button><button disabled={busy || !server.config.enabled} onClick={() => void perform(async () => { await phase4.discover(server.id); await refresh(); })}>发现工具目录</button>
      <button disabled={busy || !current || !server.config.enabled} onClick={() => void perform(async () => { await phase4.execution(server, "active", catalog!.id); await refresh(); setNotice("服务端已激活目录；调用仍需通过逐工具审核和权限检查。"); })}>激活此目录</button>
      <button disabled={busy || server.execution_state !== "active"} onClick={() => void perform(async () => { await phase4.execution(server, "draining"); await refresh(); setNotice("已停止接收新调用，等待在途调用退出。"); })}>排空调用</button>
      <button className="danger" disabled={busy || server.execution_state === "disabled"} onClick={() => void perform(async () => { await phase4.execution(server, "disabled"); await refresh(); setNotice("执行已禁用，目录与审计记录保留。"); })}>卸载执行能力</button></div>
      <h4>进程健康观察</h4>{!health.length ? <Empty>暂无健康观察，不代表连接正常。</Empty> : health.map(h => <p key={h.instance_id}>{h.instance_id} · <Badge value={h.state} /> · {h.error_code ?? "无错误码"} · 有效至 {h.expires_at}</p>)}
      <h4>冻结目录</h4>{!catalogs.length && <Empty>尚未发现工具目录。</Empty>}<div className="actions">{catalogs.map(c => <button disabled={busy} key={c.id} onClick={() => void perform(() => selectCatalog(c))}>目录 v{c.revision}</button>)}</div>
      {catalog && <><p>目录 v{catalog.revision} · {current ? "当前配置目录" : "历史目录，只读"} · {catalog.execution_enabled ? "执行已启用" : "执行未启用"}</p><p className="mono">{catalog.content_hash}</p><h4>目录差异</h4><Evidence value={catalog.diff} />{!catalog.tools.length && <Empty>此目录没有工具。</Empty>}{catalog.tools.map(tool => <section className="tool-review" key={tool.name}><h4>{tool.name}</h4><p>{tool.description}</p><details><summary>输入 / 输出 Schema 与远端声明</summary><Evidence value={{ input: tool.input_schema, output: tool.output_schema, remote_annotations: tool.annotations }} /></details>{latest.get(tool.name) ? <ReviewForm key={`${catalog.id}:${tool.name}:${latest.get(tool.name)!.lock_version}`} review={latest.get(tool.name)!} disabled={busy || !current} save={review => void perform(async () => { await phase4.review(catalog.id, review); await refresh(); setNotice("工具审核已记录。"); })} /> : <p>没有审核记录，不能授权。</p>}</section>)}</>}
    </>}</div></div>
  </section>;
}
