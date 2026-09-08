import { useState } from "react";

import { EvalPage } from "./pages/EvalPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SkillsPage } from "./pages/SkillsPage";

type Page = "skills" | "review" | "eval";

export function App() {
  const [page, setPage] = useState<Page>("skills");
  return <><header><div><p className="eyebrow">EVOAGENT / v0.3</p><h1>Skill Lifecycle Lab</h1><p>只展示服务端已持久化的版本、评测和人工决定。</p></div><nav aria-label="主导航"><button className={page === "skills" ? "active" : ""} onClick={() => setPage("skills")}>Skill 目录</button><button className={page === "review" ? "active" : ""} onClick={() => setPage("review")}>版本评审</button><button className={page === "eval" ? "active" : ""} onClick={() => setPage("eval")}>Eval 报告</button></nav></header><main>{page === "skills" ? <SkillsPage /> : page === "review" ? <ReviewPage /> : <EvalPage />}</main><footer>本地演示界面 · 写操作仍由后端状态机与 lock_version 校验</footer></>;
}
