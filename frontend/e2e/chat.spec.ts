import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/runtime-info", route => route.fulfill({ json: { provider_mode: "mock", provider: "mock", model: "mock-model", search_mode: "mock", memory_enabled: false, code_version: "0.4.0.dev0", remote_model_checked: false } }));
});

test("无项目会话可给下一任务临时授权目录", async ({ page }) => {
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  const projectId = "00000000-0000-0000-0000-000000000099";
  const now = new Date().toISOString();
  const project = { id: projectId, name: "临时目录", root: "D:/work/sample", authorization: "read", status: "available", authorization_version: 1, root_status: "available", created_at: now };
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [{ id: workspaceId, name: "Local", created_at: now }] }));
  await page.route("**/api/v1/projects", route => route.fulfill({ json: [project] }));
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().project_id).toBeNull();
      await route.fulfill({ status: 201, json: { id: "session-once", title: "临时处理", workspace_id: workspaceId, project_id: null, created_at: now } });
    } else await route.fulfill({ json: [] });
  });
  await page.route("**/api/v1/sessions/session-once/messages", route => route.fulfill({ json: [] }));
  await page.route("**/api/v1/tasks", async route => {
    expect(route.request().postDataJSON().project_id).toBe(projectId);
    await route.fulfill({ status: 202, json: { id: "task-once", status: "queued", created_at: now, latest_run: { id: "run-once" } } });
  });
  await page.route("**/api/v1/tasks/task-once", route => route.fulfill({ json: { id: "task-once", status: "queued", created_at: now, latest_run: { id: "run-once" } } }));
  await page.goto("/ui/");
  await page.getByLabel("项目使用范围").selectOption("task");
  await page.getByLabel("当前项目").selectOption(projectId);
  await page.getByLabel("发送消息").fill("临时处理");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByLabel("项目使用范围")).toHaveValue("session");
  await expect(page.getByLabel("当前项目")).toHaveValue("");
});

test("网页对话可发送、等待回复并在刷新后恢复同一 Session", async ({ page }) => {
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  const sessions: Array<{ id: string; title: string; workspace_id: string; created_at: string }> = [];
  const messages: Array<{ id: string; task_id: string; run_id: string; sequence: number; kind: string; role: string; content: string; created_at: string }> = [];
  let taskCount = 0;
  const now = new Date().toISOString();
  await page.route("**/api/v1/runtime-info", route => route.fulfill({ json: { provider_mode: "real", provider: "openai_compatible", model: "deepseek-flash", search_mode: "mock", memory_enabled: false, code_version: "0.4.0.dev0", remote_model_checked: false } }));
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [{ id: workspaceId, name: "Local workspace", created_at: now }] }));
  await page.route("**/api/v1/sessions", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().workspace_id).toBe(workspaceId);
      const item = { id: "session-1", title: route.request().postDataJSON().title, workspace_id: workspaceId, created_at: now };
      sessions.push(item);
      await route.fulfill({ status: 201, json: item });
    } else await route.fulfill({ json: sessions });
  });
  await page.route("**/api/v1/sessions/session-1/messages", (route) => route.fulfill({ json: messages }));
  await page.route("**/api/v1/sessions/session-1/memories", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/runs/run-1/context", (route) => route.fulfill({ json: {
    run_id: "run-1", status: "completed", error_code: null, config_hash: null,
    policy: {}, max_output_tokens: null, revisions: [],
  } }));
  await page.route("**/api/v1/runs/run-1/retrieval", (route) => route.fulfill({ status: 404, json: { error: { code: "retrieval_batch_not_found", message: "none" } } }));
  await page.route("**/api/v1/tasks", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.session_id).toBe("session-1");
    taskCount += 1;
    messages.push({ id: `goal-${taskCount}`, task_id: `task-${taskCount}`, run_id: `run-${taskCount}`, sequence: messages.length + 1, kind: "goal", role: "user", content: body.goal, created_at: now });
    await route.fulfill({ status: 202, json: { id: `task-${taskCount}`, status: "queued", latest_run: { id: `run-${taskCount}`, provider: "openai_compatible", model: "deepseek-flash" } } });
  });
  await page.route("**/api/v1/tasks/task-*", async (route) => {
    const id = route.request().url().split("/").at(-1)!;
    if (!messages.some((message) => message.id === `answer-${id}`)) {
      messages.push({ id: `answer-${id}`, task_id: id, run_id: id.replace("task", "run"), sequence: messages.length + 1, kind: "terminal", role: "assistant", content: `回复 ${id}`, created_at: now });
    }
    await route.fulfill({ json: { id, status: "completed", latest_run: { id: id.replace("task", "run"), provider: "openai_compatible", model: "deepseek-flash" } } });
  });
  await page.route("**/api/v1/runs/run-*/trace", (route) => route.fulfill({ json: {
    run_id: route.request().url().split("/").at(-2), task_id: "task-1", status: "completed", error_code: null,
    tool_calls: [{ id: "call-1", tool_name: "calculator", arguments: { expression: "12*(3+4)" }, status: "completed", result_summary: "84", error_code: null }],
    tool_effects: [], approvals: [], events: [{ event_type: "skill.none_selected", payload: { matches: [] } }],
  } }));

  await page.goto("/ui/");
  await expect(page.getByText("真实模型已配置：deepseek-flash")).toBeVisible();
  await page.getByLabel("发送消息").fill("第一问");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("回复 task-1")).toBeVisible();
  await expect(page.getByRole("heading", { name: "工具调用" })).toBeVisible();
  await expect(page.getByText("calculator")).toBeVisible();
  await page.getByRole("button", { name: "查看本会话记忆" }).click();
  await expect(page.getByLabel("Session ID")).toHaveValue("session-1");
  await page.getByRole("button", { name: "对话", exact: true }).click();
  await page.getByRole("button", { name: "查看上下文与检索证据" }).click();
  await expect(page.getByLabel("Run ID")).toHaveValue("run-1");
  await page.getByRole("button", { name: "对话", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("log").getByText("第一问", { exact: true })).toBeVisible();
  await expect(page.getByText("回复 task-1")).toBeVisible();
  await page.getByLabel("发送消息").fill("第二问");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("回复 task-2")).toBeVisible();
  expect(sessions).toHaveLength(1);
});

test("Mock 模式明确提示不是 AI 对话", async ({ page }) => {
  await page.route("**/api/v1/runtime-info", route => route.fulfill({ json: { provider_mode: "mock", provider: "mock", model: "mock-model", search_mode: "mock", memory_enabled: false, code_version: "0.4.0.dev0", remote_model_checked: false } }));
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [] }));
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: [] }));
  await page.goto("/ui/");
  await expect(page.getByText("当前是 Mock 演示")).toBeVisible();
  await expect(page.getByText(/不是 AI 对话/)).toBeVisible();
});

test("用户可设置验收条件并查看未通过的模型回答", async ({ page }) => {
  const now = new Date().toISOString();
  const acceptance = { answer_contains: ["391"], required_tools: ["calculator"], required_files: [{ path: "report.md" }] };
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [{ id: "00000000-0000-0000-0000-000000000001", name: "Local workspace", created_at: now }] }));
  await page.route("**/api/v1/sessions", route => route.fulfill({ status: route.request().method() === "POST" ? 201 : 200, json: route.request().method() === "POST" ? { id: "session-acceptance", title: "报告", workspace_id: "00000000-0000-0000-0000-000000000001", created_at: now } : [] }));
  await page.route("**/api/v1/sessions/session-acceptance/messages", route => route.fulfill({ json: [
    { id: "goal-acceptance", task_id: "task-acceptance", run_id: "run-acceptance", sequence: 1, kind: "goal", role: "user", content: "生成报告", created_at: now },
    { id: "answer-acceptance", task_id: "task-acceptance", run_id: "run-acceptance", sequence: 2, kind: "terminal", role: "assistant", content: JSON.stringify({ status: "failed", error_code: "acceptance_failed" }), created_at: now },
  ] }));
  await page.route("**/api/v1/tasks", route => {
    expect(route.request().postDataJSON().acceptance).toEqual(acceptance);
    return route.fulfill({ status: 202, json: { id: "task-acceptance", status: "queued", acceptance, latest_run: { id: "run-acceptance", provider: "mock", model: "mock-model" } } });
  });
  await page.route("**/api/v1/tasks/task-acceptance", route => route.fulfill({ json: { id: "task-acceptance", status: "failed", acceptance, latest_run: { id: "run-acceptance", provider: "mock", model: "mock-model" } } }));
  await page.route("**/api/v1/runs/run-acceptance/trace", route => route.fulfill({ json: { run_id: "run-acceptance", task_id: "task-acceptance", status: "failed", final_answer: "报告没有完成", error_code: "acceptance_failed", tool_calls: [], tool_effects: [], approvals: [], events: [{ event_type: "acceptance.checked", payload: { passed: false, checks: [] } }] } }));
  await page.goto("/ui/");
  await page.getByLabel("发送消息").fill("生成报告");
  await page.getByText("设置可核对的验收条件（可选）").click();
  await page.getByLabel("回答必须包含").fill("391");
  await page.getByLabel("必须成功调用的工具").fill("calculator");
  await page.getByLabel("必须生成的文件").fill("report.md");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alert").first()).toContainText("回答未通过设定的验收条件");
  await page.getByText("查看未通过验收的模型回答").click();
  await expect(page.getByText("报告没有完成")).toBeVisible();
});

test("对话中可处理待审批工具并取消任务", async ({ page }) => {
  let taskStatus = "waiting_user";
  let approvalStatus = "pending";
  let decision = "";
  const now = new Date().toISOString();
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [{ id: workspaceId, name: "Local workspace", created_at: now }] }));
  await page.route("**/api/v1/sessions", (route) => route.fulfill({ json: [{ id: "session-1", title: "审批", workspace_id: workspaceId, created_at: now }] }));
  await page.route("**/api/v1/sessions/session-1/messages", (route) => route.fulfill({ json: [
    { id: "goal-1", task_id: "task-1", run_id: "run-1", sequence: 1, kind: "goal", role: "user", content: "请执行任务", created_at: now },
  ] }));
  await page.route("**/api/v1/tasks/task-1/cancel", (route) => { taskStatus = "cancelled"; return route.fulfill({ json: { id: "task-1", status: taskStatus, cancel_requested: false, latest_run: { id: "run-1", provider: "mock", model: "mock" } } }); });
  await page.route("**/api/v1/tasks/task-1", (route) => route.fulfill({ json: { id: "task-1", status: taskStatus, cancel_requested: false, latest_run: { id: "run-1", provider: "mock", model: "mock" } } }));
  await page.route("**/api/v1/runs/run-1/trace", (route) => route.fulfill({ json: {
    run_id: "run-1", task_id: "task-1", status: taskStatus, error_code: null,
    tool_calls: [{ id: "call-1", tool_name: "ask_user", arguments: { question: "继续？" }, status: "waiting_user", result_summary: null, error_code: null }],
    tool_effects: [], approvals: [{ id: "approval-1", tool_call_id: "call-1", status: approvalStatus, risk: "R2", reason: "需要确认" }], events: [],
  } }));
  await page.route("**/api/v1/tool-approvals/approval-1/approve", (route) => {
    decision = route.request().postDataJSON().response;
    approvalStatus = "approved";
    taskStatus = "queued";
    return route.fulfill({ json: { id: "approval-1", status: "approved" } });
  });
  await page.goto("/ui/");
  await page.getByRole("button", { name: "审批" }).click();
  await expect(page.getByText("需要人工决定：ask_user")).toBeVisible();
  await expect(page.getByRole("button", { name: "批准" })).toBeDisabled();
  await page.getByLabel("回复或确认依据").fill("同意继续");
  await page.getByRole("button", { name: "批准" }).click();
  await expect.poll(() => decision).toBe("同意继续");
  await expect(page.getByRole("button", { name: "取消任务" })).toBeVisible();
  await page.getByRole("button", { name: "取消任务" }).click();
  await expect(page.getByText("状态：已取消")).toBeVisible();
});

test("网页可建立 Workspace 并把新对话放入所选作用域", async ({ page }) => {
  const defaultId = "00000000-0000-0000-0000-000000000001";
  const isolatedId = "workspace-2";
  const now = new Date().toISOString();
  let workspaces = [{ id: defaultId, name: "Local workspace", created_at: now }];
  const sessions = [{ id: "old-session", title: "默认会话", workspace_id: defaultId, created_at: now }];
  await page.route("**/api/v1/workspaces", async route => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().name).toBe("隔离项目");
      workspaces = [...workspaces, { id: isolatedId, name: "隔离项目", created_at: now }];
      await route.fulfill({ status: 201, json: workspaces.at(-1) });
    } else await route.fulfill({ json: workspaces });
  });
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().workspace_id).toBe(isolatedId);
      const item = { id: "isolated-session", title: "新问题", workspace_id: isolatedId, created_at: now };
      sessions.push(item); await route.fulfill({ status: 201, json: item });
    } else await route.fulfill({ json: sessions });
  });
  await page.route("**/api/v1/sessions/isolated-session/messages", route => route.fulfill({ json: [] }));
  await page.route("**/api/v1/tasks", route => route.fulfill({ status: 202, json: { id: "task-1", status: "queued", latest_run: { id: "run-1", provider: "mock", model: "mock" } } }));
  await page.route("**/api/v1/tasks/task-1", route => route.fulfill({ json: { id: "task-1", status: "running", latest_run: { id: "run-1", provider: "mock", model: "mock" } } }));
  await page.route("**/api/v1/runs/run-1/trace", route => route.fulfill({ json: { run_id: "run-1", task_id: "task-1", status: "running", error_code: null, tool_calls: [], tool_effects: [], approvals: [], events: [] } }));
  await page.goto("/ui/");
  await expect(page.getByRole("button", { name: "默认会话" })).toBeVisible();
  await page.getByLabel("新 Workspace 名称").fill("隔离项目");
  await page.getByRole("button", { name: "创建 Workspace" }).click();
  await expect(page.getByLabel("当前 Workspace")).toHaveValue(isolatedId);
  await expect(page.getByRole("button", { name: "默认会话" })).toHaveCount(0);
  await page.getByLabel("发送消息").fill("新问题");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "新问题" })).toBeVisible();
});

test("文件任务在页面上可预览产物并下载核对哈希", async ({ page }) => {
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  const now = new Date().toISOString();
  const body = "# 摘要\n\n结论：可用。\n";
  const hash = "a".repeat(64);
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [{ id: workspaceId, name: "Local workspace", created_at: now }] }));
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: [{ id: "session-file", title: "文件任务", workspace_id: workspaceId, created_at: now }] }));
  await page.route("**/api/v1/sessions/session-file/messages", route => route.fulfill({ json: [{ id: "goal-file", task_id: "task-file", run_id: "run-file", sequence: 1, kind: "goal", role: "user", content: "把笔记整理成摘要文件", created_at: now }] }));
  await page.route("**/api/v1/tasks/task-file", route => route.fulfill({ json: { id: "task-file", status: "completed", cancel_requested: false, latest_run: { id: "run-file", provider: "mock", model: "fixture" } } }));
  await page.route("**/api/v1/runs/run-file/trace", route => route.fulfill({ json: {
    run_id: "run-file", task_id: "task-file", status: "completed", final_answer: "已生成 reports/summary.md",
    error_code: null, events: [], tool_calls: [], tool_effects: [], approvals: [],
    artifacts: [{ id: "artifact-file", type: "text/markdown", uri: "run-file/reports/summary.md", content_hash: hash, size_bytes: body.length, metadata: {} }],
  } }));
  await page.route("**/api/v1/artifacts/artifact-file", route => route.fulfill({ json: {
    id: "artifact-file", run_id: "run-file", type: "text/markdown", name: "summary.md",
    content_type: "text/markdown", content_hash: hash, size_bytes: body.length, created_at: now,
    metadata: {}, preview: body, preview_truncated: false,
    download_url: "/api/v1/artifacts/artifact-file/download",
    note: "内容为二进制或未启用预览时 preview 为空；下载响应头带 SHA-256 供核对。",
  } }));
  await page.goto("/ui/");
  await page.getByRole("button", { name: "文件任务" }).click();
  await expect(page.getByRole("heading", { name: "产物（F-04）" })).toBeVisible();
  // 页面上直接给出登记哈希，用户下载后可以逐位核对。
  await expect(page.getByText(hash)).toBeVisible();
  // 点"预览"才请求详情，并把正文显示出来。
  await page.getByRole("button", { name: "预览" }).click();
  await expect(page.locator(".chat-artifact-preview pre")).toContainText("结论：可用。");
  const download = page.getByRole("link", { name: "下载" });
  await expect(download).toHaveAttribute("href", "/api/v1/artifacts/artifact-file/download");
  // 这里只核对页面把下载入口指向了产物接口；"下载到的字节与登记哈希一致"由后端端到端测试
  // （tests/e2e/test_task_artifacts.py）逐字节核对，浏览器点击下载在无头环境下不走可拦截的路由。
});

test("页面展示超时、无效参数和 UNKNOWN 副作用", async ({ page }) => {
  const workspaceId = "00000000-0000-0000-0000-000000000001";
  const now = new Date().toISOString();
  let mode: "timeout" | "invalid" | "waiting_user" | "cancelled" = "timeout";
  await page.route("**/api/v1/workspaces", route => route.fulfill({ json: [{ id: workspaceId, name: "Local workspace", created_at: now }] }));
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: [{ id: "session-negative", title: "负路径", workspace_id: workspaceId, created_at: now }] }));
  await page.route("**/api/v1/sessions/session-negative/messages", route => route.fulfill({ json: [{ id: "goal-negative", task_id: "task-negative", run_id: "run-negative", sequence: 1, kind: "goal", role: "user", content: "负路径", created_at: now }] }));
  await page.route("**/api/v1/tasks/task-negative", route => route.fulfill({ json: { id: "task-negative", status: mode === "waiting_user" || mode === "cancelled" ? mode : "failed", cancel_requested: false, latest_run: { id: "run-negative", provider: "mock", model: "fixture" } } }));
  await page.route("**/api/v1/runs/run-negative/trace", route => route.fulfill({ json: {
    run_id: "run-negative", task_id: "task-negative", status: mode === "waiting_user" || mode === "cancelled" ? mode : "failed", error_code: mode === "timeout" ? "provider_timeout" : mode === "invalid" ? "invalid_arguments" : "side_effect_unknown",
    tool_calls: mode === "timeout" ? [] : [{ id: "call-negative", tool_name: "file_write", arguments: { path: "report.md", overwrite: true }, status: mode === "invalid" ? "failed" : "running", result_summary: null, error_code: mode === "invalid" ? "invalid_arguments" : null }],
    tool_effects: mode === "waiting_user" || mode === "cancelled" ? [{ tool_call_id: "call-negative", status: "unknown" }] : [],
    approvals: mode === "waiting_user" ? [{ id: "approval-negative", tool_call_id: "call-negative", status: "pending", risk: "R2", reason: "外部状态需核对" }] : [], events: [],
  } }));
  await page.route("**/api/v1/tool-approvals/approval-negative/approve", route => route.fulfill({ json: { id: "approval-negative", status: "approved" } }));
  await page.goto("/ui/"); await page.getByRole("button", { name: "负路径" }).click();
  await expect(page.getByRole("alert")).toContainText("模型服务超时");
  mode = "invalid"; await page.reload(); await page.getByRole("button", { name: "负路径" }).click();
  await expect(page.getByRole("alert")).toContainText("工具参数无效（invalid_arguments）");
  await expect(page.locator(".chat-tool-list")).toContainText("工具参数无效（invalid_arguments）");
  mode = "waiting_user"; await page.reload(); await page.getByRole("button", { name: "负路径" }).click();
  await expect(page.getByText("状态：等待人工处理")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("副作用结果不确定，需要人工确认（side_effect_unknown）");
  await expect(page.getByText(/外部操作结果不确定。确认重试/)).toBeVisible();
  await expect(page.getByPlaceholder("retry 或 committed:实际结果")).toBeVisible();
  await expect(page.getByRole("button", { name: "拒绝" })).toHaveCount(0);
  mode = "cancelled"; await page.reload(); await page.getByRole("button", { name: "负路径" }).click();
  await expect(page.getByText(/外部操作结果仍不确定；任务结束不代表外部动作未发生/)).toBeVisible();
});
