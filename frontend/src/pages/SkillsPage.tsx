import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { SkillDetail, SkillSummary } from "../api/types";
import { Badge, Empty, ErrorNotice, Loading } from "../components/State";

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [error, setError] = useState("");

  const reload = () => {
    setError("");
    api.listSkills().then(setSkills).catch((reason: Error) => setError(reason.message));
  };

  useEffect(reload, []);

  const choose = async (id: string) => {
    try {
      setSelected(await api.getSkill(id));
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const status = async (operation: "disable" | "enable" | "deprecate") => {
    if (!selected) return;
    try {
      await api.setStatus(selected.id, operation, selected.lock_version);
      setSelected(await api.getSkill(selected.id));
      reload();
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  return (
    <section className="page-grid">
      <article className="panel">
        <div className="panel-title"><h2>Skill 目录</h2><button onClick={reload}>刷新</button></div>
        {error && <ErrorNotice message={error} />}
        {skills === null ? <Loading /> : skills.length === 0 ? <Empty>还没有 Skill。</Empty> : (
          <ul className="skill-list">
            {skills.map((skill) => (
              <li key={skill.id}>
                <button className="skill-row" onClick={() => choose(skill.id)}>
                  <span><strong>{skill.name}</strong><small>{skill.description}</small></span>
                  <Badge value={skill.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </article>
      <article className="panel">
        <h2>详情与版本</h2>
        {!selected ? <Empty>从左侧选择一个 Skill。</Empty> : (
          <>
            <div className="hero-line"><h3>{selected.slug}</h3><Badge value={selected.status} /></div>
            <p>{selected.description}</p>
            <p className="mono">lock_version: {selected.lock_version}</p>
            <div className="actions">
              {selected.status === "enabled" && <button onClick={() => status("disable")}>临时禁用</button>}
              {selected.status === "disabled" && <button onClick={() => status("enable")}>重新启用</button>}
              {selected.status !== "deprecated" && <button className="danger" onClick={() => status("deprecate")}>弃用</button>}
            </div>
            <table><thead><tr><th>版本</th><th>状态</th><th>哈希</th></tr></thead><tbody>
              {selected.versions.map((version) => <tr key={version.id}><td>v{version.version}</td><td><Badge value={version.lifecycle_status} /></td><td className="mono">{version.content_hash.slice(0, 18)}…</td></tr>)}
            </tbody></table>
          </>
        )}
      </article>
    </section>
  );
}
