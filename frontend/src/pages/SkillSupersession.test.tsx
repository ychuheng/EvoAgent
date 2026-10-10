import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SkillSupersessionPanel } from "./SkillSupersessionPanel";
import type { SkillSummary } from "../api/types";
const old: SkillSummary = { id: "old", name: "old", slug: "old", description: "method", workspace_id: "workspace", project_id: null, status: "enabled", active_version_id: null, lock_version: 2 };
const trial = { id: "trial", skill_id: "merged", version_id: "merged-version", workspace_id: "workspace", project_id: null, status: "active", lock_version: 3, validation_request_id: "validation", report_hash: "report" };
const json = (body: unknown) => new Response(JSON.stringify(body));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function choose() {
  fireEvent.click(screen.getByRole("button", { name: "加载同范围有效试用" }));
  await screen.findByRole("option", { name: "merged / merged-version" });
  fireEvent.change(screen.getByLabelText("替换试用"), { target: { value: "trial" } });
  fireEvent.click(screen.getByRole("button", { name: "检查并冻结替换版本" }));
  await screen.findByLabelText("替换依据");
}

test("核对不弃用，确认使用旧方法、替换方法和试用的冻结版本", async () => {
  let body: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/supersede")) { body = JSON.parse(String(init?.body)); return json({ status: "deprecated" }); }
    if (url.includes("skill-trials?")) return json({ items: [trial] });
    if (url.includes("trial-readiness?")) return json({ ready: true, report_hash: "report" });
    if (url.includes("skill-versions/")) return json({ id: "merged-version", merge_candidate: true });
    return json(url.endsWith("/old") ? { ...old, lock_version: 7 } : { ...old, id: "merged", lock_version: 9 });
  }));
  const changed = vi.fn(); render(<SkillSupersessionPanel skill={old} onChanged={changed} />);
  await choose(); expect(body).toBeNull();
  const button = screen.getByRole("button", { name: "确认替换并弃用当前方法" }); expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText("替换依据"), { target: { value: "verified combined method" } });
  fireEvent.click(button); await screen.findByRole("status");
  expect(body).toEqual({ replacement_id: "merged", replacement_version_id: "merged-version", replacement_trial_id: "trial", expected_lock_version: 7, expected_replacement_lock_version: 9, expected_trial_lock_version: 3, reason: "verified combined method" });
  expect(changed).toHaveBeenCalledTimes(1);
});

test("健康状态拒绝时清除冻结信息，不能自动重复弃用", async () => {
  let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/supersede")) { posts++; return new Response(JSON.stringify({ detail: { code: "supersession_healthy_replacement_required" } }), { status: 422 }); }
    if (url.includes("skill-trials?")) return json({ items: [trial] });
    if (url.includes("trial-readiness?")) return json({ ready: true, report_hash: "report" });
    if (url.includes("skill-versions/")) return json({ id: "merged-version", merge_candidate: true });
    return json(url.endsWith("/old") ? old : { ...old, id: "merged" });
  }));
  render(<SkillSupersessionPanel skill={old} onChanged={() => {}} />); await choose();
  fireEvent.change(screen.getByLabelText("替换依据"), { target: { value: "checked" } });
  fireEvent.click(screen.getByRole("button", { name: "确认替换并弃用当前方法" }));
  await screen.findByRole("alert"); expect(screen.getByRole("alert")).toHaveTextContent("健康状态尚未核实");
  expect(screen.queryByRole("button", { name: "确认替换并弃用当前方法" })).not.toBeInTheDocument();
  expect(posts).toBe(1);
});
