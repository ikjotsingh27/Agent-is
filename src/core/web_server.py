"""Password-protected local/LAN web interface for Pilot, using only Python's standard library.

Security:
- Off by default until the user sets a password and turns Web access on in Settings.
- Passwords are stored only as salted PBKDF2-HMAC-SHA256 hashes (never plaintext).
- Brute-force protection: 5 wrong passwords lock that client IP for 60 seconds.
- Authenticated sessions use random 32-byte HttpOnly + SameSite=Strict cookies, plus an X-Pilot-Client
  header check on state-changing requests.
- File downloads are strictly confined to data/agent/files/ via Workspace.path(); settings.json and API keys
  are never exposed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import secrets
import socket
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .workspace import MAX_INCOMING_FILE_BYTES, WorkspaceError, file_size_label

PBKDF2_ROUNDS = 200_000
MAX_LOGIN_FAILS = 5
LOCKOUT_SECONDS = 60.0
SESSION_TTL_SECONDS = 24 * 3600


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return salt, digest


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    if not password or not salt_hex or not hash_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(candidate, hash_hex)


def local_ip() -> str:
    """Best-effort LAN IPv4 address for opening Pilot from a phone on the same Wi-Fi."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


WEB_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pilot</title>
<style>
:root {
  --bg: #15171c; --sidebar: #1b1e24; --card: #21252d; --border: #2c313b;
  --field: #191c22; --text: #e9ebef; --muted: #9aa1ad; --accent: #7d9bff;
  --on-accent: #0f1115; --ok: #3ecf8e; --warn: #f2b544; --bad: #f06464; --nav: #2a2f3a;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: "Segoe UI", system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; overflow: hidden; }
button, input, textarea { font-family: inherit; color: var(--text); }
.hidden { display: none !important; }

/* Login */
#login-wrap { margin: auto; width: 100%; max-width: 380px; padding: 20px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
h1, h2, h3 { margin: 0 0 8px 0; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; margin-top: 8px; }
.ok { color: var(--ok); font-size: 13px; }
.warn { color: var(--warn); font-size: 13px; }
input[type="password"], input[type="text"], textarea {
  width: 100%; background: var(--field); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; font-size: 14px; outline: none;
}
input:focus, textarea:focus { border-color: var(--accent); }
.btn { background: var(--accent); color: var(--on-accent); border: none; border-radius: 8px; padding: 9px 16px; font-weight: 600; cursor: pointer; font-size: 14px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-sec { background: var(--nav); color: var(--text); border: none; border-radius: 8px; padding: 8px 13px; font-weight: 600; cursor: pointer; font-size: 13px; }
.btn-sec:hover { background: var(--border); }

/* App layout */
#app { display: flex; width: 100%; height: 100vh; }
#sidebar { width: 250px; background: var(--sidebar); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding: 16px 12px; gap: 6px; flex-shrink: 0; }
.brand { font-size: 20px; font-weight: 700; padding: 4px 10px 10px 10px; display: flex; justify-content: space-between; align-items: center; }
.nav-btn { width: 100%; text-align: left; background: transparent; border: none; color: var(--text); padding: 9px 12px; border-radius: 8px; cursor: pointer; font-size: 14px; display: flex; justify-content: space-between; align-items: center; }
.nav-btn.active { background: var(--nav); font-weight: 600; }
.badge { background: var(--warn); color: #0f1115; border-radius: 999px; font-size: 11px; font-weight: 700; padding: 1px 7px; }
.section-lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); padding: 10px 10px 4px 10px; }
#session-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 3px; }
.sess-row { display: flex; align-items: center; border-radius: 7px; padding: 2px 4px; }
.sess-row.active { background: var(--nav); }
.sess-name { flex: 1; background: none; border: none; color: var(--text); text-align: left; padding: 7px 8px; font-size: 13px; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-del { background: none; border: none; color: var(--muted); cursor: pointer; padding: 4px 7px; border-radius: 5px; font-size: 14px; }
.sess-del:hover { color: var(--bad); background: var(--border); }

#main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.screen { flex: 1; display: flex; flex-direction: column; min-height: 0; padding: 20px 26px; }
.header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; background: var(--ok); }
.status-dot.busy { background: var(--warn); }
.status-dot.paused { background: var(--bad); }
.btn-danger { background: var(--bad); color: #ffffff; border: none; border-radius: 8px; padding: 9px 13px; font-weight: 700; cursor: pointer; font-size: 13px; }
.btn-ok { background: var(--ok); color: #0f1115; border: none; border-radius: 8px; padding: 9px 13px; font-weight: 700; cursor: pointer; font-size: 13px; }

/* Chat */
#chat-log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; padding-right: 6px; }
.msg { display: flex; flex-direction: column; gap: 4px; max-width: 820px; }
.who { font-weight: 700; font-size: 13px; }
.who.you { color: #a9bcff; }
.who.pilot { color: var(--accent); }
.body { white-space: pre-wrap; line-height: 1.5; font-size: 14px; }
.meta { font-size: 12px; color: var(--muted); }

/* Thinking steps accordion */
.steps-box { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin: 4px 0; font-size: 13px; }
.steps-box summary { cursor: pointer; color: var(--muted); font-weight: 600; list-style: none; display: flex; align-items: center; gap: 6px; }
.steps-box summary::-webkit-details-marker { display: none; }
.step-list { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; border-left: 2px solid var(--border); padding-left: 10px; }
.step-item { display: flex; flex-direction: column; gap: 2px; }
.step-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
.step-icon { margin-right: 6px; font-weight: 700; }
.step-icon.running { color: var(--warn); }
.step-icon.done { color: var(--ok); }
.step-icon.error { color: var(--bad); }
.step-detail { color: var(--muted); font-size: 12px; margin-left: 18px; white-space: pre-wrap; }

/* File cards & composer */
.file-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 6px; max-width: 560px; }
.file-card a { text-decoration: none; }
#chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.chip { background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 4px 10px; font-size: 12px; display: flex; align-items: center; gap: 6px; }
.chip button { background: none; border: none; color: var(--muted); cursor: pointer; font-weight: 700; }
#composer { margin-top: 10px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; display: flex; align-items: center; gap: 10px; }
#composer textarea { border: none; background: transparent; padding: 6px 4px; resize: none; min-height: 24px; max-height: 110px; line-height: 1.4; flex: 1; }
#plus-btn { width: 36px; height: 36px; border-radius: 8px; background: var(--nav); color: var(--text); border: none; font-size: 20px; font-weight: 600; cursor: pointer; flex-shrink: 0; }
#plus-btn:hover { background: var(--border); }

/* Approvals & Memory */
.scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; max-width: 780px; }
.cmd-box { background: var(--field); padding: 9px 11px; border-radius: 6px; font-family: Consolas, monospace; font-size: 13px; white-space: pre-wrap; margin: 6px 0; }
.approval-card { border: 2px solid var(--warn); }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }

@media (max-width: 760px) {
  #sidebar { width: 190px; }
  .screen { padding: 14px; }
}
</style>
</head>
<body>
<div id="login-wrap">
  <div class="card">
    <h1>Pilot</h1>
    <p class="muted" style="margin-bottom:14px">Enter your Pilot web password to connect.</p>
    <form id="login-form">
      <input id="pw-input" type="password" placeholder="Password" autocomplete="current-password" required>
      <div id="login-err" class="err"></div>
      <div style="margin-top:14px"><button class="btn" style="width:100%" type="submit">Sign in</button></div>
    </form>
  </div>
</div>

<div id="app" class="hidden">
  <aside id="sidebar">
    <div class="brand">
      <span>Pilot</span>
    </div>
    <button class="nav-btn active" data-tab="chat">Chat</button>
    <button class="nav-btn" data-tab="workspace">Workspace &amp; Terminal</button>
    <button class="nav-btn" data-tab="approvals"><span>Approvals</span><span id="ap-badge" class="badge hidden">0</span></button>
    <button class="nav-btn" data-tab="memory">Memory</button>
    <button class="nav-btn" data-tab="activity">Activity</button>
    <button class="nav-btn" data-tab="settings">Settings</button>
    <div style="flex:1"></div>
    <button class="btn-danger" id="pause-btn" style="margin-top:6px">Pause All</button>
    <button class="btn-sec" id="logout-btn" style="margin-top:4px">Sign out</button>
  </aside>

  <main id="main">
    <section id="tab-chat" class="screen">
      <div class="header">
        <div>
          <h2 id="chat-title" style="display:inline">Chat</h2>
          <span class="muted" style="margin-left:12px"><span id="status-dot" class="status-dot"></span><span id="status-txt">Ready</span></span>
        </div>
        <button class="btn-sec" id="clear-chat-btn" type="button">New chat</button>
      </div>
      <div id="chat-log"></div>
      <div id="live-steps" class="hidden"></div>
      <div id="chips"></div>
      <div id="composer">
        <button id="plus-btn" type="button" title="Attach any file">+</button>
        <button id="mic-btn" class="btn-sec" type="button" title="Record a voice note">Mic</button>
        <input id="file-picker" type="file" multiple class="hidden">
        <textarea id="msg-input" rows="1" placeholder="Message Pilot... (Enter sends, Shift+Enter new line)"></textarea>
        <button id="send-btn" class="btn" type="button">Send</button>
      </div>
    </section>

    <section id="tab-workspace" class="screen hidden">
      <div class="header">
        <div>
          <h2 style="display:inline">Workspace &amp; Terminal</h2>
          <span id="ws-meta" class="muted" style="margin-left:12px"></span>
        </div>
        <button class="btn-sec" id="ws-upload-btn" type="button">+ Upload file</button>
        <input id="ws-file-picker" type="file" multiple class="hidden">
      </div>
      <div class="scroll" style="max-width:920px">
        <div class="card">
          <h3>Live Terminal (isolated workspace)</h3>
          <p class="muted" style="margin:0 0 8px 0">Runs inside Pilot's workspace folder with the 2 GB RAM cap, 2-minute timeout, and isolated workspace Python when enabled.</p>
          <div style="display:flex;gap:8px">
            <input id="term-input" type="text" placeholder="Type a command (e.g. dir, python --version, ls)..." style="font-family:Consolas,monospace">
            <button id="term-run" class="btn" type="button">Run</button>
          </div>
          <pre id="term-out" class="cmd-box" style="margin-top:10px;min-height:110px;max-height:280px;overflow:auto">Ready.</pre>
        </div>
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <h3>Workspace files</h3>
            <span id="ws-path-label" class="muted"></span>
          </div>
          <div id="ws-files-list" style="display:flex;flex-direction:column;gap:6px;margin-top:8px"></div>
        </div>
      </div>
    </section>

    <section id="tab-approvals" class="screen hidden">
      <div class="header"><h2>Approvals</h2></div>
      <div id="approvals-body" class="scroll"></div>
    </section>

    <section id="tab-memory" class="screen hidden">
      <div class="header"><h2>Memory</h2></div>
      <div class="scroll">
        <div class="card">
          <h3>Add or edit a note</h3>
          <input id="mem-title" type="text" placeholder="Title (e.g. Preferences)" style="margin:8px 0">
          <textarea id="mem-body" rows="3" placeholder="Facts to remember..."></textarea>
          <div class="row">
            <button class="btn" id="mem-save" type="button">Save note</button>
            <span id="mem-status" class="muted"></span>
          </div>
        </div>
        <div id="memory-list" style="display:flex;flex-direction:column;gap:10px"></div>
      </div>
    </section>

    <section id="tab-activity" class="screen hidden">
      <div class="header"><h2>Activity &amp; Health</h2></div>
      <div class="scroll">
        <div class="card">
          <h3>Connector health</h3>
          <div id="health-pills" class="row" style="margin-top:8px"></div>
        </div>
        <div class="card">
          <h3>Today's summary</h3>
          <div id="daily-summary" class="muted" style="margin-top:6px;white-space:pre-wrap"></div>
        </div>
        <input id="act-filter" type="text" placeholder="Filter activity...">
        <div id="activity-list" style="display:flex;flex-direction:column;gap:8px"></div>
      </div>
    </section>

    <section id="tab-settings" class="screen hidden">
      <div class="header"><h2>Settings &amp; Usage</h2></div>
      <div class="scroll">
        <div class="card">
          <h3>Model &amp; Environment</h3>
          <div id="set-provider" class="muted" style="margin-top:4px"></div>
          <div id="set-web-urls" class="ok" style="margin-top:6px;word-break:break-all"></div>
        </div>
        <div class="card">
          <h3>Usage &amp; System Resources</h3>
          <div id="set-alerts" class="warn" style="margin-bottom:6px"></div>
          <div id="set-usage" class="body" style="font-size:13px"></div>
        </div>
        <div class="card">
          <h3>Daily &amp; Workspace Limits</h3>
          <div class="row">
            <label class="muted">Daily tokens <input id="lim-tok" type="text" style="width:120px"></label>
            <label class="muted">Daily $ <input id="lim-usd" type="text" style="width:80px"></label>
            <label class="muted">Workspace GB <input id="lim-gb" type="text" style="width:80px"></label>
            <button id="lim-save" class="btn" type="button">Save limits</button>
          </div>
          <div id="lim-msg" class="muted" style="margin-top:6px"></div>
        </div>
      </div>
    </section>
  </main>
</div>

<script>
const H = {"X-Pilot-Client": "web"};
let state = null;
let pendingUploads = [];
let activeTab = "chat";
let lastRenderKey = "";

async function api(url, opts = {}) {
  opts.headers = Object.assign({}, H, opts.headers || {});
  const res = await fetch(url, opts);
  if (res.status === 401 && url !== "/api/login") { showLogin(); throw new Error("unauthorized"); }
  return res;
}
function esc(s) {
  return String(s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
function showLogin() {
  document.getElementById("login-wrap").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
}
function showApp() {
  document.getElementById("login-wrap").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
}

function renderStepsHtml(steps, openByDefault) {
  if (!steps || !steps.length) return "";
  const totalSec = steps.reduce((a, s) => a + (Number(s.seconds) || 0), 0);
  const running = steps.some(s => s.status === "running");
  const summary = running
    ? `Working... (${steps.length} step${steps.length === 1 ? "" : "s"})`
    : `Thought &amp; steps (${steps.length} step${steps.length === 1 ? "" : "s"}${totalSec > 0 ? " · " + totalSec.toFixed(1) + "s" : ""})`;
  const rows = steps.map(s => {
    const icon = s.status === "running" ? "●" : (s.status === "error" ? "✕" : "✓");
    const sec = s.seconds ? `<span class="muted">${Number(s.seconds).toFixed(1)}s</span>` : "";
    const detail = s.detail ? `<div class="step-detail">${esc(s.detail)}</div>` : "";
    return `<div class="step-item"><div class="step-head"><span><span class="step-icon ${esc(s.status)}">${icon}</span>${esc(s.label)}</span>${sec}</div>${detail}</div>`;
  }).join("");
  return `<details class="steps-box"${openByDefault ? " open" : ""}><summary>▸ ${summary}</summary><div class="step-list">${rows}</div></details>`;
}

function render(s) {
  state = s;
  const apCount = (s.pending_approvals || []).length;
  const badge = document.getElementById("ap-badge");
  badge.textContent = String(apCount);
  badge.classList.toggle("hidden", apCount === 0);

  // Status & Pause All
  const dot = document.getElementById("status-dot");
  const stxt = document.getElementById("status-txt");
  const pbtn = document.getElementById("pause-btn");
  dot.classList.toggle("paused", !!s.paused);
  dot.classList.toggle("busy", !s.paused && !!s.busy);
  stxt.textContent = s.paused ? "Paused (Pause All)" : (s.busy ? (s.activity || "Thinking...") : "Ready");
  document.getElementById("send-btn").disabled = !!s.busy || !!s.paused;
  pbtn.textContent = s.paused ? "Resume Pilot" : "Pause All";
  pbtn.className = s.paused ? "btn-ok" : "btn-danger";

  // Live steps panel
  const live = document.getElementById("live-steps");
  if (s.busy && s.live_steps && s.live_steps.length) {
    live.classList.remove("hidden");
    live.innerHTML = renderStepsHtml(s.live_steps, true);
  } else {
    live.classList.add("hidden");
    live.innerHTML = "";
  }

  // Chat log (re-render only when messages changed)
  const key = JSON.stringify(s.messages || []);
  if (key !== lastRenderKey) {
    lastRenderKey = key;
    const log = document.getElementById("chat-log");
    log.innerHTML = (s.messages || []).map(m => {
      const isYou = m.who === "you" || m.who === "you_phone";
      const label = isYou ? ("You" + (m.who === "you_phone" && m.extra ? ` (on ${esc(m.extra)})` : "")) : (m.who === "pilot" ? "Pilot" : "Problem");
      const cls = isYou ? "you" : (m.who === "pilot" ? "pilot" : "err");
      const stepsHtml = renderStepsHtml(m.steps, false);
      const filesHtml = (m.files || []).map(f => `
        <div class="file-card">
          <div><b>${esc(f.name)}</b> <span class="muted">(${esc(f.size_label || "")})</span>${f.note ? `<div class="muted">${esc(f.note)}</div>` : ""}</div>
          <a class="btn-sec" href="/api/files?path=${encodeURIComponent(f.rel_path)}" download>Download</a>
        </div>
      `).join("");
      const metaHtml = (!isYou && m.extra) ? `<div class="meta">${esc(m.extra)}</div>` : "";
      return `<div class="msg"><div class="who ${cls}">${label}</div>${stepsHtml}<div class="body">${esc(m.text)}</div>${metaHtml}${filesHtml}</div>`;
    }).join("");
    log.scrollTop = log.scrollHeight;
  }

  // Approvals tab
  const ab = document.getElementById("approvals-body");
  let aHtml = `<h3>Waiting for your OK (${apCount})</h3>`;
  if (!apCount) aHtml += `<div class="card muted">Nothing is waiting right now.</div>`;
  for (const item of (s.pending_approvals || [])) {
    const via = item.connector || "Terminal";
    const heading = via === "Gmail" ? "Pilot wants to send an email" : "Pilot wants to run this command";
    aHtml += `<div class="card approval-card">
      <div style="display:flex;justify-content:space-between;gap:8px"><b>${heading}</b><span class="muted">Via: ${esc(via)}</span></div>
      <div class="cmd-box">${esc(item.action)}</div>
      <div>Why: ${esc(item.reason)}</div>
      ${item.risks && item.risks.length ? `<div class="muted">Needs your OK because: ${esc(item.risks.join("; "))}.</div>` : ""}
      ${item.flag ? `<div class="warn" style="font-weight:600;margin-top:6px">${esc(item.flag)}</div>` : ""}
      <div class="row">
        <button class="btn" onclick="resolveApproval('${esc(item.id)}','once')">Approve</button>
        <button class="btn-sec" onclick="resolveApproval('${esc(item.id)}','deny')">Deny</button>
        ${item.rule ? `<button class="btn-sec" onclick="resolveApproval('${esc(item.id)}','always')">Always allow "${esc(item.rule)}"</button>` : ""}
      </div>
    </div>`;
  }
  aHtml += `<h3 style="margin-top:10px">Recent decisions</h3>`;
  if (!(s.approval_history || []).length) aHtml += `<div class="card muted">No decisions yet in this session.</div>`;
  for (const item of (s.approval_history || [])) {
    aHtml += `<div class="card"><b class="${item.status === "deny" ? "err" : "ok"}">${esc(item.status)}</b> <span class="muted">${esc(item.created_at || "")}</span><div class="cmd-box">${esc(item.action)}</div><div class="muted">Why: ${esc(item.reason)}</div></div>`;
  }
  ab.innerHTML = aHtml;

  // Memory tab
  const ml = document.getElementById("memory-list");
  if (!(s.memory || []).length) {
    ml.innerHTML = `<div class="card muted">No memory notes yet.</div>`;
  } else {
    ml.innerHTML = (s.memory || []).map(n => `
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div><b>${esc(n.title)}</b> <span class="muted">(${esc(n.slug)}.md)</span></div>
          <div>
            <button class="btn-sec" onclick="editNote(${JSON.stringify(n.title).replace(/"/g, '&quot;')}, ${JSON.stringify(n.content).replace(/"/g, '&quot;')})">Edit</button>
            <button class="btn-sec" onclick="deleteNote('${esc(n.slug)}')">Delete</button>
          </div>
        </div>
        <div class="body" style="margin-top:8px">${esc(n.content)}</div>
      </div>
    `).join("");
  }

  // Activity & Connector health tab
  const hp = document.getElementById("health-pills");
  if (hp) {
    hp.innerHTML = (s.connectors || []).map(c => {
      const cls = c.health === "green" ? "ok" : (c.health === "red" ? "err" : (c.health === "yellow" ? "warn" : "muted"));
      const dot = c.health === "none" ? "○" : "●";
      return `<span class="chip ${cls}">${dot} ${esc(c.name)}: ${esc(c.status)}</span>`;
    }).join("") || `<span class="muted">No connectors loaded.</span>`;
  }
  const ds = document.getElementById("daily-summary");
  if (ds) {
    const lines = s.daily_summary || [];
    ds.textContent = lines.length ? lines.slice(-8).reverse().map(x => "• " + x).join("\n") : "Nothing recorded today yet.";
  }
  const al = document.getElementById("activity-list");
  if (al) {
    const q = (document.getElementById("act-filter")?.value || "").trim().toLowerCase();
    const rows = (s.activity_log || []).filter(e => !q || `${e.source} ${e.kind} ${e.summary}`.toLowerCase().includes(q));
    al.innerHTML = rows.length ? rows.map(e => `
      <div class="card" style="${e.long_run ? "border-color:var(--warn)" : ""}">
        <div style="display:flex;justify-content:space-between;gap:8px">
          <b>${esc(e.start_time)}${e.end_time && e.end_time !== e.start_time ? " to " + esc(e.end_time) : ""} · ${esc(e.source)}</b>
          <span class="${e.long_run ? "warn" : "muted"}">${e.seconds ? e.seconds.toFixed(1) + "s" : ""}${e.tokens ? " · " + e.tokens + " tok" : ""}${e.long_run ? " · ran long" : ""}</span>
        </div>
        <div class="body" style="margin-top:4px">${esc(e.summary)}</div>
      </div>
    `).join("") : `<div class="card muted">No matching activity yet.</div>`;
  }

  // Workspace tab
  const wm = document.getElementById("ws-meta");
  if (wm && s.workspace) {
    wm.textContent = `${s.workspace.summary}${s.workspace.has_venv ? " · Isolated Python: On" : ""}`;
  }
  const wf = document.getElementById("ws-files-list");
  if (wf && s.workspace) {
    const items = s.workspace.files || [];
    wf.innerHTML = items.length ? items.map(f => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 8px;background:var(--field);border-radius:6px">
        <div><b>${f.is_dir ? "📁 " : ""}${esc(f.rel_path)}</b> <span class="muted">(${esc(f.size_label)})</span></div>
        <div style="display:flex;gap:6px">
          ${f.is_dir ? "" : `<a class="btn-sec" href="/api/files?path=${encodeURIComponent(f.rel_path)}" download style="text-decoration:none">Download</a>`}
          ${f.is_dir ? "" : `<button class="btn-sec" onclick="deleteWsFile('${esc(f.rel_path)}')">Delete</button>`}
        </div>
      </div>
    `).join("") : `<div class="muted">Workspace is empty. Upload files or ask Pilot to create something.</div>`;
  }

  // Settings tab
  const sp = document.getElementById("set-provider");
  if (sp && s.provider) {
    sp.textContent = `Provider: ${s.provider.name} · Main: ${s.provider.main_model} · Cheap: ${s.provider.cheap_model}`;
  }
  const su = document.getElementById("set-web-urls");
  if (su && s.web) {
    su.textContent = s.web.public_url ? `Public HTTPS link: ${s.web.public_url}` : (`Local: ${(s.web.urls || []).join(" | ")}`);
  }
  const sa = document.getElementById("set-alerts");
  if (sa && s.usage) {
    sa.textContent = (s.usage.alerts || []).join(" · ");
  }
  const sus = document.getElementById("set-usage");
  if (sus && s.usage) {
    const r = s.usage.resources || {};
    const t = s.usage.today || {};
    const w = s.usage.week || {};
    sus.textContent = `Today: ${t.calls || 0} calls · ${(t.total_tokens || 0).toLocaleString()} tokens · $${(t.usd || 0).toFixed(4)}\n`
      + `Last 7 days: ${w.calls || 0} calls · ${(w.total_tokens || 0).toLocaleString()} tokens · $${(w.usd || 0).toFixed(4)}\n`
      + `Resources: RAM ${r.ram_mb || 0} MB (${r.ram_pct || 0}%) · CPU ${r.cpu_pct || 0}%`
      + (r.gpu_pct !== null && r.gpu_pct !== undefined ? ` · GPU ${r.gpu_pct}%` : "")
      + ` · Workspace disk ${r.disk_mb || 0} MB (${r.disk_pct || 0}%)`;
    const lt = document.getElementById("lim-tok");
    if (lt && document.activeElement !== lt && document.activeElement !== document.getElementById("lim-usd") && document.activeElement !== document.getElementById("lim-gb") && s.usage.limits) {
      lt.value = String(s.usage.limits.daily_tokens);
      document.getElementById("lim-usd").value = String(s.usage.limits.daily_usd);
      document.getElementById("lim-gb").value = String(s.usage.limits.max_disk_gb);
    }
  }
}

async function poll() {
  try {
    const res = await api("/api/state");
    if (res.ok) render(await res.json());
  } catch (e) {}
}

function renderChips() {
  const box = document.getElementById("chips");
  box.innerHTML = pendingUploads.map((f, i) => `
    <span class="chip">${esc(f.name)} (${esc(f.size_label)}) <button onclick="removeUpload(${i})">×</button></span>
  `).join("");
}
window.removeUpload = (i) => { pendingUploads.splice(i, 1); renderChips(); };

async function sendMessage() {
  const ta = document.getElementById("msg-input");
  const text = ta.value.trim();
  if (!text && !pendingUploads.length) return;
  const rels = pendingUploads.map(u => u.rel_path);
  ta.value = "";
  ta.style.height = "auto";
  pendingUploads = [];
  renderChips();
  await api("/api/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text, attachments: rels}),
  });
  await poll();
}

window.selectSession = async (id) => {
  await api("/api/sessions/select", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id})});
  switchTab("chat");
  await poll();
};
window.deleteSession = async (id) => {
  await api("/api/sessions/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id})});
  await poll();
};
window.resolveApproval = async (id, choice) => {
  await api("/api/approvals/resolve", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id, choice})});
  await poll();
};
window.editNote = (title, content) => {
  document.getElementById("mem-title").value = title;
  document.getElementById("mem-body").value = content;
};
window.deleteNote = async (slug) => {
  await api("/api/memory/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({slug})});
  await poll();
};

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  ["chat", "workspace", "approvals", "memory", "activity", "settings"].forEach(t => document.getElementById("tab-" + t).classList.toggle("hidden", t !== tab));
}
window.deleteWsFile = async (rel) => {
  if (!confirm("Delete " + rel + " from workspace?")) return;
  await api("/api/workspace/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({path: rel})});
  await poll();
};
async function runTerminal() {
  const inp = document.getElementById("term-input");
  const out = document.getElementById("term-out");
  const cmd = inp.value.trim();
  if (!cmd) return;
  inp.value = "";
  out.textContent += `\n$ ${cmd}\n...`;
  out.scrollTop = out.scrollHeight;
  const res = await api("/api/workspace/terminal", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({command: cmd}),
  });
  const data = await res.json().catch(() => ({}));
  const body = data.error ? `Error: ${data.error}` : `${data.output || "(no output)"}\n[exit ${data.exit_code}]`;
  out.textContent = out.textContent.replace(/\.\.\.$/, "") + body + "\n";
  out.scrollTop = out.scrollHeight;
  await poll();
}
document.getElementById("term-run").addEventListener("click", runTerminal);
document.getElementById("term-input").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); runTerminal(); } });
document.getElementById("ws-upload-btn").addEventListener("click", () => document.getElementById("ws-file-picker").click());
document.getElementById("ws-file-picker").addEventListener("change", async (e) => {
  for (const file of e.target.files) {
    await api("/api/upload", {
      method: "POST",
      headers: {"X-File-Name": encodeURIComponent(file.name), "Content-Type": "application/octet-stream"},
      body: await file.arrayBuffer(),
    });
  }
  e.target.value = "";
  await poll();
});
document.getElementById("lim-save").addEventListener("click", async () => {
  const res = await api("/api/settings/limits", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      daily_tokens: document.getElementById("lim-tok").value,
      daily_usd: document.getElementById("lim-usd").value,
      max_disk_gb: document.getElementById("lim-gb").value,
    }),
  });
  const d = await res.json().catch(() => ({}));
  const msg = document.getElementById("lim-msg");
  msg.textContent = d.ok ? "Limits saved." : (d.error || "Could not save limits.");
  msg.className = d.ok ? "ok" : "err";
  await poll();
});
document.querySelectorAll(".nav-btn").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));

document.getElementById("clear-chat-btn").addEventListener("click", async () => {
  await api("/api/chat/clear", {method: "POST"});
  switchTab("chat");
  await poll();
});
document.getElementById("pause-btn").addEventListener("click", async () => {
  const path = (state && state.paused) ? "/api/resume" : "/api/pause";
  await api(path, {method: "POST"});
  await poll();
});
document.getElementById("logout-btn").addEventListener("click", async () => {
  await api("/api/logout", {method: "POST"});
  showLogin();
});
document.getElementById("send-btn").addEventListener("click", sendMessage);
const msgInput = document.getElementById("msg-input");
msgInput.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
msgInput.addEventListener("input", () => {
  msgInput.style.height = "auto";
  msgInput.style.height = Math.min(110, msgInput.scrollHeight) + "px";
});
document.getElementById("plus-btn").addEventListener("click", () => document.getElementById("file-picker").click());
let mediaRec = null, recChunks = [];
const micBtn = document.getElementById("mic-btn");
if (micBtn) {
  micBtn.addEventListener("click", async () => {
    if (mediaRec && mediaRec.state === "recording") {
      mediaRec.stop();
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("Voice recording requires a browser with microphone access (HTTPS or localhost).");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      recChunks = [];
      mediaRec = new MediaRecorder(stream);
      mediaRec.ondataavailable = (ev) => { if (ev.data && ev.data.size) recChunks.push(ev.data); };
      mediaRec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        micBtn.textContent = "Mic";
        micBtn.style.background = "";
        micBtn.style.color = "";
        const blob = new Blob(recChunks, {type: mediaRec.mimeType || "audio/webm"});
        const ext = (mediaRec.mimeType || "").includes("ogg") ? "ogg" : ((mediaRec.mimeType || "").includes("wav") ? "wav" : "webm");
        const res = await api("/api/upload", {
          method: "POST",
          headers: {"X-File-Name": encodeURIComponent("voice-note." + ext), "Content-Type": "application/octet-stream"},
          body: await blob.arrayBuffer(),
        });
        if (res.ok) { pendingUploads.push(await res.json()); renderChips(); }
      };
      mediaRec.start();
      micBtn.textContent = "Stop";
      micBtn.style.background = "var(--warn)";
      micBtn.style.color = "#0f1115";
    } catch (err) {
      alert("Could not start microphone: " + (err.message || err));
    }
  });
}
document.getElementById("file-picker").addEventListener("change", async (e) => {
  for (const file of e.target.files) {
    const res = await api("/api/upload", {
      method: "POST",
      headers: {"X-File-Name": encodeURIComponent(file.name), "Content-Type": "application/octet-stream"},
      body: await file.arrayBuffer(),
    });
    if (res.ok) { pendingUploads.push(await res.json()); renderChips(); }
    else { alert((await res.json()).error || "Upload failed"); }
  }
  e.target.value = "";
});
document.getElementById("mem-save").addEventListener("click", async () => {
  const title = document.getElementById("mem-title").value.trim();
  const content = document.getElementById("mem-body").value.trim();
  if (!title) return;
  await api("/api/memory/save", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({title, content})});
  document.getElementById("mem-title").value = "";
  document.getElementById("mem-body").value = "";
  document.getElementById("mem-status").textContent = "Saved.";
  await poll();
});
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = document.getElementById("pw-input").value;
  const err = document.getElementById("login-err");
  err.textContent = "";
  const res = await fetch("/api/login", {
    method: "POST",
    headers: Object.assign({"Content-Type": "application/json"}, H),
    body: JSON.stringify({password: pw}),
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    document.getElementById("pw-input").value = "";
    showApp();
    await poll();
  } else {
    err.textContent = data.error || "Wrong password.";
  }
});

(async () => {
  const res = await fetch("/api/auth");
  const data = await res.json().catch(() => ({}));
  if (data.authenticated) { showApp(); await poll(); }
  else { showLogin(); }
  setInterval(() => {
    if (!document.getElementById("app").classList.contains("hidden")) poll();
  }, 550);
})();
</script>
</body>
</html>
"""


class PilotWebServer:
    """Background HTTP server exposing Pilot behind a password login."""

    def __init__(self, controller, host: str = "127.0.0.1", port: int = 8765):
        self.controller = controller
        self.host = host
        self.port = int(port)
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}  # cookie token -> expires_at
        self._fails: dict[str, list[float]] = {}  # ip -> [count, lock_until]
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._web_busy = False
        self._web_activity = ""
        self._web_steps: list[dict] = []

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def actual_port(self) -> int:
        return int(self._httpd.server_address[1]) if self._httpd else self.port

    def urls(self) -> list[str]:
        port = self.actual_port
        out = [f"http://127.0.0.1:{port}"]
        if self.host == "0.0.0.0":
            lan = local_ip()
            if lan and lan != "127.0.0.1":
                out.append(f"http://{lan}:{port}")
        return out

    def start(self) -> None:
        if self._httpd is not None:
            return
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # keep console quiet and never log cookies/paths
                return

            def _send_json(self, code: int, payload: dict, extra_headers: dict | None = None):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra_headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict:
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 2 * 1024 * 1024)
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    data = json.loads(raw.decode("utf-8"))
                    return data if isinstance(data, dict) else {}
                except (ValueError, OSError):
                    return {}

            def _cookie_token(self) -> str:
                raw = self.headers.get("Cookie") or ""
                jar = cookies.SimpleCookie()
                try:
                    jar.load(raw)
                except cookies.CookieError:
                    return ""
                morsel = jar.get("pilot_session")
                return morsel.value if morsel else ""

            def _is_authed(self) -> bool:
                return server_ref._check_token(self._cookie_token())

            def _require_csrf(self) -> bool:
                return self.headers.get("X-Pilot-Client") == "web"

            def _client_ip(self) -> str:
                remote = self.client_address[0]
                if remote in ("127.0.0.1", "::1"):
                    cf_ip = (self.headers.get("CF-Connecting-IP") or "").strip()
                    if cf_ip:
                        return cf_ip
                    xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                    if xff:
                        return xff
                return remote

            def _cookie_attrs(self) -> str:
                proto = (self.headers.get("X-Forwarded-Proto") or "").lower()
                secure = "; Secure" if proto == "https" else ""
                return f"HttpOnly; SameSite=Strict; Path=/{secure}"

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/":
                    raw = WEB_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if path == "/api/auth":
                    self._send_json(200, {"authenticated": self._is_authed(), "app": "Pilot"})
                    return
                if not self._is_authed():
                    self._send_json(401, {"error": "Sign in required."})
                    return
                if path == "/api/state":
                    self._send_json(200, server_ref._build_state())
                    return
                if path == "/api/workspace/files":
                    sub = (parse_qs(parsed.query).get("path") or [""])[0]
                    c = server_ref.controller
                    self._send_json(200, {
                        "files": c.workspace_files(sub),
                        "summary": c.workspace.summary(),
                        "has_venv": c.has_workspace_venv(),
                    })
                    return
                if path == "/api/files":
                    rel = (parse_qs(parsed.query).get("path") or [""])[0]
                    try:
                        target = server_ref.controller.workspace.path(rel)
                    except WorkspaceError:
                        self._send_json(403, {"error": "Access denied."})
                        return
                    if not target.is_file():
                        self._send_json(404, {"error": "File not found."})
                        return
                    data = target.read_bytes()
                    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    safe_dl = target.name.replace('"', "_").replace("\r", "").replace("\n", "")
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Disposition", f'attachment; filename="{safe_dl}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._send_json(404, {"error": "Not found."})

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path
                if not self._require_csrf():
                    self._send_json(403, {"error": "Missing client header."})
                    return
                if path == "/api/login":
                    ip = self._client_ip()
                    wait_s = server_ref._check_lockout(ip)
                    if wait_s > 0:
                        self._send_json(429, {"error": f"Too many wrong passwords. Try again in {int(wait_s) + 1} seconds."})
                        return
                    body = self._read_json()
                    pw = str(body.get("password") or "")
                    cfg = server_ref.controller.settings.get("web_access", default={}) or {}
                    if not verify_password(pw, cfg.get("password_salt", ""), cfg.get("password_hash", "")):
                        server_ref._record_fail(ip)
                        self._send_json(401, {"error": "Wrong password."})
                        return
                    server_ref._clear_fail(ip)
                    tok = server_ref._issue_token()
                    self._send_json(
                        200, {"ok": True},
                        extra_headers={"Set-Cookie": f"pilot_session={tok}; {self._cookie_attrs()}"},
                    )
                    return
                if path == "/api/logout":
                    server_ref._revoke_token(self._cookie_token())
                    self._send_json(
                        200, {"ok": True},
                        extra_headers={"Set-Cookie": f"pilot_session=; {self._cookie_attrs()}; Max-Age=0"},
                    )
                    return
                if not self._is_authed():
                    self._send_json(401, {"error": "Sign in required."})
                    return
                if path == "/api/upload":
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        length = 0
                    if length <= 0 or length > MAX_INCOMING_FILE_BYTES:
                        self._send_json(400, {"error": "File is empty or over the 50 MB web upload limit."})
                        return
                    raw_name = unquote(self.headers.get("X-File-Name") or "upload.bin")
                    data = self.rfile.read(length)
                    try:
                        saved = server_ref.controller.workspace.save_inbox_bytes(raw_name, data)
                    except WorkspaceError as exc:
                        self._send_json(400, {"error": str(exc)})
                        return
                    self._send_json(200, {
                        "rel_path": server_ref.controller.workspace.show(saved),
                        "name": saved.name,
                        "size": saved.stat().st_size,
                        "size_label": file_size_label(saved.stat().st_size),
                    })
                    return
                body = self._read_json()
                if path == "/api/chat":
                    ok, msg = server_ref._start_chat(str(body.get("text") or ""), body.get("attachments") or [])
                    self._send_json(200 if ok else 409, {"ok": ok, "error": "" if ok else msg})
                    return
                if path == "/api/chat/clear":
                    server_ref.controller.reset_chat()
                    self._send_json(200, {"ok": True})
                    return
                if path == "/api/sessions/new":
                    sess = server_ref.controller.new_session()
                    self._send_json(200, {"id": sess["id"]})
                    return
                if path == "/api/sessions/select":
                    ok = server_ref.controller.select_session(str(body.get("id") or ""))
                    self._send_json(200 if ok else 404, {"ok": ok})
                    return
                if path == "/api/sessions/delete":
                    ok = server_ref.controller.delete_session(str(body.get("id") or ""))
                    self._send_json(200 if ok else 404, {"ok": ok})
                    return
                if path == "/api/approvals/resolve":
                    ok = server_ref.controller.resolve_approval(str(body.get("id") or ""), str(body.get("choice") or "deny"))
                    self._send_json(200, {"ok": ok})
                    return
                if path == "/api/memory/save":
                    note = server_ref.controller.save_memory_note(str(body.get("title") or ""), str(body.get("content") or ""))
                    self._send_json(200, {"slug": note.slug, "title": note.title})
                    return
                if path == "/api/memory/delete":
                    ok = server_ref.controller.delete_memory_note(str(body.get("slug") or ""))
                    self._send_json(200, {"ok": ok})
                    return
                if path == "/api/pause":
                    server_ref.controller.pause_all()
                    self._send_json(200, {"ok": True, "paused": True})
                    return
                if path == "/api/resume":
                    server_ref.controller.resume_all()
                    self._send_json(200, {"ok": True, "paused": False})
                    return
                if path == "/api/workspace/terminal":
                    res = server_ref.controller.run_workspace_terminal(str(body.get("command") or ""))
                    self._send_json(200 if not res.get("error") else 400, res)
                    return
                if path == "/api/workspace/delete":
                    ok, err = server_ref.controller.delete_workspace_file(str(body.get("path") or ""))
                    self._send_json(200 if ok else 400, {"ok": ok, "error": err})
                    return
                if path == "/api/settings/limits":
                    err = server_ref.controller.save_limits(
                        body.get("daily_tokens", ""), body.get("daily_usd", ""), body.get("max_disk_gb", "")
                    )
                    self._send_json(200 if not err else 400, {"ok": not err, "error": err})
                    return
                self._send_json(404, {"error": "Not found."})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="pilot-web")
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except OSError:
                pass
        with self._lock:
            self._tokens.clear()

    # --- auth & rate limiting -------------------------------------------------------------------
    def _issue_token(self) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[tok] = time.monotonic() + SESSION_TTL_SECONDS
        return tok

    def _check_token(self, tok: str) -> bool:
        if not tok:
            return False
        now = time.monotonic()
        with self._lock:
            exp = self._tokens.get(tok)
            if exp is None or now > exp:
                self._tokens.pop(tok, None)
                return False
            self._tokens[tok] = now + SESSION_TTL_SECONDS
            return True

    def _revoke_token(self, tok: str) -> None:
        with self._lock:
            self._tokens.pop(tok, None)

    def _check_lockout(self, ip: str) -> float:
        now = time.monotonic()
        with self._lock:
            rec = self._fails.get(ip)
            if not rec:
                return 0.0
            return max(0.0, rec[1] - now)

    def _record_fail(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            count, lock_until = self._fails.get(ip, [0, 0.0])
            count += 1
            if count >= MAX_LOGIN_FAILS:
                lock_until = now + LOCKOUT_SECONDS
                count = 0
            self._fails[ip] = [count, lock_until]

    def _clear_fail(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    # --- live state & chat runner ---------------------------------------------------------------
    def record_live_step(self, step: dict) -> None:
        with self._lock:
            for idx, existing in enumerate(self._web_steps):
                if existing.get("id") == step.get("id"):
                    self._web_steps[idx] = dict(step)
                    break
            else:
                self._web_steps.append(dict(step))
            self._web_activity = step.get("label") or self._web_activity

    def _start_chat(self, text: str, rel_paths: list) -> tuple[bool, str]:
        with self._lock:
            if self._web_busy:
                return False, "Pilot is already working on a message."
            self._web_busy = True
            self._web_steps = []
            self._web_activity = "Thinking..."

        resolved = []
        for rel in rel_paths or []:
            try:
                p = self.controller.workspace.path(str(rel))
                if p.is_file():
                    resolved.append(p)
            except WorkspaceError:
                continue

        def worker():
            try:
                self.controller.send_from_web(text, attachments=resolved or None)
            finally:
                with self._lock:
                    self._web_busy = False
                    self._web_activity = ""
                    self._web_steps = []

        threading.Thread(target=worker, daemon=True, name="pilot-web-chat").start()
        return True, ""

    def _build_state(self) -> dict:
        c = self.controller
        with self._lock:
            busy = self._web_busy or c.is_busy()
            activity = self._web_activity or c.current_activity()
            live_steps = list(self._web_steps) or c.live_steps_snapshot()
        return {
            "paused": c.is_paused(),
            "active_session_id": c.active_session_id,
            "sessions": [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in c.list_sessions()],
            "messages": c.active_display_messages(),
            "busy": busy,
            "activity": activity,
            "live_steps": live_steps,
            "pending_approvals": [
                {"id": a.id, "action": a.action, "reason": a.reason, "risks": a.risks,
                 "rule": a.rule, "flag": a.flag, "connector": getattr(a, "connector", "Terminal"),
                 "created_at": a.created_at}
                for a in c.pending_approvals()
            ],
            "approval_history": [
                {"id": a.id, "action": a.action, "reason": a.reason, "status": a.status,
                 "connector": getattr(a, "connector", "Terminal"), "created_at": a.created_at}
                for a in c.approval_history()[:15]
            ],
            "memory": [{"slug": n.slug, "title": n.title, "content": n.content} for n in c.memory_notes()],
            "connectors": [
                {"id": x["id"], "name": x["name"], "status": x["status"], "health": x["health"]}
                for x in c.connector_cards()
            ],
            "daily_summary": c.daily_summary_lines()[-15:],
            "activity_log": [
                {"start_time": e.start_time, "end_time": e.end_time, "seconds": e.seconds,
                 "source": e.source, "kind": e.kind, "summary": e.summary, "tokens": e.tokens,
                 "long_run": e.long_run}
                for e in c.activity_entries(limit=40)
            ],
            "workspace": {
                "summary": c.workspace.summary(),
                "has_venv": c.has_workspace_venv(),
                "used_bytes": c.workspace.used(),
                "max_bytes": c.workspace.max_bytes,
                "max_ram_bytes": c.workspace.max_ram,
                "command_seconds": c.workspace.command_seconds,
                "files": c.workspace_files(),
            },
            "usage": {
                **c.period_usage(),
                "alerts": c.usage_alerts(),
                "limits": c.limits_config(),
                "resources": c.agent.usage.resource_samples("hour")[-1] if c.agent.usage.resource_samples("hour") else {},
            },
            "web": c.web_config(),
            "provider": {
                "name": c.settings.provider,
                "main_model": c.settings.get("models", "main"),
                "cheap_model": c.settings.get("models", "cheap"),
            },
        }
