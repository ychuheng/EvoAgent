import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";

import { chat, type ChatMessage, type ChatSession, type ChatWorkspace, type RuntimeInfo } from "../api/chat";
import { failure } from "../components/Evidence";
import { TaskInspector } from "./TaskInspector";
import { errorLabel, taskStatusLabel } from "./taskLabels";

const STORAGE_KEY = "evoagent-chat-session";
const DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001";
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function displayMessage(message: ChatMessage): string {
  if (message.kind !== "terminal") return message.content;
  try {
    const failure = JSON.parse(message.content) as { status?: string; error_code?: string };
    if (failure.status === "cancelled") return "任务已取消。";
    if (failure.status && failure.error_code) return `任务${taskStatusLabel[failure.status] ?? failure.status}：${errorLabel(failure.error_code)}`;
  } catch { /* Normal replies are plain text. */ }
  return message.content;
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
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void chat.runtimeInfo().then((info) => { if (active) setRuntimeInfo(info); })
      .catch(() => { if (active) setRuntimeInfo(null); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.all([chat.sessions(), chat.workspaces()]).then(([items, available]) => {
      if (!active) return;
      setSessions([...items].reverse());
      setWorkspaces(available);
      const saved = localStorage.getItem(STORAGE_KEY);
      const selected = items.find((item) => item.id === saved);
      if (selected) { setWorkspaceId(selected.workspace_id); setSessionId(selected.id); }
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
    void chat.messages(sessionId).then((items) => {
      if (!active) return;
      setMessages(items);
      setPendingTask(unfinishedTask(items));
      setSelectedTask(items.at(-1)?.task_id ?? null);
    }).catch((reason: unknown) => { if (active) setError(failure(reason)); });
    return () => { active = false; };
  }, [sessionId]);

  useEffect(() => {
    if (!pendingTask || !sessionId) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const task = await chat.task(pendingTask);
        if (!active) return;
        setTaskStatus(task.status);
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
      if (active) timer = window.setTimeout(() => { void poll(); }, 900);
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

  async function send(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const goal = draft.trim();
    if (!goal || sending || pendingTask) return;
    setSending(true);
    setError("");
    try {
      let id = sessionId;
      if (!id) {
        const created = await chat.createSession(goal.slice(0, 80), workspaceId);
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
    </aside>
    <section className="panel chat-main" aria-label="对话内容">
      <div className="chat-runtime" role="status">
        {runtimeInfo?.provider_mode === "mock" ? <><strong>当前是 Mock 演示</strong><span>回复由离线脚本生成，不是 AI 对话。请按运行说明配置真实模型。</span></> :
          runtimeInfo?.provider_mode === "real" ? <><strong>真实模型已配置：{runtimeInfo.model}</strong><span>连接及 Worker 配置会在任务执行时验证。{runtimeInfo.search_mode === "mock" ? "网页搜索仍是 Mock。" : ""}{!runtimeInfo.memory_enabled ? "记忆召回未开启。" : ""}</span></> :
          <><strong>运行模式未确认</strong><span>请检查 API 服务；任务详情会显示实际使用的模型。</span></>}
      </div>
      {sessionId && <button type="button" className="chat-detail-button" onClick={() => onOpenMemory(sessionId)}>查看本会话记忆</button>}
      <div className="chat-history" role="log" aria-live="polite">
        {messages.length === 0 && <p className="state">输入问题开始对话。消息会保存在当前 Session 中。</p>}
        {messages.map((message) => <article className={`chat-bubble ${message.role}`} key={message.id}>
          <small>{message.role === "user" ? "你" : "EvoAgent"}</small>
          <p>{displayMessage(message)}</p>
          {message.task_id && <button type="button" className="chat-detail-button" onClick={() => setSelectedTask(message.task_id)}>{selectedTask === message.task_id ? "正在查看执行过程" : "查看执行过程"}</button>}
        </article>)}
        {pendingTask && <p role="status" className="state">{taskStatus === "waiting_user" ? "等待人工决定，请在下方处理。" : `Agent 正在处理… ${taskStatusLabel[taskStatus] ?? taskStatus}`}</p>}
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
    </section>
  </div>;
}
