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
<title>Practice Outreach</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  background: #f7f7f5;
  color: #1a1a1a;
  min-height: 100vh;
  padding: 40px 16px 60px;
}

.page { max-width: 780px; margin: 0 auto; }

.header { margin-bottom: 28px; }
.header h1 { font-size: 18px; font-weight: 600; color: #111; letter-spacing: -0.01em; }
.header p  { margin-top: 4px; font-size: 13px; color: #888; }

/* Input section */
.card {
  background: #fff;
  border: 1px solid #e5e5e3;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

label { display: block; font-size: 12px; font-weight: 500; color: #555; margin-bottom: 6px; }

textarea {
  width: 100%;
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 10px 12px;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.5;
  color: #111;
  resize: vertical;
  min-height: 76px;
  outline: none;
  transition: border-color 0.12s;
  background: #fff;
}
textarea:focus { border-color: #aaa; }
textarea::placeholder { color: #bbb; }

.actions { display: flex; align-items: center; gap: 8px; margin-top: 10px; }

.btn {
  display: inline-flex; align-items: center; gap: 5px;
  border: none; border-radius: 6px; padding: 7px 14px;
  font-family: inherit; font-size: 13px; font-weight: 500;
  cursor: pointer; transition: opacity 0.12s; white-space: nowrap;
}
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-primary { background: #111; color: #fff; }
.btn-primary:not(:disabled):hover { background: #333; }
.btn-secondary { background: #f0f0ee; color: #444; }
.btn-secondary:not(:disabled):hover { background: #e5e5e3; }

.hint { margin-left: auto; font-size: 11px; color: #bbb; }

/* Status bar */
.status-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0; margin-bottom: 8px;
}
.status-left { display: flex; align-items: center; gap: 7px; }
.dot {
  width: 7px; height: 7px; border-radius: 50%; background: #ccc;
  flex-shrink: 0; transition: background 0.2s;
}
.dot.running { background: #111; animation: blink 1.4s ease-in-out infinite; }
.dot.done    { background: #22c55e; }
.dot.error   { background: #ef4444; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }

.status-label { font-size: 12px; color: #888; }
.turn-count   { font-size: 12px; color: #bbb; }

/* Activity feed */
#feed {
  background: #fff;
  border: 1px solid #e5e5e3;
  border-radius: 8px;
  height: 520px;
  overflow-y: auto;
  padding: 12px 0;
}
#feed::-webkit-scrollbar { width: 4px; }
#feed::-webkit-scrollbar-thumb { background: #e0e0e0; border-radius: 2px; }

.row-status {
  padding: 2px 16px;
  font-size: 11px;
  color: #bbb;
  font-style: italic;
}
.row-text {
  padding: 1px 16px;
  font-size: 13.5px;
  line-height: 1.6;
  color: #222;
}
.row-tool {
  margin: 8px 12px 2px;
  padding: 7px 10px;
  background: #f7f7f5;
  border: 1px solid #e8e8e6;
  border-radius: 6px;
}
.tool-name {
  font-size: 11px;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tool-input {
  font-family: "SF Mono", "Fira Mono", monospace;
  font-size: 11px;
  color: #888;
  margin-top: 2px;
  word-break: break-all;
}
.row-result {
  margin: 0 12px 6px;
  padding: 4px 10px;
  font-family: "SF Mono", "Fira Mono", monospace;
  font-size: 11px;
  color: #aaa;
  word-break: break-all;
}
.row-done {
  margin: 10px 12px 4px;
  padding: 8px 12px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #15803d;
}
.row-error {
  margin: 10px 12px 4px;
  padding: 8px 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #dc2626;
}
.empty-state {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #ccc; font-size: 13px;
}
</style>
</head>
<body>
<div class="page">

  <div class="header">
    <h1>Practice Outreach</h1>
    <p>Describe your target — the agent finds practices, checks the CRM, scrapes emails, and routes every contact automatically.</p>
  </div>

  <div class="card">
    <label for="goal">Goal</label>
    <textarea id="goal" rows="3"
      placeholder="Find 25 independent endocrinologists and 25 orthopedic surgeon practices in Texas, private practice only, no hospital or health system affiliation"></textarea>
    <div class="actions">
      <button class="btn btn-primary" id="runBtn" onclick="startRun()">Run</button>
      <button class="btn btn-secondary" id="stopBtn" onclick="stopRun()" disabled>Stop</button>
      <span class="hint">⌘ Enter to run</span>
    </div>
  </div>

  <div class="status-bar">
    <div class="status-left">
      <div class="dot" id="dot"></div>
      <span class="status-label" id="statusLabel">Ready</span>
    </div>
    <span class="turn-count" id="turnCount"></span>
  </div>

  <div id="feed"><div class="empty-state">Output will appear here</div></div>

</div>

<script>
let controller = null;

document.getElementById('goal').addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') startRun();
});

function setStatus(state, label) {
  document.getElementById('dot').className = 'dot ' + state;
  document.getElementById('statusLabel').textContent = label;
}

function addRow(cls, html) {
  const feed = document.getElementById('feed');
  const empty = feed.querySelector('.empty-state');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = cls;
  div.innerHTML = html;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
  return div;
}

// For streamed text we reuse the last row-text div
let lastTextDiv = null;
function appendText(text) {
  const feed = document.getElementById('feed');
  const empty = feed.querySelector('.empty-state');
  if (empty) empty.remove();
  if (!lastTextDiv) {
    lastTextDiv = document.createElement('div');
    lastTextDiv.className = 'row-text';
    feed.appendChild(lastTextDiv);
  }
  lastTextDiv.textContent += text;
  feed.scrollTop = feed.scrollHeight;
}
function flushText() { lastTextDiv = null; }

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function startRun() {
  const goal = document.getElementById('goal').value.trim();
  if (!goal) return;

  document.getElementById('feed').innerHTML = '<div class="empty-state">Starting…</div>';
  document.getElementById('turnCount').textContent = '';
  lastTextDiv = null;
  setStatus('running', 'Running');
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
      addRow('row-error', 'HTTP ' + resp.status);
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
      const lines = buf.split('\\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        handleEvent(ev);
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      flushText();
      addRow('row-error', 'Connection error: ' + esc(err.message));
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
    flushText();
    setStatus('', 'Stopped');
  }
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'text':
      appendText(ev.content);
      break;
    case 'tool_call':
      flushText();
      addRow('row-tool',
        '<div class="tool-name">' + esc(ev.name) + '</div>' +
        '<div class="tool-input">' + esc(ev.input_preview) + '</div>');
      break;
    case 'tool_result':
      addRow('row-result', esc(ev.preview));
      break;
    case 'status':
      flushText();
      addRow('row-status', esc(ev.message));
      break;
    case 'done':
      flushText();
      addRow('row-done', '&#10003; Done &mdash; ' + ev.turns + ' turn' + (ev.turns === 1 ? '' : 's'));
      document.getElementById('turnCount').textContent = ev.turns + ' turns';
      setStatus('done', 'Done');
      break;
    case 'error':
      flushText();
      addRow('row-error', esc(ev.message));
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
