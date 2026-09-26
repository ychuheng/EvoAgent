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
  const initial = await (await page.request.get(`${base}/api/v1/tasks/${taskId}`)).json();
  assert.equal(initial.status, "waiting_user");
  const before = await (await page.request.get(`${base}/api/v1/runs/${initial.latest_run.id}/trace`)).json();
  assert.ok(before.tool_effects.some((effect) => effect.status === "unknown"));
  assert.ok(before.approvals.some((approval) => approval.status === "pending"));
  await page.addInitScript((sessionId) => {
    localStorage.setItem("evoagent-chat-session", sessionId);
  }, initial.session_id);
  await page.goto(`${base}/ui/`);
  await page.getByText("当前是 Mock 演示").waitFor();
  await page.getByText("外部操作结果不确定。", { exact: false }).waitFor();
  await page.getByPlaceholder(/committed:实际结果/).fill("committed:report.md");
  await page.getByRole("button", { name: "批准" }).click();
  let final;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    final = await (await page.request.get(`${base}/api/v1/tasks/${taskId}`)).json();
    if (["completed", "failed", "cancelled"].includes(final.status)) break;
    await page.waitForTimeout(1000);
  }
  assert.equal(final.status, "completed");
  const trace = await (await page.request.get(`${base}/api/v1/runs/${final.latest_run.id}/trace`)).json();
  assert.ok(trace.tool_effects.some((effect) => effect.status === "committed"));
  await page.locator(".chat-bubble.assistant").last().getByText(/report.md/).waitFor();
  process.stdout.write(JSON.stringify({
    session_id: initial.session_id,
    task_id: taskId,
    run_id: trace.run_id,
    status: final.status,
    effect_status: "committed",
    browser_approval: true,
  }) + "\n");
} finally {
  await browser.close();
}
