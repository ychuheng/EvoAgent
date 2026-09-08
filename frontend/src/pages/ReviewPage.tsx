import { useState } from "react";

import { api } from "../api/client";
import type { SkillDetail, VersionDetail } from "../api/types";
import { Badge, Empty, ErrorNotice } from "../components/State";

export function ReviewPage() {
  const [versionId, setVersionId] = useState("");
  const [against, setAgainst] = useState("");
  const [version, setVersion] = useState<VersionDetail | null>(null);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [changes, setChanges] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = async () => {
    try {
      setError("");
      const detail = await api.getVersion(versionId.trim());
      setVersion(detail);
      setSkill(await api.getSkill(detail.skill_id));
      setChanges(against.trim() ? (await api.getDiff(detail.id, against.trim())).changes : []);
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const review = async (action: "approve" | "reject") => {
    if (!version || !skill) return;
    try {
      await api.review(version.id, {
        action,
        expected_lock_version: skill.lock_version,
        reviewer: "local-ui",
        reason: action === "approve" ? "人工确认门禁证据" : "人工拒绝候选",
      });
      setNotice(action === "approve" ? "版本已发布" : "版本已拒绝");
      await load();
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const rollback = async () => {
    if (!version || !skill) return;
    try {
      await api.rollback(skill.id, version.id, skill.lock_version);
      setNotice("已经回滚到目标版本");
      await load();
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  return <section className="panel wide">
    <h2>版本差异与人工评审</h2>
    <div className="form-row"><input aria-label="版本 ID" placeholder="候选版本 ID" value={versionId} onChange={(event) => setVersionId(event.target.value)} /><input aria-label="对比版本 ID" placeholder="可选：父版本 ID" value={against} onChange={(event) => setAgainst(event.target.value)} /><button onClick={load}>读取</button></div>
    {error && <ErrorNotice message={error} />}{notice && <p className="notice">{notice}</p>}
    {!version ? <Empty>输入版本 ID 查看定义、来源和门禁。</Empty> : <>
      <div className="hero-line"><h3>版本 v{version.version}</h3><Badge value={version.lifecycle_status} /></div>
      <p className="mono">{version.content_hash}</p>
      <div className="actions">{version.lifecycle_status === "review_required" && <><button onClick={() => review("approve")}>批准并发布</button><button className="danger" onClick={() => review("reject")}>拒绝</button></>}{version.lifecycle_status === "retired" && <button className="danger" onClick={rollback}>回滚到此版本</button>}</div>
      <h3>结构化差异</h3>{changes.length === 0 ? <Empty>没有加载对比，或定义没有变化。</Empty> : <pre>{JSON.stringify(changes, null, 2)}</pre>}
      <h3>来源</h3><pre>{JSON.stringify(version.sources, null, 2)}</pre>
      <h3>QualityGate</h3>{!version.gate_report ? <Empty>尚无门禁报告。</Empty> : <table><thead><tr><th>层</th><th>规则</th><th>结果</th></tr></thead><tbody>{version.gate_report.checks.map((check) => <tr key={check.name}><td>{check.layer}</td><td>{check.name}</td><td><Badge value={check.passed ? "passed" : "failed"} /></td></tr>)}</tbody></table>}
    </>}
  </section>;
}
