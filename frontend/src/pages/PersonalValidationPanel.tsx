import { useRef, useState } from "react";
import { chat, type TaskTrace } from "../api/chat";
import { learning, learningError, type LearningRequest, type ValidationItem, type ValidationFixture } from "../api/learning";

type CaseForm = { goal: string; input: string; expected: string; fixtureId: string };
type Verdict = "pass" | "fail" | "unknown";
type Claim = { verdict: Verdict; notes: string };
const blank = (): CaseForm => ({ goal: "", input: "", expected: "", fixtureId: "" });
const keyOf = (item: ValidationItem) => `${item.evidence_refs.find(ref => ref.type === "eval_run")?.id}:${item.criterion_id}`;

export function PersonalValidationPanel({ row, enabled, onChanged }: { row: LearningRequest; enabled: boolean; onChanged: () => Promise<void> }) {
  const [detail, setDetail] = useState<LearningRequest | null>(null);
  const [positive, setPositive] = useState<CaseForm>(blank);
  const [negative, setNegative] = useState<CaseForm>(blank);
  const [family, setFamily] = useState("general");
  const [fixtures, setFixtures] = useState<ValidationFixture[]>([]);
  const [review, setReview] = useState("");
  const [consent, setConsent] = useState(false);
  const [claims, setClaims] = useState<Record<string, Claim>>({});
  const [traces, setTraces] = useState<Record<string, TaskTrace>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submission = useRef<{ signature: string; id: string } | null>(null);
  async function act(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await operation(); } catch (reason) { setError(learningError(reason)); } finally { setBusy(false); }
  }
  function identity(body: unknown) {
    const signature = JSON.stringify(body);
    if (submission.current?.signature !== signature) submission.current = { signature, id: crypto.randomUUID() };
    return submission.current.id;
  }
  async function load() {
    const current = await learning.get(row.id);
    setDetail(current); setClaims({}); setTraces({}); setConsent(false); submission.current = null;
  }
  const available = row.available_actions.includes("prepare_validation") || row.request_kind === "validate";
  if (!available) return null;
  const writable = enabled && !busy;
  const report = detail?.validation_report;
  function caseFields(label: string, value: CaseForm, update: (next: CaseForm) => void) {
    return <fieldset disabled={!writable}><legend>{label}</legend>
      <label>验证任务<textarea aria-label={`${label}任务 ${row.id}`} value={value.goal} maxLength={8000} onChange={event => update({ ...value, goal: event.target.value })} /></label>
      <label>新输入<textarea aria-label={`${label}输入 ${row.id}`} value={value.input} maxLength={16000} onChange={event => update({ ...value, input: event.target.value })} /></label>
      {fixtures.length > 0 && <><label>公共文件样例<select aria-label={`${label}文件样例 ${row.id}`} value={value.fixtureId} onChange={event => update({ ...value, fixtureId: event.target.value })}><option value="">使用文本输入</option>{fixtures.map(item => <option key={item.fixture_id} value={item.fixture_id}>{item.fixture_id}</option>)}</select></label>{fixtures.find(item => item.fixture_id === value.fixtureId)?.files.map(file => <div key={file.path}><p>{file.path}</p><pre>{file.content}</pre></div>)}</>}
      <label>由你核对的业务结果<textarea aria-label={`${label}结果 ${row.id}`} value={value.expected} maxLength={2000} onChange={event => update({ ...value, expected: event.target.value })} /></label>
    </fieldset>;
  }
  async function prepare() {
    if (!detail?.source?.content_hash || !consent || !review.trim()) return;
    const cases = [positive, negative].map((value, index) => ({
      case_key: index === 0 ? "positive_new" : "counter_new", case_kind: index === 0 ? "positive" : "counterexample",
      task_family: family, public_input: { goal: value.goal, ...(value.input.trim() ? { inputs: { text: value.input } } : {}) },
      ...(value.fixtureId ? { fixture_id: value.fixtureId } : {}),
      criteria: [{ criterion_id: "business_check", kind: "user", description: "按你提交的业务结果核对实际输出", expected: value.expected, business_criterion: true }],
    }));
    const body = { expected_parent_lock_version: detail.lock_version, reviewed_source_hash: detail.source.content_hash, independence_reason: review, repeats: 1, cases };
    await learning.prepareValidation(row.id, { ...body, client_request_id: identity(body) });
    await onChanged(); setDetail(null);
  }
  async function submitJudgments() {
    if (!detail?.validation_report_hash || !report) return;
    const judgments = report.items.filter(item => item.judge_origin === "user" && claims[keyOf(item)]?.notes.trim()).map(item => ({
      eval_run_id: item.evidence_refs.find(ref => ref.type === "eval_run")!.id, criterion_id: item.criterion_id,
      verdict: claims[keyOf(item)].verdict, observed: { notes: claims[keyOf(item)].notes }, reason: claims[keyOf(item)].notes,
    }));
    if (!judgments.length) return;
    const body = { expected_lock_version: detail.lock_version, expected_report_hash: detail.validation_report_hash, judgments };
    await learning.judgeValidation(row.id, { ...body, client_request_id: identity(body) });
    await onChanged(); setDetail(null); setClaims({});
  }
  const filled = [positive, negative].every(value => value.goal.trim() && (value.input.trim() || value.fixtureId) && value.expected.trim());
  return <section aria-label={`个人验证 ${row.id}`}>
    <p>当前仅提供离线 Mock 演练，不调用付费模型；人工判定也不会启用 Skill。真实试用尚未开放。</p>
    <button disabled={busy} type="button" onClick={() => void act(load)}>{row.request_kind === "validate" ? "查看验证与业务判定" : "准备新输入验证"}</button>
    {detail && row.request_kind === "propose" && <>
      <p>请先审查这份方法的来源，再提供不同于学习素材的新输入。反例应包含不适用或容易误用的情况。</p>
      <button disabled={busy} type="button" onClick={() => void act(async () => { const trace = await chat.trace(row.origin_run_id); setTraces(values => ({ ...values, source: trace })); })}>查看来源运行</button>
      {traces.source && <pre>{JSON.stringify(traces.source, null, 2)}</pre>}
      <label>任务类型<select disabled={!writable} aria-label={`验证任务类型 ${row.id}`} value={family} onChange={event => setFamily(event.target.value)}>{["general", "coding", "research", "document", "data", "file_management"].map((value, index) => <option key={value} value={value}>{["通用", "编码", "研究", "文档", "数据", "文件管理"][index]}</option>)}</select></label>
      <button type="button" disabled={!writable} onClick={() => void act(async () => { setFixtures(await learning.validationFixtures()); })}>加载公共文件样例</button>
      <p>文件样例只在每臂独立副本中执行，不复制你的真实项目，也不允许提交宿主路径。请核对样例内容和业务判据。</p>
      {caseFields("正例", positive, setPositive)}{caseFields("反例", negative, setNegative)}
      <label>来源与新输入的审查依据<textarea aria-label={`输入审查依据 ${row.id}`} disabled={!writable} maxLength={2000} value={review} onChange={event => setReview(event.target.value)} /></label>
      <label><input type="checkbox" disabled={!writable} checked={consent} onChange={event => setConsent(event.target.checked)} />我已核对来源，这些输入没有用于提炼此方法</label>
      <button type="button" disabled={!writable || !filled || !consent || !review.trim() || !detail.source?.content_hash} onClick={() => void act(prepare)}>冻结正反例（暂不执行）</button>
    </>}
    {detail?.available_actions.includes("start_validation") && <button type="button" disabled={!writable} onClick={() => void act(async () => { await learning.startValidation(detail); await onChanged(); setDetail(null); })}>执行离线验证</button>}
    {detail && report && <>
      <p>业务判定：{report.business_verification === "passed" ? "已确认" : report.business_verification === "failed" ? "有失败项" : "待核对"}。本轮模型：{report.cost.provider}；不具备试用资格。</p>
      <p>方法采用检查：{report.adoption_verification === "passed" ? "正例采用、反例未采用" : report.adoption_verification === "failed" ? "未通过，不能据此试用" : "历史报告缺少实际采用证据"}。进入上下文不代表已经遵循方法或业务正确。</p>
      {report.items.map(item => { const key = keyOf(item), runId = item.evidence_refs.find(ref => ref.type === "run")?.id, claim = claims[key] ?? { verdict: "unknown" as Verdict, notes: "" }; return <fieldset key={key} disabled={busy}>
        <legend>{item.case_key} · {item.arm === "treatment" ? "候选方法" : "对照"} · 第 {item.repeat + 1} 次</legend>
        <p>{item.description ?? item.criterion_id} · 当前结果：{item.verdict}</p><pre>{JSON.stringify({ expected: item.expected, observed: item.observed }, null, 2)}</pre>
        <p>实际方法：{item.actual_selection?.verified ? item.actual_selection.applied ? `已进入上下文（版本 ${item.actual_selection.selection?.version_id ?? "未知"}）` : "未采用" : "缺少可信采用证据"}</p>
        {runId && <><button type="button" onClick={() => void act(async () => { const trace = await chat.trace(runId); setTraces(values => ({ ...values, [runId]: trace })); })}>查看实际输出 {item.case_key} {item.arm}</button>{traces[runId] && <><pre>{JSON.stringify(traces[runId], null, 2)}</pre>{traces[runId].artifacts?.map(artifact => <a key={artifact.id} href={`/api/v1/artifacts/${artifact.id}/download`} download>下载验证产物</a>)}</>}</>}
        {item.judge_origin === "user" && detail.available_actions.includes("judge_validation") && <>
          <label>你的判定<select disabled={!enabled} aria-label={`判定 ${key}`} value={claim.verdict} onChange={event => setClaims(values => ({ ...values, [key]: { ...claim, verdict: event.target.value as Verdict } }))}><option value="unknown">尚不能确定</option><option value="pass">符合业务结果</option><option value="fail">不符合业务结果</option></select></label>
          <label>你观察到的结果和依据<textarea disabled={!enabled} aria-label={`判定依据 ${key}`} maxLength={2000} value={claim.notes} onChange={event => setClaims(values => ({ ...values, [key]: { ...claim, notes: event.target.value } }))} /></label>
        </>}
      </fieldset>; })}
      <button type="button" disabled={!writable || !Object.values(claims).some(value => value.notes.trim())} onClick={() => void act(submitJudgments)}>保存填写的业务判定</button>
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
