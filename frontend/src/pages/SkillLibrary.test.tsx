import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SkillLibraryPanel } from "./SkillLibraryPanel";
import type { SkillSummary } from "../api/types";

const skills: SkillSummary[] = ["first", "second"].map(id => ({ id, name: id, slug: id, description: "method", status: "enabled", active_version_id: null, lock_version: 0, workspace_id: "workspace", project_id: null }));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("整理建议只在点击后查询，不自动合并或删除低使用方法", async () => {
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ examined_count: 20, truncated: true, unavailable_count: 1,
    merge_hints: [{ parents: [{ skill_id: "first", version_id: "v1" }, { skill_id: "second", version_id: "v2" }], reason: "lexical_similarity", score: 0.8 }],
    low_usage: [{ skill_id: "second", reason: "no_selection_in_30_days" }] })));
  vi.stubGlobal("fetch", fetcher);
  render(<SkillLibraryPanel scope={skills[0]} skills={skills} />);
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看整理建议" }));
  expect(await screen.findByRole("status")).toHaveTextContent("未修改任何方法");
  expect(screen.getByRole("status")).toHaveTextContent("不代表整个方法库");
  expect(screen.getByText(/first 与 second/)).toHaveTextContent("请先比较内容");
  expect(screen.getByText(/second · 保留/)).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/skills/library-suggestions?workspace_id=workspace");
});

test("项目整理请求保持显式项目范围，查询失败不显示旧建议", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => { urls.push(url); return new Response("{}", { status: 503 }); }));
  render(<SkillLibraryPanel scope={{ ...skills[0], project_id: "project" }} skills={skills} />);
  fireEvent.click(screen.getByRole("button", { name: "查看整理建议" }));
  await screen.findByRole("alert");
  expect(urls).toEqual(["/api/v1/skills/library-suggestions?workspace_id=workspace&project_id=project"]);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});
