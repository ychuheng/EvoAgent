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

test("前一任务运行时可以提交下一任务并显示持久队列", async () => {
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  localStorage.setItem("evoagent-chat-session", "session-1");
  const messages = [{
    id: "message-1", task_id: "task-1", run_id: "run-1", sequence: 1,
    kind: "goal", role: "user", content: "先处理 A", created_at: new Date().toISOString(),
  }];
  const submitted: string[] = [];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    const path = new URL(url, "http://localhost").pathname;
    let payload: unknown = {};
    if (path.endsWith("/runtime-info")) payload = { provider_mode: "mock", worker_status: "ready" };
    else if (path.endsWith("/sessions") && !options?.method) payload = [{ id: "session-1", title: "测试", workspace_id: workspaceId }];
    else if (path.endsWith("/workspaces")) payload = [{ id: workspaceId, name: "默认" }];
    else if (path.endsWith("/projects")) payload = [];
    else if (path.endsWith("/sessions/session-1/messages")) payload = messages;
    else if (path.endsWith("/tasks/task-1")) payload = { id: "task-1", status: "running", created_at: new Date().toISOString() };
    else if (path.endsWith("/tasks") && options?.method === "POST") {
      const body = JSON.parse(String(options.body)) as { goal: string };
      submitted.push(body.goal);
      messages.push({ id: "message-2", task_id: "task-2", run_id: "run-2", sequence: 2, kind: "goal", role: "user", content: body.goal, created_at: new Date().toISOString() });
      payload = { id: "task-2", status: "queued", created_at: new Date().toISOString(), latest_run: { id: "run-2", provider: "mock", model: "mock" } };
    }
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  }));
  render(<App />);
  const queueButton = await screen.findByRole("button", { name: "排队下一任务" });
  fireEvent.change(screen.getByLabelText("发送消息"), { target: { value: "接着处理 B" } });
  fireEvent.click(queueButton);
  await waitFor(() => expect(submitted).toEqual(["接着处理 B"]));
  expect(await screen.findByText(/后续排队 1 项/)).toBeInTheDocument();
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

test("产物可以先预览再下载，并按类型/二进制如实提示", async () => {
  const artifacts = [
    {
      id: "artifact-md", type: "text/markdown", uri: "run-1/report.md",
      content_hash: "a".repeat(64), size_bytes: 24, metadata: {},
    },
    {
      id: "artifact-bin", type: "application/octet-stream", uri: "run-1/data.bin",
      content_hash: "b".repeat(64), size_bytes: 8, metadata: {},
    },
  ];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    const path = new URL(url, "http://localhost").pathname;
    const payload = path.endsWith("/tasks/task-artifacts") ? {
      id: "task-artifacts", status: "completed", cancel_requested: false, acceptance: null,
      latest_run: { id: "run-artifacts", provider: "mock", model: "fixture" },
    } : path.endsWith("/runs/run-artifacts/trace") ? {
      run_id: "run-artifacts", task_id: "task-artifacts", status: "completed",
      final_answer: "报告已生成", error_code: null, events: [], tool_calls: [],
      tool_effects: [], approvals: [], artifacts,
    } : path.endsWith("/artifacts/artifact-md") ? {
      id: "artifact-md", run_id: "run-artifacts", type: "text/markdown", name: "report.md",
      content_type: "text/markdown", content_hash: "a".repeat(64), size_bytes: 24,
      created_at: "2026-09-28T00:00:00Z", metadata: {}, preview: "# 报告\n结论：可用。\n",
      preview_truncated: false, download_url: "/api/v1/artifacts/artifact-md/download",
      note: "内容为二进制或未启用预览时 preview 为空；下载响应头带 SHA-256 供核对。",
    } : path.endsWith("/artifacts/artifact-bin") ? {
      id: "artifact-bin", run_id: "run-artifacts", type: "application/octet-stream",
      name: "data.bin", content_type: "application/octet-stream",
      content_hash: "b".repeat(64), size_bytes: 8, created_at: "2026-09-28T00:00:00Z",
      metadata: {}, preview: null, preview_truncated: false,
      download_url: "/api/v1/artifacts/artifact-bin/download",
      note: "内容为二进制或未启用预览时 preview 为空；下载响应头带 SHA-256 供核对。",
    } : {};
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  }));
  render(<TaskInspector taskId="task-artifacts" onOpenVersion={vi.fn()} onOpenContext={vi.fn()} />);

  // 产物列表带登记哈希与下载入口。
  expect(await screen.findByRole("heading", { name: "产物（F-04）" })).toBeInTheDocument();
  expect(screen.getByText(new RegExp(`SHA-256 ${"a".repeat(64)}`))).toBeInTheDocument();
  const download = screen.getAllByRole("link", { name: "下载" });
  expect(download[0]).toHaveAttribute("href", "/api/v1/artifacts/artifact-md/download");

  // 点"预览"后才请求详情，并把正文显示出来。
  const previewButtons = screen.getAllByRole("button", { name: "预览" });
  fireEvent.click(previewButtons[0]);
  expect(await screen.findByText(/# 报告/)).toBeInTheDocument();
  expect(screen.getByText(/下载响应头带 SHA-256 供核对/)).toBeInTheDocument();

  // 二进制产物不假装有正文，只指向下载核对。
  fireEvent.click(screen.getAllByRole("button", { name: "预览" })[0]);
  expect(await screen.findByText(/该产物没有文本预览/)).toBeInTheDocument();
});

test("产物预览失败（例如内容与登记哈希不一致）时显示原因", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    const path = new URL(url, "http://localhost").pathname;
    if (path.endsWith("/artifacts/artifact-broken")) {
      return Promise.resolve(new Response(JSON.stringify({
        error: { code: "artifact_mismatch", message: "产物内容哈希与登记记录不一致，拒绝导出" },
      }), { status: 409 }));
    }
    const payload = path.endsWith("/tasks/task-broken") ? {
      id: "task-broken", status: "completed", cancel_requested: false, acceptance: null,
      latest_run: { id: "run-broken", provider: "mock", model: "fixture" },
    } : {
      run_id: "run-broken", task_id: "task-broken", status: "completed", final_answer: null,
      error_code: null, events: [], tool_calls: [], tool_effects: [], approvals: [],
      artifacts: [{
        id: "artifact-broken", type: "text/plain", uri: "run-broken/report.txt",
        content_hash: "c".repeat(64), size_bytes: 4, metadata: {},
      }],
    };
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  }));
  render(<TaskInspector taskId="task-broken" onOpenVersion={vi.fn()} onOpenContext={vi.fn()} />);

  fireEvent.click(await screen.findByRole("button", { name: "预览" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("预览失败");
});

test("项目编辑审批先显示实际文件差异再允许批准", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    const path = new URL(url, "http://localhost").pathname;
    const payload = path.endsWith("/tasks/task-edit") ? {
      id: "task-edit", status: "waiting_user", cancel_requested: false, acceptance: null,
      latest_run: { id: "run-edit", provider: "mock", model: "fixture" },
    } : path.endsWith("/runs/run-edit/trace") ? {
      run_id: "run-edit", task_id: "task-edit", status: "waiting_user", final_answer: null,
      error_code: "approval_required", events: [], tool_effects: [], artifacts: [],
      tool_calls: [{ id: "call-edit", tool_name: "edit_file", arguments: { path: "src/main.py" }, status: "pending", result_summary: null, error_code: null }],
      approvals: [{ id: "approval-edit", tool_call_id: "call-edit", status: "pending", risk: "R1", reason: "修改项目文件" }],
    } : path.endsWith("/tool-approvals/approval-edit/preview") ? {
      approval_id: "approval-edit", file_count: 1, added_lines: 1, removed_lines: 1,
      files: [{ path: "src/main.py", created: false, added_lines: 1, removed_lines: 1, diff: "-old\n+new", diff_truncated: false }],
    } : {};
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  }));
  render(<TaskInspector taskId="task-edit" onOpenVersion={vi.fn()} onOpenContext={vi.fn()} />);
  expect(await screen.findByText(/拟修改 1 个文件/)).toBeInTheDocument();
  expect(screen.getByText("src/main.py · 修改 · +1/-1")).toBeInTheDocument();
  expect(screen.getByText((_text, element) => element?.tagName === "PRE" && element.textContent === "-old\n+new")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准" })).toBeEnabled();
});

test("审批时文件差异变动会阻止提交批准", async () => {
  let previews = 0;
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const path = new URL(url, "http://localhost").pathname;
    const payload = path.endsWith("/tasks/task-edit") ? {
      id: "task-edit", status: "waiting_user", cancel_requested: false, acceptance: null,
      latest_run: { id: "run-edit", provider: "mock", model: "fixture" },
    } : path.endsWith("/runs/run-edit/trace") ? {
      run_id: "run-edit", task_id: "task-edit", status: "waiting_user", final_answer: null,
      error_code: "approval_required", events: [], tool_effects: [], artifacts: [],
      tool_calls: [{ id: "call-edit", tool_name: "edit_file", arguments: {}, status: "pending", result_summary: null, error_code: null }],
      approvals: [{ id: "approval-edit", tool_call_id: "call-edit", status: "pending", risk: "R1", reason: "改文件" }],
    } : path.endsWith("/preview") ? {
      approval_id: "approval-edit", file_count: 1, added_lines: 1, removed_lines: 1,
      files: [{ path: "src/main.py", created: false, added_lines: 1, removed_lines: 1, diff: previews++ ? "-old\n+changed" : "-old\n+new", diff_truncated: false }],
    } : {};
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<TaskInspector taskId="task-edit" onOpenVersion={vi.fn()} onOpenContext={vi.fn()} />);
  await screen.findByText(/拟修改 1 个文件/);
  fireEvent.click(screen.getByRole("button", { name: "批准" }));
  expect(await screen.findByText(/文件差异已变化/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some((call: unknown[]) => String(call[0]).endsWith("/approve"))).toBe(false);
});
