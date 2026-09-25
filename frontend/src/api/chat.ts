import { request } from "./client";

export type ChatWorkspace = { id: string; name: string; created_at: string };
export type ChatSession = { id: string; title: string; workspace_id: string; created_at: string };
export type ChatMessage = {
  id: string;
  task_id: string | null;
  run_id: string | null;
  sequence: number;
  kind: "goal" | "terminal";
  role: "user" | "assistant";
  content: string;
  created_at: string;
};
export type ChatTask = {
  id: string;
  status: string;
  cancel_requested: boolean;
  latest_run: { id: string; provider: string; model: string };
};
export type TaskTrace = {
  run_id: string;
  task_id: string;
  status: string;
  error_code: string | null;
  events: Array<{ event_type: string; payload: Record<string, unknown> }>;
  tool_calls: Array<{
    id: string;
    tool_name: string;
    arguments: Record<string, unknown>;
    status: string;
    result_summary: string | null;
    error_code: string | null;
  }>;
  tool_effects: Array<{ tool_call_id: string; status: string }>;
  approvals: Array<{
    id: string;
    tool_call_id: string;
    status: string;
    risk: string;
    reason: string;
  }>;
};

export const chat = {
  workspaces: () => request<ChatWorkspace[]>("/workspaces"),
  createWorkspace: (name: string) => request<ChatWorkspace>("/workspaces", {
    method: "POST", body: JSON.stringify({ name }),
  }),
  sessions: () => request<ChatSession[]>("/sessions"),
  createSession: (title: string, workspaceId: string) => request<ChatSession>("/sessions", {
    method: "POST", body: JSON.stringify({ title, workspace_id: workspaceId }),
  }),
  messages: (sessionId: string) =>
    request<ChatMessage[]>(`/sessions/${encodeURIComponent(sessionId)}/messages`),
  createTask: (sessionId: string, goal: string) => request<ChatTask>("/tasks", {
    method: "POST", body: JSON.stringify({ session_id: sessionId, goal }),
  }),
  task: (taskId: string) => request<ChatTask>(`/tasks/${encodeURIComponent(taskId)}`),
  trace: (runId: string) => request<TaskTrace>(`/runs/${encodeURIComponent(runId)}/trace`),
  cancel: (taskId: string) => request<ChatTask>(`/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }),
  decideApproval: (approvalId: string, decision: "approve" | "reject", response: string) =>
    request(`/tool-approvals/${encodeURIComponent(approvalId)}/${decision}`, {
      method: "POST", body: JSON.stringify({ response: response.trim() || null }),
    }),
};
