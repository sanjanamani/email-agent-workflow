"""
server.py — FastAPI web frontend for the practice outreach agent.

Usage:
    python server.py           # starts on http://localhost:8000
    PORT=8080 python server.py

Opens a browser UI where you type a goal and watch the agent work in real time.
The agent output streams back as Server-Sent Events so you see each token and
every tool call as it happens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from agent import run_agent_stream

log = logging.getLogger(__name__)
app = FastAPI()
_executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    goal: str


@app.post("/run")
async def run(req: RunRequest):
    """
    Start the agent and stream events back as Server-Sent Events.
    Each event is a JSON object on a `data:` line.
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _producer():
        try:
            for event in run_agent_stream(req.goal):
                asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(exc)}), loop
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    loop.run_in_executor(_executor, _producer)

    async def _event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Frontend (inline HTML — no separate build step needed)
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Practice Outreach Agent</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Inter', system-ui, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 2rem 1rem;
  }

  .container { width: 100%; max-width: 860px; }

  h1 {
    font-size: 1.5rem;
    font-weight: 700;
    color: #f1f5f9;
    margin-bottom: 0.25rem;
  }
  .subtitle {
    font-size: 0.875rem;
    color: #64748b;
    margin-bottom: 1.75rem;
  }

  .input-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
  }

  textarea {
    width: 100%;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #e2e8f0;
    font-size: 0.9375rem;
    line-height: 1.6;
    padding: 0.75rem 1rem;
    resize: vertical;
    min-height: 80px;
    outline: none;
    transition: border-color 0.15s;
  }
  textarea:focus { border-color: #38bdf8; }
  textarea::placeholder { color: #475569; }

  .row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-top: 0.75rem;
  }

  button {
    background: #0ea5e9;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 0.6rem 1.4rem;
    font-size: 0.9375rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s;
    white-space: nowrap;
  }
  button:hover:not(:disabled) { background: #38bdf8; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }

  #stopBtn {
    background: #475569;
  }
  #stopBtn:hover:not(:disabled) { background: #64748b; }

  .hint {
    font-size: 0.78rem;
    color: #475569;
    margin-left: auto;
  }

  /* Log */
  .log-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    overflow: hidden;
  }
  .log-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #334155;
    font-size: 0.8125rem;
    color: #64748b;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  #statusDot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #334155;
    display: inline-block;
    margin-right: 0.4rem;
    transition: background 0.3s;
  }
  #statusDot.running { background: #38bdf8; animation: pulse 1.2s infinite; }
  #statusDot.done    { background: #4ade80; animation: none; }
  #statusDot.error   { background: #f87171; animation: none; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.4; }
  }

  #log {
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.8125rem;
    line-height: 1.65;
    padding: 1rem;
    height: 540px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }

  /* Event colours */
  .ev-text   { color: #e2e8f0; }
  .ev-tool   { color: #38bdf8; }
  .ev-result { color: #64748b; }
  .ev-status { color: #94a3b8; font-style: italic; }
  .ev-done   { color: #4ade80; font-weight: 600; }
  .ev-error  { color: #f87171; font-weight: 600; }

  /* Scrollbar */
  #log::-webkit-scrollbar { width: 6px; }
  #log::-webkit-scrollbar-track { background: transparent; }
  #log::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
</style>
</head>
<body>
<div class="container">
  <h1>Practice Outreach Agent</h1>
  <p class="subtitle">Describe what you want — the agent will find practices, check the CRM, scrape emails, and route contacts automatically.</p>

  <div class="input-card">
    <textarea id="goal" rows="3"
      placeholder="Find 25 independent endocrinologists and 25 orthopedic surgeon practices in Texas, private practice only, no hospital or health system affiliation"></textarea>
    <div class="row">
      <button id="runBtn" onclick="startRun()">&#9654; Run Agent</button>
      <button id="stopBtn" onclick="stopRun()" disabled>&#9632; Stop</button>
      <span class="hint">Cmd+Enter to run</span>
    </div>
  </div>

  <div class="log-card">
    <div class="log-header">
      <span><span id="statusDot"></span><span id="statusLabel">Idle</span></span>
      <span id="turnCount"></span>
    </div>
    <div id="log"></div>
  </div>
</div>

<script>
let controller = null;

document.getElementById('goal').addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') startRun();
});

function setStatus(state, label) {
  const dot = document.getElementById('statusDot');
  dot.className = state;
  document.getElementById('statusLabel').textContent = label;
}

function appendLine(cls, text) {
  const log = document.getElementById('log');
  const span = document.createElement('span');
  span.className = cls;
  span.textContent = text;
  log.appendChild(span);
  log.scrollTop = log.scrollHeight;
}

function appendInline(cls, text) {
  const log = document.getElementById('log');
  // Reuse the last span if it has the same class, otherwise create new
  const last = log.lastElementChild;
  if (last && last.className === cls) {
    last.textContent += text;
  } else {
    const span = document.createElement('span');
    span.className = cls;
    span.textContent = text;
    log.appendChild(span);
  }
  log.scrollTop = log.scrollHeight;
}

async function startRun() {
  const goal = document.getElementById('goal').value.trim();
  if (!goal) return;

  // Clear previous output
  document.getElementById('log').innerHTML = '';
  document.getElementById('turnCount').textContent = '';
  setStatus('running', 'Running…');
  document.getElementById('runBtn').disabled = true;
  document.getElementById('stopBtn').disabled = false;

  controller = new AbortController();

  try {
    const resp = await fetch('/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({goal}),
      signal: controller.signal,
    });

    if (!resp.ok) {
      appendLine('ev-error', `HTTP ${resp.status}: ${await resp.text()}\\n`);
      setStatus('error', 'Error');
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});

      // Parse complete SSE lines
      const lines = buf.split('\\n');
      buf = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        handleEvent(ev);
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      appendLine('ev-error', `\\nConnection error: ${err.message}\\n`);
      setStatus('error', 'Error');
    }
  } finally {
    document.getElementById('runBtn').disabled = false;
    document.getElementById('stopBtn').disabled = true;
    controller = null;
  }
}

function stopRun() {
  if (controller) {
    controller.abort();
    appendLine('ev-status', '\\n[Stopped by user]\\n');
    setStatus('', 'Stopped');
  }
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'text':
      appendInline('ev-text', ev.content);
      break;
    case 'tool_call':
      appendLine('ev-tool', `\\n⚙  ${ev.name}(${ev.input_preview})\\n`);
      break;
    case 'tool_result':
      appendLine('ev-result', `   → ${ev.preview}\\n`);
      break;
    case 'status':
      appendLine('ev-status', `${ev.message}\\n`);
      break;
    case 'done':
      appendLine('ev-done', `\\n✓ Done in ${ev.turns} turn(s)\\n`);
      document.getElementById('turnCount').textContent = `${ev.turns} turns`;
      setStatus('done', 'Done');
      break;
    case 'error':
      appendLine('ev-error', `\\n✗ ${ev.message}\\n`);
      setStatus('error', 'Error');
      break;
  }
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting server on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
