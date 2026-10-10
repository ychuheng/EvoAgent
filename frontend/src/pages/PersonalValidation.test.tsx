import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { LearningRequest } from "../api/learning";
import { PersonalValidationPanel } from "./PersonalValidationPanel";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const parent: LearningRequest = { id: "parent", workspace_id: "workspace", origin_run_id: "source", status: "completed", stage: "reviewed", request_kind: "propose", lock_version: 3, candidate_version_id: "version", validation_report_hash: "hash", error_code: null, available_actions: ["prepare_validation"], source: { id: "source-id", status: "valid", revocation_epoch: 0, content_hash: "sha256:source-hash" } };

test("只提交明确选择的公共文件样例，展示内容且不提交宿主路径", async () => {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("personal-validation-fixtures")) return json([
      { fixture_id: "first_sample", files: [{ path: "rows.csv", content: "id,value\n001,2" }] },
      { fixture_id: "second_sample", files: [{ path: "note.txt", content: "A public paragraph" }] },
    ]);
    if (!init?.method) return json(parent);
    bodies.push(JSON.parse(String(init.body)));
    return json({ ...parent, id: "child" }, 202);
  }));
  const changed = vi.fn(async () => {});
  render(<PersonalValidationPanel row={parent} enabled onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "准备新输入验证" }));
  fireEvent.click(await screen.findByRole("button", { name: "加载公共文件样例" }));
  await screen.findByLabelText("正例文件样例 parent");
  for (const [label, fixtureId] of [["正例", "first_sample"], ["反例", "second_sample"]]) {
    fireEvent.change(screen.getByLabelText(`${label}任务 parent`), { target: { value: "核对输入" } });
    fireEvent.change(screen.getByLabelText(`${label}结果 parent`), { target: { value: "文件内容保持完整" } });
    fireEvent.change(screen.getByLabelText(`${label}文件样例 parent`), { target: { value: fixtureId } });
  }
  expect(screen.getByText("A public paragraph")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("输入审查依据 parent"), { target: { value: "已独立核对这些公开样例" } });
  const freeze = screen.getByRole("button", { name: "冻结正反例（暂不执行）" });
  expect(freeze).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(freeze);
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect((bodies[0].cases as { fixture_id: string }[]).map(item => item.fixture_id)).toEqual(["first_sample", "second_sample"]);
  expect(JSON.stringify(bodies[0])).not.toContain("root");
  expect(JSON.stringify(bodies[0])).not.toContain("rows.csv");
});

test("冻结新输入须有明确授权；网络重试保留提交身份且不启动验证", async () => {
  const submissions: Record<string, unknown>[] = [];
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    if (!init?.method) return json(parent);
    submissions.push(JSON.parse(String(init.body)));
    return submissions.length === 1 ? json({ detail: { code: "temporary_failure" } }, 503) : json({ ...parent, id: "child" }, 202);
  }));
  const changed = vi.fn(async () => {});
  render(<PersonalValidationPanel row={parent} enabled onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "准备新输入验证" }));
  const freeze = await screen.findByRole("button", { name: "冻结正反例（暂不执行）" });
  for (const label of ["正例", "反例"]) {
    fireEvent.change(screen.getByLabelText(`${label}任务 parent`), { target: { value: `${label}任务` } });
    fireEvent.change(screen.getByLabelText(`${label}输入 parent`), { target: { value: label === "正例" ? "00201" : "plain paragraph" } });
    fireEvent.change(screen.getByLabelText(`${label}结果 parent`), { target: { value: "保持原始内容" } });
  }
  fireEvent.change(screen.getByLabelText("输入审查依据 parent"), { target: { value: "已核对原任务及新输入" } });
  expect(freeze).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(freeze);
  await screen.findByRole("alert");
  fireEvent.click(freeze);
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[0].reviewed_source_hash).toBe(parent.source?.content_hash);
  expect(submissions[0]).not.toHaveProperty("provider");
  expect(calls.some(url => url.endsWith("validation-start") || url.includes("trials"))).toBe(false);
});

const validation: LearningRequest = { ...parent, id: "validation", request_kind: "validate", status: "ready_for_review", stage: "validation_review", available_actions: ["judge_validation"], validation_report_hash: "sha256:report-hash", validation_report: {
  business_verification: "pending", trial_eligible: false, cost: { provider: "mock" },
  items: [
    { case_key: "positive", case_kind: "positive", arm: "treatment", repeat: 0, criterion_id: "business", expected: "00201", observed: null, verdict: "unknown", judge_origin: "user", evidence_refs: [{ type: "run", id: "run" }, { type: "eval_run", id: "eval" }] },
    { case_key: "negative", case_kind: "counterexample", arm: "control", repeat: 0, criterion_id: "safety", expected: true, observed: true, verdict: "pass", judge_origin: "machine", evidence_refs: [{ type: "run", id: "other-run" }, { type: "eval_run", id: "other-eval" }] },
  ],
} };

test("业务判定默认未知，只提交已填写的用户项，不把机器成功改成人工通过", async () => {
  let body: Record<string, unknown> = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("judgments")) { body = JSON.parse(String(init?.body)); return json({ report_hash: "new", trial_eligible: false }, 201); }
    return json(validation);
  }));
  const changed = vi.fn(async () => {});
  render(<PersonalValidationPanel row={validation} enabled onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "查看验证与业务判定" }));
  const result = await screen.findByLabelText("判定 eval:business");
  expect(result).toHaveValue("unknown");
  expect(screen.queryByLabelText("判定 other-eval:safety")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存填写的业务判定" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("判定依据 eval:business"), { target: { value: "只看到摘要，目前不能核对标识符" } });
  fireEvent.click(screen.getByRole("button", { name: "保存填写的业务判定" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(body.expected_lock_version).toBe(validation.lock_version);
  expect(body.expected_report_hash).toBe(validation.validation_report_hash);
  expect(body.judgments).toEqual([{ eval_run_id: "eval", criterion_id: "business", verdict: "unknown", observed: { notes: "只看到摘要，目前不能核对标识符" }, reason: "只看到摘要，目前不能核对标识符" }]);
  expect(body).not.toHaveProperty("actor"); expect(body).not.toHaveProperty("trial_eligible");
});

test("关闭学习后仍可查看验证证据，不能提交判定", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(validation)));
  render(<PersonalValidationPanel row={validation} enabled={false} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "查看验证与业务判定" }));
  expect(await screen.findByLabelText("判定 eval:business")).toBeDisabled();
  expect(screen.getByRole("button", { name: "查看实际输出 positive treatment" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "保存填写的业务判定" })).toBeDisabled();
});
