import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SkillUsagePanel } from "./SkillUsagePanel";
import type { SkillTrial } from "../api/learning";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const trial: SkillTrial = { id: "trial", skill_id: "skill", version_id: "version", workspace_id: "workspace", project_id: "project", status: "active", lock_version: 1, validation_request_id: "validation", report_hash: "hash", suspension_reason: null };
const summary = { selected_count: 2, projected_count: 1, unprojected_count: 1, outcomes: { verified_success: 0, verified_failure: 0, unknown: 1 }, skill_related_failures: 0, tool_call_count: 3, accounted_input_tokens: 4, accounted_output_tokens: 5, token_coverage: "settled_spend_records_only", elapsed_sample_count: 0, elapsed_sample_truncated: false, elapsed_mean_seconds: null, causal_benefit_established: false };

test("使用证据按需加载并保留项目范围，未知不计作成功，分页替换旧页", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    return new Response(JSON.stringify(url.includes("usage-summary") ? summary : { items: [{ id: url.includes("cursor=") ? "second" : "first", run_id: url.includes("cursor=") ? "run-second" : "run-first", feedback_revision: 1, outcome: "unknown", attribution: "uncertain", verification_origin: "unknown" }], next_cursor: url.includes("cursor=") ? null : "next" }));
  }));
  render(<SkillUsagePanel trial={trial} />);
  expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "查看使用证据 trial" }));
  await screen.findByText(/确认成功 0，确认失败 0，未知 1/);
  expect(calls.every(url => url.includes("workspace_id=workspace") && url.includes("project_id=project"))).toBe(true);
  expect(screen.getByText(/不能证明技能带来了因果收益/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "下一页使用证据" }));
  await screen.findByText(/run-second/);
  expect(screen.queryByText(/run-first/)).not.toBeInTheDocument();
  expect(calls.some(url => url.includes("cursor=next"))).toBe(true);
});

test("读取失败显示稳定错误，不显示凭空编造的使用统计", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { code: "usage_scope_invalid" } }), { status: 422 })));
  render(<SkillUsagePanel trial={trial} />);
  fireEvent.click(screen.getByRole("button", { name: "查看使用证据 trial" }));
  await screen.findByRole("alert");
  expect(screen.queryByText(/近 30 天被选择/)).not.toBeInTheDocument();
});
