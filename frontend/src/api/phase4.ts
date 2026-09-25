import { request } from "./client";

export interface Memory {
  entry_id: string; version_id: string; fact_key: string; scope: string;
  lock_version: number; revision: number; kind: string; status: string;
  content: string | null; content_hash: string; confidence: number; confidence_method: string;
  expires_at: string | null;
  maintenance_job_id?: string | null;
  sources?: { message_id: string; source_hash: string; locator: unknown }[];
  events?: { action: string; actor: string; reason: string; created_at: string }[];
}
export interface SessionMessage { id: string; sequence: number; role: string; content: string; kind: string; }
export interface MaintenanceJob {
  id: string; kind: string; status: string; attempts: number; result: unknown;
  error_code: string | null; next_attempt_at: string | null;
}
export interface ServerConfig {
  name: string; transport: "stdio" | "streamable_http";
  launch_profile_id: string | null; endpoint_profile_id: string | null; secret_ref: string | null;
  connection_timeout: number; call_timeout: number; max_concurrency: 1; enabled: boolean;
}
export interface MCPServer {
  id: string; config: ServerConfig; lock_version: number; latest_revision: number;
  execution_state: "active" | "draining" | "disabled"; execution_version: number;
  active_catalog_id: string | null;
}
export interface ToolReview {
  tool_name: string; lock_version: number; approved: boolean;
  risk: "R0" | "R1" | "R2" | "R3";
  effect: "read_only" | "idempotent_write" | "non_idempotent_write";
  reviewer: string; reason: string;
}
export interface Catalog {
  id: string; revision: number; config_version: number; content_hash: string;
  execution_enabled: boolean; diff: unknown;
  tools: { name: string; description: string; input_schema: unknown; output_schema: unknown; annotations: unknown }[];
}
export interface Health {
  instance_id: string; state: string; config_version: number; error_code: string | null; expires_at: string;
}
export interface ContextEvidence {
  run_id: string; status: string; error_code: string | null; config_hash: string | null;
  policy: { mode?: string; version?: number; budget?: Record<string, number>; counter?: string; strict?: boolean };
  max_output_tokens: number | null;
  revisions: { id: string; revision: number; parent_id: string | null; input_hash: string; policy_hash: string; artifact_id: string; estimate: unknown; created_at: string }[];
}
export interface RetrievalEvidence {
  batch_id: string; config: unknown; generation: number | null; degraded: boolean; selected_count: number;
  selections: { source_key: string; source_hash: string; text_hash: string; rank: number; omission_reason: string | null; evidence: unknown }[];
}
const id = encodeURIComponent;
const json = (body: unknown, method = "POST") => ({ method, body: JSON.stringify(body) });
export const phase4 = {
  messages: (session: string) => request<SessionMessage[]>(`/sessions/${id(session)}/messages`),
  propose: (session: string, sourceMessageId: string, factKey: string, content: string, scope: "session" | "workspace") => request<Memory>(`/sessions/${id(session)}/memories`, json({ source_message_id: sourceMessageId, fact_key: factKey, content, kind: "preference", scope })),
  extract: (session: string, message: string) => request<Memory[]>(`/sessions/${id(session)}/memory-extractions/${id(message)}`, json({})),
  memories: (session: string) => request<Memory[]>(`/sessions/${id(session)}/memories`),
  memory: (session: string, version: string) => request<Memory>(`/sessions/${id(session)}/memories/${id(version)}`),
  decide: (session: string, memory: Memory, action: "confirm" | "reject" | "revoke" | "erase") => request<Memory>(`/sessions/${id(session)}/memories/${id(memory.version_id)}/decision`, json({ action, expected_lock_version: memory.lock_version })),
  job: (job: string) => request<MaintenanceJob>(`/maintenance-jobs/${id(job)}`),
  retryJob: (job: string) => request(`/maintenance-jobs/${id(job)}/retry`, json({})),
  servers: () => request<MCPServer[]>("/mcp/servers"),
  createServer: (config: ServerConfig) => request<MCPServer>("/mcp/servers", json(config)),
  updateServer: (server: MCPServer, enabled: boolean) => request<MCPServer>(`/mcp/servers/${id(server.id)}`, json({ expected_lock_version: server.lock_version, config: { ...server.config, enabled } }, "PUT")),
  discover: (server: string) => request(`/mcp/servers/${id(server)}/discover`, json({})),
  catalogs: (server: string) => request<Catalog[]>(`/mcp/servers/${id(server)}/catalogs`),
  health: (server: string) => request<Health[]>(`/mcp/servers/${id(server)}/health`),
  reviews: (catalog: string) => request<ToolReview[]>(`/mcp/catalogs/${id(catalog)}/reviews`),
  review: (catalog: string, review: ToolReview) => request<ToolReview>(`/mcp/catalogs/${id(catalog)}/reviews`, json({ tool_name: review.tool_name, approved: review.approved, risk: review.risk, effect: review.effect, reviewer: review.reviewer, reason: review.reason, expected_lock_version: review.lock_version })),
  execution: (server: MCPServer, state: MCPServer["execution_state"], catalog?: string) => request<MCPServer>(`/mcp/servers/${id(server.id)}/execution`, json({ expected_lock_version: server.lock_version, expected_execution_version: server.execution_version, state, catalog_id: catalog ?? null })),
  context: (run: string) => request<ContextEvidence>(`/runs/${id(run)}/context`),
  retrieval: (run: string) => request<RetrievalEvidence>(`/runs/${id(run)}/retrieval`),
};
