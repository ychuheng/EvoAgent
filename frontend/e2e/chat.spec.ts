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

  await page.goto("/ui/");
  await page.getByLabel("发送消息").fill("第一问");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("回复 task-1")).toBeVisible();
  await page.reload();
  await expect(page.getByText("第一问", { exact: true })).toBeVisible();
  await expect(page.getByText("回复 task-1")).toBeVisible();
  await page.getByLabel("发送消息").fill("第二问");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("回复 task-2")).toBeVisible();
  expect(sessions).toHaveLength(1);
});
