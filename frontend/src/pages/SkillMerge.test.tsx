import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SkillMergePanel } from "./SkillMergePanel";
import type { SkillSummary } from "../api/types";

const skills: SkillSummary[] = ["first", "second"].map((id, index) => ({ id, name: id, slug: id, description: "method", status: "enabled", active_version_id: null, lock_version: index + 4, workspace_id: "workspace", project_id: null }));
const json = (body: unknown) => new Response(JSON.stringify(body));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function prepare() {
  fireEvent.click(screen.getByLabelText("合并 first"));
  fireEvent.click(screen.getByLabelText("合并 second"));
  fireEvent.click(screen.getByRole("button", { name: "读取并冻结所选版本" }));
  await screen.findByLabelText("合并方法内容");
}

test("合并只提交冻结父版本和人工内容，不调用启用或弃用", async () => {
  const calls: string[] = []; let submitted: Record<string, unknown> = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    if (url.endsWith("/merge-proposals")) { submitted = JSON.parse(String(init?.body)); return json({ id: "merge-request", status: "ready_for_review", candidate_version_id: "candidate" }); }
    if (url.includes("/skill-versions/")) return json({ definition: { name: "first", steps: [] } });
    const parent = skills.find(s => url.endsWith(s.id))!;
    return json({ ...parent, versions: [{ id: `${parent.id}-version`, content_hash: `sha256:${parent.id}`, lifecycle_status: "draft", version: 1 }] });
  }));
  const reload = vi.fn(); render(<SkillMergePanel skills={skills} onCreated={reload} />);
  await prepare();
  expect(calls.filter(url => url.endsWith("/merge-proposals"))).toHaveLength(0);
  const confirm = screen.getByRole("button", { name: "确认创建合并候选" }); expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText("合并依据"), { target: { value: "remove repeated verification steps" } });
  fireEvent.click(confirm);
  await screen.findByRole("status");
  expect(submitted.parents).toEqual([{ skill_id: "first", version_id: "first-version", content_hash: "sha256:first", lock_version: 4 }, { skill_id: "second", version_id: "second-version", content_hash: "sha256:second", lock_version: 5 }]);
  expect(submitted.workspace_id).toBe("workspace"); expect(submitted.project_id).toBeNull();
  expect(submitted.definition).toEqual({ name: "combined_method", steps: [] });
  expect(calls.some(url => /activate|deprecate|review/.test(url))).toBe(false);
  expect(reload).toHaveBeenCalledTimes(1);
});

test("网络失败重试使用同一幂等键，编辑后使用新键", async () => {
  const bodies: Array<{ client_request_id: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/merge-proposals")) { bodies.push(JSON.parse(String(init?.body))); throw new Error("temporary connection failure"); }
    if (url.includes("/skill-versions/")) return json({ definition: { name: "first" } });
    const parent = skills.find(s => url.endsWith(s.id))!;
    return json({ ...parent, versions: [{ id: `${parent.id}-version`, content_hash: "hash", lifecycle_status: "draft" }] });
  }));
  render(<SkillMergePanel skills={skills} onCreated={() => {}} />); await prepare();
  fireEvent.change(screen.getByLabelText("合并依据"), { target: { value: "reviewed" } });
  fireEvent.click(screen.getByRole("button", { name: "确认创建合并候选" })); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "确认创建合并候选" })); await screen.findByRole("alert");
  expect(bodies).toHaveLength(2); expect(bodies[0].client_request_id).toBe(bodies[1].client_request_id);
  fireEvent.change(screen.getByLabelText("合并依据"), { target: { value: "changed judgment" } });
  fireEvent.click(screen.getByRole("button", { name: "确认创建合并候选" })); await screen.findByRole("alert");
  expect(bodies[2].client_request_id).not.toBe(bodies[1].client_request_id);
});

test("不同范围的父方法不能进入合并内容确认", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url); const parent = skills.find(s => url.endsWith(s.id))!;
    return json({ ...parent, project_id: parent.id === "second" ? "project" : null });
  }));
  render(<SkillMergePanel skills={skills} onCreated={() => {}} />);
  fireEvent.click(screen.getByLabelText("合并 first")); fireEvent.click(screen.getByLabelText("合并 second"));
  fireEvent.click(screen.getByRole("button", { name: "读取并冻结所选版本" }));
  await screen.findByRole("alert"); expect(screen.queryByLabelText("合并方法内容")).not.toBeInTheDocument();
  expect(calls).toHaveLength(2);
});
