import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

afterEach(() => vi.restoreAllMocks());

test("Skill 列表为空时显示明确空状态", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200 })));
  render(<App />);
  expect(await screen.findByText("还没有 Skill。")).toBeInTheDocument();
});

test("后端错误会显示给用户", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "database_unavailable", message: "数据库不可用" } }), { status: 503 })));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("数据库不可用");
});

test("可以切换到评测报告页", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200 })));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Eval 报告" }));
  await waitFor(() => expect(screen.getByText("配对评测报告")).toBeInTheDocument());
});
