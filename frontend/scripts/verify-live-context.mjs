import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const base = process.env.EVOAGENT_ACCEPTANCE_BASE_URL;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) {
  throw new Error("Set EVOAGENT_ACCEPTANCE_BASE_URL to a loopback HTTP address with a port");
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(`${base}/ui/`);
  await page.getByText(/真实模型已配置：/).waitFor();
  const longGoal = `请只回复“收到”。以下是不可省略的用户正文：${"长".repeat(20000)}`;
  await page.getByLabel("发送消息").fill(longGoal);
  const taskResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/tasks") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const created = await (await taskResponse).json();
  let task;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    task = await (await page.request.get(`${base}/api/v1/tasks/${created.id}`)).json();
    if (["completed", "failed", "cancelled"].includes(task.status)) break;
    await page.waitForTimeout(1000);
  }
  assert.equal(task.status, "failed");
  const trace = await (await page.request.get(`${base}/api/v1/runs/${task.latest_run.id}/trace`)).json();
  assert.equal(trace.error_code, "context_budget_exceeded");
  assert.equal(trace.tool_calls.length, 0);
  await page.getByText(/上下文超过预算（context_budget_exceeded）/).first().waitFor();
  process.stdout.write(JSON.stringify({
    session_id: task.session_id,
    task_id: task.id,
    run_id: trace.run_id,
    error_code: trace.error_code,
    tool_calls: trace.tool_calls.length,
    browser_verified: true,
  }) + "\n");
} finally {
  await browser.close();
}
