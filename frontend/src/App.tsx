import { useState } from "react";

import { EvalPage } from "./pages/EvalPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SkillsPage } from "./pages/SkillsPage";

import { MemoryPage } from "./pages/MemoryPage";
import { MCPPage } from "./pages/MCPPage";
import { ContextPage } from "./pages/ContextPage";
import { ChatPage } from "./pages/ChatPage";

type Page = "chat" | "skills" | "review" | "eval" | "memory" | "mcp" | "context";

export function App() {
  const [page, setPage] = useState<Page>("chat");
  return <><header><div><p className="eyebrow">EVOAGENT / v0.4</p><h1>Agent Evidence Lab</h1><p>与 Agent 对话，查看事实来源、工具授权与运行证据。</p></div><nav aria-label="主导航"><button className={page === "chat" ? "active" : ""} onClick={() => setPage("chat")}>对话</button><button className={page === "skills" ? "active" : ""} onClick={() => setPage("skills")}>Skill 目录</button><button className={page === "review" ? "active" : ""} onClick={() => setPage("review")}>版本评审</button><button className={page === "eval" ? "active" : ""} onClick={() => setPage("eval")}>Eval 报告</button><button className={page === "memory" ? "active" : ""} onClick={() => setPage("memory")}>Memory 管理</button><button className={page === "mcp" ? "active" : ""} onClick={() => setPage("mcp")}>MCP 管理</button><button className={page === "context" ? "active" : ""} onClick={() => setPage("context")}>上下文证据</button></nav></header><main>{page === "chat" ? <ChatPage /> : page === "skills" ? <SkillsPage /> : page === "review" ? <ReviewPage /> : page === "eval" ? <EvalPage /> : page === "memory" ? <MemoryPage /> : page === "mcp" ? <MCPPage /> : <ContextPage />}</main><footer>本地演示界面 · 任务和会话由后端持久化</footer></>;
}
