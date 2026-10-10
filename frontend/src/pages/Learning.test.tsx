import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FeedbackPanel } from "./FeedbackPanel";
import { LearningPage } from "./LearningPage";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const policy = { mode: "manual", lock_version: 1, daily_limit_micros: null, request_limit_micros: null, daily_candidate_limit: 3, cooldown_seconds: 86400, max_source_risk: "R1", learning_enabled: true };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

test("反馈需要显式方法授权；网络重试沿用同一提交身份", async () => {
  const submissions: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("learning-policy")) return json(policy);
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    submissions.push(body);
    return submissions.length === 1 ? json({ detail: { code: "temporary_failure" } }, 503) : json({ id: "feedback", routing: "method", learning_request_id: "request", revision: 1, learning_revision: 1 }, 201);
  }));
  render(<FeedbackPanel runId="run" workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "反馈与记成方法" }));
  fireEvent.change(screen.getByLabelText("反馈类型"), { target: { value: "method" } });
  const consent = await screen.findByRole("checkbox", { name: "用于改进方法" });
  await waitFor(() => expect(consent).toBeEnabled());
  expect(consent).not.toBeChecked();
  fireEvent.click(consent);
  fireEvent.change(screen.getByLabelText("纠正与方法"), { target: { value: "read identifiers as strings" } });
  fireEvent.click(screen.getByRole("button", { name: "提交任务反馈" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "提交任务反馈" }));
  expect(await screen.findByRole("status")).toHaveTextContent("方法请求已排队");
  expect(submissions).toHaveLength(2);
  expect(submissions[0].learn_from_feedback).toBe(true);
  expect(submissions[0].client_request_id).toBe(submissions[1].client_request_id);
});

test("事实反馈不会被勾选为方法学习", async () => {
  let submission: Record<string, unknown> = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("learning-policy")) return json(policy);
    submission = JSON.parse(String(init?.body));
    return json({ id: "feedback", routing: "none", learning_request_id: null });
  }));
  render(<FeedbackPanel runId="run" workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "反馈与记成方法" }));
  fireEvent.change(screen.getByLabelText("反馈类型"), { target: { value: "fact" } });
  expect(screen.queryByRole("checkbox", { name: "用于改进方法" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "提交任务反馈" }));
  await screen.findByRole("status");
  expect(submission.intent).toBe("fact");
  expect(submission.learn_from_feedback).toBe(false);
});

test("候选确认调用学习审查，不调用版本启用；分页保留既有行", async () => {
  const calls: string[] = [];
  const row = { id: "request-1", workspace_id: "workspace", origin_run_id: "run", status: "ready_for_review", stage: "candidate", request_kind: "propose", lock_version: 3, candidate_version_id: "version", validation_report_hash: "hash", error_code: null, available_actions: ["review"] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    if (url.endsWith("/workspaces")) return json([{ id: "workspace", name: "test" }]);
    if (url.includes("learning-policy")) return json(policy);
    if (url.includes("cursor=")) return json({ items: [{ ...row, id: "request-2", status: "queued", available_actions: ["cancel"] }], next_cursor: null });
    if (url.endsWith("/review")) {
      expect(JSON.parse(String(init?.body))).toEqual({ action: "acknowledge", reason: "worth validating", expected_lock_version: 3 });
      return json({ ...row, status: "completed" });
    }
    return json({ items: [row], next_cursor: "request-1" });
  }));
  render(<LearningPage />);
  await screen.findByText("候选待审");
  fireEvent.click(screen.getByRole("button", { name: "加载更多学习请求" }));
  await screen.findByText(/请求 request-2/);
  expect(screen.getByText(/请求 request-1/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("审查依据 request-1"), { target: { value: "worth validating" } });
  fireEvent.click(screen.getByRole("button", { name: "确认候选（不启用）" }));
  await waitFor(() => expect(calls.some(url => url.endsWith("/learning-requests/request-1/review"))).toBe(true));
  expect(calls.some(url => url.includes("/approve") || url.includes("/trials"))).toBe(false);
});


test("后台建议模式须用户保存策略，不自动启用或执行验证", async () => {
  const updates: Record<string, unknown>[] = [];
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    if (url.endsWith("/workspaces")) return json([{ id: "workspace", name: "test" }]);
    if (url.includes("learning-policy")) {
      if (init?.method === "PUT") updates.push(JSON.parse(String(init.body)));
      return json(updates.length ? { ...policy, mode: "suggest", lock_version: 2 } : policy);
    }
    return json({ items: [], next_cursor: null });
  }));
  render(<LearningPage />);
  const mode = await screen.findByLabelText("学习模式");
  expect(mode).toHaveValue("manual");
  fireEvent.change(mode, { target: { value: "suggest" } });
  expect(updates).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "保存学习策略" }));
  await waitFor(() => expect(updates).toHaveLength(1));
  expect(updates[0].mode).toBe("suggest");
  expect(calls.some(url => url.includes("/trial") || url.includes("validation-start"))).toBe(false);
});
