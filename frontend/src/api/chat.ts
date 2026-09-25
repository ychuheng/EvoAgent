import { request } from "./client";

export type ChatSession = { id: string; title: string; created_at: string };
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
  latest_run: { id: string; provider: string; model: string };
};

export const chat = {
  sessions: () => request<ChatSession[]>("/sessions"),
  createSession: (title: string) => request<ChatSession>("/sessions", {
    method: "POST", body: JSON.stringify({ title }),
  }),
  messages: (sessionId: string) =>
    request<ChatMessage[]>(`/sessions/${encodeURIComponent(sessionId)}/messages`),
  createTask: (sessionId: string, goal: string) => request<ChatTask>("/tasks", {
    method: "POST", body: JSON.stringify({ session_id: sessionId, goal }),
  }),
  task: (taskId: string) => request<ChatTask>(`/tasks/${encodeURIComponent(taskId)}`),
};
