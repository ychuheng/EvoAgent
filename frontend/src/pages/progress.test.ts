import { describe, expect, it } from "vitest";

import { EMPTY_PROGRESS, applyEvent, applyEvents, stepLabel, type StreamEvent } from "./progress";

function event(sequence: number, type: string, payload: Record<string, unknown> = {}): StreamEvent {
  return { sequence, type, payload, created_at: "2026-09-27T00:00:00Z" };
}

describe("事件投影（I-01）", () => {
  it("连续模型进度折叠展示，重连游标仍覆盖每个已提交事件", () => {
    const plan = applyEvents(EMPTY_PROGRESS, [
      event(1, "model.requested"),
      event(2, "model.delta", { text_delta: "first" }),
      event(3, "model.delta", { text_delta: "latest" }),
      event(4, "model.completed"),
      event(5, "tool.started", { name: "file_read" }),
      event(6, "model.delta", { text_delta: "legacy" }),
    ]);
    expect(plan.steps.map((step) => step.sequence)).toEqual([1, 3, 4, 5, 6]);
    expect(plan.steps[1].detail).toBe("latest");
    expect(plan.steps[4].detail).toBe("legacy");
    expect(plan.lastSequence).toBe(6);
    expect(applyEvent(plan, event(3, "model.delta"))).toBe(plan);
  });
  it("按序号去重，重连补发不会重复显示同一工具", () => {
    const plan = applyEvents(EMPTY_PROGRESS, [
      event(1, "task.queued"),
      event(2, "tool.started", { name: "list_dir" }),
      event(3, "tool.completed", { name: "list_dir" }),
    ]);
    const replayed = applyEvents(plan, [
      event(2, "tool.started", { name: "list_dir" }),
      event(3, "tool.completed", { name: "list_dir" }),
      event(4, "run.completed"),
    ]);
    expect(plan.steps).toHaveLength(3);
    expect(replayed.steps).toHaveLength(4);
    expect(replayed.steps.filter((step) => step.type === "tool.started")).toHaveLength(1);
    expect(replayed.lastSequence).toBe(4);
  });

  it("游标只前进，乱序到达的旧事件被丢弃", () => {
    const plan = applyEvent(applyEvent(EMPTY_PROGRESS, event(5, "tool.started", { name: "x" })), event(3, "task.queued"));
    expect(plan.lastSequence).toBe(5);
    expect(plan.steps.map((step) => step.sequence)).toEqual([5]);
  });

  it("终态事件置位 terminal 并清空当前动作", () => {
    const running = applyEvent(EMPTY_PROGRESS, event(1, "tool.started", { name: "search_text" }));
    expect(running.current).toContain("search_text");
    const finished = applyEvent(running, event(2, "run.failed", { error_code: "run_timeout" }));
    expect(finished.terminal).toBe(true);
    expect(finished.current).toBeNull();
  });

  it("授权撤销既是终态也给出可读说明", () => {
    const plan = applyEvent(EMPTY_PROGRESS, event(1, "authorization.revoked", { message: "项目授权已被撤销，本次调用被拒绝" }));
    expect(plan.terminal).toBe(true);
    expect(plan.steps[0].label).toContain("授权已被撤销");
    expect(plan.steps[0].detail).toContain("本次调用被拒绝");
  });

  it("未知事件类型也保留原始类型，不静默丢弃", () => {
    const label = stepLabel(event(1, "future.event", { a: 1 }));
    expect(label.label).toBe("future.event");
    expect(label.detail).toContain("a");
  });

  it("工具失败展示错误码而不是静默成功", () => {
    const label = stepLabel(event(1, "tool.failed", { name: "edit_file", error_code: "edit_conflict" }));
    expect(label.label).toContain("edit_file");
    expect(label.detail).toBe("edit_conflict");
  });

  it("冻结输入与输入变化都有可读说明且计入终态", () => {
    const frozen = applyEvent(
      EMPTY_PROGRESS,
      event(1, "input.frozen", { files: [{ path: "data/notes.txt" }, { path: "report.pdf" }] }),
    );
    expect(frozen.steps[0].label).toContain("2 个文件");
    expect(frozen.steps[0].detail).toContain("data/notes.txt");
    expect(frozen.terminal).toBe(false);

    const changed = applyEvent(frozen, event(2, "input.changed", { message: "内容已变化" }));
    expect(changed.terminal).toBe(true);
    expect(changed.steps[1].label).toContain("输入已变化");
  });
});
