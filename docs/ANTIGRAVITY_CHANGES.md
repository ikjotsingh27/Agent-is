# Handoff & Exact Change Log (Antigravity -> Claude Code)

**Date:** 2026-11-21  
**Continued from:** Session 3b pause (after owner's first window try hit the 1,024-token cut-off on `"make me a pdf file for criminology"`).  
**Environment note:** `git` is not installed / not in `PATH` on this Windows PC, so changes are live in the working tree without a git commit. Run tests with:
```powershell
python -m unittest discover -s tests -t .
```

---

## Summary of What Was Done

Picked up directly from the top two open items recorded in `docs/STATE.md` and `docs/BLUEPRINT.md`:

1. **Fixed cut-off replies / output token cap when tools are enabled**
   - Kept `limits.max_output_tokens = 1024` for plain chat (preserving `tests/core/test_settings.py`).
   - Added `limits.max_output_tokens_with_tools = 4096` and used it in `Agent.run_task()` whenever any tools are enabled.
   - Added `Response.truncated: bool = False` so providers report cut-off / broken-JSON tool calls cleanly instead of silently turning truncated tool-call JSON into `{}` or returning a message telling beginners to edit `data/settings.json`.
   - Added a single automatic retry in `Agent.run_task()` when a response is truncated (appending a valid `assistant` placeholder + `user` smaller-parts hint so both Anthropic and Gemini role alternation stay valid), falling back to a plain-language message if still cut off.
   - Partial text replies cut off by `length`/`max_tokens` keep their text and append a plain continuation note.
2. **Added built-in `make_pdf` tool (hidden Edge `--print-to-pdf`, stdlib-only, nothing to install)**
   - Added `src/core/pdf.py` to convert Markdown or plain text to clean, print-styled HTML5. All text is `html.escape()`d before inline Markdown formatting, and `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">` blocks all scripts, local `file://` reads, and network requests.
   - Added `Browser.print_pdf()` in `src/core/browser.py` using `--no-pdf-header-footer` and `--print-to-pdf=...` inside the existing 2 GB Windows job object, with atomic temp-file replacement, `%PDF-` verification, disk-cap check, and guaranteed temp cleanup.
   - Added `make_pdf` to `AgentTools` (`TOOL_GROUPS["files"]`, index 4 right after `write_file` so `tools.tools()[3]` stays `write_file`). Supports either `content` directly or `source_file` (an existing `.md`/`.txt` file in the workspace for very long documents written in chunks).
3. **Small test-hygiene fix in `Controller.__init__`**
   - Defaulted `self.usage_dir` to `self.settings.path.parent / "usage"` when `self.settings.path` is set (matching how `workspace_root` already worked). Previously `tests/core/test_agent_tools.py` wrote mock `claude-sonnet-5-5` lines into the owner's real `data/usage/2026-11-21.jsonl`.

---

## Exact File-by-File Changes

### New Files Created

| File | Purpose |
| --- | --- |
| `src/core/pdf.py` | Pure-stdlib Markdown/plain-text to safe HTML5 converter (`markdown_to_html(text, title="")`) with print CSS (`@page` 18 mm margins, headings, lists, tables, blockquotes, code blocks, horizontal rules), full `html.escape()` prior to inline formatting, title deduplication, and strict CSP (`default-src 'none'; style-src 'unsafe-inline'`). |
| `docs/ANTIGRAVITY_CHANGES.md` | This handoff and exact change log. |
| `CLAUDE.md` | Short startup pointer inside `pilot/` telling Claude Code to read `docs/BLUEPRINT.md`, `docs/STATE.md`, and `docs/ANTIGRAVITY_CHANGES.md`. |
| `../CLAUDE.md` | 4-line pointer at `C:\Workstation for Claude\CLAUDE.md` in case Claude Code launches from the parent directory. |

### Existing Files Modified

#### 1. `src/core/settings.py`
- **`DEFAULTS["limits"]`**: added `"max_output_tokens_with_tools": 4096` alongside existing `"max_output_tokens": 1024`.

#### 2. `data/settings.example.json`
- **`limits`**: added `"max_output_tokens_with_tools": 4096` to stay in sync with `DEFAULTS`.

#### 3. `src/core/providers/base.py`
- **`Response`**: added field `truncated: bool = False`.

#### 4. `src/core/providers/openai_compatible.py`
- **`OpenAICompatibleProvider._parse()`**:
  - Tracks `bad_tool_args` when `json.loads(fn.get("arguments") or "{}")` raises `ValueError` or returns a non-`dict` (previously silently turned broken JSON into `{}`).
  - Removed the hardcoded text `"(The reply was cut off by the output limit. Raise limits.max_output_tokens in data/settings.json.)"`.
  - Sets `truncated = bad_tool_args or (finish == "length" and not text and not tool_calls)`.
  - When `truncated` is `True`, clears `tool_calls = []` and strips broken `tool_use` blocks from `content` so no incomplete tool call runs or poisons chat history.
  - Passes `truncated=truncated` into `Response(...)`.

#### 5. `src/core/providers/anthropic_provider.py`
- **`AnthropicProvider._parse()`**:
  - Reads `stop_reason = data.get("stop_reason", "")`.
  - Sets `truncated = (stop_reason == "max_tokens") and (bool(tool_calls) or not text)`.
  - When `truncated` is `True`, clears `tool_calls = []` and keeps only text blocks in `content`.
  - Passes `truncated=truncated` into `Response(...)`.

#### 6. `src/core/agent.py`
- Added constants:
  - `CUT_OFF_MESSAGE`: plain-language fallback without `settings.json` instructions.
  - `CUT_OFF_SUFFIX`: short continuation note appended when a partial text reply hits `length`/`max_tokens`.
- Added helper methods on `Agent`:
  - `_max_output_tokens(self, limits: dict) -> int`: returns `max(max_output_tokens, max_output_tokens_with_tools)` when `self.registry.names()` is non-empty, else `max_output_tokens`.
  - `_retry_hint(self) -> str`: asks the model to retry in smaller parts, mentioning `write_file` (`append=true`) and `make_pdf` (`source_file`) when those tools are enabled.
- Updated `Agent.run_task()`:
  - Uses `self._max_output_tokens(limits)` instead of raw `max_output_tokens`.
  - Checks `cut_off = response.truncated or (response.stop_reason in ("length", "max_tokens") and not response.text and not response.tool_calls)`.
  - On first `cut_off` (when `call_no < max_calls`), appends an assistant placeholder + user `_retry_hint()` and retries once (`retried_truncation = True`).
  - If still `cut_off` after retry (or on final call), appends and returns `CUT_OFF_MESSAGE`.
  - On non-empty text reply with `stop_reason in ("length", "max_tokens")` and no tool calls, keeps the text and appends `CUT_OFF_SUFFIX`.

#### 7. `src/core/browser.py`
- Added `import uuid`.
- **`Browser._launch(self, args, timeout=45, what="browse")`**: added optional `what` parameter (`"browse"` default keeps existing messages identical; `"pdf"` uses PDF-specific plain-language error messages for missing browser, timeout, or memory limit).
- Added **`Browser.print_pdf(self, html_doc: str, output_path: Path) -> Path`**:
  - Writes temp HTML inside `self.ws.root` (`data/agent/.tmp-pdf-<token>.html`).
  - Runs hidden Edge/Chrome via `_launch(["--no-pdf-header-footer", f"--print-to-pdf={tmp_pdf}", tmp_html.as_uri()], what="pdf")` inside the 2 GB memory cap.
  - Verifies output exists, is non-empty, and starts with `b"%PDF-"`.
  - Unlinks temp HTML, checks `self.ws.check_space(-old_size)` so the finished PDF respects the 5 GB disk cap, then atomically moves it with `os.replace(tmp_pdf, output_path)`.
  - Always cleans up temp files in `finally:`.

#### 8. `src/core/agent_tools.py`
- Imported `markdown_to_html` from `.pdf`.
- Added `"make_pdf"` to `TOOL_GROUPS["files"]` (`["list_files", "read_file", "write_file", "make_pdf"]`).
- Added `MAX_PDF_SOURCE_BYTES = 2 * 1024 * 1024`.
- Added **`AgentTools.make_pdf(self, args: dict) -> str`**:
  - Validates `path` (non-empty, appends `.pdf` if missing, workspace-confined via `self.ws.path()`, rejects directories).
  - Accepts either `content` (Markdown/plain text) or `source_file` (existing workspace file <= 2 MB), plus optional `title`. Rejects empty input or both at once.
  - Pre-checks disk space, updates activity status (`"Creating PDF: ..."`), converts via `markdown_to_html()`, prints via `self.browser.print_pdf()`, and returns saved path, size, and workspace summary.
- Registered `Tool("make_pdf", ...)` in `AgentTools.tools()` immediately after `write_file` (preserving `write_file` at index 3).

#### 9. `src/ui/controller.py`
- **`Controller.__init__`**: changed `self.usage_dir = usage_dir` to `self.usage_dir = usage_dir or (self.settings.path.parent / "usage" if self.settings.path else None)` so temporary settings paths in tests write usage into their temp folder instead of `data/usage/`.

#### 10. Tests Updated
- `tests/core/test_providers.py`: added 3 tests (`test_max_tokens_marks_empty_or_incomplete_tool_use_as_truncated`, `test_length_empty_reply_is_marked_truncated`, `test_broken_tool_call_json_is_marked_truncated_and_dropped`).
- `tests/core/test_agent.py`: added 4 tests (`test_output_limit_rises_when_tools_are_enabled`, `test_cut_off_reply_retries_once_in_smaller_parts_and_recovers`, `test_cut_off_twice_returns_friendly_message_without_settings_jargon`, `test_partial_text_cut_off_keeps_text_and_adds_note`).
- `tests/core/test_agent_tools.py`: expanded `test_tool_groups` for `make_pdf` and added `FakePdfRunner` + `PdfTests` (4 tests: `test_markdown_to_html_formats_blocks_and_escapes_unsafe_html`, `test_make_pdf_content_and_source_file_and_limits` including pre- and post-print disk cap checks, `test_make_pdf_without_browser_gives_plain_message`, and `test_make_pdf_real_browser` using real headless Edge).

#### 11. Docs Updated
- `docs/STATE.md`: updated "Where we are", "What exists", "Test results", "Open issues", and "Next steps".
- `docs/BLUEPRINT.md`: added newest Progress Log row at the top.

---

## Verification Results (2026-11-21)

### Wave 1 (Output-limit fix + `make_pdf`)
- **Unit tests + 15-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **115 tests OK** (up from 104).
- **Offline baseline token check:** `python -m src check --offline` -> **335 input tokens** with all 8 tools enabled (target <= 3,000: OK).
- **Real Edge PDF check:** `test_make_pdf_real_browser` generated a valid `%PDF-1.4` file in ~1.5 s inside the 2 GB Windows job object with no leftover temp files.
- **Real Gemini end-to-end check** (`gemini-3.5-flash-lite`, throwaway workspace, approvals auto-denied):
  - Prompt: `"make me a pdf file for criminology"`
  - Result: **3 calls, 2,282 tokens, 0 approval prompts**, loaded `['make_pdf']`, wrote valid 43,262-byte `%PDF-1.4` (`criminology_overview.pdf`), replied cleanly.

---

## Wave 2 (Same Date: 2026-11-21) — Two-Way File Exchange & Discord Connector

Requested by the owner:
- Let the user send **any kind of file** to Pilot AND let Pilot send any file back — in the **desktop Chat window**, **Telegram**, and **Discord** (keeping Pilot's existing UI style, no visual redesign).
- Add a **Discord connector** with the same guided detailing and safety rules as Telegram.
- *(Owner chose to skip Google RCS for now after reviewing that Google has no personal RCS bot API — only RCS for Business via Google Cloud test-device mode.)*

### Wave 2 — New Files Created

| File | Purpose |
| --- | --- |
| `src/connectors/discord/__init__.py` | Exports `DiscordConnector`, `create`. |
| `src/connectors/discord/guide.json` | 6 plain-language setup steps (Developer Portal link, Message Content Intent toggle, bot token, private server invite, Developer Mode User ID, Test connection), two fields (`bot_token` secret + `owner_user_id`), privacy note. |
| `src/connectors/discord/gateway.py` | Pure-stdlib RFC 6455 TLS WebSocket client (`WebSocketClient`, `WebSocketClosed`, `WebSocketError`) supporting masked sends, fragmented frames, Ping/Pong, and close-code parsing (`4004`, `4014`). |
| `src/connectors/discord/connector.py` | `DiscordError`, `DiscordClient` (REST v10 with `DiscordBot` User-Agent, multipart upload up to 25 MB, CDN-only attachment download up to 50 MB), `DiscordRunner` (Gateway v10 thread with heartbeats, reconnects, close-code `4004`/`4014` plain fixes, owner-only DM filter ignoring server channels/bots/strangers, two-way file exchange), `DiscordConnector`, `create()`. |
| `src/connectors/discord/test.py` | Validates bot token shape & numeric User ID before network, checks `GET /users/@me`, opens DM via `POST /users/@me/channels`, sends hello DM, returns amber `nearly=True` guidance on Discord error `50007` (DM blocked). |
| `src/connectors/discord/README.md` | Maintainer notes and rules matching `telegram/README.md`. |
| `tests/connectors/test_discord.py` | 11 unit tests covering registry discovery, validation, token redaction, `50007` `nearly` flow, owner-only DM filter, close code `4014`, message splitting, untrusted-host download rejection, two-way files through `Controller`, and a real loopback TCP WebSocket framing test. |

### Wave 2 — Existing Files Modified

#### 1. `src/core/workspace.py`
- Added `MAX_INCOMING_FILE_BYTES = 50 * 1024 * 1024`, `file_size_label(n)`, and `safe_filename(name, fallback="file")` (sanitizes path separators, Windows forbidden chars, `.`/`..`, long names).
- Added on `Workspace`:
  - `save_inbox_bytes(filename, data) -> Path`: enforces the 5 GB disk cap and writes to `data/agent/files/inbox/` with unique suffixing (`-2`, `-3`, ...).
  - `import_file(src) -> Path`: streams any local file from the user's PC into `inbox/` respecting the 5 GB disk cap.
  - `describe_attachments(paths) -> str`: compact prompt header listing workspace paths, sizes, and MIME types.

#### 2. `src/core/agent_tools.py`
- Added `SharedFile(path, rel_path, name, size, note="")` dataclass.
- Added `"send_file"` to `TOOL_GROUPS["files"]` (`["list_files", "read_file", "write_file", "make_pdf", "send_file"]`).
- Added optional `on_send_file=None` callback to `AgentTools.__init__`.
- Added `AgentTools.send_file(args)` + registered `Tool("send_file", ...)` right after `make_pdf` (workspace-confined via `ws.path()`, rejects missing files and folders).

#### 3. `src/connectors/base.py`
- Added pure-stdlib `encode_multipart(fields, files) -> (bytes, content_type)` shared by Telegram and Discord uploads.
- Added `call_ask(ask, text, attachments=None) -> (reply, files)` preserving backward compatibility with 1-argument `ask(text) -> str` test stubs.
- Updated `Connector.make_runner(..., workspace=None)` signature.

#### 4. `src/connectors/telegram/connector.py` & `README.md`
- Added `get_file()`, `download_file()` (20 MB Telegram Bot API cap), and `send_document()` (multipart upload, 50 MB Telegram Bot API cap) to `TelegramClient`, with injectable `multipart_transport` and `download_transport`.
- Updated `TelegramRunner` (accepts optional `workspace=None`) to download incoming `document`, `photo`, `audio`, `video`, `voice`, `animation`, and `sticker` attachments into `workspace/inbox/`, pass them + captions to `call_ask`, and upload any returned `SharedFile`s via `sendDocument`. Preserves the existing fallback reply when `workspace is None` or no downloadable `file_id` exists.
- Updated `src/connectors/telegram/README.md`.

#### 5. `src/ui/controller.py`
- Added `files: list` to `Reply`, wired `on_send_file` into `AgentTools` to collect `SharedFile`s per turn.
- Added `attach_files(paths) -> (saved_paths, err)` (cleans up partial copies on `WorkspaceError`).
- Updated `send(text, allow_over_cap=False, attachments=None)` and `_send(...)` to prepend `workspace.describe_attachments(attachments)` and return `Reply(..., files=...)`.
- Added `_ask_from_connector_full(text, attachments=None) -> (reply_text, files)` and passed it + `workspace=self.workspace` to `make_runner` (kept `_ask_from_connector(text) -> str` intact for existing tests).
- Redacted all secret fields declared in `connector.fields` during `check_connector()`.

#### 6. `src/ui/chat_view.py`, `src/ui/app.py`, `src/ui/connectors_view.py`, `src/cli.py`
- `chat_view.py`: added a **`+`** file attachment button on the bottom-left of the message input box (matching the Send button height on the right), removable `filename (size) ×` attachment chips (`remove_attachment()` / `clear_attachments()`), `add_attachments()` helper, and inline file cards (`Open`, `Save as...`, `Show in folder`) for files returned by `send_file` (preserved across theme changes).
- `app.py`: updated `_connector_event` to render returned connector files in Chat alongside `(on Telegram)` / `(on Discord)` replies.
- `connectors_view.py`: made `show_guide()` scrollable using existing `scroll_area` so 6-step guides like Discord never clip, pre-filled saved non-secret fields (like `owner_user_id`), and updated `COMING` text.
- `cli.py`: wired `on_send_file` in terminal chat to print `[File ready: <path>]`.

#### 7. Wave 2 Tests & Walkthrough Updated
- `tests/helpers.py`: extended `FakeTelegram` with `user_sends_document()`, `getFile`, `multipart_transport`, `download_transport`, and added `"share a file"` scripted flow to `FakeModelService`.
- `tests/core/test_agent_tools.py`: added `FileSharingTests` (4 tests: `safe_filename`/inbox dedup/disk cap, `send_file` + `Controller` round-trip, workspace boundary/folder checks, `encode_multipart`).
- `tests/connectors/test_telegram.py`: added `test_two_way_file_transfer_on_telegram`.
- `tests/connectors/test_discord.py`: added 11 Discord + WebSocket tests.
- `tools/walkthrough.py`: expanded from 15 to **17 steps** (`file_sharing` in Chat + `discord_guide` on Connectors).

### Wave 2 Verification Results (2026-11-21)
- **Full unit test suite + 17-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **131 tests OK** (up from 115).
- **Offline baseline token check:** `python -m src check --offline` -> **349 input tokens** with all 9 tools enabled (target <= 3,000: OK).
- **Real Discord API check (fake token):** `discord/test.py` reached `https://discord.com/api/v10/users/@me` cleanly (no Cloudflare 403) and returned `"Discord did not accept this bot token."` with the Developer Portal Reset Token fix.
- **Real `data/usage/2026-11-21.jsonl` untouched** by unit test runs (still 73 lines).

---

## Wave 3 (Same Date: 2026-11-21) — Chat Text Box Fix, Native PDF/Office `read_file`, and Session 4 (Memory, Approvals Screen, Second-Check Review, Cheap-Model Router)

Requested by the owner:
- Fix the Chat text box alignment (single-line messages were floating near the top of a tall 3-line box above the `+` and `Send` buttons).
- Make `read_file` natively read uploaded `.pdf` and Office (`.docx`, `.pptx`, `.xlsx`, `.odt`) files with the standard library so the model does not waste calls writing scripts when the user attaches a PDF.
- Continue developing Pilot according to `docs/BLUEPRINT.md` -> **Session 4** (Memory files & Memory screen, persistent conversation across restarts, Approvals queue & Approvals screen with sidebar pending count badge, Second-check review step, and cheap-model routing).

### Wave 3 — New Files Created

| File | Purpose |
| --- | --- |
| `src/core/memory.py` | Plain Markdown memory store (`MemoryStore`, `Note`, `slugify()`) reading/writing `.md` files in `data/memory/` with `list_notes()`, `get_note()`, `save_note(..., append=...)`, `delete_note()`, and `search()`. |
| `src/core/approvals.py` | `ApprovalQueue`, `ApprovalItem`, and `second_check(action, reason, risks, redact)` which flags unusual risky actions in plain words (recursive deletes, personal folders outside workspace, dynamic code execution, system/startup changes, money/purchase keywords, external recipients, reason mismatch) without ever seeing API keys or secrets. |
| `src/core/router.py` | Cheap-model router (`pick_model(text, settings, has_loaded_tools=False)`) sending short, tool-free conversational turns to `models.cheap` and tool/file/research tasks to `models.main`. |
| `src/ui/memory_view.py` | `MemoryView`: create, view, edit, and delete plain Markdown notes in `data/memory/`, plus an `Open folder` button. |
| `src/ui/approvals_view.py` | `ApprovalsView`: live pending list with `Second check:` warnings and `Approve` / `Deny` / `Always allow` buttons, plus `Recent decisions` history. |
| `tests/core/test_session4.py` | 8 unit tests covering `MemoryStore`, `remember`/`recall`/`forget` tools, conversation + memory persistence across `Controller` restarts, `second_check`, `ApprovalQueue` cross-screen resolution & badge notifications, `pick_model` cheap-model routing, and `.docx`/`.pdf` extraction via `read_file`. |

### Wave 3 — Existing Files Modified

#### 1. `src/ui/chat_view.py`
- Replaced the tall 3-line composer box with a compact, centered bar (`padx=10, pady=8`) where `self.input` starts at `height=1` (vertically aligned with `+` on the left and `Send` on the right), auto-expands up to 4 lines as you type (`_auto_resize_input`), and resets to `height=1` on Send.
- Updated `show_approval()` to display `request.flag` (the **Second check** warning in amber) and synchronize with `ApprovalQueue` so resolving an item from the Approvals screen also updates the inline Chat card.

#### 2. `src/core/pdf.py` & `src/core/agent_tools.py` & `src/core/workspace.py`
- Added `extract_pdf_text(path)` (pure-stdlib `zlib` + PDF 1.5 `/ObjStm` unpacking + per-font `/ToUnicode` CMap + `BT`/`ET` parser; verified on the owner's uploaded `Discussion Assignments (1).pdf`) and `extract_office_text(path)` (pure-stdlib `zipfile` + `xml.etree.ElementTree` for `.docx`, `.pptx`, `.xlsx`, `.odt`) in `src/core/pdf.py`.
- Updated `AgentTools.read_file()` to automatically extract readable text from `.pdf`, `.docx`, `.pptx`, `.xlsx`, and `.odt` files.
- Added `"memory": ["remember", "recall", "forget"]` to `TOOL_GROUPS` and registered `remember`, `recall`, and `forget` tools on `AgentTools`.
- Added `flag: str = ""` and `item_id: str = ""` to `ApprovalRequest`.

#### 3. `src/core/context.py` & `src/core/agent.py`
- Added optional `storage_path` + `load()` / `save()` to `Conversation` (persisted atomically to `data/memory/.chat_history.json` after each turn, and unlinked on `reset()` / `New chat`).
- Updated `Agent.run_task()` to route call 1 through `pick_model()` and call `self.conversation.save()` when a turn finishes.

#### 4. `src/ui/controller.py`, `src/ui/app.py`, `src/ui/settings_view.py`
- `controller.py`: wired `self.memory = MemoryStore(...)`, `self.approvals = ApprovalQueue(redact=self.settings.redact)`, persistent `Conversation(storage_path=self.memory.dir / ".chat_history.json")`, `saved_transcript()`, `pending_approvals()`, `pending_approvals_count()`, `approval_history()`, `resolve_approval()`, `memory_notes()`, `save_memory_note()`, and `delete_memory_note()`.
- `app.py`: expanded `SCREENS = ("Chat", "Connectors", "Approvals", "Memory", "Settings")`, restored `saved_transcript()` on startup, and added `_update_approvals_badge()` (`Approvals  (N)` in amber bold when pending > 0).
- `settings_view.py`: added `"memory": "Memory"` to `GROUP_LABELS`.
- `tools/walkthrough.py`: expanded from 17 to **19 steps** (added `memory_screen` and `approvals_screen` badge/resolution checks).

### Wave 3 Verification Results (2026-11-21)
- **Full unit test suite + 19-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **139 tests OK** (up from 131).
- **Offline baseline token check:** `python -m src check --offline` -> **398 input tokens** with all 12 tools enabled (target <= 3,000: OK).
- **Real PDF extraction verified:** `extract_pdf_text()` extracted 6,616 characters of clean text from the owner's uploaded `Discussion Assignments (1).pdf` in ~10 ms using only `re` + `zlib`.

---

## Wave 4 (Same Date: 2026-11-21) — Saved Chat Sessions, Step-by-Step Thinking Display, and Password-Protected Web Access

Requested by the owner:
- Save every chat session automatically on disk so clicking `New chat` keeps old chats in a clickable sidebar list instead of wiping them.
- Show step-by-step thinking and tool actions one by one live (like major AI apps), collapsing into a clickable `Thought & steps` accordion above each reply.
- Connect Pilot to a password-protected web page accessible from a browser or phone.

### Wave 4 — New Files Created

| File | Purpose |
| --- | --- |
| `src/core/sessions.py` | `SessionStore` saving each conversation as its own JSON file in `data/chats/<id>.json` (auto-titled from the first user message, stores `turns`, `summary_lines`, and rich `display` including steps and files), tracks `_active.txt`, supports `new_session`, `select_session`, `delete_session`, and auto-migrates legacy `data/memory/.chat_history.json`. |
| `src/core/web_server.py` | Pure-stdlib (`http.server.ThreadingHTTPServer`) password-protected web app (`PilotWebServer`, `hash_password`, `verify_password`, `local_ip`). Stores only salted PBKDF2-HMAC-SHA256 hashes (200k rounds), enforces 5-try / 60s per-IP brute-force lockout, uses `HttpOnly; SameSite=Strict` cookies + `X-Pilot-Client: web` CSRF header, serves a responsive dark-themed SPA with Chat, live step-by-step thinking, two-way file upload/download (strictly confined to `data/agent/files/`), Saved Sessions, Approvals (with Second-check flags), and Memory. |
| `tests/core/test_sessions_steps_web.py` | 7 unit & real-HTTP tests covering multi-session creation/switching/deletion/restart persistence, legacy `.chat_history.json` migration, live `on_step` emissions & provider reasoning capture, password hashing, unauthenticated `401`, CSRF `403`, brute-force `429` lockout, file upload/download + path traversal block, and end-to-end web chat/memory/sessions/logout. |

### Wave 4 — Existing Files Modified

#### 1. `src/core/providers/base.py`, `openai_compatible.py`, `anthropic_provider.py`
- Added `reasoning: str = ""` to `Response`, capturing `reasoning_content`/`reasoning` (OpenAI-compatible) and `thinking` blocks (Anthropic) when present.

#### 2. `src/core/agent.py`
- Added `steps: list` to `TaskResult`, optional `on_step` callback to `Agent.__init__`, and `_step_label_for_tool()`.
- Emits live `running` -> `done`/`error` steps with durations and redacted details for each thinking phase and each tool call, saved on `TaskResult.steps`.

#### 3. `src/ui/controller.py`
- Wired `SessionStore` (`data/chats/`), migrating legacy `.chat_history.json` automatically; added `new_session()`, `select_session()`, `delete_session()`, `list_sessions()`, `active_display_messages()`, `send_from_web()`, and `on_sessions_changed`.
- Wired `on_step` -> live steps snapshot + web server sync, and persisted steps + files into each session's `display`.
- Wired `PilotWebServer` lifecycle (`web_config()`, `configure_web()`, `start_web_if_enabled()`, `stop_web()`), refusing to start without a password and allowing approvals to be resolved from the web UI too.

#### 4. `src/ui/chat_view.py`, `src/ui/app.py`, `src/ui/settings_view.py`, `.gitignore`
- `chat_view.py`: added live `Thinking & steps` panel right above the composer while working (`update_live_step()`), plus collapsible `▸ Thought & steps (N steps · X.Xs)` accordion cards above each Pilot reply (`_build_steps_card()`), and `reload_transcript()` for switching saved sessions.
- `app.py`: added `SAVED SESSIONS` list + `+ New` / `×` controls in the left sidebar, live `Web: On (:port)` badge in footer, automatic `start_web_if_enabled()` / `stop_web()`.
- `settings_view.py`: added the **Web access (password-protected)** card (Password input, `This computer only` vs `Same Wi-Fi network`, Port, `Save & Turn on` / `Turn off`, `Open web page`).
- `.gitignore`: added `data/chats/`.
- `tools/walkthrough.py`: expanded from 19 to **20 steps** (added `saved_sessions`, `Thought & steps` check, and Settings `Web access` card check).

### Wave 4 Verification Results (2026-11-21)
- **Full unit test suite + 20-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **146 tests OK** (up from 139).
- **Offline baseline token check:** `python -m src check --offline` -> **398 input tokens** with all 12 tools enabled (target <= 3,000: OK).

---

## Wave 5 (Same Date: 2026-11-21) — Session 5 (Always-On Loop, Start with Windows, Activity Log, Live Usage & Resource Dashboard, Pause All)

Built Session 5 per `docs/BLUEPRINT.md`:
- Always-on background scheduler (`src/core/scheduler.py`) with quiet runs (`NOTHING_NEW`), user-selectable ignored check-in policy (`keep_going` vs `pause` after 6 unanswered check-ins), and headless `python -m src daemon` / `Pilot.pyw --background`.
- Start with Windows toggle writing/removing `Pilot.cmd` in the user's Windows Startup folder (no admin rights needed).
- Read-only, searchable **Activity** screen (`src/ui/activity_view.py`) backed by `data/usage/activity.jsonl` and plain-language daily summaries (`data/usage/YYYY-MM-DD-summary.txt`), highlighting long runs (>= 45s) and supporting text export.
- Upgraded **Usage & System Resources** dashboard in Settings (`src/ui/settings_view.py` + `src/core/usage.py`) with live Windows RAM/CPU/workspace disk gauges (color-coded green/amber/red), 1-minute trend sparkline (Last hour / Last day / Last week), Today / Last 7 days / Last 30 days & per-source token breakdown, automatic limit/resource alerts, and plain-text report export.
- Big red **Pause All** / green **Resume Pilot** emergency button in the desktop sidebar, Settings, and the password-protected Web UI (`/api/pause`, `/api/resume`), which immediately stops connectors and scheduled checks, denies pending approvals, and blocks new chat tasks until resumed.

### Wave 5 — New Files Created

| File | Purpose |
| --- | --- |
| `src/core/activity.py` | `ActivityLog` & `ActivityEntry`: records redacted events to `data/usage/activity.jsonl`, appends non-quiet entries to `data/usage/YYYY-MM-DD-summary.txt`, flags long runs (`>= 45s`), searches entries, and exports plain text reports. |
| `src/core/scheduler.py` | `Scheduler`, `ScheduledJob`, `QUIET_TOKEN = "NOTHING_NEW"`, `MAX_IGNORED_BEFORE_PAUSE = 6`, and `is_start_with_windows_enabled()` / `set_start_with_windows()` writing `Pilot.cmd` (`pythonw -m src daemon`). |
| `src/ui/activity_view.py` | `ActivityView`: searchable timeline, Today's daily summary card, long-run amber highlights, Refresh and Export log buttons. |
| `tests/core/test_session5.py` | 9 unit tests covering `ActivityLog` (redaction, long runs, quiet-run summary filtering, search, export), `Scheduler` (due jobs, quiet runs, ignored pause policy + user activity reset, Pause All block), `set_start_with_windows` (in temp dir), `UsageTracker` periods/sources/samples/alerts/export, `Controller.pause_all`/`resume_all`, Web `/api/pause` & `/api/resume`, and `run_daemon`. |

### Wave 5 — Existing Files Modified

#### 1. `src/core/usage.py` & `src/core/agent.py`
- `usage.py`: added stdlib Windows `ctypes` `_process_ram_mb()`, `_process_cpu_pct()`, `sample_resources()`, optional `source="window"` on `record()`, `totals_for_days()`, `period_summary()`, `record_resource_sample()`, `resource_samples(window)`, `alerts()`, and `export_report()`.
- `agent.py`: added `source: str = "window"` parameter to `run_task()` forwarded to `usage.record()`.

#### 2. `src/ui/controller.py`
- Wired `self.activity = ActivityLog(...)`, `self.scheduler = Scheduler(...)`, and optional `startup_dir` parameter (for clean test isolation).
- Added `is_paused()`, `pause_all()`, `resume_all()`, `start_scheduler()`, `stop_scheduler()`, `_run_scheduled_job()`, `_on_schedule_report()`, `scheduled_jobs()`, `add_scheduled_job()`, `remove_scheduled_job()`, `set_scheduled_job_enabled()`, `when_ignored_policy()`, `set_when_ignored_policy()`, `start_with_windows()`, `set_start_with_windows()`, `activity_entries()`, `daily_summary_lines()`, `export_activity()`, `sample_resources()`, `resource_samples()`, `period_usage()`, `usage_alerts()`, `export_usage()`.
- Logged chat tasks, errors, approvals, pauses, and scheduled runs in `ActivityLog`, and refused new tasks when paused.

#### 3. `src/ui/app.py` & `src/ui/settings_view.py`
- `app.py`: expanded `SCREENS = ("Chat", "Connectors", "Approvals", "Memory", "Activity", "Settings")`, added red **Pause All** / green **Resume Pilot** button in sidebar footer, wired `ActivityView`, `start_scheduler()`/`stop_scheduler()`, and 60s periodic `sample_resources()` in `_poll()`.
- `settings_view.py`: added **Always-on & Schedules** card (Pause/Resume, Start with Windows toggle, ignored check-in policy, add/pause/delete scheduled checks) and upgraded **Usage & System Resources** card (alerts banner, RAM/CPU/disk canvas meters, sparkline history with Hour/Day/Week buttons, Today/7d/30d & per-source summary, Export report).

#### 4. `src/core/web_server.py`, `src/cli.py`, `Pilot.pyw`, `tools/walkthrough.py`
- `web_server.py`: added `"paused"` to `/api/state`, `POST /api/pause` and `POST /api/resume`, and the **Pause All** / **Resume Pilot** button in the web sidebar.
- `cli.py` & `Pilot.pyw`: added `python -m src daemon` (`run_daemon()`) and `Pilot.pyw --background` for headless always-on mode.
- `tools/walkthrough.py`: isolated `startup_dir` into `self.tmp / "startup"` and expanded from 20 to **23 steps** (`activity_screen`, `pause_all_resume`, `always_on_usage`).

### Wave 5 Verification Results (2026-11-21)
- **Full unit test suite + 23-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **155 tests OK** (up from 146).
- **Offline baseline token check:** `python -m src check --offline` -> **398 input tokens** with all 12 tools enabled (target <= 3,000: OK).
- **Real `data/usage/2026-11-21.jsonl` untouched** by unit test runs.

---

## Wave 6 (Same Date: 2026-11-21) — Session 6 (Gmail & Google Calendar Connectors, Connector Health Screen, DuckDuckGo Fallback, Automatic Cleanup, Optional Workspace Python, GPU & Command Activity Logging)

Completed **Session 6** (the final planned build session in `docs/BLUEPRINT.md`):
- **Gmail connector (`src/connectors/gmail/`):** pure-stdlib `imaplib` + `smtplib` over SSL using a 16-letter Google App Password (no Google Cloud Console). Scoped to one mailbox/label (default `INBOX`), read-only by default, optional `read_and_send` mode where `gmail_send` **always** triggers an Approve/Deny card + second-check review (never offers "Always allow"), never deletes mail, never touches Google Drive, wraps all email text with prompt-injection warnings, and exposes lazy tools (`gmail_list`, `gmail_read`, optional `gmail_send`) ONLY when Gmail is connected and enabled.
- **Google Calendar connector (`src/connectors/calendar/`):** pure-stdlib HTTPS + RFC 5545 `.ics` parser using Google Calendar's private secret iCal link (stored under `connector_secrets.calendar.ical_url`, redacted everywhere, SSRF-checked via `check_url()`). Strictly read-only, scoped to the one calendar chosen, skips cancelled events, unfolds RFC 5545 lines, wraps text with prompt-injection warnings, and exposes lazy tool `calendar_events` ONLY when connected and enabled.
- **Connector Health screen (`src/ui/connectors_view.py` + `src/ui/controller.py` + `src/core/web_server.py`):** Green / Yellow / Red health overview bar + live `Check health` button on Connectors (and in the Web UI's new `Activity & Health` tab), with plain-language failure reporting logged to `ActivityLog` (never silent).
- **DuckDuckGo HTML search fallback (`src/core/browser.py`):** falls back to `html.duckduckgo.com` (unwrapping `/l/?uddg=...` redirects) whenever Bing returns 0 results.
- **Optional Workspace Python venv (`src/core/workspace.py` + `src/ui/settings_view.py`):** lets the owner create/remove an isolated Python environment inside `data/agent/venv` so future `pip install`s stay inside the 5 GB cap without modifying system Python.
- **Polish & Blueprint gap completion:** automatic cleanup of stale `data/tool_output/` (>14 days) and stray `.tmp-pdf-*` files (`cleanup_old_data()`), optional NVIDIA GPU % sampling via `nvidia-smi` when present, and per-command logging (`kind="command"`) into the searchable Activity log.

### Wave 6 — New Files Created

| File | Purpose |
| --- | --- |
| `src/connectors/gmail/__init__.py` | Exports `GmailConnector`, `create`. |
| `src/connectors/gmail/guide.json` | 4-step beginner Gmail App Password guide, `email`, `app_password` (secret), `mailbox` (default `INBOX`), and `access` choice buttons (`read_only` vs `read_and_send`). |
| `src/connectors/gmail/connector.py` | `GmailError`, `GmailClient` (IMAP SSL readonly + SMTP SSL), `GmailConnector` (`health_check`, `make_tools`: `gmail_list`, `gmail_read`, approval-gated `gmail_send`), HTML-to-text stripping, prompt-injection banner. |
| `src/connectors/gmail/test.py` | Validates Gmail address + 16-letter App Password, tests IMAP readonly access to chosen mailbox, plain-language errors & fixes. |
| `src/connectors/gmail/README.md` | Maintainer notes & safety rules. |
| `src/connectors/calendar/__init__.py` | Exports `CalendarConnector`, `create`. |
| `src/connectors/calendar/guide.json` | 3-step beginner Google Calendar secret iCal guide (`calendar_name`, secret `ical_url`). |
| `src/connectors/calendar/connector.py` | `CalendarError`, `CalendarEvent`, pure-stdlib RFC 5545 `parse_ics_events()`, `validate_ical_url()` (HTTPS + SSRF check), `CalendarClient`, `CalendarConnector` (`health_check`, `make_tools`: `calendar_events`). |
| `src/connectors/calendar/test.py` | Validates HTTPS secret iCal link and reports total + upcoming 7-day events. |
| `src/connectors/calendar/README.md` | Maintainer notes & safety rules. |
| `tests/connectors/test_session6.py` | 7 comprehensive tests covering connector discovery order (`telegram`, `gmail`, `calendar`, `discord`), Gmail scope/read-only/send-approval/HTML-strip/redaction, Calendar `.ics` parsing/SSRF blocking/dynamic tools, Green/Yellow/Red Connector Health + Activity error logging, DuckDuckGo search fallback, Workspace venv PATH injection, `cleanup_old_data()`, and command Activity logging. |

### Wave 6 — Existing Files Modified

#### 1. `src/connectors/base.py`, `telegram/connector.py`, `discord/connector.py`
- `base.py`: added default `make_tools()` and `health_check()` methods on `Connector`.
- `telegram/connector.py` & `discord/connector.py`: implemented live `health_check()` (`getMe` / `GET /users/@me`).

#### 2. `src/core/browser.py`, `src/core/workspace.py`, `src/core/usage.py`, `src/core/agent.py`, `src/core/agent_tools.py`
- `browser.py`: added `_DuckDuckGoResults` HTML parser, DuckDuckGo `/l/?uddg=...` unwrapping in `_real_url()`, and automatic fallback in `Browser.search()`.
- `workspace.py`: added `venv_dir`, `has_venv()`, `create_venv()`, `remove_venv()`, and automatic `VIRTUAL_ENV` + `PATH` prepending in `Workspace.run()` when workspace venv exists.
- `usage.py`: added `_sample_gpu_pct()` (optional NVIDIA GPU utilization via `nvidia-smi` when installed) and `cleanup_old_data(base_dir, max_age_days=14)`.
- `agent.py`: added friendly step labels in `_step_label_for_tool()` for `gmail_list`, `gmail_read`, `gmail_send`, and `calendar_events`.
- `agent_tools.py`: added optional `on_command(cmd, started, ended, exit_code)` callback in `run_command`.

#### 3. `src/ui/controller.py`, `src/ui/connectors_view.py`, `src/ui/settings_view.py`, `src/core/web_server.py`, `tools/walkthrough.py`
- `controller.py`: dynamically loads enabled connectors' `make_tools()` in `rebuild()` (zero idle token overhead when off!), rebuilds on connect/toggle, adds `check_connectors_health()` & `health` status (`green`/`yellow`/`red`/`none`) on `connector_cards()`, logs connector problems & executed commands to `ActivityLog`, runs `cleanup_old_data()` on startup, and exposes `has_workspace_venv()` / `create_workspace_venv()` / `remove_workspace_venv()`.
- `connectors_view.py`: made grid scrollable, added **Connector health** overview card with Green/Yellow/Red pills + live `Check health` button, and added `"choices"` button-group & `"default"` pre-fill support in `show_guide()`.
- `settings_view.py`: added optional **Isolated workspace Python** Create/Remove control in Agent workspace card, plus GPU bar / status line in Usage & System Resources.
- `web_server.py`: added **Activity & Health** tab (`tab-activity`) showing Connector Health pills, Today's summary, and filterable Activity timeline.
- `tools/walkthrough.py`: expanded from 23 to **24 steps** (`gmail_calendar_health` checking the health bar, live health check, Gmail guide, and Google Calendar guide).

### Wave 6 Verification Results (2026-11-21)
- **Full unit test suite + 24-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **162 tests OK** (up from 155).
- **Offline baseline token check:** `python -m src check --offline` -> **398 input tokens** with all 12 core tools enabled and 0 connectors on (target <= 3,000: OK).
- **Real `data/usage/2026-11-21.jsonl` untouched** by unit test runs.

### Post-Session 6 Owner UI Preference Update (2026-11-21)
- Owner asked to keep **simple persistent chat history** (automatically remembering messages, steps, and files across restarts) **without** a multi-session/projects list in the sidebar.
- Removed the `SAVED SESSIONS` / `+ New` list from the desktop sidebar (`src/ui/app.py`) and the Web UI sidebar (`src/core/web_server.py`).
- Made `New chat` (desktop `App.clear_chat()` and Web `POST /api/chat/clear` -> `Controller.reset_chat()`) clear the single persistent chat history cleanly instead of stacking multiple session entries.
- Updated `tools/walkthrough.py` (`chat_history_persists`) and `tests/core/test_sessions_steps_web.py` accordingly.

---

## Wave 7 (Same Date: 2026-11-21) — Full Blueprint Deep Audit & Final Polish

Performed a line-by-line audit of `docs/BLUEPRINT.md` and every file in `src/` (83 Python files compiled cleanly, **0 third-party imports** confirmed by AST scan) and closed every remaining gap:

### Wave 7 — New Files Created

| File | Purpose |
| --- | --- |
| `src/core/voice.py` | Pure-stdlib voice note support: `VoiceRecorder` via Windows `winmm.dll` `mciSendStringW` (records `.wav` directly into `workspace/files/inbox/`), `is_audio_file()`, and `encode_audio_block()` building OpenAI-compatible `input_audio` payloads (`.wav`, `.mp3`, `.ogg`, `.oga`, `.m4a`, `.flac`, `.aac`, `.webm` up to 10 MB). |
| `src/core/skills.py` | Lazy skill loader (`SkillStore`, `Skill`) reading optional plain Markdown files from `data/skills/*.md` with `triggers:` headers. Zero prompt tokens added unless a user message matches a trigger, and injected into the turn's user message so `system_prompt()` stays unchanged for prompt caching. |
| `tests/core/test_blueprint_complete.py` | 10 unit tests covering `ApprovalQueue` `item_id`/connector/cross-screen listener sync, `second_check()` new-email & large-dollar warnings, approved-recipient memory, `permissions.py` money/trading `NEVER` block, email/calendar router rules, never-silent scheduler crash reporting & Telegram owner notification, `VoiceRecorder` + `input_audio` (OpenAI-compatible & Anthropic stripping) + single-call media injection, lazy `SkillStore`, and `Controller.save_limits()`. |

### Wave 7 — Existing Files Modified

#### 1. `src/core/approvals.py`, `src/core/agent_tools.py`, `src/connectors/gmail/connector.py`, `src/ui/approvals_view.py`, `src/ui/chat_view.py`, `src/core/web_server.py`
- **Bug fix:** `ApprovalQueue.enqueue()` now sets `request.item_id = item_id` so resolving an approval from the Approvals screen or Web UI updates the inline Chat card immediately (marshaled onto the Tk main thread via `app._results.put()`).
- Added `connector: str = "Terminal"` on `ApprovalRequest` and `ApprovalItem` (`Gmail` for `gmail_send`, `Terminal` for `run_command`), rendered across Chat, Approvals, and Web UI (`Via: Gmail` / `Via: Terminal`).
- Upgraded `second_check()` to detect `Send email to <addr>` against `known_recipients` (saved automatically once approved in `Controller.resolve_approval()`) and flag brand-new recipients + large dollar purchases (`$100+`).

#### 2. `src/core/permissions.py`, `src/core/router.py`, `src/core/scheduler.py`
- `permissions.py`: added case-insensitive `NEVER` block enforcing the Blueprint's "moving money between accounts or trading securities" rule (both on command text and inside workspace Python scripts).
- `router.py`: added email/calendar/schedule/voice keywords to `_HEAVY_WORDS` so Gmail & Calendar queries route to `models.main`.
- `scheduler.py`: wrapped `run_job(job)` in `try/except` inside `tick()` so a failing scheduled job NEVER fails silently, always calls `on_report()` with a plain failure explanation, and advances `last_run` to avoid crash loops.

#### 3. `src/core/providers/openai_compatible.py`, `anthropic_provider.py`, `src/core/agent.py`, `src/ui/controller.py`, `src/ui/chat_view.py`, `src/core/web_server.py`
- Added native voice-note understanding across Desktop Chat (`Mic` button -> Windows `VoiceRecorder`), Web UI (`Mic` button -> browser `MediaRecorder`), Telegram voice notes, and Discord audio attachments. Audio bytes are sent ONLY on call 1 (`media_blocks`) and never stored in `Conversation.turns`, keeping history compact.
- Wired `SkillStore` (`data/skills/`) into `Controller` & `Agent`.
- Added `Controller._notify_owner_remote()` to push non-quiet scheduled updates/failures and away-mode approval alerts to connected Telegram/Discord.

#### 4. `src/ui/settings_view.py`, `src/ui/welcome_view.py`, `src/core/workspace.py`, `data/settings.example.json`, `docs/BLUEPRINT.md`
- Added beginner-friendly **Daily & workspace limits** controls (Daily tokens, Daily $, Workspace GB, `Save limits`) inside Settings -> Usage & System Resources, and removed remaining user-facing `data/settings.json` jargon from `agent.py`, `chat_view.py`, `workspace.py`, `settings_view.py`, and `anthropic_provider.py`.
- Added plain-language **Privacy & trust** summary to Welcome Step 1 + optional Telegram / Gmail / Google Calendar / Discord buttons on Welcome Step 3, and added the **Privacy and trust** section to `docs/BLUEPRINT.md`.
- Updated `data/settings.example.json` to match all current defaults.

### Wave 7 Verification Results (2026-11-21)
- **Static audit:** 83 Python files compiled cleanly; **0 third-party imports** across `src/`, `tests/`, `tools/`.
- **Full unit test suite + 24-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **172 tests OK** (up from 162).
- **Offline baseline token check:** `python -m src check --offline` -> **398 input tokens** (target <= 3,000: OK).
- **Real live Gemini end-to-end check (throwaway folder, zero impact on real `data/`):**
  - Fresh `"hello"` with all 12 tools enabled: **1 call, 388 total tokens** (real measured Gemini API).
  - Real `.wav` voice note via `input_audio`: **2 calls, 1,102 tokens**, Gemini accurately heard the generated 440 Hz tone (`"The audio clip contains the sound of a tone (a steady beep)."`).
  - Memory `remember` + fresh `Controller` restart: created `preferences.md`, restored all 6 chat transcript messages, emitted 38 live step-by-step events.

---

## Wave 8 (Same Date: 2026-11-21) — Free Online Access (Cloudflare Tunnel), Online Workspace & Live Terminal, GitHub Pages Site & Complete UI Design Spec

Requested by the owner (Option 2 — \$0/mo online now, cloud-ready later, plus full program data for custom UI design):

### Wave 8 — New Files Created

| File | Purpose |
| --- | --- |
| `src/core/tunnel.py` | Pure-stdlib `CloudflareTunnel` wrapper around `cloudflared` (free Quick Tunnels `https://<slug>.trycloudflare.com` + optional named tunnel token for a custom domain later). Auto-discovers `cloudflared` across `PATH` and WinGet package folders; refuses to run without password-protected Web access; shuts down on `Pause All` or app exit. |
| `docs/index.html` | Ready-to-publish free **GitHub Pages** landing site (`Settings -> Pages -> main / docs`) with a localStorage-backed "Open your live Pilot workspace" launcher box for jumping straight into the owner's Cloudflare Tunnel URL or custom domain. |
| `docs/UI_DESIGN_SPEC.md` | Complete UI Design Blueprint & JSON Data Contract (theme tokens, every screen/component, full `GET /api/state` schema, every `POST` endpoint, connector guides, and tool list — zero secrets) so the owner can design a custom frontend and wire it directly to Pilot's backend. |
| `Dockerfile` | Lightweight cloud container recipe (`python:3.13-slim` + headless `chromium`, `python -m src web --host 0.0.0.0`) so migrating to Railway / Render / VPS later takes zero code rewrites. |
| `tests/core/test_online_workspace.py` | 6 tests covering `CloudflareTunnel` URL parsing & stop, `Controller` tunnel password/pause guards, Web Workspace Files + Live Terminal (`NEVER` block enforced, traversal blocked), Web Settings limits API, `CF-Connecting-IP` per-visitor brute-force isolation, `X-Forwarded-Proto: https` `; Secure` cookies, and `python -m src web` headless runner. |

### Wave 8 — Existing Files Modified

- `src/core/agent_tools.py` & `src/core/browser.py`: added cross-platform shell fallback (`/bin/bash -c` on Linux/macOS alongside PowerShell on Windows) and `shutil.which` fallback for Chromium/Chrome on Linux.
- `src/ui/controller.py`: added `start_public_tunnel()`, `stop_public_tunnel()`, `workspace_files()`, `delete_workspace_file()`, and `run_workspace_terminal()` (running inside Pilot's workspace + isolated venv + RAM/time caps + `NEVER` block + Activity logging).
- `src/core/web_server.py`: added **Workspace & Terminal** tab (`tab-workspace`) and **Settings & Usage** tab (`tab-settings`), added `/api/workspace/files`, `/api/workspace/terminal`, `/api/workspace/delete`, `/api/settings/limits`, enriched `/api/state` with `workspace`, `usage`, `web`, and `provider`, and added Cloudflare-safe `_client_ip()` (`CF-Connecting-IP`) + HTTPS `; Secure` cookie flags.
- `src/ui/settings_view.py` & `src/ui/app.py`: added **Put online (free HTTPS link)** / **Stop public link** / **Open public link** / **Copy link** buttons in Settings -> Web access and `Web: Online (HTTPS)` badge in sidebar.
- `src/cli.py`: added `python -m src web [--host] [--port] [--tunnel]` (also honors `PORT` and `PILOT_WEB_PASSWORD` env vars).

### Wave 8 Verification Results (2026-11-21)
- Installed `Cloudflare.cloudflared` (v2026.11.1) via `winget` cleanly in user scope (`find_cloudflared()` detected it automatically; verified real Cloudflare Edge assigned a live `https://*.trycloudflare.com` URL cleanly and shut down cleanly).
- Note: `Git.Git` installer requires a Windows UAC administrator prompt, so the owner will run `winget install Git.Git` (or install GitHub Desktop) when ready to push the repo to GitHub.
- **Full unit test suite + 24-step UI walkthrough:** `python -m unittest discover -s tests -t .` -> **178 tests OK** (up from 172); offline baseline stays **398 tokens**.

---

## Remaining Live / Manual Owner Checks (Code Build Complete!)

All 6 build sessions in `docs/BLUEPRINT.md` are implemented and passing automated tests (162 tests + 24-step UI walkthrough). What remains is hands-on verification with real accounts and hardware:
1. **Real connectors:** Test Telegram with a real BotFather token, Discord with a real bot token, Gmail with a real 16-letter App Password, and Google Calendar with a real secret iCal link.
2. **Always-on reboot check:** Toggle `Start at login: On` in Settings -> Always-on & Schedules, reboot Windows, and confirm `pythonw -m src daemon` comes up quietly.
3. **First-run non-technical user test:** Session 6 done-criterion — have someone who has never seen Pilot complete first-run setup in under 10 minutes.
4. **Optional Workspace Python venv:** Click `Create workspace Python` in Settings -> Agent workspace if the owner wants future `pip install`s isolated inside `data/agent/venv`.
