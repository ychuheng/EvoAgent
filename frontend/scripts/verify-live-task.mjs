import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const base = process.env.EVOAGENT_ACCEPTANCE_BASE_URL;
const taskId = process.env.EVOAGENT_ACCEPTANCE_TASK_ID;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || !taskId || !/^[0-9a-f-]{36}$/.test(taskId)) {
  throw new Error("Set loopback EVOAGENT_ACCEPTANCE_BASE_URL and UUID EVOAGENT_ACCEPTANCE_TASK_ID");
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  const task = await (await page.request.get(`${base}/api/v1/tasks/${taskId}`)).json();
  const trace = await (await page.request.get(`${base}/api/v1/runs/${task.latest_run.id}/trace`)).json();
  const sessions = await (await page.request.get(`${base}/api/v1/sessions`)).json();
  const session = sessions.find((item) => item.id === task.session_id);
  assert.ok(session);
  await page.addInitScript((sessionId) => {
    localStorage.setItem("evoagent-chat-session", sessionId);
  }, task.session_id);
  await page.goto(`${base}/ui/`);
  await page.getByText(/真实模型已配置：/).waitFor();
  if (task.status === "failed" && trace.error_code === "provider_network_error") {
    await page.getByText(/模型服务连接失败（provider_network_error）/).first().waitFor();
  } else if (task.status === "completed") {
    await page.locator(".chat-bubble.assistant").last().waitFor();
  } else {
    throw new Error(`Unsupported task state: ${task.status}/${trace.error_code}`);
  }
  assert.equal(await page.getByLabel("当前 Workspace").inputValue(), session.workspace_id);
  process.stdout.write(JSON.stringify({
    task_id: taskId,
    run_id: trace.run_id,
    status: task.status,
    error_code: trace.error_code,
    workspace_id: session.workspace_id,
    browser_verified: true,
  }) + "\n");
} finally {
  await browser.close();
}
