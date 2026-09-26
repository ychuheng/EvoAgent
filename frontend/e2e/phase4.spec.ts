import { expect, test } from "@playwright/test";
import { memory, server, catalog, review, context, retrieval } from "../src/test/phase4-fixtures";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/runtime-info", route => route.fulfill({ json: { provider_mode: "mock", provider: "mock", model: "mock-model", search_mode: "mock", memory_enabled: false, code_version: "0.4.0.dev0", remote_model_checked: false } }));
  await page.route("**/api/v1/skills", r => r.fulfill({ json: [] }));
  await page.route("**/api/v1/sessions", r => r.fulfill({ json: [] }));
  await page.route("**/api/v1/workspaces", r => r.fulfill({ json: [{ id: "00000000-0000-0000-0000-000000000001", name: "Local workspace", created_at: "2026-09-25" }] }));
});
test("Memory 来源、版本冲突、确认与异步删除", async ({ page }) => {
  let current = { ...memory }; let conflict = true; let jobStatus = "pending";
  await page.route("**/api/v1/sessions/s-1/messages", r => r.fulfill({ json: [] }));
  await page.route("**/api/v1/sessions/s-1/memories", r => r.fulfill({ json: [current] }));
  await page.route("**/api/v1/sessions/s-1/memories/memory-1", r => r.fulfill({ json: current }));
  await page.route("**/api/v1/sessions/s-1/memories/memory-1/decision", async r => {
    expect(r.request().postDataJSON().expected_lock_version).toBe(current.lock_version);
    if (conflict) { conflict = false; await r.fulfill({ status: 409, json: { error: { code: "memory_version_conflict", message: "conflict" } } }); return; }
    const erase = r.request().postDataJSON().action === "erase";
    current = { ...current, lock_version: current.lock_version + 1, status: erase ? "revoked" : "confirmed", maintenance_job_id: erase ? "job-1" : null };
    await r.fulfill({ json: current });
  });
  await page.route("**/api/v1/maintenance-jobs/job-1", r => r.fulfill({ json: { id: "job-1", status: jobStatus, attempts: 1, error_code: null, result: null, next_attempt_at: null } }));
  await page.goto("/ui/"); await page.getByRole("button", { name: "Memory 管理" }).click();
  await page.getByLabel("Session ID").fill("s-1"); await page.getByText("读取记忆").click(); await page.getByRole("button", { name: /language/ }).click();
  await expect(page.getByText(/sha256:source/)).toBeVisible();
  await page.getByText("确认事实").click(); await expect(page.getByRole("alert")).toContainText("请刷新后重新审核");
  await page.getByText("刷新事实").click(); await page.getByText("确认事实").click(); await expect(page.getByRole("status")).toContainText("已记录决定");
  await page.getByText("撤销并清理内容").click(); await expect(page.getByLabel("删除任务")).toContainText("pending");
  jobStatus = "completed"; current.content = null;
  await page.getByText("刷新事实").click(); await expect(page.getByLabel("删除任务")).toContainText("completed");
});

test("从用户消息创建作用域明确的记忆候选", async ({ page }) => {
  let items: typeof memory[] = [];
  await page.route("**/api/v1/sessions/s-1/messages", r => r.fulfill({ json: [
    { id: "message-1", sequence: 1, role: "user", kind: "goal", content: "请记住我偏好中文回答。" },
    { id: "message-2", sequence: 2, role: "assistant", kind: "answer", content: "知道了" },
  ] }));
  await page.route("**/api/v1/sessions/s-1/memories", async r => {
    if (r.request().method() === "POST") {
      expect(r.request().postDataJSON()).toEqual({
        source_message_id: "message-1", fact_key: "preference.language",
        content: "偏好中文回答", kind: "preference", scope: "workspace",
      });
      items = [{ ...memory, fact_key: "preference.language", content: "偏好中文回答" }];
      await r.fulfill({ json: items[0] });
      return;
    }
    await r.fulfill({ json: items });
  });
  await page.goto("/ui/"); await page.getByRole("button", { name: "Memory 管理" }).click();
  await page.getByLabel("Session ID").fill("s-1"); await page.getByText("读取记忆").click();
  await page.getByLabel("记忆来源消息").selectOption("message-1");
  await page.getByLabel("事实键").fill("preference.language");
  await page.getByLabel("事实原文").fill("偏好中文回答");
  await page.getByLabel("记忆作用域").selectOption("workspace");
  await page.getByText("创建候选").click();
  await expect(page.getByRole("status")).toContainText("未确认的事实不会注入新任务");
  await expect(page.getByRole("button", { name: /preference.language/ })).toContainText("proposed");
});

test("MCP 目录审核、秘密不回显、排空与卸载", async ({ page }) => {
  let current = { ...server }; let approval = { ...review };
  await page.route("**/api/v1/mcp/servers", r => r.fulfill({ json: [current] }));
  await page.route("**/api/v1/mcp/servers/server-1/catalogs", r => r.fulfill({ json: [{ ...catalog, execution_enabled: current.execution_state === "active" }] }));
  await page.route("**/api/v1/mcp/servers/server-1/health", r => r.fulfill({ json: [{ instance_id: "worker-1", state: "stale", config_version: 2, expires_at: "2026-09-22", error_code: null }] }));
  await page.route("**/api/v1/mcp/catalogs/catalog-1/reviews", async r => {
    if (r.request().method() === "POST") { const body = r.request().postDataJSON(); expect(body.expected_lock_version).toBe(approval.lock_version); approval = { ...approval, ...body, lock_version: approval.lock_version + 1 }; }
    await r.fulfill({ json: r.request().method() === "POST" ? approval : [approval] });
  });
  await page.route("**/api/v1/mcp/servers/server-1/execution", async r => {
    const body = r.request().postDataJSON(); expect(body.expected_execution_version).toBe(current.execution_version); expect(body.expected_lock_version).toBe(2);
    current = { ...current, execution_state: body.state, execution_version: current.execution_version + 1 }; await r.fulfill({ json: current });
  });
  await page.goto("/ui/"); await page.getByRole("button", { name: "MCP 管理" }).click(); await page.getByRole("button", { name: /Fixture Server/ }).click();
  await expect(page.getByText("stale", { exact: true })).toBeVisible(); await expect(page.getByText("private-reference-do-not-render")).toHaveCount(0);
  await page.getByLabel("风险等级").selectOption("R1"); await page.getByLabel("副作用").selectOption("read_only"); await page.getByLabel("审核理由").fill("只读契约已核对"); await page.getByText("批准工具").click();
  await expect(page.getByRole("status")).toContainText("审核已记录");
  await page.getByText("排空调用").click(); await expect(page.getByRole("status")).toContainText("等待在途调用退出");
  await page.getByText("卸载执行能力").click(); await expect(page.getByRole("status")).toContainText("执行已禁用");
});

test("上下文预算、修订、检索降级与空命中", async ({ page }) => {
  let empty = false;
  await page.route("**/api/v1/runs/run-1/context", r => r.fulfill({ json: context }));
  await page.route("**/api/v1/runs/run-1/retrieval", r => r.fulfill({ json: empty ? { ...retrieval, selections: [] } : retrieval }));
  await page.goto("/ui/"); await page.getByRole("button", { name: "上下文证据" }).click(); await page.getByLabel("Run ID").fill("run-1"); await page.getByText("查看上下文").click();
  await expect(page.getByText(/context_budget_exceeded/)).toBeVisible(); await expect(page.getByText("修订 1", { exact: true })).toBeVisible(); await expect(page.getByText("省略原因：budget")).toBeVisible();
  await expect(page.getByText(/检索状态：已降级/)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/context-mobile.png", fullPage: true });
  empty = true; await page.getByText("查看上下文").click(); await expect(page.getByText("批次已冻结，没有候选命中。")).toBeVisible();
});
