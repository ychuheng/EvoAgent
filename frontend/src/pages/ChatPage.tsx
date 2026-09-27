import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";

import { chat, type ChatMessage, type ChatSession, type ChatWorkspace, type Project, type RuntimeInfo } from "../api/chat";
import { useTaskEventStream } from "../api/taskEvents";
import { failure } from "../components/Evidence";
import { TaskInspector } from "./TaskInspector";
import { errorLabel, taskStatusLabel } from "./taskLabels";

const STORAGE_KEY = "evoagent-chat-session";
const PROJECT_STORAGE_KEY = "evoagent-chat-project";
const DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001";
const TERMINAL = new Set(["completed", "failed", "cancelled", "timeout", "limit_reached", "authorization_revoked"]);

function projectStatusLabel(project: Project): string {
  if (project.status === "revoked") return "授权已撤销";
  if (project.root_available === false) return "目录不可用";
  if (project.status === "unavailable") return "目录不可用";
  return project.authorization === "read_write" ? "可写" : "只读";
}

function displayMessage(message: ChatMessage): string {
  if (message.kind === "instruction") return message.content;
  if (message.kind !== "terminal") return message.content;
  try {
    const failure = JSON.parse(message.content) as { status?: string; error_code?: string };
    if (failure.status === "cancelled") return "任务已取消。";
    if (failure.status && failure.error_code) return `任务${taskStatusLabel[failure.status] ?? failure.status}：${errorLabel(failure.error_code)}`;
  } catch { /* Normal replies are plain text. */ }
  return message.content;
}

/** 运行中补充的约束只在"最后一个 goal 之后没有 terminal"时才有意义。 */
function instructionTarget(messages: ChatMessage[]): string | null {
  let lastGoal: string | null = null;
  for (const message of messages) {
    if (message.kind === "goal") lastGoal = message.task_id;
    else if (message.kind === "terminal") lastGoal = null;
  }
  return lastGoal;
}

function unfinishedTask(messages: ChatMessage[]): string | null {
  const last = messages.at(-1);
  return last?.kind === "goal" ? last.task_id : null;
}

export function ChatPage({ onOpenVersion, onOpenContext, onOpenMemory }: { onOpenVersion: (id: string) => void; onOpenContext: (id: string) => void; onOpenMemory: (id: string) => void }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [workspaces, setWorkspaces] = useState<ChatWorkspace[]>([]);
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [workspaceId, setWorkspaceId] = useState(DEFAULT_WORKSPACE_ID);
  const [workspaceName, setWorkspaceName] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [requiredText, setRequiredText] = useState("");
  const [requiredTool, setRequiredTool] = useState("");
  const [requiredFile, setRequiredFile] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingTask, setPendingTask] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState("");
  const [queuedSince, setQueuedSince] = useState<number | null>(null);
  const [queueWaitSeconds, setQueueWaitSeconds] = useState(0);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectPath, setProjectPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectWritable, setProjectWritable] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [instructionNotice, setInstructionNotice] = useState("");

  useEffect(() => {
    let active = true;
    void chat.runtimeInfo().then((info) => { if (active) setRuntimeInfo(info); })
      .catch(() => { if (active) setRuntimeInfo(null); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.all([chat.sessions(), chat.workspaces(), chat.projects()]).then(([items, available, registered]) => {
      if (!active) return;
      setSessions([...items].reverse());
      setWorkspaces(available);
      // 列表接口异常时不阻塞对话：项目区退化为"无可用项目"。
      setProjects(Array.isArray(registered) ? registered : []);
      const savedProject = localStorage.getItem(PROJECT_STORAGE_KEY);
      const usable = Array.isArray(registered) ? registered : [];
      if (usable.some((item) => item.id === savedProject)) setProjectId(savedProject);
      const saved = localStorage.getItem(STORAGE_KEY);
      const selected = items.find((item) => item.id === saved);
      if (selected) {
        setWorkspaceId(selected.workspace_id);
        setSessionId(selected.id);
        if (selected.project_id) setProjectId(selected.project_id);
      }
    }).catch((reason: unknown) => { if (active) setError(failure(reason)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!sessionId) { setMessages([]); setPendingTask(null); setSelectedTask(null); return; }
    let active = true;
    setMessages([]);
    setPendingTask(null);
    setSelectedTask(null);
    setTaskStatus("");
    setQueuedSince(null);
    setQueueWaitSeconds(0);
    void chat.messages(sessionId).then((items) => {
      if (!active) return;
      setMessages(items);
      setPendingTask(unfinishedTask(items));
      setSelectedTask(items.at(-1)?.task_id ?? null);
    }).catch((reason: unknown) => { if (active) setError(failure(reason)); });
    return () => { active = false; };
  }, [sessionId]);

  // I-01/I-02：进度来自 SSE 事件投影；状态轮询只作为兜底，间隔放宽到 3 秒。
  const { progress, streamError } = useTaskEventStream(pendingTask, taskStatus);
  useEffect(() => {
    if (!pendingTask || !sessionId) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const task = await chat.task(pendingTask);
        if (!active) return;
        setTaskStatus(task.status);
        setQueuedSince(task.status === "queued" ? Date.parse(task.created_at) : null);
        setQueueWaitSeconds(task.status === "queued" ? Math.max(0, (Date.now() - Date.parse(task.created_at)) / 1000) : 0);
        if (TERMINAL.has(task.status)) {
          const items = await chat.messages(sessionId);
          if (!active) return;
          setMessages(items);
          setPendingTask(null);
          return;
        }
      } catch (reason) {
        if (active) setError(failure(reason));
      }
      if (active) timer = window.setTimeout(() => { void poll(); }, 3_000);
    };
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [pendingTask, sessionId]);

  function selectSession(id: string) {
    setError("");
    const selected = sessions.find((item) => item.id === id);
    if (selected) setWorkspaceId(selected.workspace_id);
    localStorage.setItem(STORAGE_KEY, id);
    setSessionId(id);
  }

  function newSession() {
    setError("");
    localStorage.removeItem(STORAGE_KEY);
    setSessionId(null);
    setMessages([]);
    setPendingTask(null);
    setSelectedTask(null);
    setTaskStatus("");
    setQueuedSince(null);
    setQueueWaitSeconds(0);
  }

  async function createWorkspace() {
    const name = workspaceName.trim();
    if (!name) return;
    setError("");
    try {
      const created = await chat.createWorkspace(name);
      setWorkspaces(items => [...items, created]);
      newSession();
      setWorkspaceId(created.id);
      setWorkspaceName("");
    } catch (reason) { setError(failure(reason)); }
  }

  async function refreshProjects() {
    const registered = await chat.projects();
    setProjects(Array.isArray(registered) ? registered : []);
  }

  async function selectProject(id: string) {
    setError("");
    const next = id === "" ? null : id;
    setProjectId(next);
    if (next === null) localStorage.removeItem(PROJECT_STORAGE_KEY);
    else localStorage.setItem(PROJECT_STORAGE_KEY, next);
    if (sessionId) {
      try {
        const updated = await chat.selectSessionProject(sessionId, next);
        setSessions((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      } catch (reason) { setError(failure(reason)); }
    }
  }

  async function registerProject() {
    const path = projectPath.trim();
    if (!path) return;
    setError("");
    try {
      const created = await chat.registerProject(path, projectName.trim(), projectWritable ? "read_write" : "read");
      await refreshProjects();
      setProjectPath("");
      setProjectName("");
      setProjectWritable(false);
      await selectProject(created.id);
    } catch (reason) { setError(failure(reason)); }
  }

  async function toggleProjectWrite() {
    const current = projects.find((item) => item.id === projectId);
    if (!current) return;
    setError("");
    try {
      const next = current.authorization === "read_write" ? "read" : "read_write";
      await chat.setProjectAuthorization(current.id, next);
      await refreshProjects();
    } catch (reason) { setError(failure(reason)); }
  }

  async function revokeProject() {
    const current = projects.find((item) => item.id === projectId);
    if (!current) return;
    setError("");
    try {
      await chat.revokeProject(current.id, "本地页面收回授权");
      await refreshProjects();
    } catch (reason) { setError(failure(reason)); }
  }

  async function sendInstruction() {
    const content = instruction.trim();
    const target = pendingTask ?? instructionTarget(messages);
    if (!content || !target) return;
    setError("");
    try {
      const created = await chat.addInstruction(target, content);
      setInstruction("");
      setInstructionNotice(
        `已接受 ${new Date(created.created_at).toLocaleTimeString()}：它会在下一个模型/工具边界生效，不会改写已经执行或审批中的动作。`,
      );
      if (sessionId) setMessages(await chat.messages(sessionId));
    } catch (reason) {
      setInstructionNotice("");
      setError(failure(reason));
    }
  }

  async function send(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const goal = draft.trim();
    if (!goal || sending || pendingTask) return;
    setSending(true);
    setError("");
    try {
      let id = sessionId;
      if (!id) {
        const created = await chat.createSession(goal.slice(0, 80), workspaceId, projectId);
        id = created.id;
        setSessions((items) => [created, ...items]);
        selectSession(id);
      }
      const acceptance = requiredText.trim() || requiredTool.trim() || requiredFile.trim() ? {
        answer_contains: requiredText.trim() ? [requiredText.trim()] : [],
        required_tools: requiredTool.trim() ? [requiredTool.trim()] : [],
        required_files: requiredFile.trim() ? [{ path: requiredFile.trim() }] : [],
      } : null;
      const task = await chat.createTask(id, goal, acceptance);
      setDraft("");
      setRequiredText("");
      setRequiredTool("");
      setRequiredFile("");
      setMessages(await chat.messages(id));
      setPendingTask(task.id);
      setSelectedTask(task.id);
      setTaskStatus(task.status);
      setQueuedSince(task.status === "queued" ? Date.parse(task.created_at) : null);
      setQueueWaitSeconds(0);
    } catch (reason) {
      setError(failure(reason));
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  }

  return <div className="chat-layout">
    <aside className="panel chat-sidebar">
      <div className="panel-title"><h2>对话</h2><button type="button" onClick={newSession}>新对话</button></div>
      <label>Workspace<select aria-label="当前 Workspace" value={workspaceId} onChange={event => { newSession(); setWorkspaceId(event.target.value); }}>{workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <div className="form-row"><input aria-label="新 Workspace 名称" value={workspaceName} onChange={event => setWorkspaceName(event.target.value)} placeholder="新建 Workspace" /><button type="button" disabled={!workspaceName.trim()} onClick={() => void createWorkspace()}>创建 Workspace</button></div>
      <ul className="skill-list">{sessions.filter(item => item.workspace_id === workspaceId).map((item) =>
        <li key={item.id}><button type="button" className={`skill-row ${sessionId === item.id ? "selected" : ""}`} onClick={() => selectSession(item.id)}>{item.title}</button></li>
      )}</ul>
      <div className="chat-project">
        <label>授权项目<select aria-label="当前项目" value={projectId ?? ""} onChange={event => void selectProject(event.target.value)}>
          <option value="">不使用项目</option>
          {projects.map(item => <option key={item.id} value={item.id} disabled={item.status === "revoked"}>{item.name} · {projectStatusLabel(item)}</option>)}
        </select></label>
        {projectId && (() => {
          const current = projects.find(item => item.id === projectId);
          if (!current) return null;
          return <div className="chat-project-detail">
            <p className="chat-meta">Agent 可见的根：<code>{current.root}</code>（授权版本 {current.authorization_version}）</p>
            <p className="chat-meta">{projectStatusLabel(current)}。切换项目不影响已经在跑的任务；撤销或改级会让在跑任务的下一次工具调用被拒绝并留下审计。</p>
            <button type="button" className="chat-detail-button" onClick={() => void toggleProjectWrite()}>{current.authorization === "read_write" ? "改为只读授权" : "改为可写授权"}</button>
            <button type="button" className="chat-detail-button" onClick={() => void revokeProject()}>撤销该授权</button>
            <button type="button" className="chat-detail-button" onClick={() => void refreshProjects()}>重新检查目录</button>
          </div>;
        })()}
        <details className="chat-project-register"><summary>登记新的项目目录</summary>
          <label>绝对路径<input aria-label="项目根路径" value={projectPath} onChange={event => setProjectPath(event.target.value)} placeholder="例如：D:\work\my-repo" /></label>
          <label>显示名（可选）<input aria-label="项目显示名" value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="例如：示例仓库" /></label>
          <label className="chat-checkbox"><input type="checkbox" checked={projectWritable} onChange={event => setProjectWritable(event.target.checked)} />同时授予可写授权（Agent 可修改文件）</label>
          <p className="chat-meta">默认只读。路径必须是绝对路径、已存在且不是符号链接；登记后 Agent 只能看到这个根。</p>
          <button type="button" disabled={!projectPath.trim()} onClick={() => void registerProject()}>登记</button>
        </details>
      </div>
    </aside>
    <section className="panel chat-main" aria-label="对话内容">
      <div className="chat-runtime" role="status">
        {runtimeInfo?.provider_mode === "mock" ? <><strong>当前是 Mock 演示</strong><span>回复由离线脚本生成，不是 AI 对话。请按运行说明配置真实模型。</span></> :
          runtimeInfo?.provider_mode === "real" ? <><strong>真实模型已配置：{runtimeInfo.model}</strong><span>{runtimeInfo.worker_status === "ready" ? "Worker 在线；模型连接及配置一致性将在任务执行时验证。" : runtimeInfo.worker_status === "missing" ? "未检测到在线 Worker，任务可能持续排队；请检查 Worker 容器。" : "Worker 状态无法判断；模型连接将在任务执行时验证。"}{runtimeInfo.search_mode === "mock" ? "网页搜索仍是 Mock。" : `网页搜索提供方：${runtimeInfo.search_mode}；可用性将在任务执行时验证。`}{!runtimeInfo.memory_enabled ? "记忆召回未开启。" : ""}</span></> :
          <><strong>运行模式未确认</strong><span>请检查 API 服务；任务详情会显示实际使用的模型。</span></>}
      </div>
      {sessionId && <button type="button" className="chat-detail-button" onClick={() => onOpenMemory(sessionId)}>查看本会话记忆</button>}
      <div className="chat-history" role="log" aria-live="polite">
        {messages.length === 0 && <p className="state">输入问题开始对话。例如“你好，请介绍你能做什么”，或“用 calculator 计算 17 × 23”。消息会保存在当前 Session 中。</p>}
        {messages.map((message) => <article className={`chat-bubble ${message.role} ${message.kind === "instruction" ? "instruction" : ""}`} key={message.id}>
          <small>{message.kind === "instruction" ? (message.injected_at ? "运行中补充（已注入）" : "运行中补充（待注入）") : message.role === "user" ? "你" : "EvoAgent"}</small>
          <p>{displayMessage(message)}</p>
          {message.kind === "instruction" && <small className="chat-meta">接受时间 {new Date(message.created_at).toLocaleTimeString()}{message.injected_at ? ` · 注入时间 ${new Date(message.injected_at).toLocaleTimeString()}` : " · 等待下一个模型/工具边界"}</small>}
          {message.task_id && <button type="button" className="chat-detail-button" onClick={() => setSelectedTask(message.task_id)}>{selectedTask === message.task_id ? "正在查看执行过程" : "查看执行过程"}</button>}
        </article>)}
        {pendingTask && <p role="status" className="state">{taskStatus === "waiting_user" ? "等待人工决定，请在下方处理。" : `Agent 正在处理… ${taskStatusLabel[taskStatus] ?? taskStatus}${progress.current ? ` · ${progress.current}` : ""}`}</p>}
        {pendingTask && taskStatus === "queued" && queuedSince !== null && queueWaitSeconds >= 30 && <p role="alert" className="state">任务已排队超过 30 秒，Worker 尚未领取。请检查 Worker 容器是否运行；模型连接状态要在任务开始执行后才能确认。</p>}
        {progress.steps.length > 0 && <details className="chat-progress" open={!!pendingTask}>
          <summary>执行进度（已收到 {progress.steps.length} 个事件）</summary>
          <ol>{progress.steps.map((step) => <li key={step.sequence} data-event-type={step.type}>
            <span className="chat-progress-label">{step.label}</span>
            {step.detail && <span className="chat-progress-detail">{step.detail}</span>}
          </li>)}</ol>
        </details>}
        {streamError && <p role="alert" className="state">{streamError}</p>}
      </div>
      {selectedTask && <TaskInspector key={selectedTask} taskId={selectedTask} onOpenVersion={onOpenVersion} onOpenContext={onOpenContext} />}
      {error && <p role="alert" className="error">{error}</p>}
      <form onSubmit={(event) => { void send(event); }} className="chat-compose">
        <label htmlFor="chat-input">发送消息</label>
        <textarea id="chat-input" rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} placeholder="向 Agent 提问，或让它使用工具完成任务" />
        <details className="chat-acceptance"><summary>设置可核对的验收条件（可选）</summary>
          <p className="chat-meta">请在任务文字中说明要求；这里的条件只用于结果核对，不会代替任务指令。未填写时回答不会被独立判定为正确。</p>
          <label>回答必须包含<input value={requiredText} maxLength={200} onChange={(event) => setRequiredText(event.target.value)} placeholder="例如：391" /></label>
          <label>必须成功调用的工具<input value={requiredTool} maxLength={64} onChange={(event) => setRequiredTool(event.target.value)} placeholder="例如：calculator" /></label>
          <label>必须生成的文件<input value={requiredFile} maxLength={1024} onChange={(event) => setRequiredFile(event.target.value)} placeholder="例如：report.md" /></label>
        </details>
        <div className="chat-actions"><small>Enter 发送 · Shift+Enter 换行</small><button disabled={!draft.trim() || sending || !!pendingTask}>{sending ? "提交中…" : "发送"}</button></div>
      </form>
      {(pendingTask ?? instructionTarget(messages)) && <form className="chat-instruction" onSubmit={(event) => { event.preventDefault(); void sendInstruction(); }}>
        <label htmlFor="chat-instruction-input">运行中补充约束（I-03）</label>
        <p className="chat-meta">在当前任务执行期间追加约束；它会在下一个模型/工具边界生效，不会改写已经执行或等待审批的动作。要开始新任务，请直接用上面的输入框发送。</p>
        <textarea id="chat-instruction-input" rows={2} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="例如：只改 src/fieldnotes 下的模块，不要动测试" />
        <div className="chat-actions"><small>{instructionNotice || "接受后会显示接受时间与注入时间。"}</small><button disabled={!instruction.trim()}>追加约束</button></div>
      </form>}
    </section>
  </div>;
}
