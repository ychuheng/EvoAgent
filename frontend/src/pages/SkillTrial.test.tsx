import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { LearningRequest } from "../api/learning";
import { SkillTrialPanel, TrialCandidatePanel } from "./SkillTrialPanel";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const row: LearningRequest = { id: "validation", workspace_id: "workspace", origin_run_id: "run", request_kind: "validate", status: "completed", stage: "validated", lock_version: 4, candidate_version_id: "candidate", validation_report_hash: "hash", error_code: null, available_actions: [] };
const json = (body: unknown) => new Response(JSON.stringify(body));

test("不满足试用门禁时不提供启用操作，也不会调用正式批准", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => { calls.push(url); return json({ ready: false, evidence_ready: false, reasons: ["real_model_required"], report_hash: null, skill_lock_version: 1 }); }));
  render(<TrialCandidatePanel row={row} />);
  fireEvent.click(screen.getByRole("button", { name: "检查试用就绪条件" }));
  await screen.findByText("尚不能试用：real_model_required");
  expect(screen.queryByRole("button", { name: "确认限定试用" })).not.toBeInTheDocument();
  expect(calls).toHaveLength(1);
});

test("限定试用发送服务端就绪版本CAS与人工依据，不移动正式指针", async () => {
  const calls: string[] = [];
  let body: Record<string, unknown> = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    if (url.includes("trial-readiness")) return json({ ready: true, evidence_ready: true, reasons: [], report_hash: "hash", skill_lock_version: 7 });
    body = JSON.parse(String(init?.body));
    return json({ id: "trial", status: "active" });
  }));
  render(<TrialCandidatePanel row={row} />);
  fireEvent.click(screen.getByRole("button", { name: "检查试用就绪条件" }));
  const activate = await screen.findByRole("button", { name: "确认限定试用" });
  expect(activate).toBeDisabled();
  fireEvent.change(screen.getByLabelText("试用依据 validation"), { target: { value: "independently checked new inputs" } });
  fireEvent.click(activate);
  await screen.findByRole("status");
  expect(body).toEqual({ workspace_id: "workspace", project_id: null, validation_request_id: "validation", expected_lock_version: 7, reason: "independently checked new inputs" });
  expect(calls.some(url => url.includes("approve"))).toBe(false);
});

test("挂起已有试用不依赖启用能力，发送试用自身CAS", async () => {
  let stopped = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/suspend")) {
      expect(JSON.parse(String(init?.body))).toEqual({ expected_lock_version: 3, reason: "verified failure" });
      stopped = true; return json({ id: "trial", status: "suspended" });
    }
    return json({ items: [{ id: "trial", skill_id: "skill", version_id: "version", project_id: null, status: stopped ? "suspended" : "active", lock_version: 3, suspension_reason: null }] });
  }));
  render(<SkillTrialPanel workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "查看或刷新试用记录" }));
  const suspend = await screen.findByRole("button", { name: "挂起试用 trial" });
  expect(suspend).toBeDisabled();
  fireEvent.change(screen.getByLabelText("试用操作依据 trial"), { target: { value: "verified failure" } });
  fireEvent.click(suspend);
  await waitFor(() => expect(screen.queryByRole("button", { name: "挂起试用 trial" })).not.toBeInTheDocument());
});
