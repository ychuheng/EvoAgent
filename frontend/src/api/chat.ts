import { request } from "./client";

export type ChatWorkspace = { id: string; name: string; created_at: string };
export type RuntimeInfo = {
  provider_mode: "mock" | "real";
  provider: string;
  model: string;
  search_mode: string;
  memory_enabled: boolean;
  code_version: string;
  remote_model_checked: boolean;
  worker_status: "ready" | "missing" | "unknown";
  execution_mode?: "container" | "trusted_windows_host";
};
export type ChatSession = {
  id: string;
  title: string;
  workspace_id: string;
  project_id: string | null;
  created_at: string;
};
export type ChatMessage = {
  id: string;
  task_id: string | null;
  run_id: string | null;
  sequence: number;
  kind: "goal" | "terminal" | "instruction";
  role: "user" | "assistant";
  content: string;
  injected_at: string | null;
  created_at: string;
};
export type TaskFamily = "general" | "coding" | "research" | "document" | "data" | "file_management";
export type ChatTask = {
  family?: TaskFamily | null;
  id: string;
  status: string;
  created_at: string;
  cancel_requested: boolean;
  project_id: string | null;
  acceptance: {
    answer_contains: string[];
    required_tools: string[];
    required_files: Array<{ path: string; sha256?: string | null }>;
  } | null;
  latest_run: { id: string; provider: string; model: string };
};

export type Project = {
  id: string;
  name: string;
  root: string;
  authorization: "read" | "read_write";
  status: "available" | "unavailable" | "revoked";
  authorization_version: number;
  created_at: string;
  root_available: boolean | null;
  /** 实测根状态：区分挂载缺失、被替换、权限不足等，便于给出可执行的提示。 */
  root_status: "available" | "missing" | "not_a_directory" | "permission_denied" | "unreadable" | null;
};
export type TaskTrace = {
  run_id: string;
  task_id: string;
  status: string;
  final_answer: string | null;
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
  artifacts?: Array<{
    id: string;
    type: string;
    uri: string;
    content_hash: string;
    size_bytes: number;
    metadata: Record<string, unknown>;
  }>;
  approvals: Array<{
    id: string;
    tool_call_id: string;
    status: string;
    risk: string;
    reason: string;
  }>;
  sources?: {
    searches: Array<{ tool_call_id: string; query: string; provider: string; result_count: number; urls: string[]; observed_at: string }>;
    reads: Array<{ tool_call_id: string; requested_url: string; final_url: string; content_sha256: string; content_bytes: number; text_truncated: boolean; status_code: number; observed_at: string }>;
    answer_links: Array<{ url: string; level: "fetched_text" | "search_snippet" | "unobserved"; tool_call_id: string | null }>;
  };
};

export type ApprovalPreview = {
  approval_id: string;
  file_count: number;
  added_lines: number;
  removed_lines: number;
  files: Array<{ path: string; created: boolean; added_lines: number; removed_lines: number; diff: string; diff_truncated: boolean }>;
};

/** F-04 产物详情：预览可能截断，二进制或未启用预览时 preview 为 null。 */
export type ArtifactDetail = {
  id: string;
  run_id: string;
  type: string;
  name: string;
  content_type: string;
  content_hash: string;
  size_bytes: number;
  created_at: string;
  metadata: Record<string, unknown>;
  redaction_status?: string;
  redaction_policy_version?: number | null;
  current_policy_version?: number;
  preview: string | null;
  preview_truncated: boolean;
  download_url: string;
  note: string;
};

export const chat = {
  reviewArtifact: (id: string, body: { reason: string; expected_policy_version: number | null; expected_content_hash: string; client_request_id: string; offline: boolean }) => request<{ outcome: string; replayed: boolean; current_redaction_status: string; note: string }>(`/artifacts/${id}/quarantine-review`, { method: "POST", body: JSON.stringify(body) }),
  runtimeInfo: () => request<RuntimeInfo>("/runtime-info"),
  workspaces: () => request<ChatWorkspace[]>("/workspaces"),
  createWorkspace: (name: string) => request<ChatWorkspace>("/workspaces", {
    method: "POST", body: JSON.stringify({ name }),
  }),
  sessions: () => request<ChatSession[]>("/sessions"),
  createSession: (title: string, workspaceId: string, projectId?: string | null) => request<ChatSession>("/sessions", {
    method: "POST", body: JSON.stringify({ title, workspace_id: workspaceId, project_id: projectId ?? null }),
  }),
  selectSessionProject: (sessionId: string, projectId: string | null) =>
    request<ChatSession>(`/sessions/${encodeURIComponent(sessionId)}/project`, {
      method: "PUT", body: JSON.stringify({ project_id: projectId }),
    }),
  projects: () => request<Project[]>("/projects"),
  registerProject: (path: string, name: string, authorization: "read" | "read_write") =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify({ path, name: name || null, authorization }) }),
  checkProject: (projectId: string) =>
    request<Project>(`/projects/${encodeURIComponent(projectId)}/check`, { method: "POST" }),
  setProjectAuthorization: (projectId: string, authorization: "read" | "read_write") =>
    request<Project>(`/projects/${encodeURIComponent(projectId)}/authorization`, {
      method: "PUT", body: JSON.stringify({ authorization }),
    }),
  revokeProject: (projectId: string, reason: string) =>
    request<Project>(`/projects/${encodeURIComponent(projectId)}/revoke`, {
      method: "POST", body: JSON.stringify({ reason }),
    }),
  messages: (sessionId: string) =>
    request<ChatMessage[]>(`/sessions/${encodeURIComponent(sessionId)}/messages`),
  createTask: (sessionId: string, goal: string, acceptance?: ChatTask["acceptance"], projectId?: string | null, family?: TaskFamily) => request<ChatTask>("/tasks", {
    method: "POST", body: JSON.stringify({ session_id: sessionId, goal, ...(family ? { family } : {}), acceptance: acceptance ?? null, ...(projectId !== undefined ? { project_id: projectId } : {}) }),
  }),
  task: (taskId: string) => request<ChatTask>(`/tasks/${encodeURIComponent(taskId)}`),
  addInstruction: (taskId: string, content: string) =>
    request<{
      id: string;
      task_id: string;
      content: string;
      session_sequence: number;
      created_at: string;
      injected_at: string | null;
    }>(`/tasks/${encodeURIComponent(taskId)}/instructions`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  trace: (runId: string) => request<TaskTrace>(`/runs/${encodeURIComponent(runId)}/trace`),
  cancel: (taskId: string) => request<ChatTask>(`/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }),
  decideApproval: (approvalId: string, decision: "approve" | "reject", response: string) =>
    request(`/tool-approvals/${encodeURIComponent(approvalId)}/${decision}`, {
      method: "POST", body: JSON.stringify({ response: response.trim() || null }),
    }),
  approvalPreview: (approvalId: string) => request<ApprovalPreview>(`/tool-approvals/${encodeURIComponent(approvalId)}/preview`),
  artifact: (artifactId: string) => request<ArtifactDetail>(`/artifacts/${encodeURIComponent(artifactId)}`),
};
