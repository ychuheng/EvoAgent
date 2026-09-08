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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(
      body.error?.message ?? body.detail ?? `请求失败：HTTP ${response.status}`,
      body.error?.code ?? "http_error",
      response.status,
    );
  }
  return (await response.json()) as T;
}

export const api = {
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
