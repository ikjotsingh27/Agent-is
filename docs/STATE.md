# State (read this first at the start of every session)

Last updated: 2026-11-21, **Full Blueprint + Online Workspace & Free HTTPS Tunnel complete** (all 6 sessions + free Cloudflare Tunnel public HTTPS link, Web UI Workspace Files & Live Terminal tab, GitHub Pages site `docs/index.html`, cloud-ready `Dockerfile`, and `docs/UI_DESIGN_SPEC.md` data contract; **178 tests pass**; see `docs/ANTIGRAVITY_CHANGES.md` for exact diff details).

## Where we are

Sessions 1 and 2 are done and confirmed by the owner on their Windows 11 PC (Session 1: real Gemini, 41-token hello; Session 2: the owner opened the window and said "it works").

Session 3 (connector framework and Telegram) is built and now also supports two-way file transfer + a full Discord DM connector (`src/connectors/discord/`). **Not done yet:** live tests with real accounts (real Telegram phone bot, real Discord bot, real Gmail App Password, real Google Calendar secret iCal link).

Session 3b (agent workspace, terminal, browser, adjustable permissions), added by the owner, is built and tested, plus: output-limit fix + auto-retry, built-in `make_pdf` tool, native PDF/Office (`docx`/`pptx`/`xlsx`/`odt`) text extraction in `read_file`, two-way any-file sharing in Chat / Telegram / Discord (`+` button, removable attachment chips, `send_file`, clickable file cards), DuckDuckGo search fallback, and optional isolated Workspace Python (`data/agent/venv`).

Session 4 + owner follow-up requests (memory files & Memory screen, persistent chat history saved automatically across restarts in `data/chats/` without a cluttered sessions/projects sidebar, live step-by-step thinking & collapsible `Thought & steps` accordion, password-protected web UI in `src/core/web_server.py`, Approvals queue & screen with sidebar badge, second-check safety review, and cheap-model routing) are built and verified.

Session 5 (always-on background scheduler & quiet runs, `python -m src daemon` / `Pilot.pyw --background`, Start with Windows toggle, searchable **Activity** timeline & plain-language daily summaries, upgraded **Usage & System Resources** dashboard with live RAM/CPU/GPU/disk gauges + sparkline + alerts + export, and the big red **Pause All** button across desktop and web) is built and verified.

Session 6 + full-blueprint audit (Gmail connector scoped to one mailbox/label with read-only default and mandatory approval for `gmail_send`, Google Calendar read-only `.ics` connector, Green/Yellow/Red **Connector health** overview + live `Check health` button on Connectors & Web UI, DuckDuckGo search fallback, automatic cleanup of old `data/tool_output/` files, per-command Activity logging, optional Workspace Python venv in Settings, pure-stdlib **Voice notes** in Chat/Web/Telegram/Discord via Gemini `input_audio`, lazy `data/skills/*.md` loader, connector-labeled Approvals with cross-screen sync & new-email/large-purchase second check, money/trading `NEVER` block, beginner Settings limits controls, and Welcome privacy summary + multi-connector step) is built and verified: **172 unit tests and the 24-step walkthrough pass; 398 offline tokens; real Gemini measured 388 total tokens on `"hello"` and heard a real `.wav` audio clip cleanly**. **See `docs/ANTIGRAVITY_CHANGES.md`** for the exact file-by-file record of all changes made since Claude paused.

## What exists

- `src/core/settings.py`: settings file with defaults, API keys kept only in `data/settings.json`, keys hidden in any printed text. Default provider is `gemini`; `ui.theme` is `dark` or `light`. Limits include `max_output_tokens` (1024) and `max_output_tokens_with_tools` (4096).
- `src/core/providers/`: one `Provider.complete()` interface (`Response` includes `truncated: bool`).
  - `openai_compatible.py`: Google Gemini (free tier) through Google's OpenAI-compatible endpoint. The key goes in a header, never the URL. `list_models()` asks which models a key can use (no tokens). `reasoning_effort: low` keeps hidden thinking short. Cut-off replies (`finish_reason == "length"` with empty output) and truncated/malformed tool-call JSON are marked `truncated=True` and broken tool calls are dropped instead of executed with `{}`.
  - `anthropic_provider.py`: Anthropic (paid). Prompt caching is on. Never tested against the real API. Also marks `stop_reason == "max_tokens"` empty replies or incomplete `tool_use` blocks as `truncated=True`.
  - `mock_provider.py`: fake replies for tests and `--demo`.
  - `__init__.py`: `PROVIDER_INFO` (label, default models, key help) and `get_provider()`.
- `src/core/configure.py`: setup rules shared by the terminal and the window. Model-name checks (refuse commands and pasted keys), picking free models, setting the free-tier price to $0, cleaning junk price entries, and the one-call connection test.
- `src/core/agent.py`: the agent loop. Hard cap of 8 model calls per task, rollback of the turn on any error, trimming of big tool output. Raises `max_tokens` to `limits.max_output_tokens_with_tools` (4096) when tools are enabled. If a reply or tool call is cut off by the output limit, automatically retries once instructing the model to work in smaller parts; if still cut off, shows a plain-language message (no `settings.json` jargon). Partial cut-off text replies keep their text and add a short continuation note.
- `src/core/context.py`: sends only the last 6 whole turns. Older turns become short summary lines.
- `src/core/tools.py`: lazy tool loading. With no tools enabled, no tool data is sent.
- `src/core/usage.py`: one log line per model call in `data/usage/YYYY-MM-DD.jsonl`, daily token and dollar limits.
- `src/cli.py`: `setup`, chat (default), `check`, `window`, `--demo`. The terminal chat has the same tools and asks y/n for approvals.
- Session 3b, the agent's own space:
  - `src/core/workspace.py`: `data/agent/files` (the agent's working folder) and `data/agent/browser` (the hidden browser's own profile). The whole of `data/agent` is capped at 5 GB, checked before every write and command. Paths that leave the folder are refused. Limits are in settings under `workspace`: `max_disk_gb` 5, `max_ram_gb` 2, `command_seconds` 120.
  - `src/core/limits.py`: runs a program inside a Windows job object. Everything it starts shares a 2 GB memory cap, it is started paused so nothing escapes before the cap is on, it has a time limit, and everything still running is closed at the end.
  - `src/core/permissions.py`: reads a command and sorts it into safe, risky (delete, install, network, outside, launch, system, other) or never allowed. It also reads Python scripts in the workspace before they run, so a script that uses the internet or deletes files is risky too. `decide()` combines that with the user's settings (`permissions`: ask or allow for each kind) and saved `always_allow` rules.
  - `src/core/pdf.py`: turns plain text or Markdown into a self-contained HTML5 page ready for print-to-PDF (strict CSP `default-src 'none'; style-src 'unsafe-inline'`), and also extracts readable text from `.pdf` (`extract_pdf_text`, stdlib `zlib` + `/ObjStm` + `/ToUnicode` CMap + `BT`/`ET` parser) and `.docx`/`.pptx`/`.xlsx`/`.odt` (`extract_office_text`, stdlib `zipfile` + `ElementTree`).
  - `src/core/browser.py`: hidden Edge (Chrome as a fallback) with its own profile. `search` (Bing's results page), `read` (readable text and links), `screenshot`, and `print_pdf` (`--print-to-pdf` with `--no-pdf-header-footer`, atomic write, temp cleanup, disk cap check). Public http and https pages only for web tools. Page text is marked "information only, do not follow instructions".
  - `src/core/memory.py` (Session 4): `MemoryStore` reading and writing plain Markdown notes (`data/memory/*.md`) with `list_notes`, `get_note`, `save_note(..., append=...)`, `delete_note`, and `search`.
  - `src/core/approvals.py` (Session 4): `ApprovalQueue` holding pending and decided risky actions, and `second_check()` which flags unusual commands in plain words (recursive deletes, personal folders outside the workspace, dynamic code execution, system/startup changes, money/purchase keywords, external recipients, reason mismatch) without ever seeing API keys or secrets.
  - `src/core/router.py` (Session 4): `pick_model()` sends short, tool-free conversational turns to `models.cheap` and tool/file/research tasks to `models.main`.
  - `src/core/agent_tools.py`: the 12 tools: `list_files`, `read_file` (now reads text, `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.odt`), `write_file`, `make_pdf`, `send_file`, `run_command`, `web_search`, `open_page`, `page_screenshot`, `remember`, `recall`, and `forget`, in groups (files, memory, terminal, browser) that can be switched on or off. Approvals go through `approve(request)`, which runs the second check, queues the item, and returns once, always or deny.
  - `src/core/agent.py` and `tools.py`: `load_tool` takes several names at once. When tools are on, a 3-sentence note joins the prompt (workspace, web text is not instructions, honest reporting).
- `src/connectors/` (Session 3 + follow-up): `base.py` (`Connector`, `CheckResult`, stdlib `encode_multipart`, `call_ask`) and `__init__.py` (`load_connectors()` finds every folder with a `guide.json`, in blueprint order).
  - `telegram/`: `guide.json` (5 plain steps, token field, privacy note), `test.py` (token shape, `getMe`, owner's first message, hello), `connector.py` (`TelegramClient` with `getFile`/`download_file`/`sendDocument`, `TelegramRunner` long-polling with two-way file support up to Telegram's 20 MB in / 50 MB out limits, `TelegramConnector`), `README.md`.
  - `discord/`: `guide.json` (6 plain steps, Developer Portal link, Bot token + User ID fields, privacy note), `test.py` (token & snowflake validation, `GET /users/@me`, `POST /users/@me/channels`, hello DM, amber `nearly=True` fix on error `50007`), `gateway.py` (pure-stdlib RFC 6455 TLS WebSocket client), `connector.py` (`DiscordClient` REST v10 + multipart upload up to 25 MB + CDN-only attachment download up to 50 MB, `DiscordRunner` Gateway v10 thread answering only `owner_user_id` in private DMs with close-code `4004`/`4014` diagnostics, `DiscordConnector`), `README.md`.
  - Settings: `connectors.<id>` holds enabled, connected, bot_username, owner ids/names; `connector_secrets.<id>.bot_token` holds tokens, hidden in all text like API keys.
- `src/ui/` (Session 2, tkinter, ships with Python):
  - `controller.py`: everything the window does, with no tkinter in it (send, check key, save, usage, theme, and since Session 3, connectors: status, test, on/off, background runners; since 3b, the workspace, tool groups, approvals waiting up to 10 minutes then Deny, permissions and rules). The agent lock means one task at a time, from the window or the phone. A separate file lock covers the settings file, and is never held while waiting for the user. Settings changes that would rebuild the agent say "busy" during a task instead of freezing the window. Unit-tested.
  - `app.py`: the window, slim sidebar (Chat, Connectors, Settings), screen switching, background thread for model calls, connector news handed safely to the window, and connectors stopped when the window closes.
  - `connectors_view.py` (Session 3): the cards (status, Set up, Turn on or off) and the guided setup page drawn from `guide.json` (numbered steps, link buttons, Paste, Test connection, a plain result in green, amber or red).
  - `theme.py`: all colors and fonts, dark and light, plus widget helpers.
  - `chat_view.py`: conversation, status dot (green ready, amber thinking, red problem) with a line saying what the agent is doing ("Running a command...", "Searching the web for..."), message box (Enter sends, Shift+Enter new line), New chat, Yes/No box at a daily limit, and approval cards (the exact command, why, what makes it risky, with Approve, Deny and "Always allow").
  - `approvals_view.py` (Session 4): waiting risky actions with `Second check:` warnings and Approve / Deny / Always allow buttons, plus Recent decisions history.
  - `memory_view.py` (Session 4): create, read, edit, and delete plain Markdown notes in `data/memory/`, plus Open folder.
  - `settings_view.py` (scrolls now): provider dropdown, masked key field with Paste and "Get a key", Test connection (fills the model dropdowns with the key's real models), Save (takes effect at once, chat kept), the Agent workspace card (path, space used, Open folder, Files/Memory/Terminal/Browser on or off), the Permissions card (Ask first or Allowed for each kind, the never-allowed list, always-allow rules with Remove), Dark/Light, today's usage.
  - `welcome_view.py`: first launch when there is no key. 1) pick Gemini (free, recommended) or Anthropic (paid), 2) step-by-step key instructions, "Open the key page", Paste, Test key (must pass, then it saves), 3) optional "Chat from your phone?": Set up Telegram, or Skip for now, 4) "Start chatting" opens Chat with "Hello!" typed in.
- `Pilot.pyw`: double-click to open the window with no console. If it cannot start, it shows a message box and writes `data/crash.log` with keys removed.
- `tools/walkthrough.py`: presses the real window's buttons like a new user would, with a fake model, a fake Telegram and a throwaway settings folder, and checks 19 steps (including two-way file sharing, the Discord guide, the Memory screen, and the Approvals screen + sidebar badge). `--shots FOLDER` saves a screenshot of each step. `tools/screenshot.py` saves PNGs with the standard library only. Run it after any screen change.
- `tests/`: 139 unit tests (stdlib `unittest`), including the window smoke test, Telegram and Discord connectors (with two-way file transfer and a real loopback WebSocket test), workspace inbox + `send_file`, native PDF/DOCX extraction, Session 4 memory/approvals/second-check/router, limits, permissions, browser parsing, PDF rendering (unit + real Edge), truncation retry, and the 19-step walkthrough.

## Test results

- 2026-11-21, owner's PC, Python 3.13.17 (installed this session with winget, user scope): `python -m unittest discover -s tests -t .` ran 67 tests, all passed.
- **MEASURED 2026-11-21 on the owner's Windows 11 PC:** `python -m src check` against the real Gemini API (gemini-3.5-flash-lite, free tier) reported **41 input tokens** for a fresh "hello" with no connectors on. Target 3,000 or less: OK. Setup's key test passed ("Connected. The model answered: OK.").
- 2026-11-21: the window's chat path (`Controller.send("hello")`) against real Gemini replied "Hello! How can I help you today?" in 1 call, 50 tokens (input plus output), $0.
- 2026-11-21: screenshots of the welcome steps 1 and 2, Chat (dark and light) and Settings (dark) checked on the owner's PC. Two layout bugs were found and fixed: the message box was pushed off screen, and the Send button was hidden.

- 2026-11-21 (Session 3): 80 unit tests pass. The visible walkthrough passed 12 of 12 steps on the owner's PC, and its screenshots were checked. On its first run it found 4 failures: the sidebar reopened a half-done setup page instead of the Connectors cards (a real bug, fixed), and the Enter key was not reaching the chat box (a test problem: a person clicks into the box first, and the walkthrough now does too). Enter works.
- 2026-11-21 (Session 3): the real Telegram API was called with a fake token and gave "Telegram did not accept this bot token." with the BotFather fix.

- 2026-11-21 (Session 3b, owner's PC): 104 unit tests pass, and the walkthrough passes 15 of 15. Its screenshots caught an approval card whose buttons were off screen (fixed). Limits checked for real: a 3 GB grab inside a 512 MB cap failed with MemoryError, a 60-second command was stopped at the time limit, and a normal program it started was closed afterwards. Hidden Edge, inside the cap: a search gave 8 results in about 12 seconds (first run), a page read took about 8 seconds, and a screenshot under 1 second.
- 2026-11-21 (Session 3b, real Gemini, gemini-3.5-flash-lite, throwaway workspace): it wrote test/hello.txt and listed files (3 calls, no approval), searched the web and answered (3 calls), and on "delete test/hello.txt" showed an approval card; the Deny was respected and the reply said so honestly. The real test found a bug: `Get-Date -UFormat "%Y-%m-%d"` asked for approval because `%` was read as a Windows path variable. After the fix it ran without asking and gave the right date.
- 2026-11-21 (Session 3b): a fresh "hello" with all 7 tools on is about **318 input tokens** (offline estimate, target 3,000), because only one line per tool is sent until a tool is loaded.
- 2026-11-21 (Session 4, owner's PC): **139 unit tests pass** (including all 19 walkthrough steps, Session 4 memory/approvals/second-check/router tests, and native PDF/DOCX `read_file` extraction). A fresh "hello" with all 12 tools on is **398 input tokens** (offline estimate, target 3,000).
- 2026-11-21 (Session 4): verified `extract_pdf_text()` on the owner's uploaded `Discussion Assignments (1).pdf` (extracted 6,616 characters of clean text in ~10 ms using stdlib `re` + `zlib`).
- 2026-11-21 (Session 3b follow-up): real Discord API (`discord.com/api/v10`) called with a fake token returned `"Discord did not accept this bot token."` with the Developer Portal Reset Token fix (no Cloudflare block).
- 2026-11-21 (Session 3b follow-up, real Gemini, `gemini-3.5-flash-lite`, throwaway workspace): ran the exact failing user prompt `"make me a pdf file for criminology"`. Gemini loaded `make_pdf` (no `pip install`, 0 approval prompts), generated a real 43 KB `%PDF-1.4` file (`criminology_overview.pdf`), and answered cleanly in **3 calls, 2,282 tokens total**.
- 2026-11-21 (Session 3b follow-up): fixed `Controller.__init__` to default `usage_dir` next to `settings.path` (matching how `workspace` already worked), so tests with temp settings no longer pollute the real `data/usage/` log.

## Not verified yet

- **Session 3b in the window by the owner:** asking the agent to do real work, approval cards, and the Permissions card.
- Windows Store apps (like the new Notepad) started by a command are launched by Windows itself, outside the memory cap and clean-up. That is why opening programs is a risky kind that asks first.
- Search quality through Bing's page varies: one test query gave fairly generic results. It may need a second search source.

- **Session 3 on a real phone:** making a bot with BotFather, the in-app test with a real token, and chatting from the phone.

- The owner confirmed the window works (2026-11-21) with their saved key. Not yet tried by hand: the welcome flow from scratch (it only shows when no key is saved) and the Yes/No box at a daily limit.
- `Pilot.pyw` by double-click (needs the `.pyw` file type linked to Python, which the python.org installer normally does).
- Anthropic against the real API (the owner chose free Gemini, so this may never be needed).
- Gemini free-tier limits are set by Google per project and change. Check them in AI Studio.
- Claude prices in settings were looked up in October 2026 and can change.

## Decisions and simplifications

- 2026-11-21 (Session 3b, owner's choices): a Pilot folder with limits, not a Linux sandbox (WSL is not installed, Windows 11 Home has no Windows Sandbox, and this needs nothing installed). Only risky commands ask. The browser reads and searches, with no logins or forms. **Honest limit:** this is a fence, not a vault. A script the agent writes can reach the rest of the PC; the command check reads scripts first and asks when it sees internet, delete or system use, but a determined script could hide that.
- 2026-11-21 (Session 3b, owner's request): **permissions can grow.** Each risky kind can be switched from "Ask first" to "Allowed" in Settings, and approval cards offer "Always allow" for plain commands (rules show in Settings with Remove). "Always allow" is not offered for paths outside the workspace, system changes or commands Pilot cannot check; those can only be allowed for the whole kind. Never allowed whatever the settings say: reading saved passwords or credential stores, reading Pilot's own settings or usage files, turning off Windows security; moving money and trading stay blocked by the blueprint.
- 2026-11-21 (Session 3b): a small piece of Session 4 was built early: approval cards in Chat (with 10 minutes and no answer meaning Deny). The Approvals screen with a pending list and sidebar badge, and the "second check" review step, are still Session 4.
- 2026-11-21 (Session 3b): the walkthrough runs off screen inside the unit tests and clicks Send there instead of pressing Enter, so it never takes your keyboard; the visible run (`--shots`) tests Enter.

- 2026-11-21 (Session 3): **Pilot answers only the owner.** The first private chat that messages the bot during setup becomes `owner_chat_id`; everyone else gets no reply. Why: a bot username can be found by anyone, and the agent and the owner's quota must not be usable by strangers. The test result names the owner ("Pilot will answer only Sam") so the user can spot a mix-up.
- 2026-11-21 (Session 3): **one conversation for the phone and the window.** Phone messages show in the window's Chat, marked "on Telegram". One lock means one message at a time.
- 2026-11-21 (Session 3): **daily limits are never overridden from the phone.** The phone gets "Open Pilot on your computer to decide whether to go on." Only the window can say yes.
- 2026-11-21 (Session 3): replying to the owner in their own bot chat is the conversation itself, not "sending a message on the user's behalf", so it does not wait for approval. Approvals (Session 4) cover messages to other people.
- 2026-11-21 (Session 3): `/start` and non-text messages (photos, voice notes) get a fixed reply with no model call. Voice notes come later.
- 2026-11-21 (Session 3): Telegram runs only while the window is open; the guide says so plainly. Always-on is Session 5.
- 2026-11-21 (Session 3): the welcome's "connect one connector" step is optional (Skip for now), so the welcome still takes under five minutes for someone who does not use Telegram.
- 2026-11-21 (Session 3): the sidebar's Connectors button always opens the cards, never a half-done setup page.

- 2026-11-21 (Session 2): **tkinter for the window.** It ships with Python, so there is still nothing to install. Cards are flat panels, not rounded, because tkinter cannot round corners simply. There is no scrollbar in Chat, because tkinter cannot color it on Windows and a white bar looked broken; the mouse wheel scrolls.
- 2026-11-21 (Session 2): the window's logic lives in `src/ui/controller.py` so it is testable without a screen. Setup rules moved from `cli.py` into `src/core/configure.py`, so the terminal setup and the window cannot drift apart. `cli.py` behaves exactly as before.
- 2026-11-21 (Session 2): provider error messages now say "in Settings (or run 'python -m src setup')", because they show in both the window and the terminal.
- 2026-11-21 (Session 2): the welcome step "connect one connector" is left out until Session 3, because no connectors exist yet. The last welcome page says connecting apps comes later.
- 2026-11-21 (Session 1 follow-up): the owner does not want to pay for the Anthropic API, so the default provider is now Google Gemini's free tier. Anthropic still works by choosing it in Settings. Setup asks Google which models the key can use and picks the newest stable Flash-Lite (the most free requests per day) instead of hardcoding a name, because model names change often. Free-tier caveats: Google sets daily and per-minute limits, and on the free tier Google may use prompts to improve its products (the welcome screen says so). The same provider file can add Groq, OpenRouter or Ollama later as one more entry.
- 2026-11-21 (Session 1 follow-up): setup refuses a pasted API key or a command typed into a model question, confirms "Got your key (N characters)" after the hidden key question, and removes junk left by earlier attempts. Why: the owner's key was pasted into a visible model question and saved as a model name. That key was shown on screen and in the build chat, and the owner was asked to delete it and make a new one.
- Hermes is far too large to copy (over a million lines). Pilot is a small rewrite that borrows ideas only. Nothing was copied, see `NOTICE.md`.
- Hermes sends tool definitions and a skills list on every call and allows very many iterations. Pilot avoids all three: lazy tools, no skills list in the prompt, 8-call cap.
- Old turns are summarized by trimming (free), not by a model call.
- The conversation lives in memory only. It is lost when the program closes. Saved chats come with the memory work in Session 4.
- Python standard library only, so there is nothing to install and nothing to break on Windows.

## Session 3 plan (connector framework and Telegram) - built as planned

Done when: the user sets up Telegram only by following in-app steps and can message the agent from their phone.

- **Framework (`src/connectors/`).** Each connector folder holds `guide.json` (plain-language steps, fields, privacy note), `connector.py` (logic), `test.py` (the connection check, returns a plain pass or fail with a fix), and `README.md`. `src/connectors/__init__.py` finds the folders, so adding a connector means adding a folder.
- **Settings.** `connectors.<id>` holds on/off, connected, and what the test learned. Secrets such as the bot token live in `connector_secrets.<id>` in `data/settings.json`, and are hidden in all text like API keys. A card shows Connected only after a passing test.
- **Telegram, standard library only (Bot API over HTTPS).** The test checks the token format, asks Telegram who the bot is, and looks for the user's first message to the bot. That person's chat becomes the **only** chat Pilot answers; everyone else is ignored, so a stranger who finds the bot cannot use the owner's agent or quota. On success, the bot sends the owner a "Pilot is connected" message.
- **Running.** While the window is open, a background thread long-polls Telegram. No messages means no model calls (quiet runs). Phone and window share one conversation, guarded by a lock, and phone messages show in the window's chat marked "on Telegram". Problems are reported, never silent: the bot replies with the plain-words error, and the card shows Problem. A daily limit on the phone gets a reply saying to continue from the computer, with no override from the phone. Running with the window closed is Session 5.
- **Screens.** A Connectors screen (cards with status and an On/Off switch) and a guided setup page drawn from `guide.json` (numbered steps, link buttons, a Paste field, Test connection, plain result). The welcome flow gains an optional step: "Set up Telegram" or "Skip for now".
- **Button-pressing walkthrough (`tools/walkthrough.py`).** It drives the real window with a fake model and a fake Telegram: clicks through welcome, pastes, tests, connects Telegram, receives a phone message, switches theme, and answers the daily-limit box. It checks the result of every step and saves a screenshot of each to a folder. A unit test runs the same walkthrough without screenshots.
- **Not in Session 3:** photos and voice notes from Telegram (text only for now, with a polite reply for anything else), approvals (Session 4), and always-on running (Session 5).

## Session 3b plan (agent workspace, terminal, browser), added by the owner on 2026-11-21

The owner put the real Telegram phone test aside for later and asked for the agent's own base first: about 5 GB of disk, 2 GB of RAM, a browser and a terminal. Owner's choices: a Pilot folder with limits (not a Linux sandbox), approval only for risky commands, and a read-and-search browser (no logins or forms).

Done when: in the window, the agent can make files in its workspace, run safe commands there without asking, ask Approve or Deny for risky ones, and search the web and read pages. The 5 GB and 2 GB limits must actually hold.

- **Workspace** (`src/core/workspace.py`): `data/agent/` holds `files/` (the agent's working folder) and `browser/` (the hidden browser's own profile). Pilot caps the whole of `data/agent/` at 5 GB: it checks before every write and command, and stops with a plain message when full.
- **RAM limit** (`src/core/limits.py`): every program the agent starts (terminal commands and the hidden browser) runs inside a Windows "job object" capped at 2 GB in total, with a time limit (120 seconds for commands). When the job ends, everything it started is closed, so nothing keeps running in the background. Standard library only (ctypes).
- **Tools**, loaded only when the model asks for them:
  - files: `list_files`, `read_file`, `write_file`, inside the workspace only.
  - terminal: `run_command`, in PowerShell inside the workspace.
  - browser: `web_search`, `open_page` (readable text and links), `page_screenshot`.
  - Groups can be switched on or off in Settings.
- **Risk check for commands** (`src/core/command_risk.py`): reading, listing, writing in the workspace and running a script there just run. These wait for Approve: deleting, installing, network commands, anything that names a path outside the workspace, and system or registry changes. Honest limit: a script the agent writes can still reach the rest of the PC. It is a fence, not a vault, as the owner was told.
- **Permissions that can grow** (owner's request, 2026-11-21): each kind of risky action (delete, install, network, outside the workspace, system changes) has its own setting, "Ask first" (default) or "Allowed", changeable later in a Permissions card in Settings as the agent earns trust. The Approve card adds "Always allow commands like this", which saves a rule listed in Settings with a Remove button. Some things stay blocked whatever the settings say, matching the blueprint's "never allowed": reading saved passwords or credential stores, moving money, and trading.
- **Approve or Deny** (an early piece of Session 4): a card in Chat shows the exact command and why the agent wants it. With no answer after 10 minutes, the answer is Deny. The terminal chat asks y/n. The full Approvals screen stays in Session 4.
- **Browser:** hidden Edge (`--headless`), already on the PC, so nothing to install. Search goes through Bing's results page. Only http and https; local network addresses and files are refused. Page text is labelled as information only, never instructions (the blueprint's prompt-injection rule). Big pages are trimmed, with the full copy kept on disk.
- **Status line:** Chat shows what the agent is doing ("Running a command...", "Reading a page...").
- **Efficiency:** tool names go in the prompt (one line each); full definitions are sent only when asked for, and `load_tool` can load several at once. Check: a fresh "hello" must stay under 3,000 tokens with all tools on.

## Open issues

- *(Resolved 2026-11-21, Session 6)* Workspace Python isolation: added optional `Create workspace Python` / `Remove workspace Python` inside `data/agent/venv` in Settings -> Agent workspace, so future `pip install`s stay inside the 5 GB cap when enabled.
- *(Resolved 2026-11-21, Session 6)* Bing search fallback: added automatic DuckDuckGo HTML fallback when Bing returns 0 results.
- *(Resolved 2026-11-21, Session 3b follow-up)* Cut-off replies at 1,024 tokens and PDF generation: `limits.max_output_tokens_with_tools` is now 4,096; cut-off replies and broken tool-call JSON retry once in smaller parts and fall back to a plain-language message (no `settings.json` mention); built-in `make_pdf` tool renders Markdown/text or an existing workspace file to PDF via hidden Edge `--print-to-pdf` (nothing to install). See `docs/ANTIGRAVITY_CHANGES.md`.

## Next steps

1. **Owner hands-on testing:** Launch `python -m src window` and test Saved Sessions, live step-by-step thinking, Password-Protected Web Access, Activity & Usage Dashboard, Pause All, and optional `Create workspace Python`.
2. **Real connector tests:** Connect real accounts via guided setup in Connectors (Telegram bot on phone, Discord DM bot, Gmail App Password, Google Calendar secret iCal link) and run `Check health`.
3. **Always-on reboot test:** Enable `Start at login` in Settings -> Always-on & Schedules and verify background recovery (`pythonw -m src daemon`) after a Windows sign-out / reboot.
4. **First-run non-technical user test (Session 6 done-criterion):** Have someone unfamiliar with Pilot complete first-run setup alone in under 10 minutes.
5. **Optional future work:** Privacy & trust section in `docs/BLUEPRINT.md`, voice note transcription, or customer/hosted phase when ready.
