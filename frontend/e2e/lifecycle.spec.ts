import { expect, test, type Page } from "@playwright/test";

const skill = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "math_report",
  slug: "math_report",
  description: "生成数学报告",
  status: "enabled",
  active_version_id: null,
  lock_version: 0,
  versions: [],
};

async function mockEmptySkills(page: Page) {
  await page.route("**/api/v1/skills", (route) =>
    route.fulfill({ status: 200, json: [] }),
  );
}

test("查看配对报告", async ({ page }) => {
  await mockEmptySkills(page);
  await page.route("**/api/v1/eval-experiments/*/report", (route) =>
    route.fulfill({
      status: 200,
      json: {
        report: {
          experiment_id: "exp-1",
          pair_count: 2,
          comparable_pairs: 2,
          baseline_success_rate: 0.5,
          skill_success_rate: 1,
          safety_regressions: 0,
          pairs: [
            {
              case_key: "case-1",
              task_family: "math",
              repeat_index: 0,
              comparable: true,
              success_delta: 1,
              token_delta: -10,
              tool_call_delta: -1,
              safety_regression: false,
            },
          ],
        },
        report_hash: "sha256:report",
        gate_report: { passed: true, checks: [] },
        gate_report_hash: "sha256:gate",
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Eval 报告" }).click();
  await page.getByLabel("实验 ID").fill("exp-1");
  await page.getByRole("button", { name: "查看报告" }).click();
  await expect(page.getByText("100.0%")).toBeVisible();
  await expect(page.getByText("case-1")).toBeVisible();
});

test("批准 REVIEW_REQUIRED 版本", async ({ page }) => {
  await mockEmptySkills(page);
  let approved = false;
  await page.route("**/api/v1/skill-versions/version-1", (route) =>
    route.fulfill({
      status: 200,
      json: {
        id: "version-1",
        skill_id: skill.id,
        parent_version_id: null,
        version: 1,
        lifecycle_status: approved ? "active" : "review_required",
        content_hash: "sha256:skill",
        evaluation_report_hash: "sha256:report",
        gate_report_hash: "sha256:gate",
        definition: {},
        sources: [],
        gate_report: { passed: true, checks: [] },
      },
    }),
  );
  await page.route(`**/api/v1/skills/${skill.id}`, (route) =>
    route.fulfill({
      status: 200,
      json: { ...skill, active_version_id: approved ? "version-1" : null },
    }),
  );
  await page.route("**/api/v1/skill-versions/version-1/review", async (route) => {
    approved = true;
    await route.fulfill({
      status: 200,
      json: {
        skill_id: skill.id,
        active_version_id: "version-1",
        status: "enabled",
        lock_version: 1,
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "版本评审" }).click();
  await page.getByLabel("版本 ID", { exact: true }).fill("version-1");
  await page.getByRole("button", { name: "读取" }).click();
  await page.getByRole("button", { name: "批准并发布" }).click();
  await expect(page.getByText("版本已发布")).toBeVisible();
});

test("回滚到 RETIRED 版本", async ({ page }) => {
  await mockEmptySkills(page);
  await page.route("**/api/v1/skill-versions/version-old", (route) =>
    route.fulfill({
      status: 200,
      json: {
        id: "version-old",
        skill_id: skill.id,
        parent_version_id: null,
        version: 1,
        lifecycle_status: "retired",
        content_hash: "sha256:old",
        evaluation_report_hash: "sha256:report",
        gate_report_hash: "sha256:gate",
        definition: {},
        sources: [],
        gate_report: { passed: true, checks: [] },
      },
    }),
  );
  await page.route(`**/api/v1/skills/${skill.id}`, (route) =>
    route.fulfill({
      status: 200,
      json: { ...skill, active_version_id: "version-new" },
    }),
  );
  await page.route(`**/api/v1/skills/${skill.id}/rollback`, (route) =>
    route.fulfill({
      status: 200,
      json: {
        skill_id: skill.id,
        active_version_id: "version-old",
        status: "enabled",
        lock_version: 1,
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "版本评审" }).click();
  await page.getByLabel("版本 ID", { exact: true }).fill("version-old");
  await page.getByRole("button", { name: "读取" }).click();
  await page.getByRole("button", { name: "回滚到此版本" }).click();
  await expect(page.getByText("已经回滚到目标版本")).toBeVisible();
});
