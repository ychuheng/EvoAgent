import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MemoryPage } from "./pages/MemoryPage";
import { MCPPage } from "./pages/MCPPage";
import { ContextPage } from "./pages/ContextPage";
import { phase4 } from "./api/phase4";
import { ApiError, request } from "./api/client";
import { memory, server, catalog, review, context, retrieval } from "./test/phase4-fixtures";
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function openMemory() {
  vi.spyOn(phase4, "memories").mockResolvedValue([memory]);
  vi.spyOn(phase4, "memory").mockResolvedValue(memory);
  render(<MemoryPage />);
  fireEvent.change(screen.getByLabelText("Session ID"), { target: { value: "session-1" } });
  fireEvent.click(screen.getByText("读取记忆"));
  fireEvent.click(await screen.findByRole("button", { name: /language/ }));
  await screen.findByText("事实来源");
}
async function openMCP() {
  vi.spyOn(phase4, "servers").mockResolvedValue([server]);
  vi.spyOn(phase4, "catalogs").mockResolvedValue([catalog]);
  vi.spyOn(phase4, "health").mockResolvedValue([]);
  vi.spyOn(phase4, "reviews").mockResolvedValue([review]);
  render(<MCPPage />);
  fireEvent.click(await screen.findByRole("button", { name: /Fixture Server/ }));
  await screen.findByText("审核理由");
}
test("Memory 空列表与读取错误", async () => {
  const list = vi.spyOn(phase4, "memories").mockResolvedValue([]);
  render(<MemoryPage />);
  fireEvent.change(screen.getByLabelText("Session ID"), { target: { value: "session-1" } });
  fireEvent.click(screen.getByText("读取记忆"));
  expect(await screen.findByText("此作用域还没有记忆。")).toBeInTheDocument();
  list.mockRejectedValue(new Error("数据库不可用"));
  fireEvent.click(screen.getByText("读取记忆"));
  expect(await screen.findByRole("alert")).toHaveTextContent("数据库不可用");
});
test("Memory 版本冲突不伪造成功，提交当前锁版本", async () => {
  const decide = vi.spyOn(phase4, "decide").mockRejectedValue(new ApiError("conflict", "memory_version_conflict", 409));
  await openMemory(); fireEvent.click(screen.getByText("确认事实"));
  expect(await screen.findByRole("alert")).toHaveTextContent("请刷新后重新审核");
  expect(decide).toHaveBeenCalledWith("session-1", memory, "confirm");
  expect(screen.queryByText("服务端已记录决定。")).not.toBeInTheDocument();
});
test("Memory 撤销后展示失败删除任务并允许重试", async () => {
  await openMemory();
  vi.spyOn(phase4, "decide").mockResolvedValue({ ...memory, status: "revoked" });
  vi.mocked(phase4.memory).mockResolvedValue({ ...memory, status: "revoked", maintenance_job_id: "job-1" });
  const job = vi.spyOn(phase4, "job").mockResolvedValue({ id: "job-1", kind: "erase", status: "failed", attempts: 1, result: null, error_code: "storage_unavailable", next_attempt_at: null });
  const retry = vi.spyOn(phase4, "retryJob").mockResolvedValue({});
  fireEvent.click(screen.getByText("撤销并清理内容"));
  expect(await screen.findByText(/storage_unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("清理是否完成请查看删除任务");
  job.mockResolvedValue({ id: "job-1", kind: "erase", status: "pending", attempts: 1, result: null, error_code: null, next_attempt_at: null });
  fireEvent.click(screen.getByText("重试删除任务"));
  await waitFor(() => expect(retry).toHaveBeenCalledWith("job-1"));
  expect(await screen.findByText("pending")).toBeInTheDocument();
});
test("MCP 空列表与服务不可用可刷新", async () => {
  const list = vi.spyOn(phase4, "servers").mockRejectedValue(new Error("服务不可用"));
  render(<MCPPage />); expect(await screen.findByRole("alert")).toHaveTextContent("服务不可用");
  list.mockResolvedValue([]); fireEvent.click(screen.getByText("刷新 Server"));
  expect(await screen.findByText("还没有 MCP Server。")).toBeInTheDocument();
});
test("MCP 不显示秘密引用，审核冲突保留服务器状态", async () => {
  await openMCP();
  expect(screen.queryByText(/private-reference-do-not-render/)).not.toBeInTheDocument();
  expect(screen.getByText(/已配置引用/)).toBeInTheDocument();
  vi.spyOn(phase4, "review").mockRejectedValue(new ApiError("conflict", "mcp_review_conflict", 409));
  fireEvent.change(screen.getByLabelText("审核理由"), { target: { value: "已核对契约" } });
  fireEvent.click(screen.getByText("批准工具"));
  expect(await screen.findByRole("alert")).toHaveTextContent("mcp_review_conflict");
  expect(screen.queryByText("工具审核已记录。")).not.toBeInTheDocument();
});
test("MCP 旧目录不能审核和激活", async () => {
  await openMCP();
  vi.mocked(phase4.catalogs).mockResolvedValue([{ ...catalog, config_version: 1 }]);
  fireEvent.click(screen.getByText("刷新 Server"));
  await screen.findByText(/历史目录，只读/);
  expect(screen.getByText("激活此目录")).toBeDisabled();
  expect(screen.getByText("批准工具")).toBeDisabled();
});
test("上下文预算和检索省略证据分开展示", async () => {
  vi.spyOn(phase4, "context").mockResolvedValue(context); vi.spyOn(phase4, "retrieval").mockResolvedValue(retrieval);
  render(<ContextPage />); fireEvent.change(screen.getByLabelText("Run ID"), { target: { value: "run-1" } }); fireEvent.click(screen.getByText("查看上下文"));
  expect(await screen.findByText(/检索状态：已降级/)).toBeInTheDocument();
  expect(screen.getByText(/context_budget_exceeded/)).toBeInTheDocument();
  expect(screen.getByText("省略原因：budget")).toBeInTheDocument();
});
test("检索批次缺失与空命中不能混淆，读取失败清除旧证据", async () => {
  const get = vi.spyOn(phase4, "context").mockResolvedValue({ ...context, revisions: [], policy: {} });
  vi.spyOn(phase4, "retrieval").mockRejectedValue(new ApiError("missing", "retrieval_batch_not_found", 404));
  render(<ContextPage />); fireEvent.change(screen.getByLabelText("Run ID"), { target: { value: "run-1" } }); fireEvent.click(screen.getByText("查看上下文"));
  expect(await screen.findByText("没有冻结检索批次，不能当作零命中。")).toBeInTheDocument();
  get.mockRejectedValue(new Error("Run 不存在")); fireEvent.click(screen.getByText("查看上下文"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Run 不存在");
  expect(screen.queryByText("冻结预算")).not.toBeInTheDocument();
});
test("204 不解析 JSON；结构化校验错误不显示对象", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ input: "do-not-render-input" }] }), { status: 422 })));
  expect(await request("/empty")).toBeUndefined();
  await expect(request("/invalid")).rejects.toThrow("请求失败：HTTP 422");
});
