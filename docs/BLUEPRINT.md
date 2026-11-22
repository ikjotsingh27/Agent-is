# Blueprint: Simple Always-On Personal Agent

Nov 21, 2026

This manual tells Claude Code what to build, where everything lives, and how to resume after a pause. The app is a simplified, friendlier take on Hermes Agent (MIT license): always on, your own API key, and guided setup for every connector.

## How to use this manual

Read this section first at the start of every session, then follow it before touching any code.

1. Open the Progress Log at the end of this doc and find the last entry. That is where the previous session stopped.
2. Read the repo's `docs/STATE.md` file, which holds the current status, open bugs, and the next task.
3. Run the app's test command and confirm everything passes before adding anything new.
4. Work on one build session at a time from the Build Plan. Do not start the next one early.
5. Before ending or pausing, update `docs/STATE.md` and add a Progress Log entry: what was done, what is broken, what is next.

Rules that never change:

- Keep it simple. If a feature makes setup harder for a beginner, simplify it or cut it.
- Never hardcode API keys. Keys live only in the local settings file.
- Never delete or rewrite working code without saying why in `docs/STATE.md`.
- Keep the Hermes MIT copyright notice in any file copied or adapted from Hermes.

## Vision and goals

The app is an always-on personal agent that runs on the user's computer, thinks through a cloud model using the user's own API key, and gets setup help from the app itself. It combines what people like about Hermes Agent and the Grok bot, with a much friendlier interface.

The one rule that guides every choice: a teenager with no technical background should be able to set it up and use it without leaving the app to hunt for instructions.

Goals:

- Runs 24/7 in the background on Windows, with a simple window to chat and manage settings.
- Calls a cloud model through an API key. The provider is a setting, so Anthropic can be swapped for another service by changing one dropdown and one key.
- Every connector (Telegram, email, calendar, and so on) has built-in step-by-step setup instructions and a test button.
- Memory is stored in plain, readable files the user can open and edit.
- Risky actions always wait for the user's approval.

Not goals: a huge plugin marketplace, running local models, or matching every Hermes feature. Start small and add later.

## Architecture

The architecture is drawn as a diagram in the original doc. In words: the agent core sits in the middle. The desktop window (chat, connectors, settings) talks to the core. The core talks to the connectors (Telegram, Gmail, Calendar). Below the core are three more parts: Approvals (risky actions wait here), Memory files (plain readable notes), and the Cloud model API (your key, swappable provider).

The agent core is the only part that talks to everything else. The window, the connectors, the approvals queue, the memory files, and the cloud model all connect through it, so any one of them can be swapped without touching the rest.

## Repo layout

The project name is a placeholder (Pilot) until the user picks one. Hermes Agent's code is cloned into `reference/hermes` for reading only. Nothing in that folder is edited; useful parts are rewritten or adapted into `src/`.

| Path | What it does |
| --- | --- |
| `docs/BLUEPRINT.md` | Copy of this manual |
| `docs/STATE.md` | Current status, open bugs, next task. Update every session. |
| `reference/hermes/` | Read-only clone of Hermes Agent (MIT) for borrowing ideas and code |
| `src/core/agent.py` | The agent loop: receive message, think, use tools, reply |
| `src/core/providers/` | One file per model provider, all with the same interface |
| `src/core/memory.py` | Reads and writes memory as plain markdown files |
| `src/core/approvals.py` | Holds risky actions until the user approves or denies |
| `src/core/scheduler.py` | The always-on background loop and scheduled tasks |
| `src/connectors/` | One folder per connector, each with its setup guide and test |
| `src/ui/` | The desktop window: chat, connectors, approvals, settings |
| `data/settings.json` | API keys and choices, stored locally, never committed to git |
| `data/memory/` | The agent's memory files |
| `tests/` | Automated tests, one folder per area |

Each connector folder holds the same four files: `connector.py` (the logic), `guide.json` (the setup steps shown in the app), `test.py` (the connection check), and `README.md` (notes for Claude Code).

Added for efficiency and usage tracking: `src/core/usage.py` (counts tokens and money, and samples RAM, CPU, GPU, and disk), `src/core/context.py` (keeps each model call small by summarizing old messages), `src/core/router.py` (sends easy jobs to a cheaper model), and `data/usage/` (one log file per day, including the daily summary).

## Interface design

The window has a slim left sidebar and one main panel. It should feel like a modern phone app: rounded cards, plain words, big buttons, and a dark theme by default with a light option. No terminal-style screens and no jargon.

| Screen | What the user sees | Key behavior |
| --- | --- | --- |
| Chat | Conversation with the agent, message box at the bottom, a status dot showing the agent is running | Works by text and voice notes; shows what the agent is doing in one line |
| Connectors | A grid of cards, one per service, each with a toggle and a status (Not set up, Connected, Problem) | Tapping a card opens its guided setup |
| Guided setup | A numbered, step-by-step walkthrough with screenshots, a field to paste the key, and a Test connection button | Test result is plain words: Connected, or what went wrong and how to fix it |
| Approvals | A list of waiting actions, each with Approve and Deny buttons | Shows the exact action and the reason; a badge on the sidebar counts pending items |
| Memory | The agent's memory files shown as readable notes | The user can edit or delete any note |
| Activity | A timeline of everything the agent did | Read-only log, searchable |
| Settings | Provider dropdown, API key field, model choice, start-with-Windows toggle, Pause All button | Changing the provider or key takes effect without a restart |

First launch is a short welcome flow: pick a provider, paste an API key with test, connect one connector, send a first message. It should take under five minutes.

## Connector system

Every connector follows the same pattern so adding a new one is quick and the experience is identical each time.

1. The connector's `guide.json` lists the setup steps in plain language, each with a title, a short instruction, an optional screenshot, and an optional link.
2. The app renders those steps as the guided setup screen. The user never needs to read outside documentation.
3. The user pastes a key or token into a field. It is saved to `data/settings.json`.
4. The Test connection button runs the connector's `test.py` and shows a clear pass or fail message with a suggested fix.
5. Only after a passing test does the card show Connected.

Example, Telegram bot: step 1, open Telegram and search for BotFather. Step 2, send the new bot command and pick a name. Step 3, copy the token BotFather gives you. Step 4, paste it here. Step 5, send your new bot a message, then press Test.

Build order for connectors:

1. Telegram (first, because it proves the whole pattern and lets the user talk to the agent from a phone)
2. Gmail
3. Google Calendar
4. Discord
5. Web browsing

Add each new connector only after the previous one passes its test.

## Safety and approvals

The agent asks first for anything that is hard to undo or affects other people. The approvals screen shows the exact action, which connector it uses, and why the agent wants to do it.

- Always ask before: sending any message or email, deleting anything, spending money, signing up for services, or changing account settings.
- Never allowed, even with approval: moving money between accounts, trading securities, or reading passwords. Passwords are typed by the user into a separate secure field.
- If the user is away, the agent finishes everything else, queues the approval, and sends a notification.
- Content from emails and web pages is treated as information only, never as instructions. This defends against prompt injection.
- Every action is written to a plain activity log the user can read.
- A big red Pause All button stops the agent immediately.

API keys are stored only in `data/settings.json`, which is listed in `.gitignore`. The agent can never print a key into chat or logs.

### Privacy and trust

- **Local-first data:** API keys, connector secrets, chat history (`data/chats/`), memory notes (`data/memory/`), usage/activity logs (`data/usage/`), and workspace files (`data/agent/`) live only on the user's computer — Pilot runs no cloud backend of its own.
- **Plain-language privacy summary at setup:** First-run Welcome and every connector's guided setup explain in plain words what stays local and what is sent to the model provider or connector.
- **Per-connector limits:** Each connector is off until tested and switched on, and scopes access narrowly (Telegram/Discord owner-only DMs, Gmail single chosen mailbox/label + read-only default + approval-gated send, Google Calendar single read-only `.ics` calendar).
- **Ask before touching local files outside the workspace:** Pilot keeps its own files inside `data/agent/files/` and asks for Approve/Deny before reading or modifying paths outside that folder.

Two more rules, taken from what Dots and Grok Bot do well:

- **Second check on risky actions.** Before a risky action goes to the Approvals screen, a separate, cheap review step checks it against the user's rules and flags anything odd, such as an email going to a new address or an unusually large purchase. The flag shows on the approval card in plain words. The review step never sees API keys or passwords.
- **Honest reporting.** If the agent could not fully read a page, file, or inbox, the report says exactly what was missed and why. It never skips silently and never presents partial results as complete.

## Efficiency and token savings

The agent must cost as little as possible to run. Hermes and similar agents burn tokens by re-reading history and exploring before acting. This app avoids that with the rules below. Claude Code should treat them as requirements, not nice-to-haves.

- **Direct commands.** When the user or a schedule gives a clear command, the agent runs it straight away. It does not first scan memory, browse around, or re-plan unless the command is unclear.
- **Small context.** Each model call sends only the last few messages plus a short summary of older ones, never the whole history. Old conversations are summarized once and stored.
- **Cached repeats.** Repeated work such as a daily check reuses stored results and prompt caching where the provider supports it.
- **Cheap model for easy jobs.** Simple tasks (formatting, checking a list, short replies) go to a smaller, cheaper model. The big model is used only for hard thinking. The user can change this in Settings.
- **Quiet runs.** A scheduled job that finds nothing new sends no message and makes no extra model calls.
- **Hard limits.** A daily token cap and a daily money cap, both set by the user. When a cap is reached, the agent stops and asks before continuing.
- **No loops.** A single task can make at most a set number of model calls, and the agent stops and reports if it hits that number.

### Do not copy Hermes's token overhead

Hermes GitHub issues report a fixed prompt of roughly 14,000 tokens on every call, mostly tool definitions, so even a one line "hello" goes out as a very large request. Every step of a task then resends all of it. When Claude Code reads `reference/hermes`, it must not carry this design over. Required rules:

- **Lazy tool loading.** The model starts with only a short list of tool names and one line each. A tool's full definition is sent only after the model asks for that tool. A reply that needs no tools finishes in one call.
- **Only enabled tools.** Load tools only for connectors the user has switched on. Never load every tool for every screen or platform by default.
- **Lazy skills.** No list of skills in the system prompt. A skill is loaded only when its trigger matches.
- **Short fixed prompt.** Keep the system prompt and standing instructions short and free of repeated guidance. Target for a fresh "hello": 3,000 tokens or less in total.
- **Stable prefix for caching.** Keep the fixed part of the prompt identical between calls, and do not reorder or change the tool list mid session, so provider prompt caching keeps working.
- **Trim tool output.** Large logs, web pages, and files are summarized before they go back to the model, and the full copy is kept on disk.
- **Iteration cap.** Each loop step resends the context, so the cap on model calls per task (see above) is a hard limit, not a suggestion.

Session 1 check: send "hello" with every connector switched off and record the token count on the Usage screen. If it is above the target, fix it before moving on.

## Usage dashboard and daily log

Settings has a Usage screen so the user always knows what the agent is costing and how hard it is working.

- **Live usage graphs:** RAM, CPU, GPU (if present), and disk used by the agent, shown over the last hour, day, and week, with a warning color near the limit.
- **Token and cost meter:** tokens used and money spent today, this week, and this month, split by connector and by task.
- **Daily log summary, on by default:** every day the app writes a short plain-language summary. For each task it records the start time, end time, what was done, and how many tokens it used. Example: "9:02 to 9:04, checked Gmail, no new mail, 1,200 tokens."
- **Command log:** each command the agent ran, with the time range and tokens used, searchable from the Activity screen.
- **Alerts:** a notification when spending or resource use is unusually high, or when the disk is nearly full.

Data is stored locally in `data/usage/`, one file per day, and can be exported as a plain file.

## Gaps in other agents, and how this app fills them

Based on a nine-day hands-on test of OpenAI Dots, Grok Bot, Claude Duet, and Hermes Agent by [AIMultiple](https://aimultiple.com/always-on-agents) (updated November 16, 2026), plus its notes on OpenClaw. Each gap below becomes a requirement.

| Gap seen in other agents | What this app does instead |
| --- | --- |
| Hard setup: Hermes needed a Google Cloud sign-in app, a secret file, and a code pasted into chat. OpenClaw needs server and config-file skills. | Guided in-app setup for every connector, with a test button and plain-language errors |
| Slow or late runs: some Hermes watch reports arrived over three hours after the start | Show run times in the Activity screen and warn when a job runs long |
| Resource problems: Hermes paused its own schedule and filled a 25 GB disk with raw data in days | Disk and memory limits, automatic cleanup of old data, and an alert before the disk is full |
| Surprise costs: OpenClaw cost about $11 a day in model use, even on days with no report | Live token and money meter with daily caps, quiet runs, and a small context (see Efficiency) |
| Silent failures: OpenClaw ended most runs with no report when sites blocked it, and Grok Bot once saved only a header | Every failed job sends a short message saying what failed and why, never silence |
| Check-ins that stop: Duet paused its own check-ins after six days without a reply | The user chooses how the agent behaves when ignored: keep going quietly, or pause |
| No memory screen: Grok Bot memory can only be changed by asking in chat, and dots memory is cleared by deleting the dot | A Memory screen with plain files the user can read, edit, and delete |
| Access too wide: Duet could reach the owner's whole mail and Drive, with no way to limit it to one mailbox | Per-connector permissions, with each connector limited to what the user switches on |

One more idea to consider later: a health screen that shows each connector as green, yellow, or red, so the user sees problems before a job fails.

## Build plan and progress log

Six sessions, done in order. A session is finished only when its done-criteria pass and `docs/STATE.md` is updated. This is a rough estimate; sessions may split if a connector is harder than expected.

| Session | Build | Done when |
| --- | --- | --- |
| 1 | Set up the repo, clone Hermes into `reference/hermes`, read it, write `docs/STATE.md`, build the provider system and a bare chat that talks to the Anthropic API | Typing a message in a terminal gets a model reply, and switching provider is one setting |
| 2 | The desktop window with Chat and Settings screens, plus the welcome flow | A new user can paste a key, test it, and chat from the window |
| 3 | The connector framework (guide.json renderer, test button) and the Telegram connector | The user sets up Telegram only by following in-app steps and can message the agent from their phone |
| 4 | Memory files and the Memory screen, then the approvals system and Approvals screen | Risky test actions wait for approval, and memory survives a restart |
| 5 | The always-on background loop, start-with-Windows, Activity log, and Pause All | The agent keeps running with the window closed and recovers after a reboot |
| 6 | Gmail and Google Calendar connectors, then polish, bug fixes, and a first-run test with a non-technical person | Someone who has never seen the app finishes setup alone in under ten minutes |

### Progress log

Added work from the market research: the efficiency rules (direct commands, small context, cheap model routing, caps) are built in Session 1 and 4 and checked in every later session. The Usage screen with RAM, CPU, GPU, and disk graphs and the daily log summary are built in Session 5. Failure messages and the connector health screen are added in Session 6.

Newest entry first. Add an entry at the end of every session.

| Date | Session | What was done | Broken or open | Next step |
| --- | --- | --- | --- | --- |
| 2026-11-21 | 6 (final audit & polish, Antigravity) | Simplified Chat to clean single persistent history (removed stacked `SAVED SESSIONS` sidebar per owner preference). Completed full-blueprint deep audit across all 83 Python files (0 third-party imports): fixed cross-screen approval card sync (`item_id`), added connector labels (`Via: Gmail`/`Terminal`) & new-email/large-purchase `second_check()` warnings + `known_recipients`, enforced money/trading `NEVER` block in `permissions.py`, added email/calendar router keywords, hardened never-silent scheduler crash reports + remote Telegram/Discord owner notifications, added pure-stdlib **Voice notes** (`src/core/voice.py`, Desktop `Mic` via Windows `winmm`, Web `Mic` via `MediaRecorder`, native Gemini `input_audio`), added zero-overhead **Lazy skills** (`src/core/skills.py`), added beginner-friendly **Daily & workspace limits** in Settings (removing remaining `settings.json` jargon), updated Welcome (Privacy & trust summary + multi-connector Step 3) and added the Privacy & trust section to `docs/BLUEPRINT.md`. **172 tests + 24-step walkthrough pass**; real Gemini measured **388 total tokens** on `"hello"`, heard a real `.wav` clip cleanly, and saved/restored memory + chat history. Full diff in `docs/ANTIGRAVITY_CHANGES.md`. | Only hands-on live owner checks remain (real Telegram/Discord/Gmail/Calendar accounts, Windows reboot test, and 10-minute first-run non-technical user test). | Owner tests the finished app and connects real accounts |
| 2026-11-21 | 6 (Antigravity) | Completed **Session 6**: built **Gmail connector** (`src/connectors/gmail/`: pure-stdlib IMAP SSL + SMTP SSL via 16-letter App Password, scoped to one mailbox/label, read-only default, approval-gated `gmail_send` with no Always allow, prompt-injection banners), **Google Calendar connector** (`src/connectors/calendar/`: pure-stdlib HTTPS + RFC 5545 `.ics` parser via secret iCal link, SSRF-checked, read-only, prompt-injection banners), dynamic lazy loading of connector tools (0 idle token overhead when off), Green/Yellow/Red **Connector health** overview + live `Check health` button on Connectors & Web UI (`Activity & Health` tab), DuckDuckGo HTML fallback in `Browser.search()`, automatic cleanup of old `data/tool_output/` files (`cleanup_old_data()`), optional NVIDIA GPU utilization sampling, per-command logging in `ActivityLog`, and optional isolated Workspace Python (`data/agent/venv`) in Settings. **162 tests + 24-step walkthrough pass**; fresh "hello" is 398 offline tokens. Full diff in `docs/ANTIGRAVITY_CHANGES.md`. | Hands-on live tests with real Telegram, Discord, Gmail, Google Calendar accounts, Windows reboot test, and 10-minute first-run test with a non-technical user remain. | Owner live-tests connectors, web access, and always-on daemon, then runs a first-run user test |
| 2026-11-21 | 5 (Antigravity) | Built **Session 5**: always-on background scheduler (`src/core/scheduler.py` with `NOTHING_NEW` quiet runs, `keep_going` vs `pause` after 6 ignored check-ins, `set_start_with_windows()` writing `Pilot.cmd`), headless `python -m src daemon` & `Pilot.pyw --background`, searchable read-only **Activity** screen (`src/core/activity.py`, `src/ui/activity_view.py`, `data/usage/activity.jsonl` + `YYYY-MM-DD-summary.txt`, long-run >= 45s warnings, export), upgraded **Usage & System Resources** dashboard (`src/core/usage.py`, `src/ui/settings_view.py`: Windows ctypes RAM/CPU + workspace disk gauges, Hour/Day/Week sparkline, Today/7d/30d & per-source totals, limit/disk/RAM alerts, report export), and red **Pause All** / green **Resume Pilot** emergency stop across Desktop sidebar, Settings, and Web UI (`/api/pause`, `/api/resume`). **155 tests + 23-step walkthrough pass**; fresh "hello" is 398 offline tokens. Full diff in `docs/ANTIGRAVITY_CHANGES.md`. | Real-phone Telegram, real Discord bot, and reboot test of Start with Windows await owner testing. | Session 6 (Gmail & Google Calendar connectors, connector health screen, search fallback, optional workspace venv, polish) |
| 2026-11-21 | 4 (follow-up, Antigravity) | Added **Saved Chat Sessions** (`src/core/sessions.py`, `data/chats/*.json`, clickable `SAVED SESSIONS` list with `+ New` and `×` in the sidebar, automatic migration of legacy `.chat_history.json`), **Live Step-by-Step Thinking** (`Agent` live `on_step` events + provider reasoning capture, live `Thinking & steps` panel while working, collapsible `▸ Thought & steps` accordion above each reply), and **Password-Protected Web Access** (`src/core/web_server.py`: pure-stdlib HTTP server, salted PBKDF2-SHA256 password hash, 5-try/60s brute-force lockout, HttpOnly SameSite=Strict cookie + CSRF header, responsive dark web app with Chat, live steps, two-way files, Saved Sessions, Approvals, and Memory; configured from Settings). 146 tests + 20-step walkthrough pass; fresh "hello" is 398 tokens. Full diff log in `docs/ANTIGRAVITY_CHANGES.md`. | Real-phone Telegram test and real Discord bot test still open. Workspace Python venv awaits owner decision. | Owner tries Saved Sessions, live thinking steps, and Web access; then Session 5 (always-on loop, Activity screen, live Usage graphs, Pause All) |
| 2026-11-21 | 4 (plus Chat text-box & PDF read fixes, Antigravity) | Fixed the Chat input box alignment (compact single-line height that auto-expands up to 4 lines, vertically centered with `+` and `Send`). Added pure-stdlib `.pdf` (`extract_pdf_text`) and Office `.docx`/`.pptx`/`.xlsx`/`.odt` (`extract_office_text`) reading to `read_file` (verified on owner's uploaded `Discussion Assignments (1).pdf`). Built **Session 4**: `src/core/memory.py` (`data/memory/*.md`, `remember`/`recall`/`forget` tools), persistent chat history across restarts (`data/memory/.chat_history.json`), `src/ui/memory_view.py` (Memory screen to add/edit/delete notes), `src/core/approvals.py` (`ApprovalQueue` + `second_check` review step), `src/ui/approvals_view.py` (Approvals screen + live sidebar `Approvals (N)` badge synced with Chat cards), and `src/core/router.py` (cheap-model routing for easy jobs). 139 tests + 19-step walkthrough pass; fresh "hello" with all 12 tools is 398 tokens. Full diff log in `docs/ANTIGRAVITY_CHANGES.md`. | Real-phone Telegram test and real Discord bot test still open. Workspace Python venv awaits owner decision. | Owner tries Session 4 (Memory, Approvals, PDF reading); then Session 5 (always-on loop, Activity screen, live Usage graphs, Pause All) |
| 2026-11-21 | 3b (wave 2, Antigravity) | Added two-way any-file sharing across Desktop Chat (`+` button -> removable attachment chips -> `workspace/inbox/`, `send_file` tool -> clickable `Open` / `Save as...` / `Show in folder` cards), Telegram (`getFile` download + `sendDocument` multipart upload), and a new pure-stdlib Discord connector (`src/connectors/discord/`: 6-step guided setup, Bot token + User ID test, pure-stdlib RFC 6455 TLS WebSocket Gateway v10, owner-only DMs, two-way file sharing, scrollable connector guides). Owner chose to skip Google RCS for now. 131 tests + 17-step walkthrough pass; fresh "hello" with all 9 tools is 349 tokens; real Discord API rejected a fake token cleanly. Full diff log in `docs/ANTIGRAVITY_CHANGES.md`. | Real-phone Telegram test and real Discord bot test still open. Workspace Python venv awaits owner decision. | Owner tries files, PDFs, Discord/Telegram in the window; decide workspace venv; then Session 4 (memory, full Approvals screen) |
| 2026-11-21 | 3b (follow-up, Antigravity) | Fixed the output-limit cut-off (`limits.max_output_tokens_with_tools` = 4,096 when tools are on; automatic 1-time retry in smaller parts when cut off or tool-call JSON is truncated; plain-language fallback with no `settings.json` jargon) and added built-in `make_pdf` tool (`src/core/pdf.py` + `Browser.print_pdf` via hidden Edge `--print-to-pdf`, HTML-escaped with strict CSP, supports direct Markdown/text or workspace `source_file`, nothing to install). Also isolated `Controller`'s default `usage_dir` next to `settings.path` so tests do not write to real `data/usage/`. 115 tests + 15 walkthrough steps pass; fresh "hello" with all 8 tools is 335 tokens; real Gemini `"make me a pdf file for criminology"` succeeded in 3 calls (2,282 tokens, 43 KB PDF, 0 approvals). Full diff log in `docs/ANTIGRAVITY_CHANGES.md`. | Agent workspace Python venv (`pip install` isolation inside 5 GB cap) awaits owner decision. Owner to retry in the window. Real-phone Telegram test still open. | Decide workspace venv with owner; check owner's feedback on the window test; then Telegram phone test or Session 4 |
| 2026-11-21 | 3b (owner's first try, then paused) | The owner tried the agent in the window. Approval cards worked (approved `pip install reportlab`). | "Make me a pdf" ended with "reply cut off by the output limit" (1,024 output tokens is too small for tool calls that write files). pip installed into the owner's own Python, not the workspace. | Fix the cut-off and add a built-in PDF tool (Edge print-to-PDF); discuss giving the agent its own Python in the workspace |
| 2026-11-21 | 3b (added by owner) | The agent's own base: a workspace folder capped at 5 GB, every program it runs capped at 2 GB of RAM and 2 minutes by a Windows job object, file tools, a PowerShell terminal tool, and a read-and-search browser (hidden Edge, own profile). A command check lets safe commands run and sends risky ones to an approval card (Approve, Deny, Always allow), with a never-allowed list. Permissions can grow: each risky kind can be set from Ask first to Allowed in Settings, and rules can be removed. 104 tests and the 15-step walkthrough pass; real Gemini wrote files, ran commands, searched the web and respected a Deny. "Hello" with all tools on is about 318 tokens. | Owner has not tried it in the window yet. It is a fence, not a vault: scripts can reach the rest of the PC (risky ones ask). Store apps escape the memory cap, so opening programs asks. The real-phone Telegram test is still open. | Owner tries the agent in the window. Then Session 4 (memory, full Approvals screen) |
| 2026-11-21 | 3 | Connector framework (`src/connectors/`: each folder has guide.json, connector.py, test.py and README.md, found automatically) and the Telegram connector (standard library only). Connectors screen with status cards and On/Off, a guided setup page drawn from guide.json with Paste and Test connection, and an optional "Chat from your phone?" welcome step. Pilot answers only the owner's chat, waiting costs no tokens, and phone and window share one chat. Added `tools/walkthrough.py`, which presses the window's buttons with a fake model and fake Telegram and checks 12 steps; it found a sidebar bug, now fixed. 80 tests pass. | Not yet tried with a real bot and phone (the done-criterion). Telegram only runs while the window is open (Session 5). Text messages only. | Owner sets up a real bot from the app and messages it. Then Session 4 |
| 2026-11-21 | 2 | Desktop window (tkinter, nothing to install): Chat and Settings screens, slim sidebar, dark and light themes, and a 3-step welcome flow (pick provider, paste and test key, say hello). Logic lives in a testable `ui/controller.py`, and setup rules are shared with the terminal in `core/configure.py`. Launch with `python -m src window` or `Pilot.pyw`. 67 tests pass, screens were screenshot-checked on the owner's PC, and a real Gemini chat through the window code took 1 call, 50 tokens, $0. | Owner confirmed the window works. The welcome "connect one connector" step waits for Session 3. No chat scrollbar (mouse wheel only). | Owner tries the window. Then Session 3 (connector framework and Telegram) |
| 2026-11-21 | 1 (follow-up) | Ran on the owner's Windows PC: installed Python 3.13, 51 tests passed. The owner chose not to pay for Anthropic, so a free Google Gemini provider was added and made the default. Setup now picks the key's real models and refuses keys or commands typed into model questions. Measured a fresh "hello": **41 input tokens** (target 3,000 or less). | Anthropic is still untested against the real API. | Session 2 |
| 2026-11-21 | 1 | Repo set up, Hermes read (reference only), provider system (Anthropic and mock), terminal chat, setup command, lazy tool loading, small context window, per-call usage log with daily token and money caps, 8-call cap per task. 51 unit tests pass using a fake network and a mock model. | Never run against the real Anthropic API (no key in the build environment), so the real token count of a fresh 'hello' is unmeasured. Offline estimate only. | On Windows: run `python -m src setup`, then `python -m src check`. Then Session 2 (desktop window) |
| 2026-11-21 | Planning | Blueprint written, no code yet | Project name not chosen; Windows machine still being refreshed | Start Session 1 |

## Future plans: customer version (later)

This app is personal first, built for one user. A customer version is a later decision. Do not build any of the later items now, but do follow the "keep in mind now" rules so the switch is easy.

### Keep in mind now

- Keep every setting, key, memory file, and usage log in a per-user folder, so one user's data is never mixed with another's.
- Keep the model provider, the connectors, and the interface as separate parts, so the interface can change without touching the agent core.
- Keep usage caps (tokens and money) as per-user settings, not global constants.
- Never hardcode the owner's name, keys, accounts, or paths anywhere in the code.
- Keep the guided connector setup written for a beginner, since customers will need the same walkthroughs.

### Later phase, in order

1. Sign in and accounts, so each customer has their own data and limits.
2. A simplified customer interface on top of the same agent core.
3. Hosting: each customer's agent runs on a small cloud server instead of the customer's own computer.
4. Plans with a monthly usage allowance. Idea to test: about $10 to $15 a month, with extra usage for heavy users, or customers bring their own API key and pay only for the app and hosting.
5. A plain-language privacy summary shown during setup, plus proper terms of service.

### Before setting a price

Work out the real cost per customer at the usage cap, so the margin is known. The server is the small part: a basic cloud server is about $6 a month in OpenClaw's own setup guide. Model usage is the large part, and it grows with how much each customer uses the agent (AIMultiple measured about $11 a day for OpenClaw in its test, including days with no report). The caps and the small context rules in this blueprint are what keep a low price workable. This is planning only, not financial advice.
