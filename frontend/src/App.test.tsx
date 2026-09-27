import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

test("Skill 列表为空时显示明确空状态", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Skill 目录" }));
  expect(await screen.findByText("还没有 Skill。")).toBeInTheDocument();
});

test("后端错误会显示给用户", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ error: { code: "database_unavailable", message: "数据库不可用" } }), { status: 503 }))));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("数据库不可用");
});

test("可以切换到评测报告页", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200 })));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Eval 报告" }));
  await waitFor(() => expect(screen.getByText("Skill 来源与配对评测")).toBeInTheDocument());
});

test("排队超过 30 秒时提示检查 Worker，而不宣称模型连接失败", async () => {
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  localStorage.setItem("evoagent-chat-session", "session-1");
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    const path = new URL(url, "http://localhost").pathname;
    const payload = path.endsWith("/runtime-info") ? {
      provider_mode: "real", provider: "openai_compatible", model: "example-model",
      search_mode: "mock", memory_enabled: false, code_version: "test", remote_model_checked: false,
    } : path.endsWith("/sessions") ? [{ id: "session-1", title: "测试", workspace_id: workspaceId }] :
      path.endsWith("/workspaces") ? [{ id: workspaceId, name: "默认" }] :
      path.endsWith("/sessions/session-1/messages") ? [{
        id: "message-1", task_id: "task-1", run_id: "run-1", sequence: 1,
        kind: "goal", role: "user", content: "你好", created_at: new Date(Date.now() - 60_000).toISOString(),
      }] : path.endsWith("/runs/run-1/trace") ? {
        run_id: "run-1", task_id: "task-1", status: "queued", final_answer: null,
        error_code: null, events: [], tool_calls: [], tool_effects: [], approvals: [],
      } : path.endsWith("/tasks/task-1") ? {
        id: "task-1", status: "queued", created_at: new Date(Date.now() - 60_000).toISOString(),
        cancel_requested: false, acceptance: null,
        latest_run: { id: "run-1", provider: "openai_compatible", model: "example-model" },
      } : {};
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  }));
  render(<App />);
  expect(await screen.findByText(/任务已排队超过 30 秒，Worker 尚未领取/)).toBeInTheDocument();
  expect(screen.getByText(/模型连接状态要在任务开始执行后才能确认/)).toBeInTheDocument();
});
