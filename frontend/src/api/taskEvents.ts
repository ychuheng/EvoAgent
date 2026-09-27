/**
 * I-01/I-02 的前端接入：订阅 `/api/v1/tasks/{task_id}/events` 并投影成进度。
 *
 * 断线处理：
 * - 浏览器 EventSource 会自动重连并带上 `Last-Event-ID`，但我们要自己控制补发起点，
 *   因此用 fetch + ReadableStream 手动解析，并在重连时用 `lastSequence` 作为游标；
 * - 服务端在 Run 进入终态后关闭流；关闭后本 hook 再做一次终态补查（由页面负责），
 *   避免「事件在开流前已提交」导致阶段丢失。
 */

import { useEffect, useRef, useState } from "react";

import { EMPTY_PROGRESS, applyEvents, type ProgressPlan, type StreamEvent } from "../pages/progress";

const RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECTS = 5;

export type TaskEventStream = {
  progress: ProgressPlan;
  /** 已放弃重连或流异常结束时的原因；正常终态关闭时为 null。 */
  streamError: string | null;
};

function parseFrame(frame: string): StreamEvent | null {
  let id: number | null = null;
  let data = "";
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.trimEnd();
    if (line.startsWith("id:")) {
      const parsed = Number.parseInt(line.slice(3).trim(), 10);
      if (Number.isFinite(parsed)) id = parsed;
    } else if (line.startsWith("data:")) {
      data += line.slice(5).trim();
    }
  }
  if (data === "") return null;
  try {
    const body = JSON.parse(data) as Partial<StreamEvent>;
    const sequence = typeof body.sequence === "number" ? body.sequence : id;
    if (sequence === null || sequence === undefined) return null;
    return {
      sequence,
      type: typeof body.type === "string" ? body.type : "unknown",
      payload: (body.payload as Record<string, unknown>) ?? {},
      created_at: typeof body.created_at === "string" ? body.created_at : "",
    };
  } catch {
    return null;
  }
}

export function useTaskEventStream(taskId: string | null, runId: string | null): TaskEventStream {
  const [progress, setProgress] = useState<ProgressPlan>(EMPTY_PROGRESS);
  const [streamError, setStreamError] = useState<string | null>(null);
  const cursor = useRef(0);

  useEffect(() => {
    cursor.current = 0;
    setProgress(EMPTY_PROGRESS);
    setStreamError(null);
    if (!taskId) return;

    let active = true;
    let controller: AbortController | null = null;
    let reconnects = 0;

    const connect = async () => {
      controller = new AbortController();
      try {
        const response = await fetch(
          `/api/v1/tasks/${encodeURIComponent(taskId)}/events`,
          {
            headers: cursor.current > 0 ? { "Last-Event-ID": String(cursor.current) } : {},
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) {
          throw new Error(`HTTP ${response.status}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          const events: StreamEvent[] = [];
          for (const frame of frames) {
            const event = parseFrame(frame);
            if (event !== null) events.push(event);
          }
          if (events.length > 0 && active) {
            const highest = Math.max(...events.map((item) => item.sequence));
            cursor.current = Math.max(cursor.current, highest);
            setProgress((plan) => applyEvents(plan, events));
          }
        }
        // 服务端只在 Run 进入终态后关闭流；正常关闭不重连。
      } catch (error) {
        if (!active || (error instanceof DOMException && error.name === "AbortError")) return;
        reconnects += 1;
        if (reconnects > MAX_RECONNECTS) {
          setStreamError(`进度流多次中断（${String(error)}）；页面会在下次打开时补齐。`);
          return;
        }
        window.setTimeout(() => { void connect(); }, RECONNECT_DELAY_MS);
      }
    };

    void connect();
    return () => {
      active = false;
      controller?.abort();
    };
  }, [taskId, runId]);

  return { progress, streamError };
}
