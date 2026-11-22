# Pilot — Complete UI Design Spec & Program Data Contract

This document gives you **every screen, design token, data field, JSON API endpoint, and action** in Pilot so you can design a custom user interface (in Figma, HTML/CSS, React, Tailwind, Canva, etc.) and plug it straight into Pilot's working Python backend with zero guesswork.

*(No API keys, tokens, or personal data appear in this document.)*

---

## 1. Current Visual Design Tokens (`src/ui/theme.py`)

Feel free to completely redesign colors, shapes, and typography — these are the current tokens for reference:

| Token | Dark Theme (Default) | Light Theme | Usage |
| --- | --- | --- | --- |
| `bg` | `#15171c` | `#f5f6f8` | Main page background |
| `sidebar` | `#1b1e24` | `#eceef2` | Left navigation sidebar |
| `card` | `#21252d` | `#ffffff` | Panels, message attachments, settings cards |
| `border` | `#2c313b` | `#dde1e7` | Subtle 1px card & input borders |
| `field` | `#191c22` | `#f8f9fb` | Text inputs, command boxes, terminal output |
| `text` | `#e9ebef` | `#1c1f25` | Primary text |
| `muted` | `#9aa1ad` | `#5d6470` | Secondary labels, timestamps, token counts |
| `accent` | `#7d9bff` | `#3a5bd9` | Primary buttons, Pilot speaker name, focus ring |
| `accent_hover` | `#95adff` | `#2f4cc0` | Primary button hover |
| `on_accent` | `#0f1115` | `#ffffff` | Text on top of primary buttons |
| `you` | `#a9bcff` | `#2f4cc0` | User speaker label in Chat |
| `ok` | `#3ecf8e` | `#178a5b` | Ready dot, Connected / Healthy, Approved, Resume button |
| `warn` | `#f2b544` | `#a86a00` | Thinking dot, Pending approvals badge, Second-check flags, 80–94% meters |
| `bad` | `#f06464` | `#c93636` | Error text, Problem status, Denied, ≥95% meters, **Pause All** button |
| `nav_active` | `#2a2f3a` | `#dfe3ea` | Selected sidebar item & secondary buttons |

- **Current fonts:** `Segoe UI` (10pt body, 9pt small, 10pt semibold, 16pt page title, 22pt hero title) + `Consolas` for terminal/commands.
- **Window size (Desktop):** default `1000×700`, minimum `780×560`, sidebar width `195px`.

---

## 2. All Screens & Components to Design

### A. Login Screen (Web only)
- Centered card with Pilot logo/name, password input, **Sign in** button, inline error (wrong password or `429` lockout countdown after 5 failed attempts).

### B. First-Run Welcome Flow (Desktop only — 4 steps)
1. **Step 1 — Pick AI service:** Two selectable cards (`Google Gemini (free, recommended)` vs `Anthropic Claude (paid)`) + plain-language **Privacy & trust** note.
2. **Step 2 — API key:** Numbered setup steps, **Open the key page** link button, masked key input + **Paste**, **Back**, **Test key** (shows green pass or red error).
3. **Step 3 — Optional connector:** **Set up Telegram** (primary), **Skip for now**, plus quick links for **Gmail**, **Google Calendar**, **Discord**.
4. **Step 4 — Ready:** Shows chosen model + **Start chatting** button.

### C. Persistent Shell (Sidebar)
- App title (`Pilot`)
- Navigation items:
  1. **Chat**
  2. **Workspace & Terminal** *(Web UI; Desktop opens folder + runs commands in Chat)*
  3. **Connectors** *(Desktop)*
  4. **Approvals** — with live amber counter pill `(N)` when `pending_approvals.length > 0`
  5. **Memory**
  6. **Activity**
  7. **Settings**
- Footer:
  - Big red **`Pause All`** button (switches to green **`Resume Pilot`** when `paused === true`)
  - Status badges: `Web: On (:8765)` / `Web: Online (HTTPS)` and `Using <provider>`
  - **Sign out** button (Web UI)

### D. Chat Screen
- **Header:** `Chat`, status dot (`ok` green = Ready, `warn` amber = Thinking + live 1-line activity text, `bad` red = Problem/Paused), and **`New chat`** button (clears current chat history cleanly; no sidebar session list!).
- **Message stream:**
  - `who: "you"` or `"you_phone"` (shows `"You (on Telegram)"`, `"You (on Discord)"`, `"You (on Web)"`).
  - `who: "pilot"`:
    - Collapsible **`▸ Thought & steps (N steps · X.Xs)`** accordion showing each step's icon (`●` running, `✓` done, `✕` error), label, duration, and short detail.
    - Reply body text.
    - Meta footer (`"2 calls, 1,102 tokens"` or `"answered on Telegram"`).
    - Clickable **File cards** (`name`, `size_label`, optional `note`, **Download** / **Open** / **Save as...** / **Show in folder**).
  - `who: "problem"`: red inline error message.
  - Inline **Approval card** (amber 2px border): heading, `Via: <connector>`, monospace command/action box, `Why:`, `Needs your OK because:`, amber `Second check:` warning (if present), **Approve**, **Deny**, and optional **Always allow `"<rule>"`** button.
- **Live thinking box (while `busy === true`):** shows `live_steps` updating one by one above the composer.
- **Attachment chips bar:** removable `filename (size) ×` pills before sending.
- **Composer bar:** **`+`** (attach any file), **`Mic`** (toggle voice note recording → turns amber **`Stop`** while recording), auto-growing message box (`Enter` sends, `Shift+Enter` new line), **`Send`** button.

### E. Workspace & Terminal Screen (Web UI)
- **Header:** Workspace space meter (`used / max GB`) + `Isolated Python: On/Off` badge + **`+ Upload file`** button.
- **Live Terminal card:** Command input + **Run** button + dark monospace output console (runs inside `data/agent/files/` with 2 GB RAM cap, 2-minute timeout, and `data/agent/venv` on PATH when enabled).
- **Workspace Files list:** Every file/folder inside `data/agent/files/` with size, **Download** link, and **Delete** button.

### F. Connectors Screen + Guided Setup (Desktop + Health in Web)
- **Connector Health card:** Green (`● Healthy`), Yellow (`● Off / Paused`), Red (`● Problem`), Gray (`○ Not set up`) pills for every connector + **`Check health`** button.
- **Connector Cards Grid (2 columns):** One card per connector (`Telegram`, `Gmail`, `Google Calendar`, `Discord`) with name, 1-line summary, status dot, latest health note or red problem text, **Set up** / **Set up again**, and **Turn on** / **Turn off** toggle.
- **Guided Setup Page (auto-rendered from each connector's `guide.json`):**
  - **← All connectors** back button
  - Numbered steps (`1..N`) with optional external link button
  - Input fields (text, masked secret + **Paste**, or choice toggle buttons like Gmail's `Read only` vs `Read & send`)
  - **Test connection** button + green/amber/red result + bottom privacy note.

### G. Approvals Screen
- **Waiting for your OK (`N`):** Full approval cards showing heading, `Via: <connector>`, timestamp, monospace action box, `Why:`, risks list, amber `Second check:` warning, **Approve**, **Deny**, optional **Always allow `"<rule>"`**.
- **Recent decisions:** Historical list showing `Approved · Terminal` / `Denied · Gmail` / `Always allowed · Terminal`, time, action, reason, and flag.

### H. Memory Screen
- **Header:** `Memory`, **New note**, **Open folder** (Desktop).
- **Note editor card:** Title input, multiline Markdown body, **Save note** button, status message.
- **Saved notes list:** One card per `.md` file in `data/memory/` with title, `(slug.md)`, body preview, **Edit**, and **Delete**.

### I. Activity Screen
- **Header:** `Activity`, **Refresh**, **Export log** (Desktop).
- **Search bar:** Live filter input + **Clear**.
- **Today's daily summary card:** Bullet list from `data/usage/YYYY-MM-DD-summary.txt`.
- **Timeline list:** Reverse-chronological cards (`start to end · source`, duration, token count, `quiet run` / `ran long` amber border highlight, summary).

### J. Settings Screen
1. **Model service:** Provider dropdown (`Google Gemini`, `Anthropic Claude`), masked API key + **Paste** + **Get a key**, Main model & Cheap model dropdowns, **Test connection**, **Save**.
2. **Agent workspace:** Path + used/max GB, **Open folder**, tool group toggles (`Files`, `Memory`, `Terminal`, `Browser`), and **Create / Remove workspace Python** (`data/agent/venv`).
3. **Permissions:** 7 risky action categories (`delete`, `install`, `network`, `outside`, `launch`, `system`, `other`) each toggleable between `Ask first` and `Allowed`, "Never allowed" note, and **Always allowed commands** list with **Remove**.
4. **Web access (password-protected):** Running URLs, password input, reachability toggle (`This computer only` vs `Same Wi-Fi network`), Port, **Save & Turn on** / **Turn off**, **Open web page**, plus **Put online (free HTTPS link)** / **Stop public link** / **Open public link** / **Copy link** (Cloudflare Tunnel).
5. **Always-on & Schedules:** **Pause everything** / **Resume everything**, **Start at login: On/Off**, ignored check-ins toggle (`Keep going quietly` vs `Pause after 6`), add scheduled task form (Name, Prompt, Every N min, **Add schedule**), and job list with **Pause job** / **Resume job** and **Delete job**.
6. **Look:** `Light` / `Dark` theme buttons.
7. **Usage & System Resources:** Alerts banner (amber), live RAM / CPU / GPU / Workspace disk progress bars (green < 80%, amber 80–94%, red ≥ 95%), sparkline chart with **Last hour** / **Last day** / **Last week**, Today / Last 7 days / Last 30 days & per-source token breakdown, editable **Daily tokens / Daily $ / Workspace GB** + **Save limits**, and **Export report**.

---

## 3. Complete Web HTTP JSON API Contract (`src/core/web_server.py`)

You can build **any custom HTML/CSS/JS frontend** and talk directly to these exact endpoints!

### Security Headers Required
- Every `POST` request **must** include header: `X-Pilot-Client: web` (returns `403` without it).
- After `POST /api/login`, the server sets an `HttpOnly; SameSite=Strict` cookie named `pilot_session` (plus `; Secure` over HTTPS). All subsequent requests automatically authenticate via that cookie.

### Authentication Endpoints
| Method & Path | Request Body | Response (`200`) | Errors |
| --- | --- | --- | --- |
| `GET /api/auth` | — | `{"authenticated": true\|false, "app": "Pilot"}` | — |
| `POST /api/login` | `{"password": "..."}` | `{"ok": true}` + `Set-Cookie` | `401` wrong password, `429` locked out 60s after 5 wrong tries |
| `POST /api/logout` | `{}` | `{"ok": true}` (clears cookie) | — |

### Master Live State Endpoint: `GET /api/state`
Poll every ~500–800 ms (or after actions). Returns the entire app state in one JSON object:

```json
{
  "paused": false,
  "busy": false,
  "activity": "Searching the web: ...",
  "live_steps": [
    {
      "id": 1,
      "status": "running",
      "label": "Thinking...",
      "detail": "",
      "seconds": 0.4
    }
  ],
  "messages": [
    {
      "who": "you",
      "text": "Make a PDF summary of my notes",
      "extra": "",
      "steps": [],
      "files": []
    },
    {
      "who": "pilot",
      "text": "Done! I created notes_summary.pdf and sent it to you.",
      "extra": "3 calls, 2,140 tokens",
      "steps": [
        { "id": 1, "status": "done", "label": "Planned next step (load_tool)", "detail": "", "seconds": 0.9 },
        { "id": 2, "status": "done", "label": "Creating PDF: notes_summary.pdf", "detail": "Saved PDF...", "seconds": 1.6 }
      ],
      "files": [
        {
          "rel_path": "notes_summary.pdf",
          "name": "notes_summary.pdf",
          "size": 43262,
          "size_label": "42.2 KB",
          "note": "Your summary PDF"
        }
      ]
    }
  ],
  "pending_approvals": [
    {
      "id": "approval_1",
      "action": "Remove-Item old.txt",
      "reason": "You asked me to tidy up",
      "risks": ["'Remove-Item' (delete files)"],
      "rule": "remove-item",
      "flag": "",
      "connector": "Terminal",
      "created_at": "19:42"
    }
  ],
  "approval_history": [
    {
      "id": "approval_1",
      "action": "Remove-Item old.txt",
      "reason": "You asked me to tidy up",
      "status": "deny",
      "connector": "Terminal",
      "created_at": "19:42"
    }
  ],
  "memory": [
    {
      "slug": "preferences",
      "title": "Preferences",
      "content": "Favorite color is cobalt blue."
    }
  ],
  "connectors": [
    { "id": "telegram", "name": "Telegram", "status": "Not set up", "health": "none" },
    { "id": "gmail", "name": "Gmail", "status": "Connected", "health": "green" },
    { "id": "calendar", "name": "Google Calendar", "status": "Off", "health": "yellow" },
    { "id": "discord", "name": "Discord", "status": "Problem", "health": "red" }
  ],
  "daily_summary": [
    "19:45, [window] hello -> Hello! How can I help you today?, 388 tokens."
  ],
  "activity_log": [
    {
      "start_time": "19:45",
      "end_time": "19:45",
      "seconds": 0.8,
      "source": "window",
      "kind": "chat",
      "summary": "hello -> Hello! How can I help you today?",
      "tokens": 388,
      "long_run": false
    }
  ],
  "workspace": {
    "summary": "203.6 MB of 5.00 GB used",
    "has_venv": true,
    "used_bytes": 213490000,
    "max_bytes": 5368709120,
    "max_ram_bytes": 2147483648,
    "command_seconds": 120,
    "files": [
      {
        "name": "inbox",
        "rel_path": "inbox",
        "is_dir": true,
        "size": 0,
        "size_label": "folder",
        "modified": "2026-11-21 19:40"
      },
      {
        "name": "notes_summary.pdf",
        "rel_path": "notes_summary.pdf",
        "is_dir": false,
        "size": 43262,
        "size_label": "42.2 KB",
        "modified": "2026-11-21 19:42"
      }
    ]
  },
  "usage": {
    "today": { "calls": 3, "total_tokens": 2528, "usd": 0.0, "usd_complete": true, "by_source": { "window": { "calls": 3, "tokens": 2528, "usd": 0.0 } } },
    "week":  { "calls": 3, "total_tokens": 2528, "usd": 0.0, "usd_complete": true, "by_source": {} },
    "month": { "calls": 3, "total_tokens": 2528, "usd": 0.0, "usd_complete": true, "by_source": {} },
    "alerts": [],
    "limits": { "daily_tokens": 300000, "daily_usd": 2.0, "max_disk_gb": 5.0 },
    "resources": {
      "time": "2026-11-21T19:45:56-05:00",
      "ram_mb": 89.9,
      "ram_pct": 4.4,
      "cpu_pct": 12.5,
      "gpu_pct": 1.0,
      "disk_mb": 203.61,
      "disk_pct": 4.0
    }
  },
  "web": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8765,
    "has_password": true,
    "running": true,
    "urls": ["http://127.0.0.1:8765"],
    "tunnel_available": true,
    "tunnel_running": false,
    "public_url": ""
  },
  "provider": {
    "name": "gemini",
    "main_model": "gemini-flash-lite-latest",
    "cheap_model": "gemini-flash-lite-latest"
  }
}
```

### Action Endpoints
| Method & Path | Payload / Params | Response |
| --- | --- | --- |
| `POST /api/chat` | `{"text": "...", "attachments": ["inbox/file.pdf"]}` | `{"ok": true, "error": ""}` (`409` if busy) |
| `POST /api/chat/clear` | `{}` | `{"ok": true}` |
| `POST /api/upload` | Raw file bytes + headers `X-File-Name: <url-encoded-name>`, `Content-Type: application/octet-stream` (max 50 MB) | `{"rel_path": "inbox/...", "name": "...", "size": 123, "size_label": "123 B"}` |
| `GET /api/files?path=<rel_path>` | Workspace-relative file path | Binary file download (`403` on path traversal) |
| `GET /api/workspace/files?path=<subdir>` | Optional workspace subfolder | `{"files": [...], "summary": "...", "has_venv": true}` |
| `POST /api/workspace/terminal` | `{"command": "python --version"}` | `{"ok": true, "exit_code": 0, "output": "Python 3.13.17", "timed_out": false, "hit_memory_limit": false, "error": ""}` |
| `POST /api/workspace/delete` | `{"path": "inbox/old.txt"}` | `{"ok": true, "error": ""}` |
| `POST /api/approvals/resolve` | `{"id": "approval_1", "choice": "once" \| "always" \| "deny"}` | `{"ok": true}` |
| `POST /api/memory/save` | `{"title": "Preferences", "content": "..."}` | `{"slug": "preferences", "title": "Preferences"}` |
| `POST /api/memory/delete` | `{"slug": "preferences"}` | `{"ok": true}` |
| `POST /api/pause` | `{}` | `{"ok": true, "paused": true}` |
| `POST /api/resume` | `{}` | `{"ok": true, "paused": false}` |
| `POST /api/settings/limits` | `{"daily_tokens": 300000, "daily_usd": 2.0, "max_disk_gb": 5}` | `{"ok": true, "error": ""}` |

---

## 4. Connector Setup Guides (`src/connectors/<id>/guide.json`)

| Connector ID | Display Name | Fields | Extra Rules |
| --- | --- | --- | --- |
| `telegram` | Telegram | `bot_token` (secret) | Owner-only DM lock from first message; two-way files (20 MB in / 50 MB out) |
| `gmail` | Gmail | `email`, `app_password` (secret), `mailbox` (default `INBOX`), `access` (`read_only` \| `read_and_send`) | Scoped to one mailbox/label; `gmail_send` always asks approval (no Always allow) |
| `calendar` | Google Calendar | `calendar_name` (default `My Calendar`), `ical_url` (secret) | Strictly read-only `.ics` feed for one calendar |
| `discord` | Discord | `bot_token` (secret), `owner_user_id` | Owner-only private DMs; two-way files (50 MB in / 25 MB out) |

---

## 5. Available Agent Tools (`src/core/agent_tools.py` + Connectors)

- **Files (`agent_tools.files`):** `list_files`, `read_file` (text, PDF, Word, Excel, PowerPoint, ODT), `write_file`, `make_pdf`, `send_file`
- **Memory (`agent_tools.memory`):** `remember`, `recall`, `forget`
- **Terminal (`agent_tools.terminal`):** `run_command` (PowerShell on Windows / Bash on Linux, 2 GB RAM cap, 2 min timeout)
- **Browser (`agent_tools.browser`):** `web_search` (Bing + DuckDuckGo fallback), `open_page`, `page_screenshot`
- **Gmail (loaded ONLY when Gmail is connected & on):** `gmail_list`, `gmail_read`, optional `gmail_send`
- **Google Calendar (loaded ONLY when Calendar is connected & on):** `calendar_events`

---

## 6. How to Plug Your New UI Design Into Pilot

1. **Web UI (easiest & most flexible):** Design your interface as a single HTML/CSS/JS app (or send mockups/Figma screenshots/HTML files). We drop your frontend straight into `src/core/web_server.py` (or serve it from a `web/` folder) talking to the exact `/api/*` endpoints above — zero backend rewrite needed!
2. **Public Landing Page:** Edit `docs/index.html` (hosted free on GitHub Pages, ready for your custom domain later).
3. **Desktop Window:** Edit colors/fonts in `src/ui/theme.py` or keep the desktop app minimal and use your custom Web UI in any browser!
