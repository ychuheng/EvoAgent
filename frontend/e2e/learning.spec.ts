import { expect, test } from "@playwright/test";

test("个人候选审查只确认候选，不启用正式版本", async ({ page }) => {
  const workspace = "00000000-0000-0000-0000-000000000001";
  let reviewed = false;
  const forbidden: string[] = [];
  const row = { id: "request", workspace_id: workspace, origin_run_id: "run", request_kind: "propose", status: "ready_for_review", stage: "candidate", lock_version: 0, candidate_version_id: "version", validation_report_hash: "hash", error_code: null, available_actions: ["review"] };
  await page.route("**/api/v1/**", async route => {
    const url = route.request().url();
    if (url.includes("/trials") || url.includes("/approve")) forbidden.push(url);
    if (url.endsWith("/workspaces")) return route.fulfill({ json: [{ id: workspace, name: "Local workspace" }] });
    if (url.endsWith("/learning-policy")) return route.fulfill({ json: { mode: "manual", lock_version: 0, daily_limit_micros: null, request_limit_micros: null, daily_candidate_limit: 3, cooldown_seconds: 86400, max_source_risk: "R1", learning_enabled: true } });
    if (url.includes("/learning-requests?")) return route.fulfill({ json: { items: [{ ...row, status: reviewed ? "completed" : "ready_for_review", available_actions: reviewed ? [] : ["review"] }], next_cursor: null } });
    if (url.endsWith("/skill-versions/version")) return route.fulfill({ json: { id: "version", definition: { steps: ["read identifiers as strings"] }, lifecycle_status: "draft" } });
    if (url.endsWith("/learning-requests/request/review")) {
      expect(route.request().postDataJSON()).toEqual({ action: "acknowledge", reason: "worth validating separately", expected_lock_version: 0 });
      reviewed = true;
      return route.fulfill({ json: { ...row, status: "completed" } });
    }
    if (url.endsWith("/sessions") || url.endsWith("/projects")) return route.fulfill({ json: [] });
    return route.fulfill({ json: { provider_mode: "mock", worker_status: "ready" } });
  });
  await page.goto("/ui/");
  await page.getByRole("button", { name: "个人学习", exact: true }).click();
  await expect(page.getByText("候选待审", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看候选内容" }).click();
  await expect(page.getByText(/read identifiers as strings/)).toBeVisible();
  await page.getByLabel("审查依据 request").fill("worth validating separately");
  await page.getByRole("button", { name: "确认候选（不启用）" }).click();
  await expect(page.getByText("候选已确认", { exact: true })).toBeVisible();
  expect(reviewed).toBe(true);
  expect(forbidden).toEqual([]);
});
