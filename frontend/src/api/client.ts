import type {
  ApiErrorBody,
  ReportEnvelope,
  SkillDetail,
  SkillSummary,
  VersionDetail,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(
      body.error?.message ?? (typeof body.detail === "string" ? body.detail : `请求失败：HTTP ${response.status}`),
      body.error?.code ?? "http_error",
      response.status,
    );
  }
  return response.status === 204 ? undefined as T : (await response.json()) as T;
}

export const api = {
  listDatasets: () => request<Array<{ id: string; name: string; version: number; status: string }>>("/eval-datasets"),
  listDatasetCases: (id: string) => request<Array<{ id: string; case_key: string; split: string; public_input: { goal?: string } }>>(`/eval-datasets/${id}/cases`),
  validateSource: (runId: string, caseId: string) => request<{ source_eval_run_id: string; passed: boolean; validation_results: unknown[] }>("/eval-sources/validate", { method: "POST", body: JSON.stringify({ run_id: runId, eval_case_id: caseId }) }),
  extractSkill: (sourceIds: string[]) => request<{ skill_id: string; skill_version_id: string; lifecycle_status: string }>("/skills/extractions", { method: "POST", body: JSON.stringify({ source_eval_run_ids: sourceIds }) }),
  startEvaluation: (versionId: string, datasetId: string, repeats: number) => request<{ experiment_id: string; status: string }>(`/skill-versions/${versionId}/evaluations`, { method: "POST", body: JSON.stringify({ dataset_id: datasetId, repeats }) }),
  getExperiment: (id: string) => request<{ id: string; status: string; skill_version_id: string | null }>(`/eval-experiments/${id}`),
  getPairs: (id: string) => request<Array<{ id: string; case_key: string; mode: string; passed: boolean | null; run_id: string; validation_results: unknown[] }>>(`/eval-experiments/${id}/pairs`),
  finalizeExperiment: (id: string) => request<ReportEnvelope>(`/eval-experiments/${id}/finalize`, { method: "POST" }),
  listSkills: () => request<SkillSummary[]>("/skills"),
  getSkill: (id: string) => request<SkillDetail>(`/skills/${id}`),
  getVersion: (id: string) => request<VersionDetail>(`/skill-versions/${id}`),
  getDiff: (id: string, against: string) =>
    request<{ changes: Array<Record<string, unknown>> }>(
      `/skill-versions/${id}/diff?against=${encodeURIComponent(against)}`,
    ),
  review: (
    id: string,
    body: {
      action: "approve" | "reject";
      expected_lock_version: number;
      reviewer: string;
      reason: string;
    },
  ) => request(`/skill-versions/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
  setStatus: (
    skillId: string,
    operation: "disable" | "enable" | "deprecate",
    expectedLockVersion: number,
  ) =>
    request(`/skills/${skillId}/${operation}`, {
      method: "POST",
      body: JSON.stringify({
        expected_lock_version: expectedLockVersion,
        reviewer: "local-ui",
        reason: `本地评审台执行 ${operation}`,
      }),
    }),
  rollback: (skillId: string, versionId: string, expectedLockVersion: number) =>
    request(`/skills/${skillId}/rollback`, {
      method: "POST",
      body: JSON.stringify({
        target_version_id: versionId,
        expected_lock_version: expectedLockVersion,
        reviewer: "local-ui",
        reason: "本地评审台人工回滚",
      }),
    }),
  getReport: (id: string) => request<ReportEnvelope>(`/eval-experiments/${id}/report`),
};
