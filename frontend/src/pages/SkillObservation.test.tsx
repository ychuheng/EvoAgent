import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FeedbackPanel } from "./FeedbackPanel";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const artifact = { artifact_id: "artifact-actual", content_hash: `sha256:${"a".repeat(64)}`, type: "tool_output" };

test("人工 Skill 判定只引用本次实际版本、步骤和产物；取消后不会夹带证据", async () => {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("learning-policy")) return new Response(JSON.stringify({ mode: "off" }));
    if (url.includes("skill-observation-evidence")) return new Response(JSON.stringify({ run_id: "run", versions: [{ version_id: "version-actual", origin: "trial", steps: ["read"] }], artifacts: [artifact], artifacts_truncated: false }));
    bodies.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ id: "feedback", learning_request_id: null, routing: "none" }));
  }));
  render(<FeedbackPanel runId="run" workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "反馈与记成方法" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "记录已核对的 Skill 使用结果" }));
  const submit = screen.getByRole("button", { name: "提交任务反馈" });
  expect(submit).toBeDisabled();
  await screen.findByRole("option", { name: "version-actual（trial）" });
  fireEvent.change(screen.getByLabelText("实际采用版本"), { target: { value: "version-actual" } });
  fireEvent.change(screen.getByLabelText("验收判据标识"), { target: { value: "output_correct" } });
  fireEvent.change(screen.getByLabelText("核对结果"), { target: { value: "verified_failure" } });
  fireEvent.change(screen.getByLabelText("原因归属"), { target: { value: "skill_related" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "关联步骤 read" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "产物证据 artifact-actual" }));
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await screen.findByRole("status");
  expect(bodies[0].evidence_refs).toEqual([{ type: "skill_observation", schema_version: 1, version_id: "version-actual", criterion_id: "output_correct", outcome: "verified_failure", attribution: "skill_related", associated_steps: ["read"], artifacts: [{ artifact_id: artifact.artifact_id, content_hash: artifact.content_hash }] }]);
  expect(bodies[0]).not.toHaveProperty("actor_id");
  fireEvent.click(screen.getByRole("checkbox", { name: "记录已核对的 Skill 使用结果" }));
  fireEvent.click(submit);
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1]).not.toHaveProperty("evidence_refs");
});

test("没有实际采用的 Skill 或证据时不能提交人工技能判定", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("learning-policy") ? { mode: "off" } : { run_id: "run", versions: [], artifacts: [], artifacts_truncated: false }))));
  render(<FeedbackPanel runId="run" workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "反馈与记成方法" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "记录已核对的 Skill 使用结果" }));
  await screen.findByText("本次没有可判定的实际 Skill 采用记录。");
  expect(screen.getByRole("button", { name: "提交任务反馈" })).toBeDisabled();
});


test("显式检查绑定产物哈希，通过后才可选为证据，检查不提交任务反馈", async () => {
  const checks: unknown[] = [];
  const base = { run_id: "run", versions: [{ version_id: "version-actual", origin: "trial", steps: ["read"] }], artifacts: [], artifacts_truncated: false, pending_artifacts: [artifact] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("learning-policy")) return new Response(JSON.stringify({ mode: "off" }));
    if (url.endsWith("/verify")) {
      checks.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ ...base, artifacts: [artifact], pending_artifacts: [] }));
    }
    return new Response(JSON.stringify(base));
  }));
  render(<FeedbackPanel runId="run" workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "反馈与记成方法" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "记录已核对的 Skill 使用结果" }));
  await screen.findByLabelText("待检查产物 artifact-actual");
  expect(checks).toHaveLength(0);
  expect(screen.queryByLabelText("产物证据 artifact-actual")).toBeNull();
  fireEvent.click(screen.getByLabelText("待检查产物 artifact-actual"));
  fireEvent.click(screen.getByRole("button", { name: "检查所选产物证据" }));
  await screen.findByLabelText("产物证据 artifact-actual");
  expect(checks).toEqual([{ artifacts: [{ artifact_id: artifact.artifact_id, content_hash: artifact.content_hash }] }]);
  expect(screen.getByRole("button", { name: "提交任务反馈" })).toBeDisabled();
});

test("证据检查被拒绝不会生成可引用证据", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("learning-policy")) return new Response(JSON.stringify({ mode: "off" }));
    if (url.endsWith("/verify")) return new Response(JSON.stringify({ detail: { code: "observation_evidence_check_denied", message: "denied" } }), { status: 422 });
    return new Response(JSON.stringify({ run_id: "run", versions: [{ version_id: "version-actual", origin: "trial", steps: ["read"] }], artifacts: [], artifacts_truncated: false, pending_artifacts: [artifact] }));
  }));
  render(<FeedbackPanel runId="run" workspaceId="workspace" />);
  fireEvent.click(screen.getByRole("button", { name: "反馈与记成方法" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "记录已核对的 Skill 使用结果" }));
  await screen.findByLabelText("待检查产物 artifact-actual");
  fireEvent.click(screen.getByLabelText("待检查产物 artifact-actual"));
  fireEvent.click(screen.getByRole("button", { name: "检查所选产物证据" }));
  await screen.findByRole("alert");
  expect(screen.queryByLabelText("产物证据 artifact-actual")).toBeNull();
  expect(screen.getByRole("button", { name: "提交任务反馈" })).toBeDisabled();
});
