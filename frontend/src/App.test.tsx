import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";
import { TaskInspector } from "./pages/TaskInspector";

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

test("真实模型已配置但没有 Worker 时明确提示任务可能排队", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => Promise.resolve(
    new Response(JSON.stringify(url.endsWith("/runtime-info") ? {
      provider_mode: "real", provider: "openai_compatible", model: "example-model",
      search_mode: "mock", memory_enabled: false, code_version: "test",
      remote_model_checked: false, worker_status: "missing",
    } : []), { status: 200 }),
  )));
  render(<App />);
  expect(await screen.findByText(/未检测到在线 Worker，任务可能持续排队/)).toBeInTheDocument();
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

test("执行详情区分已读正文、搜索摘要和未观察到的答复链接", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => Promise.resolve(
    new Response(JSON.stringify(url.endsWith("/tasks/task-1") ? {
      id: "task-1", status: "completed", cancel_requested: false, acceptance: null,
      latest_run: { id: "run-1", provider: "mock", model: "fixture" },
    } : {
      run_id: "run-1", task_id: "task-1", status: "completed", final_answer: "report",
      error_code: null, events: [], tool_calls: [], tool_effects: [], approvals: [],
      sources: {
        searches: [{ tool_call_id: "search-1", query: "example", provider: "mock", result_count: 2, urls: [], observed_at: "2026-09-27T00:00:00Z" }],
        reads: [{ tool_call_id: "fetch-1", requested_url: "https://example.com/a", final_url: "https://example.com/a", content_sha256: "a".repeat(64), content_bytes: 12, status_code: 200, observed_at: "2026-09-27T00:00:00Z" }],
        answer_links: [
          { url: "https://example.com/a", level: "fetched_text", tool_call_id: "fetch-1" },
          { url: "https://example.com/b", level: "search_snippet", tool_call_id: "search-1" },
          { url: "https://example.com/c", level: "unobserved", tool_call_id: null },
        ],
      },
    }), { status: 200 }),
  )));
  render(<TaskInspector taskId="task-1" onOpenVersion={vi.fn()} onOpenContext={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "网页来源证据" })).toBeInTheDocument();
  expect(screen.getByText(/仅见搜索摘要/)).toBeInTheDocument();
  expect(screen.getByText(/未见检索或读取记录/)).toBeInTheDocument();
  expect(screen.getByText(/仍需人工判断网页内容是否支持答复/)).toBeInTheDocument();
});
