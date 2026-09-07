"""不依赖前端框架的本地 Trace Viewer。"""

from fastapi.responses import HTMLResponse

VIEWER_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>EvoAgent Trace</title>
<style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 16px}
input{width:70%;padding:8px}button{padding:8px 16px}
pre{background:#111;color:#eee;padding:16px;overflow:auto}</style>
</head><body><h1>EvoAgent Trace Viewer</h1>
<p>输入 Run ID，查看数据库中已经提交的完整 Trace。</p>
<input id="run" placeholder="Run ID"><button onclick="loadTrace()">查看</button>
<pre id="out">等待查询</pre>
<script>async function loadTrace(){const id=document.getElementById('run').value.trim();
const out=document.getElementById('out');if(!id){out.textContent='请输入 Run ID';return;}
const response=await fetch('/api/v1/runs/'+encodeURIComponent(id)+'/trace');
out.textContent=JSON.stringify(await response.json(),null,2);}</script></body></html>"""


def trace_viewer() -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)
