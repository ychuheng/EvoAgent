/**
 * I-01/I-02 断线重连的前端契约：用 `Last-Event-ID` 从上次看到的序号续传，
 * 已应用的事件不重复显示；多次失败后才提示"页面会在下次打开时补齐"。
 *
 * 服务端的补发能力由 `tests/integration/test_sse_and_trace_api.py` 覆盖，
 * 这里覆盖浏览器侧：首次连接中断 → 重连必须带上游标。
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useTaskEventStream } from "./taskEvents";

function frame(sequence: number, type: string) {
  return `id: ${sequence}\ndata: ${JSON.stringify({ sequence, type, payload: {}, created_at: "2026-09-28T00:00:00Z" })}\n\n`;
}

function streamResponse(chunks: string[], failAfterChunks: boolean) {
  let index = 0;
  const encoder = new TextEncoder();
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (index < chunks.length) {
            const value = encoder.encode(chunks[index]);
            index += 1;
            return { value, done: false };
          }
          if (failAfterChunks) throw new Error("connection reset");
          return { value: undefined, done: true };
        },
      }),
    },
  } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test("进度流中断后用 Last-Event-ID 续传且不重复显示", async () => {
  const requests: Array<Record<string, string>> = [];
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    requests.push((init?.headers ?? {}) as Record<string, string>);
    if (requests.length === 1) {
      return streamResponse([frame(1, "run.started"), frame(2, "tool.started")], true);
    }
    return streamResponse([frame(3, "run.completed")], false);
  });
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => useTaskEventStream("task-1", "run-1"));

  await waitFor(() => expect(requests.length).toBeGreaterThanOrEqual(2), { timeout: 5_000 });
  // 第二次请求必须带上"已看到 1、2"的游标；服务端据此只补发后续事件。
  expect(requests[1]["Last-Event-ID"]).toBe("2");
  await waitFor(() => expect(result.current.progress.terminal).toBe(true), { timeout: 5_000 });
  expect(result.current.progress.steps.map((step) => step.sequence)).toEqual([1, 2, 3]);
  expect(result.current.progress.lastSequence).toBe(3);
  expect(result.current.streamError).toBeNull();
});

test("多次重连都失败时给出明确原因，而不是静默停住", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );

  const { result } = renderHook(() => useTaskEventStream("task-2", "run-2"));

  await waitFor(() => expect(result.current.streamError).toContain("进度流多次中断"), {
    timeout: 15_000,
  });
  expect(result.current.progress.terminal).toBe(false);
  // 5 次重连各等 1 秒，所以这条测试需要比默认 5 秒更长的预算。
}, 20_000);
