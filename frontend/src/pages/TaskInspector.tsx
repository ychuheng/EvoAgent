import { useEffect, useState } from "react";

import { chat, type ApprovalPreview, type ArtifactDetail, type ChatTask, type TaskTrace } from "../api/chat";
import { ApiError } from "../api/client";
import { phase4, type RetrievalEvidence } from "../api/phase4";
import { errorLabel, taskStatusLabel } from "./taskLabels";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const TOOL_STATUS: Record<string, string> = {
  pending: "待执行", running: "执行中", succeeded: "成功", failed: "失败",
  denied: "已拒绝", unknown: "结果不确定",
};

function sourceLink(url: string) {
  try {
    const parsed = new URL(url);
    if (["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password) {
      return <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>;
    }
  } catch { /* A malformed source remains plain text. */ }
  return <span>{url}</span>;
}

export function TaskInspector({ taskId, onOpenVersion, onOpenContext }: { taskId: string; onOpenVersion: (id: string) => void; onOpenContext: (id: string) => void }) {
  const [task, setTask] = useState<ChatTask | null>(null);
  const [trace, setTrace] = useState<TaskTrace | null>(null);
  const [retrieval, setRetrieval] = useState<RetrievalEvidence | null>(null);
  const [responses, setResponses] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<Record<string, { data?: ApprovalPreview; error?: string }>>({});
  const [artifactPreviews, setArtifactPreviews] = useState<Record<string, { data?: ArtifactDetail; error?: string }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    setTask(null);
    setTrace(null);
    setRetrieval(null);
    setError("");
    setResponses({});
    setPreviews({});
    setArtifactPreviews({});
    const refresh = async () => {
      try {
        const current = await chat.task(taskId);
        const detail = await chat.trace(current.latest_run.id);
        if (!active) return;
        setTask(current);
        setTrace(detail);
        setError("");
        if (detail.events?.some((event) => event.event_type.startsWith("retrieval."))) {
          try { setRetrieval(await phase4.retrieval(current.latest_run.id)); }
          catch (reason) { if (!(reason instanceof ApiError && reason.status === 404)) setError(String(reason)); }
        }
        if (!TERMINAL.has(current.status)) timer = window.setTimeout(() => { void refresh(); }, 1200);
      } catch (reason) {
        if (!active) return;
        setError(String(reason));
        timer = window.setTimeout(() => { void refresh(); }, 2000);
      }
    };
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [taskId]);

  async function act(action: () => Promise<unknown>, approvalId?: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      const current = await chat.task(taskId);
      setTask(current);
      setTrace(await chat.trace(current.latest_run.id));
      if (approvalId) setResponses((values) => ({ ...values, [approvalId]: "" }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function approveWithPreview(approvalId: string, response: string) {
    if (busy) return;
    setBusy(true);
    const shown = previews[approvalId]?.data;
    if (shown) {
      try {
        const current = await chat.approvalPreview(approvalId);
        if (JSON.stringify(current) !== JSON.stringify(shown)) {
          setPreviews((values) => ({ ...values, [approvalId]: { data: current } }));
          setError("文件差异已变化，请重新核对后再批准。");
          setBusy(false);
          return;
        }
      } catch (reason) {
        setPreviews((values) => ({ ...values, [approvalId]: { error: String(reason) } }));
        setError("文件差异已无法核对，不能批准。");
        setBusy(false);
        return;
      }
    }
    await act(() => chat.decideApproval(approvalId, "approve", response), approvalId);
  }

  const [reviewReasons, setReviewReasons] = useState<Record<string, string>>({});
  const [reviewRequests, setReviewRequests] = useState<Record<string, { fingerprint: string; id: string }>>({});
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});

  async function reviewArtifact(detail: ArtifactDetail) {
    const reason = (reviewReasons[detail.id] ?? "").trim();
    if (!reason || busy) return;
    const fingerprint = JSON.stringify([reason, detail.content_hash, detail.redaction_policy_version]);
    const previous = reviewRequests[detail.id];
    const id = previous?.fingerprint === fingerprint ? previous.id : crypto.randomUUID();
    setReviewRequests((values) => ({ ...values, [detail.id]: { fingerprint, id } }));
    setBusy(true);
    setError("");
    try {
      const outcome = await chat.reviewArtifact(detail.id, {
        reason, expected_policy_version: detail.redaction_policy_version ?? null,
        expected_content_hash: detail.content_hash, client_request_id: id, offline: true,
      });
      setReviewNotes((values) => ({ ...values, [detail.id]: outcome.note }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      try {
        const current = await chat.artifact(detail.id);
        setArtifactPreviews((values) => ({ ...values, [detail.id]: { data: current } }));
      } catch (reason) { setError(String(reason)); }
      setBusy(false);
    }
  }

  async function toggleArtifactPreview(artifactId: string) {
    const shown = artifactPreviews[artifactId];
    if (shown?.data || shown?.error) {
      setArtifactPreviews((values) => {
        const next = { ...values };
        delete next[artifactId];
        return next;
      });
      return;
    }
    try {
      const detail = await chat.artifact(artifactId);
      setArtifactPreviews((values) => ({ ...values, [artifactId]: { data: detail } }));
    } catch (reason) {
      // 预览失败（例如存储内容与登记哈希不一致时后端 409）必须显示原因，不能静默。
      setArtifactPreviews((values) => ({ ...values, [artifactId]: { error: String(reason) } }));
    }
  }

  const pendingApprovals = task && !TERMINAL.has(task.status)
    ? trace?.approvals.filter((approval) => approval.status === "pending") ?? [] : [];
  const previewIds = pendingApprovals
    .filter((approval) => trace?.tool_calls.some((call) => call.id === approval.tool_call_id && ["edit_file", "apply_patch"].includes(call.tool_name)))
    .map((approval) => approval.id).join(",");
  useEffect(() => {
    let active = true;
    for (const id of previewIds.split(",").filter(Boolean)) {
      void chat.approvalPreview(id).then((data) => {
        if (active) setPreviews((current) => ({ ...current, [id]: { data } }));
      }).catch((reason) => {
        if (active) setPreviews((current) => ({ ...current, [id]: { error: String(reason) } }));
      });
    }
    return () => { active = false; };
  }, [previewIds]);
  const lexicalSkills = (trace?.events ?? []).filter((event) => event.event_type === "skill.selected")
    .flatMap((event) => Array.isArray(event.payload.matches) ? event.payload.matches : [])
    .map((match) => typeof match === "object" && match !== null && "version_id" in match ? String(match.version_id) : "")
    .filter(Boolean);
  const hybridSkills = retrieval?.selections.filter((item) => !item.omission_reason && item.source_key.startsWith("skill:"))
    .map((item) => item.source_key.slice("skill:".length)) ?? [];
  const selectedSkills = [...new Set([...lexicalSkills, ...hybridSkills])];
  const selectedMemories = retrieval?.selections.filter((item) => !item.omission_reason && item.source_key.startsWith("memory:")) ?? [];
  const showError = trace?.error_code && task?.status !== "cancelled" && !(
    trace.error_code === "approval_required" && task && !TERMINAL.has(task.status)
  );
  const unresolvedEffect = trace?.tool_effects.some((effect) => effect.status === "unknown");
  const acceptancePassed = trace?.events.some((event) => event.event_type === "acceptance.checked" && event.payload.passed === true);
  return <section className="chat-inspector" aria-label="任务执行过程">
    <h3>执行过程</h3>
    <p>{task ? `状态：${taskStatusLabel[task.status] ?? task.status}` : "正在读取任务状态…"}{task?.cancel_requested ? " · 已请求取消" : ""}</p>
    {task && <p className="chat-meta">模型：{task.latest_run.provider} / {task.latest_run.model} · Task {task.id} · Run {task.latest_run.id}</p>}
    {task?.status === "completed" && <p className="chat-meta">{task.acceptance ? (acceptancePassed ? "已通过设定的验收条件；其他内容仍需核对。" : "验收记录缺失，结果不能视为已核验。") : "模型已给出回答；未设置独立验收条件，正确性尚未核验。"}</p>}
    {trace?.error_code === "acceptance_failed" && trace.final_answer && <details><summary>查看未通过验收的模型回答</summary><p>{trace.final_answer}</p></details>}
    {task && <button type="button" className="chat-detail-button" onClick={() => onOpenContext(task.latest_run.id)}>查看上下文与检索证据</button>}
    {task && !TERMINAL.has(task.status) && !task.cancel_requested && <button type="button" className="danger" disabled={busy} onClick={() => { void act(() => chat.cancel(taskId)); }}>取消任务</button>}
    {showError && <p role="alert" className="error">{task && TERMINAL.has(task.status) ? "失败原因" : "最近错误"}：{errorLabel(trace.error_code!)}</p>}
    {unresolvedEffect && task && TERMINAL.has(task.status) && <p role="alert" className="error">外部操作结果仍不确定；任务结束不代表外部动作未发生，请核对实际状态。</p>}
    {trace?.tool_calls.length ? <div className="chat-tool-list"><h4>工具调用</h4><ol>{trace.tool_calls.map((call) =>
      <li key={call.id}><strong>{call.tool_name}</strong> · {trace.approvals.some((approval) => approval.tool_call_id === call.id && approval.status === "approved") && call.status === "pending" ? "已审批，等待恢复" : TOOL_STATUS[call.status] ?? call.status}
        <details><summary>查看输入参数</summary><pre>{JSON.stringify(call.arguments, null, 2)}</pre></details>
        {call.result_summary && <p>{call.result_summary}</p>}
        {call.error_code && <p className="error">{errorLabel(call.error_code)}</p>}
      </li>
    )}</ol></div> : <p className="chat-meta">此任务尚无工具调用记录。</p>}
    <div className="chat-evidence"><h4>检索与 Skill</h4>
      {selectedSkills.length ? selectedSkills.map((id) => <button key={id} type="button" className="chat-detail-button" onClick={() => onOpenVersion(id)}>查看 Skill 版本 {id.slice(0, 8)}…</button>) :
        <p className="chat-meta">{trace?.events?.some((event) => event.event_type === "skill.none_selected" || event.event_type.startsWith("retrieval.")) ? "此任务未选中 Skill。" : "尚无 Skill 选择证据。"}</p>}
      {retrieval && <p className="chat-meta">记忆命中 {selectedMemories.length} 条 · {retrieval.degraded ? "检索已降级" : "检索未降级"} · 批次 {retrieval.batch_id}</p>}
    </div>
    {trace?.artifacts && trace.artifacts.length > 0 &&
      <div className="chat-evidence"><h4>产物（F-04）</h4>
        <p className="chat-meta">下载响应头带 SHA-256，可与下面的登记哈希逐位核对；预览过长时会指向下载。</p>
        {trace.artifacts.map((artifact) => <div key={artifact.id}>
          <p className="chat-meta">
            {artifact.uri.split("/").pop()} · {artifact.type} · {artifact.size_bytes} 字节 · SHA-256 {artifact.content_hash}
            <button type="button" className="chat-detail-button" disabled={busy}
              onClick={() => { void toggleArtifactPreview(artifact.id); }}>
              {artifactPreviews[artifact.id]?.data || artifactPreviews[artifact.id]?.error ? "收起预览" : "预览"}
            </button>
            <a className="chat-detail-button" href={`/api/v1/artifacts/${artifact.id}/download`} download>下载</a>
          </p>
          {artifactPreviews[artifact.id]?.error && <p role="alert" className="error">预览失败：{artifactPreviews[artifact.id]?.error}</p>}
          {artifactPreviews[artifact.id]?.data && (() => {
            const detail = artifactPreviews[artifact.id]?.data;
            if (!detail) return null;
            return <div className="chat-artifact-preview">
              <p className="chat-meta">{detail.note}{detail.preview_truncated ? "（预览已截断）" : ""}</p>
              {detail.redaction_status && <p>注入检查：{detail.redaction_status} · 已检查策略 {detail.redaction_policy_version ?? "未知"} · 当前策略 {detail.current_policy_version}</p>}
              {["tool_output", "context_source"].includes(detail.type) && ["quarantined", "unchecked"].includes(detail.redaction_status ?? "") && <div>
                <p>单件受限复查：最多 64 MiB，仍命中敏感规则则继续隔离；不会绕过规则。</p>
                <label>复查理由<input aria-label="产物复查理由" value={reviewReasons[detail.id] ?? ""} maxLength={2000}
                  onChange={(event) => setReviewReasons((values) => ({ ...values, [detail.id]: event.target.value }))} /></label>
                <button type="button" disabled={busy || !(reviewReasons[detail.id] ?? "").trim()} onClick={() => { void reviewArtifact(detail); }}>确认并复查此产物</button>
              </div>}
              {reviewNotes[detail.id] && <p role="status">{reviewNotes[detail.id]}</p>}
              {detail.preview === null
                ? <p className="chat-meta">该产物没有文本预览（二进制或未启用预览）；请下载后核对 SHA-256。</p>
                : <pre>{detail.preview}</pre>}
            </div>;
          })()}
        </div>)}
      </div>}
    {trace?.sources && (trace.sources.searches.length > 0 || trace.sources.reads.length > 0 || trace.sources.answer_links.length > 0) &&
      <div className="chat-evidence"><h4>网页来源证据</h4>
        <p className="chat-meta">这里核对 URL 是否出现在工具记录中；仍需人工判断网页内容是否支持答复。</p>
        {trace.sources.searches.map((search, index) =>
          <div key={`${search.tool_call_id}-${index}`}><p className="chat-meta">搜索：{search.query} · {search.provider} · {search.result_count} 条结果 · {search.observed_at}</p>
            {search.urls.length > 0 && <details><summary>查看搜索结果 URL</summary><ul>{search.urls.map((url, urlIndex) => <li key={`${url}-${urlIndex}`}>{sourceLink(url)}</li>)}</ul></details>}
          </div>
        )}
        {trace.sources.reads.map((read, index) =>
          <p className="chat-meta" key={`${read.tool_call_id}-${index}`}>已读取正文：{sourceLink(read.final_url)} · SHA-256 {read.content_sha256} · {read.observed_at}</p>
        )}
        {trace.sources.answer_links.map((link, index) =>
          <p className="chat-meta" key={`${link.url}-${index}`}>答复链接：{sourceLink(link.url)} · {link.level === "fetched_text" ? (trace.sources?.reads.some((read) => read.tool_call_id === link.tool_call_id && read.text_truncated) ? "已读取正文摘录（截断）" : "已读取正文") : link.level === "search_snippet" ? "仅见搜索摘要" : "未见检索或读取记录"}</p>
        )}
      </div>}
    {pendingApprovals.map((approval) => {
      const call = trace?.tool_calls.find((item) => item.id === approval.tool_call_id);
      const unknown = trace?.tool_effects.some((effect) => effect.tool_call_id === approval.tool_call_id && effect.status === "unknown");
      const response = responses[approval.id] ?? "";
      const needsPreview = call?.tool_name === "edit_file" || call?.tool_name === "apply_patch";
      const preview = previews[approval.id];
      return <div className="chat-approval" key={approval.id}>
        <h4>需要人工决定：{call?.tool_name ?? "工具调用"}</h4>
        <p>风险 {approval.risk} · {approval.reason}</p>
        {needsPreview && <div className="chat-evidence" aria-label="审批前文件差异">
          {!preview && <p>正在核对当前文件并生成差异…</p>}
          {preview?.error && <p role="alert" className="error">差异预览失败：{preview.error}。请刷新或拒绝，不能在未核对差异时批准。</p>}
          {preview?.data && <>
            <p>拟修改 {preview.data.file_count} 个文件 · +{preview.data.added_lines} / -{preview.data.removed_lines} 行。批准前会再次核对；执行时还会检查编辑定位与已给出的哈希前置条件。</p>
            {preview.data.files.map((file) => <details key={file.path} open><summary>{file.path} · {file.created ? "新建" : "修改"} · +{file.added_lines}/-{file.removed_lines}</summary><pre>{file.diff || "（无差异）"}</pre>{file.diff_truncated && <p className="error">差异已截断，请拒绝并缩小改动范围。</p>}</details>)}
          </>}
        </div>}
        {unknown && <p>{call?.status === "failed" ? "外部操作结果不确定，且工具已失败，无法在本任务安全重试。若确认未提交，请取消后新建任务；若确认已提交，请输入 committed:实际结果。请先核对外部状态。" : "外部操作结果不确定。确认重试请输入 retry；确认已提交请输入 committed:实际结果。请先核对外部状态。"}</p>}
        <label>回复或确认依据<input value={response} onChange={(event) => setResponses((values) => ({ ...values, [approval.id]: event.target.value }))} placeholder={unknown ? (call?.status === "failed" ? "committed:实际结果" : "retry 或 committed:实际结果") : "ask_user 工具需要填写回复"} /></label>
        <div className="actions">
          <button type="button" disabled={busy || (needsPreview && (!preview?.data || preview.data.files.some((file) => file.diff_truncated))) || (unknown && !response.trim()) || (call?.tool_name === "ask_user" && !response.trim())} onClick={() => { void approveWithPreview(approval.id, response); }}>批准</button>
          {!unknown && <button type="button" className="danger" disabled={busy} onClick={() => { void act(() => chat.decideApproval(approval.id, "reject", response), approval.id); }}>拒绝</button>}
        </div>
      </div>;
    })}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
