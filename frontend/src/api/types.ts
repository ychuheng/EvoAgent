export type SkillStatus = "enabled" | "disabled" | "deprecated";
export type VersionStatus =
  | "draft"
  | "evaluating"
  | "review_required"
  | "active"
  | "retired"
  | "rejected";

export interface SkillSummary {
  id: string;
  name: string;
  slug: string;
  description: string;
  status: SkillStatus;
  active_version_id: string | null;
  lock_version: number;
}

export interface VersionSummary {
  id: string;
  skill_id: string;
  parent_version_id: string | null;
  version: number;
  lifecycle_status: VersionStatus;
  content_hash: string;
  evaluation_report_hash: string | null;
  gate_report_hash: string | null;
}

export interface SkillDetail extends SkillSummary {
  versions: VersionSummary[];
}

export interface VersionDetail extends VersionSummary {
  definition: Record<string, unknown>;
  sources: Array<Record<string, unknown>>;
  gate_report: GateReport | null;
}

export interface GateCheck {
  name: string;
  layer: string;
  passed: boolean;
  threshold: unknown;
  actual: unknown;
}

export interface GateReport {
  passed: boolean;
  checks: GateCheck[];
}

export interface PairReport {
  case_key: string;
  task_family: string;
  repeat_index: number;
  comparable: boolean;
  success_delta: number;
  token_delta: number | null;
  tool_call_delta: number | null;
  safety_regression: boolean;
}

export interface EvaluationReport {
  experiment_id: string;
  pair_count: number;
  comparable_pairs: number;
  baseline_success_rate: number;
  skill_success_rate: number;
  safety_regressions: number;
  pairs: PairReport[];
}

export interface ReportEnvelope {
  report: EvaluationReport;
  report_hash: string;
  gate_report: GateReport | null;
  gate_report_hash: string | null;
}

export interface ApiErrorBody {
  error?: { code?: string; message?: string };
  detail?: string;
}
