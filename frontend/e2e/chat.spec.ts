import { expect, test } from "@playwright/test";

test("网页对话可发送、等待回复并在刷新后恢复同一 Session", async ({ page }) => {
  const sessions: Array<{ id: string; title: string; created_at: string }> = [];
  const messages: Array<{ id: string; task_id: string; run_id: string; sequence: number; kind: string; role: string; content: string; created_at: string }> = [];
  let taskCount = 0;
  const now = new Date().toISOString();
  await page.route("**/api/v1/sessions", async (route) => {
    if (route.request().method() === "POST") {
      const item = { id: "session-1", title: route.request().postDataJSON().title, created_at: now };
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

test("对话中可处理待审批工具并取消任务", async ({ page }) => {
  let taskStatus = "waiting_user";
  let approvalStatus = "pending";
  let decision = "";
  const now = new Date().toISOString();
  await page.route("**/api/v1/sessions", (route) => route.fulfill({ json: [{ id: "session-1", title: "审批", created_at: now }] }));
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
