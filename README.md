# Pilot

A small personal AI agent that runs on your own computer and talks to a cloud model with your own API key.
All 6 Blueprint sessions are built: a desktop window with **Chat** (automatic persistent chat history, live step-by-step thinking, voice notes via **Mic**, two-way file sharing), **Connectors** (Telegram, Gmail, Google Calendar, Discord, plus a Green/Yellow/Red Connector Health checker), **Approvals** (sidebar badge, connector labels & second-check safety review), **Memory** (plain Markdown notes), **Activity** (searchable timeline, command log & plain daily summaries), **Settings** (always-on schedules, Start with Windows, live RAM/CPU/GPU/disk usage dashboard, editable daily/disk limits, red **Pause All** emergency button, optional isolated workspace Python, and password-protected **Web access** for any browser or phone on your network). Plus the agent's 5 GB / 2 GB workspace, lazy `data/skills/` support, native PDF/Office reading, built-in PDF creation, terminal, read-only browser (Bing + DuckDuckGo fallback), and cost-saving core.
The full plan is in `docs/BLUEPRINT.md`. The current status is in `docs/STATE.md`.

## What you need

- Windows, Mac or Linux with Python 3.9 or newer. Nothing else to install: everything uses Python's standard library (the window uses tkinter, which comes with Python).
- A free Google Gemini API key from aistudio.google.com (no card needed). The window's welcome steps walk you through it. An Anthropic key works too, but it is paid. No key is needed for the demo or the tests.

## Run it (Windows, in a terminal opened in this folder)

```
python -m src window      # open the desktop window (or double-click Pilot.pyw)
python -m src web --tunnel # run password-protected web app + free public HTTPS link (Cloudflare Tunnel)
python -m src daemon      # run quietly in the background (connectors, schedules, web access)
python -m src setup       # terminal setup: pick the provider, paste your key (hidden as you type), test it
python -m src             # chat
python -m src check       # sends one 'hello' and prints how many tokens it cost. Target: 3,000 or less
python -m src --demo      # chat with a fake model, no key needed
python -m unittest discover -s tests -t .      # run the tests (178 tests incl. 24-step walkthrough)
python tools/walkthrough.py --shots shots      # press every button like a new user, with screenshots
```

If `python` is not found on Windows, try `py` in its place.

Inside the chat: `/usage` shows today's tokens and cost, `/reset` clears the chat, `/help` lists commands, `/quit` leaves.

## Where things are saved

| What | Where |
| --- | --- |
| Settings and your API key | `data/settings.json` (only on this computer, never committed) |
| Saved chat sessions | `data/chats/` |
| Usage, activity log & daily summaries | `data/usage/` (`YYYY-MM-DD.jsonl`, `activity.jsonl`, `YYYY-MM-DD-summary.txt`, `resources.jsonl`) |
| Long tool output that was trimmed | `data/tool_output/` (cleaned automatically after 14 days) |
| Memory files | `data/memory/` |
| The agent's own files (capped at 5 GB) | `data/agent/files/` |
| The agent's hidden browser profile | `data/agent/browser/` |
| Optional isolated workspace Python | `data/agent/venv/` (created from Settings if you choose) |

Copy `data/settings.example.json` to see every setting. Prices used for the cost estimate are editable in that file. If a model has no price, its cost shows as blank instead of a guess.

## How it keeps costs down

- A plain message is one small call. Tools for connectors only load when that connector is connected and switched on.
- Tools load lazily only when the model asks for one by name.
- Easy conversational messages route automatically to your cheap model.
- Scheduled checks run quietly (`NOTHING_NEW`) with no notifications or extra calls when nothing changed.
- Only the last 6 turns are sent in full. Older turns shrink to one short line each.
- At most 8 model calls per task, then it stops and tells you.
- Daily limits on tokens and dollars. At a limit it asks before going on.
- Big tool results are trimmed, with the full copy saved to disk and auto-cleaned after 14 days.

## Folder map

```
src/cli.py            terminal chat, setup, check, window, daemon
src/core/             settings, providers, agent loop, context, tools, router, memory, sessions, skills, voice,
                      approvals, scheduler, activity, usage, web_server, workspace, limits, permissions, browser, pdf
src/connectors/       one folder per connector (telegram, gmail, calendar, discord): guide.json, test, logic
tools/                walkthrough.py (24-step UI click-through) and a screenshot helper
src/ui/               the desktop window (controller.py holds the logic, the *_view.py files draw)
Pilot.pyw             double-click to open the window (or pass --background for daemon mode)
tests/                172 automated tests (stdlib unittest)
docs/                 BLUEPRINT.md (the plan), STATE.md (where we are), ANTIGRAVITY_CHANGES.md (handoff log)
reference/            Hermes Agent is cloned here for reading only (see reference/README.md)
```
