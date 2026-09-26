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

  const name = `Agent audit ${Date.now()}`;
  await page.getByLabel("新 Workspace 名称").fill(name);
  await page.getByRole("button", { name: "创建 Workspace" }).click();
  await page.waitForFunction((expected) => {
    const select = document.querySelector('select[aria-label="当前 Workspace"]');
    return select?.selectedOptions[0]?.textContent === expected;
  }, name);
  const workspaceId = await page.getByLabel("当前 Workspace").inputValue();
  assert.notEqual(workspaceId, "00000000-0000-0000-0000-000000000001");

  await page.getByLabel("发送消息").fill("请调用 calculator 计算 17*23，并在回答中写出结果 391。");
  await page.getByText("设置可核对的验收条件（可选）").click();
  await page.getByLabel("回答必须包含").fill("391");
  await page.getByLabel("必须成功调用的工具").fill("calculator");
  const taskResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/tasks") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const created = await (await taskResponse).json();
  assert.equal(created.acceptance.answer_contains[0], "391");

  let task;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    task = await (await page.request.get(`${base}/api/v1/tasks/${created.id}`)).json();
    if (["completed", "failed", "cancelled"].includes(task.status)) break;
    await page.waitForTimeout(1000);
  }
  assert.equal(task.status, "completed", `Task ended with ${task.status}`);
  const trace = await (await page.request.get(`${base}/api/v1/runs/${task.latest_run.id}/trace`)).json();
  const checked = trace.events.findLast((event) => event.event_type === "acceptance.checked");
  assert.equal(checked?.payload.passed, true);
  assert.ok(trace.tool_calls.some((call) => call.tool_name === "calculator" && call.status === "succeeded"));
  await page.locator(".chat-bubble.assistant").last().getByText(/391/).waitFor();
  await page.getByText("已通过设定的验收条件；其他内容仍需核对。").waitFor();

  const sessions = await (await page.request.get(`${base}/api/v1/sessions`)).json();
  const session = sessions.find((item) => item.id === task.session_id);
  assert.equal(session.workspace_id, workspaceId);
  process.stdout.write(JSON.stringify({
    workspace_id: workspaceId,
    session_id: session.id,
    task_id: task.id,
    run_id: trace.run_id,
    status: task.status,
    acceptance_passed: checked.payload.passed,
    tool_count: trace.tool_calls.length,
  }, null, 2) + "\n");
} finally {
  await browser.close();
}
