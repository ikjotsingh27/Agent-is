"""Everything the window does, with no tkinter in it, so it can be tested like the core.

The screens (chat_view, settings_view, welcome_view) only draw things and call these methods.
Slow methods (send, check_key) are called from a background thread by the screens, so the window never freezes.
"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..connectors import CheckResult, load_connectors
from ..core.activity import ActivityLog
from ..core.agent import Agent
from ..core.agent_tools import AgentTools, SharedFile, TOOL_GROUPS, DEFAULT_GROUPS, enabled_tool_names, permission_rows
from ..core.approvals import ApprovalQueue, extract_email_recipient
from ..core.context import Conversation
from ..core.memory import MemoryStore
from ..core.scheduler import (QUIET_INSTRUCTION, Scheduler, is_start_with_windows_enabled,
                              set_start_with_windows)
from ..core.permissions import classify as classify_command
from ..core.sessions import SessionStore
from ..core.skills import SkillStore
from ..core.tools import ToolRegistry
from ..core.tunnel import CloudflareTunnel, TunnelError, find_cloudflared
from ..core.voice import encode_audio_block
from ..core.web_server import PilotWebServer, hash_password
from ..core.workspace import Workspace, WorkspaceError, file_size_label
from ..core.configure import (check_connection, finish_models, looks_like_key, model_prefix,
                              reset_models_if_invalid, suggest_models, valid_model)
from ..core.providers import PROVIDER_INFO, PROVIDERS, ProviderError, get_provider
from ..core.settings import Settings
from ..core.usage import CapReached, UsageTracker, cleanup_old_data

KEY_PAGES = {
    "gemini": "https://aistudio.google.com/apikey",
    "anthropic": "https://console.anthropic.com/settings/keys",
}


@dataclass
class Reply:
    kind: str  # "ok", "cap" (a daily limit was reached, ask the user) or "error"
    text: str
    detail: str = ""
    files: list = field(default_factory=list)  # SharedFile items the agent sent back during this turn
    steps: list = field(default_factory=list)  # live thinking/tool step dicts taken during this turn


@dataclass
class KeyCheck:
    ok: bool
    message: str
    models: list = field(default_factory=list)  # model names to offer in the dropdowns
    main: str = ""
    cheap: str = ""


def clean_key(key: str) -> str:
    return "".join(ch for ch in (key or "") if ch.isprintable()).strip()


class Controller:
    def __init__(self, settings: Settings | None = None, provider_factory=get_provider, usage_dir=None,
                 connector_options: dict | None = None, workspace_root=None, browser=None, startup_dir=None):
        self.settings = settings or Settings.load()
        self.provider_factory = provider_factory
        self.usage_dir = usage_dir or (self.settings.path.parent / "usage" if self.settings.path else None)
        self._startup_dir = startup_dir
        self.agent: Agent | None = None
        # One lock for the agent and the settings file: the window and the phone can both send messages.
        self._lock = threading.RLock()
        self.connectors = load_connectors(**(connector_options or {}))
        self.runners: dict = {}
        self.problems: dict = {}  # connector id -> latest problem in plain words
        self.health_notes: dict = {}  # connector id -> latest health check message
        self.on_connector_event = None  # set by the window: fn(connector_id, kind, info), called from a background thread
        # The agent's workspace, memory, and saved chat sessions live next to the settings file, so tests
        # with a temporary settings file never touch the real data folder.
        base_dir = self.settings.path.parent if self.settings.path else None
        root = workspace_root or (base_dir / "agent" if base_dir else None)
        self.workspace = Workspace(self.settings, root=root)
        self.memory = MemoryStore(base_dir / "memory" if base_dir else None)
        self.skills = SkillStore(base_dir / "skills" if base_dir else None)
        self.sessions = SessionStore(base_dir / "chats" if base_dir else None)
        self.sessions.migrate_legacy_history(self.memory.dir / ".chat_history.json")
        self._session = self._load_initial_session()
        self.approvals = ApprovalQueue(
            redact=self.settings.redact,
            known_recipients_fn=lambda: self.settings.get("known_recipients", default=[]) or [],
        )
        self.activity = ActivityLog(self.settings, usage_dir=self.usage_dir)
        self.scheduler = Scheduler(
            self.settings,
            run_job=self._run_scheduled_job,
            on_report=self._on_schedule_report,
            save_settings=self._save_file,
        )
        self._browser = browser
        self._file_lock = threading.RLock()  # the settings file; never held while waiting for the user
        self.approval_handler = None  # set by the window: fn(request, answer), called from the agent's thread
        self.on_activity = None  # set by the window: fn(text), what the agent is doing right now
        self.on_step = None  # set by the window: fn(step_dict), live step-by-step thinking updates
        self.on_approvals_changed = None  # set by the window: fn(), called when pending approvals change
        self.on_sessions_changed = None  # set by the window: fn(), called when saved sessions or active chat changes
        self.on_pause_changed = None  # set by the window: fn(), called when Pause All toggles
        self.approvals.on_change = self._approvals_changed
        self._pending_sent_files: list = []
        self._live_steps: list = []
        self._busy = False
        self._current_activity = ""
        self.web_server: PilotWebServer | None = None
        self.tunnel: CloudflareTunnel | None = None
        if base_dir:
            cleanup_old_data(base_dir)
        self.rebuild()

    def _load_initial_session(self) -> dict:
        aid = self.sessions.active_id()
        if aid:
            loaded = self.sessions.load_session(aid)
            if loaded is not None:
                return loaded
        existing = self.sessions.list_sessions()
        if existing:
            loaded = self.sessions.load_session(existing[0].id)
            if loaded is not None:
                self.sessions.set_active_id(loaded["id"])
                return loaded
        return self.sessions.create_session()

    # --- state ----------------------------------------------------------------------------------
    @property
    def providers(self) -> tuple:
        return tuple(p for p in PROVIDERS if p != "mock") if self.settings.provider != "mock" else PROVIDERS

    def provider_label(self, name: str) -> str:
        return PROVIDER_INFO[name]["label"]

    def key_help(self, name: str) -> str:
        return PROVIDER_INFO[name]["key_help"]

    def needs_welcome(self) -> bool:
        return self.settings.provider != "mock" and not self.settings.api_key()

    def has_key(self, provider: str) -> bool:
        return bool(self.settings.api_key(provider))

    @property
    def theme(self) -> str:
        return self.settings.get("ui", "theme", default="dark")

    def rebuild(self) -> None:
        """Make a fresh provider and agent from the current settings. The chat so far is kept."""
        with self._lock:
            ctx = self.settings.get("context", default={}) or {}
            if self.agent is not None:
                conversation = self.agent.conversation
            else:
                conversation = Conversation(ctx.get("recent_turns", 6), ctx.get("max_summary_chars", 1200))
                conversation.turns = copy.deepcopy(self._session.get("turns") or [])
                conversation.summary_lines = list(self._session.get("summary_lines") or [])
            cap_override = self.agent.cap_override if self.agent else False
            provider = self.provider_factory(self.settings)
            hands = AgentTools(self.settings, self.workspace, approve=self._approve, on_activity=self._activity,
                               browser=self._browser, save_settings=self._save_file,
                               on_send_file=self._record_sent_file, memory=self.memory,
                               on_command=self._record_command)
            all_tools = hands.tools()
            enabled_names = list(enabled_tool_names(self.settings))
            for cid, conn in self.connectors.items():
                cdata = self.connector_data(cid)
                if cdata.get("connected") and cdata.get("enabled"):
                    extra = conn.make_tools(self.connector_secrets(cid), cdata,
                                            approve=self._approve, on_activity=self._activity)
                    for tool in extra:
                        all_tools.append(tool)
                        if tool.name not in enabled_names:
                            enabled_names.append(tool.name)
            registry = ToolRegistry(all_tools, enabled_names)
            self.agent = Agent(self.settings, provider, UsageTracker(self.settings, usage_dir=self.usage_dir),
                               registry=registry, conversation=conversation, on_step=self._handle_step,
                               skills=self.skills)
            self.agent.cap_override = cap_override

    # --- chat -----------------------------------------------------------------------------------
    def _record_sent_file(self, shared) -> None:
        self._pending_sent_files.append(shared)

    def attach_files(self, paths: list) -> tuple[list, str]:
        """Copy local files from the user's PC into workspace/inbox/. Returns (saved_paths, error_text)."""
        saved = []
        try:
            for p in paths:
                saved.append(self.workspace.import_file(p))
        except WorkspaceError as exc:
            for s in saved:
                try:
                    s.unlink(missing_ok=True)
                except OSError:
                    pass
            return [], str(exc)
        return saved, ""

    def _handle_step(self, step: dict) -> None:
        for idx, existing in enumerate(self._live_steps):
            if existing.get("id") == step.get("id"):
                self._live_steps[idx] = dict(step)
                break
        else:
            self._live_steps.append(dict(step))
        self._current_activity = step.get("label") or self._current_activity
        if self.web_server is not None:
            self.web_server.record_live_step(step)
        if self.on_step:
            self.on_step(dict(step))

    def is_busy(self) -> bool:
        return self._busy

    def current_activity(self) -> str:
        return self._current_activity

    def live_steps_snapshot(self) -> list:
        return [dict(s) for s in self._live_steps]

    def _serialize_files(self, files: list) -> list:
        out = []
        for f in files or []:
            if isinstance(f, dict):
                out.append(dict(f))
            else:
                out.append({
                    "rel_path": f.rel_path, "name": f.name, "size": f.size,
                    "size_label": file_size_label(f.size), "note": f.note,
                })
        return out

    def _hydrate_files(self, items: list) -> list:
        out = []
        for f in items or []:
            if not isinstance(f, dict):
                out.append(f)
                continue
            try:
                p = self.workspace.path(f.get("rel_path", ""))
            except WorkspaceError:
                continue
            out.append(SharedFile(path=p, rel_path=f.get("rel_path", ""), name=f.get("name", p.name),
                                  size=int(f.get("size", 0) or 0), note=f.get("note", "")))
        return out

    def _persist_session(self) -> None:
        if self.agent is not None:
            self._session["turns"] = copy.deepcopy(self.agent.conversation.turns)
            self._session["summary_lines"] = list(self.agent.conversation.summary_lines)
        self.sessions.save_session(self._session)
        self.sessions.set_active_id(self._session["id"])

    def _notify_sessions_changed(self) -> None:
        if self.on_sessions_changed:
            try:
                self.on_sessions_changed()
            except Exception:
                pass

    def send(self, text: str, allow_over_cap: bool = False, attachments: list | None = None,
             source_label: str = "") -> Reply:
        with self._lock:  # one message at a time, from the window, web page, or a connector
            return self._send(text, allow_over_cap, attachments=attachments, source_label=source_label)

    def _send(self, text: str, allow_over_cap: bool, attachments: list | None = None,
              source_label: str = "") -> Reply:
        if self.is_paused():
            return Reply("error", "Pilot is paused (Pause All is on). Click Resume Pilot to continue.")
        if allow_over_cap:
            self.agent.cap_override = True  # the user said yes: go past today's limit for this session
        source_key = (source_label or "window").lower()
        if source_key != "schedule":
            self.scheduler.note_user_activity()
        self._pending_sent_files = []
        self._live_steps = []
        self._busy = True
        self._current_activity = "Thinking..."
        raw_user = (text or "").strip()
        shown_user = raw_user or (f"[Attached: {', '.join(Path(p).name for p in attachments)}]" if attachments else "")
        if attachments and raw_user:
            shown_user = f"{raw_user}\n[Attached: {', '.join(Path(p).name for p in attachments)}]"
        prompt = raw_user
        media_blocks = []
        if attachments:
            for att in attachments:
                blk = encode_audio_block(att)
                if blk is not None:
                    media_blocks.append(blk)
            note = self.workspace.describe_attachments(attachments)
            if media_blocks:
                audio_hint = (
                    "[The audio attachment is already included directly in this message for you to hear. "
                    "Listen to it and answer directly without running commands or reading the binary audio file.]"
                )
                if prompt:
                    prompt = f"{note}\n{audio_hint}\n\n{prompt}"
                else:
                    prompt = f"{note}\n{audio_hint}\n\nListen to the user's voice note and respond directly."
            else:
                prompt = f"{note}\n\n{prompt}" if prompt else (
                    f"{note}\n\nThe user sent you these files. Acknowledge what you received and help as needed."
                )
        t0 = time.time()
        try:
            result = self.agent.run_task(prompt, source=source_key, media_blocks=media_blocks or None)
        except CapReached as exc:
            self.activity.record(summary=f"Daily limit reached: {exc}", kind="error", source=source_key,
                                 started=t0, ended=time.time())
            return Reply("cap", str(exc))
        except ProviderError as exc:
            msg = self.settings.redact(str(exc))
            self.activity.record(summary=f"Model error: {msg}", kind="error", source=source_key,
                                 started=t0, ended=time.time())
            return Reply("error", msg)
        except Exception as exc:  # never crash the window; show what went wrong in plain words
            msg = self.settings.redact(f"Something went wrong: {exc}")
            self.activity.record(summary=msg, kind="error", source=source_key, started=t0, ended=time.time())
            return Reply("error", msg)
        finally:
            self._busy = False
            self._current_activity = ""
        t1 = time.time()
        detail = f"{result.calls} call{'s' if result.calls != 1 else ''}, {result.total_tokens:,} tokens"
        files = list(self._pending_sent_files)
        steps = [dict(s) for s in result.steps]
        if source_key != "schedule":
            self.activity.record(
                summary=f"{shown_user[:120]} -> {result.text[:100]}",
                kind="chat", source=source_key, started=t0, ended=t1, tokens=result.total_tokens,
            )
            self._session.setdefault("display", []).append({
                "who": "you_phone" if source_label else "you",
                "text": shown_user,
                "extra": source_label,
                "steps": [],
                "files": [],
            })
            self._session["display"].append({
                "who": "pilot",
                "text": result.text,
                "extra": f"answered on {source_label}" if source_label else detail,
                "steps": steps,
                "files": self._serialize_files(files),
            })
            self._persist_session()
            self._notify_sessions_changed()
        return Reply("ok", result.text, detail, files=files, steps=steps)

    def send_from_web(self, text: str, attachments: list | None = None) -> Reply:
        return self.send(text, attachments=attachments, source_label="Web")

    # --- saved chat sessions --------------------------------------------------------------------
    @property
    def active_session_id(self) -> str:
        return self._session["id"]

    def list_sessions(self) -> list:
        return self.sessions.list_sessions()

    def active_display_messages(self) -> list:
        return copy.deepcopy(self._session.get("display") or [])

    def new_session(self) -> dict:
        """Save the current session (if it has messages) and open a fresh blank chat session."""
        with self._lock:
            if self._session.get("turns") or self._session.get("display"):
                self._persist_session()
            self._session = self.sessions.create_session()
            self.agent.conversation.turns = []
            self.agent.conversation.summary_lines = []
            self.agent.loaded = []
            self._notify_sessions_changed()
            return self._session

    def select_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id == self._session["id"]:
                return True
            loaded = self.sessions.load_session(session_id)
            if loaded is None:
                return False
            if self._session.get("turns") or self._session.get("display"):
                self._persist_session()
            self._session = loaded
            self.sessions.set_active_id(loaded["id"])
            self.agent.conversation.turns = copy.deepcopy(loaded.get("turns") or [])
            self.agent.conversation.summary_lines = list(loaded.get("summary_lines") or [])
            self.agent.loaded = []
            self._notify_sessions_changed()
            return True

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            was_active = session_id == self._session["id"]
            ok = self.sessions.delete_session(session_id)
            if not ok:
                return False
            if was_active:
                remaining = self.sessions.list_sessions()
                loaded = self.sessions.load_session(remaining[0].id) if remaining else None
                self._session = loaded or self.sessions.create_session()
                self.sessions.set_active_id(self._session["id"])
                self.agent.conversation.turns = copy.deepcopy(self._session.get("turns") or [])
                self.agent.conversation.summary_lines = list(self._session.get("summary_lines") or [])
                self.agent.loaded = []
            self._notify_sessions_changed()
            return True

    def reset_chat(self) -> None:
        """Clear the active persisted chat history and start fresh (no stacked session list)."""
        with self._lock:
            self._session["turns"] = []
            self._session["summary_lines"] = []
            self._session["display"] = []
            self._session["title"] = "Chat"
            self.agent.conversation.turns = []
            self.agent.conversation.summary_lines = []
            self.agent.loaded = []
            self._persist_session()
            self._notify_sessions_changed()

    def saved_transcript(self) -> list:
        """Rebuild (who, text, extra, files, steps) tuples for the active session."""
        rows = []
        for item in self._session.get("display") or []:
            files = self._hydrate_files(item.get("files") or [])
            steps = list(item.get("steps") or [])
            rows.append((item.get("who", "pilot"), item.get("text", ""), item.get("extra", ""), files, steps))
        if rows:
            return rows
        for turn in self.agent.conversation.turns:
            if not turn:
                continue
            first = turn[0]
            if first.get("role") == "user" and isinstance(first.get("content"), str):
                user_text = first["content"]
                if user_text.startswith("[Files the user sent you"):
                    parts = user_text.split("\n\n", 1)
                    user_text = parts[1] if len(parts) > 1 else user_text
                rows.append(("you", user_text, "", [], []))
            reply_text = ""
            for msg in turn[1:]:
                if msg.get("role") == "assistant":
                    content = msg.get("content")
                    if isinstance(content, str):
                        reply_text = content or reply_text
                    elif isinstance(content, list):
                        txt = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                        if txt and txt != "(cut off by output limit)":
                            reply_text = txt
            if reply_text:
                rows.append(("pilot", reply_text, "earlier in this chat", [], []))
        return rows

    def usage_line(self) -> str:
        today = self.agent.usage.totals()
        usd = f"${today['usd']:.4f}" + ("" if today["usd_complete"] else " (some calls have no price)")
        return f"Today: {today['calls']} calls, {today['total_tokens']:,} tokens, {usd}"

    # --- key and models -------------------------------------------------------------------------
    def _draft(self, provider: str, key: str, main: str = "", cheap: str = "") -> Settings:
        """A copy of the settings with the new choices, used for testing before anything is saved."""
        draft = Settings(copy.deepcopy(self.settings.values))
        draft.set("provider", provider)
        key = clean_key(key)
        if key:
            draft.set_api_key(provider, key)
        reset_models_if_invalid(draft, provider)
        secrets = draft.secrets()
        if valid_model(main, provider, secrets):
            draft.set("models", "main", main)
        if valid_model(cheap, provider, secrets):
            draft.set("models", "cheap", cheap)
        return draft

    def check_key(self, provider: str, key: str, main: str = "", cheap: str = "") -> KeyCheck:
        """Test a key without saving it. For Gemini, also find the models this key can use (no tokens)."""
        if provider != "mock" and not clean_key(key) and not self.has_key(provider):
            return KeyCheck(False, "Paste your key first.")
        draft = self._draft(provider, key, main, cheap)
        models = []
        try:
            picked = suggest_models(draft, self.provider_factory)
            if picked:
                models = picked["all"]
                if not main:
                    draft.set("models", "main", picked["main"])
                if not cheap:
                    draft.set("models", "cheap", picked["cheap"])
            answer = check_connection(draft, self.provider_factory)
        except ProviderError as exc:
            return KeyCheck(False, draft.redact(str(exc)))
        except Exception as exc:  # never leave the screen stuck on "Testing..."
            return KeyCheck(False, draft.redact(f"Something went wrong: {exc}"))
        main_m, cheap_m = draft.get("models", "main"), draft.get("models", "cheap")
        for m in (main_m, cheap_m):
            if m not in models:
                models.append(m)
        return KeyCheck(True, f"Connected. The model answered: {answer}", models, main_m, cheap_m)

    def model_problem(self, provider: str, name: str, new_key: str = "") -> str:
        """Plain-words reason a model name is not allowed, or '' when it is fine."""
        secrets = self.settings.secrets() + ([clean_key(new_key)] if clean_key(new_key) else [])
        if valid_model(name, provider, secrets):
            return ""
        if looks_like_key(name, provider, secrets):
            return "That looks like an API key. Your key goes only in the key field."
        return f"A model name starts with '{model_prefix(provider)}' and has no spaces."

    def save(self, provider: str, key: str, main: str, cheap: str) -> str:
        """Save choices and switch to them at once (no restart). Returns '' or a problem in plain words."""
        if provider not in PROVIDERS:
            return "Pick a provider."
        if provider != "mock" and not clean_key(key) and not self.has_key(provider):
            return "Paste your key first."
        for name in (main, cheap):
            problem = self.model_problem(provider, name, key)
            if problem:
                return problem
        draft = self._draft(provider, key, main, cheap)
        finish_models(draft)
        if not self._lock.acquire(blocking=False):
            return "Pilot is busy with a task. Save again when it has finished."
        try:
            self.settings.values = draft.values
            self._save_file()
            self.rebuild()
        finally:
            self._lock.release()
        return ""

    def default_models(self, provider: str) -> dict:
        if provider == self.settings.provider:
            return {"main": self.settings.get("models", "main"), "cheap": self.settings.get("models", "cheap")}
        return dict(PROVIDER_INFO[provider]["models"])

    def set_theme(self, theme: str) -> None:
        self.settings.set("ui", "theme", theme)
        self._save_file()

    def _save_file(self) -> None:
        with self._file_lock:
            self.settings.save()

    # --- connectors -----------------------------------------------------------------------------
    def connector_data(self, cid: str) -> dict:
        return dict(self.settings.get("connectors", cid, default={}) or {})

    def connector_secrets(self, cid: str) -> dict:
        return dict(self.settings.get("connector_secrets", cid, default={}) or {})

    def connector_status(self, cid: str) -> str:
        """One of: Not set up, Connected, Off, Problem."""
        data = self.connector_data(cid)
        if not data.get("connected"):
            return "Not set up"
        if not data.get("enabled"):
            return "Off"
        return "Problem" if self.problems.get(cid) else "Connected"

    def connector_cards(self) -> list:
        cards = []
        paused = self.is_paused()
        for cid, c in self.connectors.items():
            status = self.connector_status(cid)
            if status == "Problem":
                health = "red"
            elif status == "Connected" and paused:
                health = "yellow"
            elif status == "Connected":
                health = "green"
            elif status == "Off":
                health = "yellow"
            else:
                health = "none"
            cards.append({
                "id": cid, "name": c.name, "summary": c.guide.get("summary", ""), "status": status,
                "connected": bool(self.connector_data(cid).get("connected")),
                "enabled": bool(self.connector_data(cid).get("enabled")),
                "problem": self.problems.get(cid, ""),
                "health": health,
                "health_note": self.health_notes.get(cid, ""),
            })
        return cards

    def check_connectors_health(self) -> list[dict]:
        """Run lightweight health checks across every connected & enabled connector (Green/Yellow/Red)."""
        results = []
        for cid, conn in self.connectors.items():
            data = self.connector_data(cid)
            if not data.get("connected"):
                results.append({"id": cid, "name": conn.name, "health": "none", "message": "Not set up."})
                continue
            if not data.get("enabled"):
                results.append({"id": cid, "name": conn.name, "health": "yellow", "message": "Turned off."})
                continue
            if self.is_paused():
                results.append({"id": cid, "name": conn.name, "health": "yellow", "message": "Paused (Pause All)."})
                continue
            try:
                ok, msg = conn.health_check(self.connector_secrets(cid), data)
            except Exception as exc:
                ok, msg = False, f"Health check failed: {exc}"
            msg = self.settings.redact(msg)
            if ok:
                self.problems.pop(cid, None)
                self.health_notes[cid] = msg
                results.append({"id": cid, "name": conn.name, "health": "green", "message": msg})
            else:
                self.problems[cid] = msg
                self.health_notes[cid] = msg
                self.activity.record(summary=f"{conn.name} health problem: {msg}", kind="error", source="system")
                results.append({"id": cid, "name": conn.name, "health": "red", "message": msg})
        return results

    def _rebuild_if_idle(self) -> None:
        if self._lock.acquire(blocking=False):
            try:
                self.rebuild()
            finally:
                self._lock.release()

    def check_connector(self, cid: str, values: dict) -> CheckResult:
        """Run a connector's test. Only a passing test is saved, and only then does the card say Connected."""
        values = {k: clean_key(v) for k, v in values.items()}
        try:
            result = self.connectors[cid].check(values, self.connector_secrets(cid), self.connector_data(cid))
        except Exception as exc:  # never leave the screen stuck on "Testing..."
            result = CheckResult(False, f"Something went wrong: {exc}", "Try again in a moment.")
        result.message = self.settings.redact(result.message)
        for field_def in self.connectors[cid].fields:
            if field_def.get("secret"):
                secret_val = values.get(field_def["key"], "")
                if secret_val:
                    result.message = result.message.replace(secret_val, "[hidden]")
                    result.fix = result.fix.replace(secret_val, "[hidden]")
        if not result.ok:
            return result
        self.settings.set("connector_secrets", cid, {**self.connector_secrets(cid), **result.secrets})
        self.settings.set("connectors", cid, {**self.connector_data(cid), **result.data,
                                              "connected": True, "enabled": True})
        self._save_file()
        self.problems.pop(cid, None)
        self.health_notes[cid] = result.message
        self._rebuild_if_idle()
        self.start_connector(cid)
        return result

    def set_connector_enabled(self, cid: str, enabled: bool) -> None:
        if not self.connector_data(cid).get("connected"):
            return  # a connector must pass its test before it can be switched on
        self.settings.set("connectors", cid, {**self.connector_data(cid), "enabled": enabled})
        self._save_file()
        self.problems.pop(cid, None)
        self._rebuild_if_idle()
        self.start_connector(cid) if enabled else self.stop_connector(cid)

    def start_connectors(self) -> None:
        if self.is_paused():
            return
        for cid in self.connectors:
            data = self.connector_data(cid)
            if data.get("connected") and data.get("enabled"):
                self.start_connector(cid)

    def start_connector(self, cid: str) -> None:
        self.stop_connector(cid)
        label = self.connectors[cid].name
        runner = self.connectors[cid].make_runner(
            self.connector_secrets(cid), self.connector_data(cid),
            ask=lambda text, attachments=None, lbl=label: self._ask_from_connector_full(text, attachments, source_label=lbl),
            on_event=lambda kind, info: self._connector_event(cid, kind, info),
            save_data=lambda data: self._save_connector_data(cid, data),
            workspace=self.workspace)
        if runner:
            self.runners[cid] = runner
            runner.start()

    def stop_connector(self, cid: str) -> None:
        runner = self.runners.pop(cid, None)
        if runner:
            runner.stop()

    def stop_connectors(self) -> None:
        for cid in list(self.runners):
            self.stop_connector(cid)

    def _ask_from_connector_full(self, text: str, attachments: list | None = None,
                                 source_label: str = "") -> tuple[str, list]:
        """A message (and optional workspace files) from a connector. Limits are never overridden remotely."""
        reply = self.send(text, attachments=attachments, source_label=source_label)
        if reply.kind == "ok":
            return reply.text, reply.files
        if reply.kind == "cap":
            return f"{reply.text} Open Pilot on your computer to decide whether to go on.", []
        return f"Sorry, that did not work. {reply.text}", []

    def _ask_from_connector(self, text: str) -> str:
        """A message from the owner's phone. Limits are never overridden from the phone."""
        return self._ask_from_connector_full(text)[0]

    def _save_connector_data(self, cid: str, data: dict) -> None:
        self.settings.set("connectors", cid, {**self.connector_data(cid), **data})
        self._save_file()

    def _connector_event(self, cid: str, kind: str, info) -> None:
        if kind == "problem":
            msg = self.settings.redact(str(info))
            self.problems[cid] = msg
            cname = self.connectors[cid].name if cid in self.connectors else cid
            self.activity.record(summary=f"{cname} problem: {msg}", kind="error", source="system")
        elif kind == "ok":
            self.problems.pop(cid, None)
        if self.on_connector_event:
            self.on_connector_event(cid, kind, info)
    # --- the agent's workspace, approvals, memory and permissions -------------------------------
    def _approvals_changed(self) -> None:
        if self.on_approvals_changed:
            self.on_approvals_changed()

    def _notify_owner_remote(self, text: str) -> None:
        """Deliver important scheduled updates, failures, or away-mode approval alerts to connected phone/Discord."""
        msg = self.settings.redact((text or "").strip())
        if not msg:
            return
        for cid, runner in list(self.runners.items()):
            try:
                if cid == "telegram" and getattr(runner, "owner", None) is not None:
                    runner.client.send_message(runner.owner, msg)
                elif cid == "discord" and getattr(runner, "dm_channel_id", ""):
                    runner.client.send_message(runner.dm_channel_id, msg)
            except Exception:
                pass

    def _approve(self, request) -> str:
        """Called on the agent's thread: run the second check, queue the request, and wait up to 10 minutes."""
        web_live = bool(self.web_server and self.web_server.running)
        if self.approval_handler is None and not web_live:
            return "deny"  # nobody to ask (for example a test): the safe answer
        item = self.approvals.enqueue(request)

        def answer(choice: str) -> None:
            self.resolve_approval(item.id, choice)

        if self.approval_handler is not None:
            self.approval_handler(request, answer)
        else:
            # Running headless / user away: notify on phone/Discord so they can approve in the Web UI.
            self._notify_owner_remote(
                f"Pilot needs your OK ({item.connector}): {item.action[:140]}\n"
                f"Why: {item.reason}\nOpen Pilot (or your Pilot Web page) -> Approvals to Approve or Deny."
            )
        return self.approvals.wait(item, timeout=600.0)

    # --- password-protected web access & free Cloudflare HTTPS tunnel ---------------------------
    def web_config(self) -> dict:
        cfg = self.settings.get("web_access", default={}) or {}
        running = bool(self.web_server and self.web_server.running)
        tunnel_running = bool(self.tunnel and self.tunnel.running)
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "host": str(cfg.get("host") or "127.0.0.1"),
            "port": int(cfg.get("port", 8765) or 8765),
            "has_password": bool(cfg.get("password_hash") and cfg.get("password_salt")),
            "running": running,
            "urls": self.web_server.urls() if running else [],
            "tunnel_available": bool(find_cloudflared()),
            "tunnel_running": tunnel_running,
            "public_url": self.tunnel.public_url if tunnel_running else "",
        }

    def start_public_tunnel(self, tunnel_factory=None) -> tuple[str, str]:
        """Put Pilot's password-protected Web page online over HTTPS via free Cloudflare Tunnel ($0)."""
        if self.is_paused():
            return "", "Pilot is paused. Resume Pilot first."
        cfg = self.web_config()
        if not cfg["running"] or not cfg["has_password"]:
            return "", "Turn on Web access with a password first before putting Pilot online."
        self.stop_public_tunnel()
        port = self.web_server.actual_port
        token = str((self.settings.get("connector_secrets", "cloudflare", default={}) or {}).get("tunnel_token") or "")
        custom_url = str((self.settings.get("web_access", default={}) or {}).get("custom_url") or "")
        tunnel = (
            tunnel_factory(port)
            if tunnel_factory is not None
            else CloudflareTunnel(port, tunnel_token=token, custom_url=custom_url)
        )
        try:
            url = tunnel.start(timeout=20.0)
        except TunnelError as exc:
            return "", str(exc)
        self.tunnel = tunnel
        self.activity.record(summary=f"Public HTTPS tunnel started ({url})", kind="system", source="system")
        return url, ""

    def stop_public_tunnel(self) -> None:
        if self.tunnel is not None:
            self.tunnel.stop()
            self.tunnel = None

    def configure_web(self, enabled: bool, host: str = "127.0.0.1", port: int = 8765,
                      new_password: str = "") -> str:
        """Save password-protected web access settings and start or stop the server immediately."""
        host = "0.0.0.0" if host == "0.0.0.0" else "127.0.0.1"
        try:
            port = int(port)
        except (TypeError, ValueError):
            return "Port must be a number between 1024 and 65535."
        if not (1024 <= port <= 65535):
            return "Port must be between 1024 and 65535."
        cfg = dict(self.settings.get("web_access", default={}) or {})
        pw = (new_password or "").strip()
        if pw:
            if len(pw) < 4:
                return "Choose a password with at least 4 characters."
            salt, digest = hash_password(pw)
            cfg["password_salt"] = salt
            cfg["password_hash"] = digest
        if enabled and not (cfg.get("password_hash") and cfg.get("password_salt")):
            return "Set a password first before turning on Web access."
        cfg["enabled"] = bool(enabled)
        cfg["host"] = host
        cfg["port"] = port
        self.settings.set("web_access", cfg)
        self._save_file()
        self.stop_web()
        if enabled:
            try:
                self.web_server = PilotWebServer(self, host=host, port=port)
                self.web_server.start()
            except OSError as exc:
                cfg["enabled"] = False
                self.settings.set("web_access", cfg)
                self._save_file()
                self.web_server = None
                return f"Could not start web access on port {port}: {exc}"
        return ""

    def start_web_if_enabled(self) -> None:
        cfg = self.web_config()
        if cfg["enabled"] and cfg["has_password"] and not cfg["running"]:
            try:
                self.web_server = PilotWebServer(self, host=cfg["host"], port=cfg["port"])
                self.web_server.start()
            except OSError:
                self.web_server = None

    def stop_web(self) -> None:
        self.stop_public_tunnel()
        if self.web_server is not None:
            self.web_server.stop()
            self.web_server = None

    def pending_approvals(self) -> list:
        return self.approvals.pending()

    def pending_approvals_count(self) -> int:
        return self.approvals.pending_count()

    def approval_history(self) -> list:
        return self.approvals.history()

    def resolve_approval(self, item_id: str, choice: str) -> bool:
        target = next((a for a in self.approvals.pending() if a.id == item_id), None)
        ok = self.approvals.resolve(item_id, choice)
        if ok and target is not None:
            word = {"once": "Approved", "always": "Always allowed", "deny": "Denied"}.get(choice, choice)
            self.activity.record(summary=f"{word} ({target.connector}): {target.action}",
                                 kind="approval", source="system")
            if choice in ("once", "always"):
                recipient = extract_email_recipient(target.action)
                if recipient:
                    known = list(self.settings.get("known_recipients", default=[]) or [])
                    if recipient not in known:
                        known.append(recipient)
                        self.settings.set("known_recipients", known[-100:])
                        self._save_file()
        return ok

    # --- Pause All, always-on scheduler, Start with Windows, Activity & Usage (Session 5) -------
    def is_paused(self) -> bool:
        return bool(self.settings.get("runtime", "paused", default=False))

    def pause_all(self) -> None:
        """Emergency stop: pause the agent, stop connectors & scheduler, deny pending approvals."""
        self.settings.set("runtime", "paused", True)
        self._save_file()
        self.stop_connectors()
        self.stop_public_tunnel()
        self.scheduler.stop()
        for item in self.approvals.pending():
            self.approvals.resolve(item.id, "deny")
        self.activity.record(summary="Pilot paused (Pause All).", kind="pause", source="system")
        if self.on_pause_changed:
            try:
                self.on_pause_changed()
            except Exception:
                pass

    def resume_all(self) -> None:
        self.settings.set("runtime", "paused", False)
        self._save_file()
        self.start_connectors()
        self.start_scheduler()
        self.activity.record(summary="Pilot resumed.", kind="pause", source="system")
        if self.on_pause_changed:
            try:
                self.on_pause_changed()
            except Exception:
                pass

    def start_scheduler(self) -> None:
        if not self.is_paused():
            self.scheduler.start()

    def stop_scheduler(self) -> None:
        self.scheduler.stop()

    def _run_scheduled_job(self, job) -> str:
        t0 = time.time()
        reply = self.send(job.prompt + QUIET_INSTRUCTION, source_label="Schedule")
        self._last_job_timing = (t0, time.time(), reply.detail)
        return reply.text if reply.kind == "ok" else f"Scheduled job '{job.name}' failed: {reply.text}"

    def _on_schedule_report(self, job, reply_text: str, quiet: bool) -> None:
        t0, t1, _ = getattr(self, "_last_job_timing", (None, None, ""))
        if quiet:
            self.activity.record(summary=f"{job.name}: nothing new", kind="schedule",
                                 source="schedule", started=t0, ended=t1, quiet=True)
            return
        is_fail = reply_text.lower().startswith("scheduled ") and "failed:" in reply_text.lower()
        self.activity.record(summary=f"{job.name}: {reply_text[:140]}",
                             kind="error" if is_fail else "schedule",
                             source="schedule", started=t0, ended=t1)
        self._session.setdefault("display", []).append({
            "who": "pilot",
            "text": f"[Scheduled: {job.name}]\n{reply_text}",
            "extra": "scheduled check",
            "steps": [],
            "files": [],
        })
        self._persist_session()
        self._notify_sessions_changed()
        self._notify_owner_remote(f"[Scheduled: {job.name}]\n{reply_text[:800]}")

    def scheduled_jobs(self) -> list:
        return self.scheduler.jobs()

    def add_scheduled_job(self, name: str, prompt: str, interval_minutes: int = 60):
        job = self.scheduler.add_job(name, prompt, interval_minutes)
        self.activity.record(summary=f"Scheduled '{job.name}' every {job.interval_minutes} min",
                             kind="schedule", source="system")
        return job

    def remove_scheduled_job(self, job_id: str) -> bool:
        return self.scheduler.remove_job(job_id)

    def set_scheduled_job_enabled(self, job_id: str, enabled: bool) -> bool:
        return self.scheduler.set_job_enabled(job_id, enabled)

    def when_ignored_policy(self) -> str:
        return str(self.settings.get("schedule_policy", "when_ignored", default="keep_going") or "keep_going")

    def set_when_ignored_policy(self, mode: str) -> None:
        if mode in ("keep_going", "pause"):
            self.settings.set("schedule_policy", "when_ignored", mode)
            self._save_file()

    def start_with_windows(self) -> bool:
        return is_start_with_windows_enabled(self._startup_dir)

    def set_start_with_windows(self, enabled: bool) -> str:
        try:
            set_start_with_windows(bool(enabled), startup_dir=self._startup_dir)
            self.settings.set("runtime", "start_with_windows", bool(enabled))
            self._save_file()
            return ""
        except OSError as exc:
            return f"Could not update Start with Windows: {exc}"

    def activity_entries(self, query: str = "", limit: int = 120) -> list:
        return self.activity.entries(query=query, limit=limit)

    def daily_summary_lines(self) -> list:
        return self.activity.daily_summary_lines()

    def export_activity(self, dest, query: str = "") -> Path:
        return self.activity.export_text(dest, query=query)

    def sample_resources(self) -> dict:
        return self.agent.usage.record_resource_sample(self.workspace)

    def resource_samples(self, window: str = "hour") -> list:
        return self.agent.usage.resource_samples(window=window)

    def period_usage(self) -> dict:
        return self.agent.usage.period_summary()

    def usage_alerts(self) -> list:
        return self.agent.usage.alerts(self.workspace)

    def export_usage(self, dest) -> Path:
        return self.agent.usage.export_report(dest, self.workspace)

    def memory_notes(self) -> list:
        return self.memory.list_notes()

    def save_memory_note(self, title: str, content: str, append: bool = False):
        return self.memory.save_note(title, content, append=append)

    def delete_memory_note(self, title_or_slug: str) -> bool:
        return self.memory.delete_note(title_or_slug)

    def _activity(self, text: str) -> None:
        if self.on_activity:
            self.on_activity(text)

    def _record_command(self, command: str, started: float, ended: float, exit_code: int) -> None:
        short = " ".join((command or "").split())[:140]
        self.activity.record(
            summary=f"Ran command (exit {exit_code}): {short}",
            kind="command",
            source="terminal",
            started=started,
            ended=ended,
        )

    def workspace_summary(self) -> str:
        venv_txt = " · isolated Python on" if self.workspace.has_venv() else ""
        return f"{self.workspace.files}  ({self.workspace.summary()}{venv_txt})"

    def has_workspace_venv(self) -> bool:
        return self.workspace.has_venv()

    def create_workspace_venv(self) -> str:
        try:
            self.workspace.create_venv()
            self.activity.record(summary="Created isolated workspace Python environment.", kind="system", source="system")
            return ""
        except WorkspaceError as exc:
            return str(exc)

    def remove_workspace_venv(self) -> None:
        self.workspace.remove_venv()
        self.activity.record(summary="Removed isolated workspace Python environment.", kind="system", source="system")

    def workspace_files(self, subdir: str = "") -> list[dict]:
        """List files and folders inside Pilot's workspace (used by Web Workspace & custom UIs)."""
        try:
            folder = self.workspace.path(subdir)
        except WorkspaceError:
            return []
        if not folder.is_dir():
            return []
        out = []
        entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for p in entries[:250]:
            try:
                st = p.stat()
                size = 0 if p.is_dir() else st.st_size
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
            except OSError:
                size, mtime = 0, ""
            out.append({
                "name": p.name,
                "rel_path": self.workspace.show(p),
                "is_dir": p.is_dir(),
                "size": size,
                "size_label": "folder" if p.is_dir() else file_size_label(size),
                "modified": mtime,
            })
        return out

    def delete_workspace_file(self, rel_path: str) -> tuple[bool, str]:
        """Delete one file inside data/agent/files/ (never folders or paths outside the workspace)."""
        try:
            target = self.workspace.path(rel_path)
        except WorkspaceError as exc:
            return False, str(exc)
        if not target.exists():
            return False, "File not found."
        if target.is_dir():
            return False, "Folders cannot be deleted from the web file list."
        try:
            target.unlink()
        except OSError as exc:
            return False, f"Could not delete file: {exc}"
        self.activity.record(summary=f"Deleted workspace file: {self.workspace.show(target)}",
                             kind="command", source="web-terminal")
        return True, ""

    def run_workspace_terminal(self, command: str) -> dict:
        """Run one owner command inside Pilot's isolated workspace (RAM/time/disk caps + NEVER block)."""
        import sys
        cmd = (command or "").strip()
        if not cmd:
            return {"ok": False, "exit_code": 1, "output": "", "timed_out": False,
                    "hit_memory_limit": False, "error": "Enter a command first."}
        if self.is_paused():
            return {"ok": False, "exit_code": 1, "output": "", "timed_out": False,
                    "hit_memory_limit": False, "error": "Pilot is paused (Pause All). Resume Pilot first."}
        verdict = classify_command(cmd, self.workspace.files)
        if verdict.blocked:
            return {"ok": False, "exit_code": 1, "output": "", "timed_out": False,
                    "hit_memory_limit": False, "error": verdict.blocked}
        t0 = time.time()
        try:
            if sys.platform == "win32":
                script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; " + cmd
                argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
            else:
                argv = ["/bin/bash", "-c", cmd]
            res = self.workspace.run(argv)
        except WorkspaceError as exc:
            return {"ok": False, "exit_code": 1, "output": "", "timed_out": False,
                    "hit_memory_limit": False, "error": str(exc)}
        t1 = time.time()
        clean_out = self.settings.redact((res.output or "").strip())[:16000]
        self.activity.record(
            summary=f"Web terminal (exit {res.exit_code}): {' '.join(cmd.split())[:120]}",
            kind="command", source="web-terminal", started=t0, ended=t1,
        )
        return {
            "ok": res.exit_code == 0 and not res.timed_out and not res.hit_memory_limit,
            "exit_code": res.exit_code,
            "output": clean_out or "(no output)",
            "timed_out": res.timed_out,
            "hit_memory_limit": res.hit_memory_limit,
            "error": "",
        }

    def limits_config(self) -> dict:
        lim = self.settings.get("limits", default={}) or {}
        ws = self.settings.get("workspace", default={}) or {}
        return {
            "daily_tokens": int(lim.get("daily_tokens", 300000) or 300000),
            "daily_usd": float(lim.get("daily_usd", 2.0) or 2.0),
            "max_disk_gb": float(ws.get("max_disk_gb", 5) or 5),
        }

    def save_limits(self, daily_tokens, daily_usd, max_disk_gb) -> str:
        """Save beginner-friendly daily and workspace limits from Settings without editing JSON."""
        try:
            tok = int(str(daily_tokens).replace(",", "").strip())
            usd = float(str(daily_usd).replace("$", "").strip())
            disk = float(str(max_disk_gb).strip())
        except ValueError:
            return "Enter plain numbers for daily tokens, daily dollars, and workspace GB."
        if tok < 1000 or tok > 100_000_000:
            return "Daily tokens should be between 1,000 and 100,000,000."
        if usd < 0.0 or usd > 1000.0:
            return "Daily dollar limit should be between 0 and 1000."
        if disk < 0.5 or disk > 500.0:
            return "Workspace disk limit should be between 0.5 GB and 500 GB."
        self.settings.set("limits", "daily_tokens", tok)
        self.settings.set("limits", "daily_usd", round(usd, 2))
        self.settings.set("workspace", "max_disk_gb", round(disk, 2))
        self.workspace.max_bytes = int(disk * (1024 ** 3))
        self._save_file()
        return ""

    def tool_groups(self) -> dict:
        return {**DEFAULT_GROUPS, **(self.settings.get("agent_tools", default={}) or {})}

    def set_tool_group(self, group: str, on: bool) -> str:
        if group not in TOOL_GROUPS:
            return "Unknown tool group."
        if not self._lock.acquire(blocking=False):
            return "Pilot is busy with a task. Try again when it has finished."
        try:
            self.settings.set("agent_tools", group, bool(on))
            self._save_file()
            self.rebuild()
        finally:
            self._lock.release()
        return ""

    def permission_rows(self) -> list:
        return permission_rows(self.settings)

    def set_permission(self, kind: str, value: str) -> None:
        if value in ("ask", "allow"):
            self.settings.set("permissions", kind, value)
            self._save_file()

    def always_allow_rules(self) -> list:
        return list(self.settings.get("always_allow", default=[]) or [])

    def remove_rule(self, rule: str) -> None:
        self.settings.set("always_allow", [r for r in self.always_allow_rules() if r != rule])
        self._save_file()