"""The Pilot desktop window: a slim sidebar on the left, one screen on the right.

Start it with `python -m src window`, or double-click Pilot.pyw in the project folder.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk

from ..core.settings import APP_NAME
from .activity_view import ActivityView
from .approvals_view import ApprovalsView
from .chat_view import ChatView
from .connectors_view import ConnectorsView
from .controller import Controller
from .memory_view import MemoryView
from .settings_view import SettingsView
from .theme import FONT, FONT_BOLD, FONT_SMALL, FONT_TITLE, Theme
from .welcome_view import WelcomeView

SCREENS = ("Chat", "Connectors", "Approvals", "Memory", "Activity", "Settings")


def _sharp_text_on_windows() -> None:
    """Without this, Windows blurs the window on high-resolution screens."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class App:
    def __init__(self, controller: Controller | None = None, root: tk.Tk | None = None):
        _sharp_text_on_windows()
        self.controller = controller or Controller()
        self.root = root or tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1000x700")
        self.root.minsize(780, 560)
        self.transcript: list = self.controller.saved_transcript()  # (who, text, extra) so the chat survives restarts and theme changes
        self._results: queue.Queue = queue.Queue()
        self.screen = "Chat"
        # Connector news (a phone message, a problem) arrives on a background thread; hand it to the window safely.
        self.controller.on_connector_event = lambda cid, kind, info: self._results.put(
            (self._connector_event, (cid, kind, info)))
        # Approvals and "what the agent is doing" also come from the agent's thread.
        self.controller.approval_handler = lambda request, answer: self._results.put(
            (self._ask_approval, (request, answer)))
        self.controller.on_approvals_changed = lambda: self._results.put((self._approvals_changed, None))
        self.controller.on_activity = lambda text: self._results.put((self._activity, text))
        self.controller.on_step = lambda step: self._results.put((self._step, step))
        self.controller.on_sessions_changed = lambda: self._results.put((self._sessions_changed, None))
        self.controller.on_pause_changed = lambda: self._results.put((self._pause_changed, None))
        self._last_sample_tick = 0.0
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self._poll_id = self.root.after(80, self._poll)

    # --- building -------------------------------------------------------------------------------
    def build(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self.theme = t = Theme(self.controller.theme)
        t.style_ttk(self.root)
        self.root.configure(bg=t["bg"])
        self.welcome = None
        if self.controller.needs_welcome():
            self.welcome = WelcomeView(self, self.root)
            self.welcome.frame.pack(fill="both", expand=True)
            return
        self.sidebar = t.frame(self.root, bg="sidebar", width=195)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        t.label(self.sidebar, APP_NAME, bg="sidebar", font=FONT_TITLE).pack(fill="x", padx=18, pady=(20, 14))
        self.nav = {}
        for name in SCREENS:
            b = tk.Label(self.sidebar, text=name, font=FONT, anchor="w", padx=14, pady=8, cursor="hand2",
                         bg=t["sidebar"], fg=t["text"])
            b.pack(fill="x", padx=10, pady=1)
            b.bind("<Button-1>", lambda e, n=name: self.show(n))
            self.nav[name] = b

        footer = t.frame(self.sidebar, bg="sidebar")
        footer.pack(side="bottom", fill="x", padx=14, pady=(6, 14))
        self.pause_btn = tk.Button(
            footer, text="Pause All", command=self.toggle_pause, relief="flat", bd=0, cursor="hand2",
            font=FONT_BOLD, padx=12, pady=7, bg=t["bad"], fg="#ffffff",
            activebackground=t["bad"], activeforeground="#ffffff",
        )
        self.pause_btn.pack(fill="x", pady=(0, 8))
        self.web_badge = t.label(footer, "", bg="sidebar", fg="ok", font=FONT_SMALL)
        self.web_badge.pack(fill="x", padx=4)
        t.label(footer, f"Using {self.controller.settings.provider}", bg="sidebar", fg="muted",
                font=FONT_SMALL).pack(fill="x", padx=4)

        self.main = t.frame(self.root)
        self.main.pack(side="left", fill="both", expand=True)
        self.views = {
            "Chat": ChatView(self, self.main),
            "Connectors": ConnectorsView(self, self.main),
            "Approvals": ApprovalsView(self, self.main),
            "Memory": MemoryView(self, self.main),
            "Activity": ActivityView(self, self.main),
            "Settings": SettingsView(self, self.main),
        }
        self._update_approvals_badge()
        self._update_pause_ui()
        self.show(self.screen)
        if not self.controller.runners:
            self.controller.start_connectors()
        self.controller.start_scheduler()
        self.controller.start_web_if_enabled()
        self._update_web_badge()

    def show(self, name: str) -> None:
        self.screen = name
        for view_name, view in self.views.items():
            view.frame.pack_forget()
            active = view_name == name
            self.nav[view_name].configure(bg=self.theme["nav_active" if active else "sidebar"])
        self.views[name].frame.pack(fill="both", expand=True)
        if name == "Connectors":
            self.views[name].show_grid()  # the sidebar always opens the cards, not a half-done setup page
        elif name in ("Approvals", "Memory", "Activity", "Settings"):
            self.views[name].refresh()

    def _update_approvals_badge(self) -> None:
        if self.welcome is not None or "Approvals" not in getattr(self, "nav", {}):
            return
        count = self.controller.pending_approvals_count()
        label = f"Approvals  ({count})" if count else "Approvals"
        self.nav["Approvals"].configure(
            text=label,
            fg=self.theme["warn" if count else "text"],
            font=FONT_BOLD if count else FONT,
        )

    def _approvals_changed(self, _unused=None) -> None:
        if self.welcome is not None:
            return
        self._update_approvals_badge()
        if "Approvals" in self.views:
            self.views["Approvals"].refresh()

    def refresh_sidebar(self) -> None:
        """Connector status changed: keep the Connectors screen up to date."""
        if self.welcome is None and self.screen != "Connectors":
            self.views["Connectors"].refresh()

    def _connector_event(self, event) -> None:
        cid, kind, info = event
        if self.welcome is not None:
            return
        if kind == "message":  # the owner chatted from a connector: show it here too
            text, reply = info[0], info[1]
            files = info[2] if len(info) > 2 else []
            name = self.controller.connectors[cid].name
            self.views["Chat"].add("you_phone", text, name)
            self.views["Chat"].add("pilot", reply, f"answered on {name}", files=files)
        else:
            self.views["Connectors"].refresh()

    def _ask_approval(self, item) -> None:
        request, answer = item
        if self.welcome is not None:
            answer("deny")
            return
        self.show("Chat")
        self.views["Chat"].show_approval(request, answer)
        self.root.deiconify()
        self.root.lift()
        self.root.bell()

    def _activity(self, text) -> None:
        if self.welcome is None:
            self.views["Chat"].show_activity(text)

    def _step(self, step: dict) -> None:
        if self.welcome is None and "Chat" in self.views:
            self.views["Chat"].update_live_step(step)

    def _update_web_badge(self) -> None:
        if self.welcome is not None or not hasattr(self, "web_badge"):
            return
        cfg = self.controller.web_config()
        if cfg.get("tunnel_running"):
            self.web_badge.configure(text="Web: Online (HTTPS)", fg=self.theme["ok"])
        elif cfg["running"]:
            self.web_badge.configure(text=f"Web: On (:{cfg['port']})", fg=self.theme["ok"])
        else:
            self.web_badge.configure(text="")

    def _sessions_changed(self, _unused=None) -> None:
        if self.welcome is not None:
            return
        chat = self.views.get("Chat")
        if chat is not None and not chat.busy:
            saved = self.controller.saved_transcript()
            if len(saved) != len(self.transcript):
                self.transcript = saved
                chat.reload_transcript()

    def clear_chat(self) -> None:
        if self.welcome is not None:
            return
        chat = self.views.get("Chat")
        if chat is not None and chat.busy:
            return
        self.controller.reset_chat()
        self.transcript = []
        if chat is not None:
            chat.reload_transcript()
            chat.set_status("ready", "Chat cleared.")
        self.show("Chat")

    def toggle_pause(self) -> None:
        if self.controller.is_paused():
            self.controller.resume_all()
        else:
            self.controller.pause_all()
        self._update_pause_ui()

    def _pause_changed(self, _unused=None) -> None:
        self._update_pause_ui()

    def _update_pause_ui(self) -> None:
        if self.welcome is not None or not hasattr(self, "pause_btn"):
            return
        paused = self.controller.is_paused()
        t = self.theme
        if paused:
            self.pause_btn.configure(text="Resume Pilot", bg=t["ok"], fg="#0f1115",
                                     activebackground=t["ok"], activeforeground="#0f1115")
            if "Chat" in self.views:
                self.views["Chat"].set_status("problem", "Paused (Pause All). Click Resume Pilot to continue.")
        else:
            self.pause_btn.configure(text="Pause All", bg=t["bad"], fg="#ffffff",
                                     activebackground=t["bad"], activeforeground="#ffffff")
            if "Chat" in self.views and not self.views["Chat"].busy:
                self.views["Chat"].set_status("ready")
        if "Settings" in self.views:
            self.views["Settings"]._draw_always_on()

    def close(self) -> None:
        chat = getattr(self, "views", {}).get("Chat")
        if chat is not None and hasattr(chat, "recorder"):
            chat.recorder.cancel()
        self.controller.stop_connectors()
        self.controller.stop_scheduler()
        self.controller.stop_web()
        self.root.after_cancel(self._poll_id)
        self.root.destroy()

    def finish_welcome(self, first_message: str = "", open_connector: str = "") -> None:
        self.screen = "Connectors" if open_connector else "Chat"
        self.build()
        if first_message:
            self.views["Chat"].prefill(first_message)
        if open_connector:
            self.views["Connectors"].show_guide(open_connector)

    def change_theme(self, name: str) -> None:
        self.controller.set_theme(name)
        self.build()

    # --- background work ------------------------------------------------------------------------
    def run_in_background(self, work, on_done) -> None:
        """Run slow work (a model call) off the window's thread, then hand the result back safely."""
        def worker():
            try:
                result = work()
            except Exception as exc:  # the screens show this in plain words
                result = exc
            self._results.put((on_done, result))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        import time
        try:
            while True:
                on_done, result = self._results.get_nowait()
                if isinstance(result, Exception):
                    from tkinter import messagebox
                    messagebox.showerror(APP_NAME, self.controller.settings.redact(f"Something went wrong: {result}"))
                else:
                    on_done(result)
        except queue.Empty:
            pass
        now = time.monotonic()
        if now - self._last_sample_tick >= 60.0 and self.welcome is None:
            self._last_sample_tick = now
            try:
                self.controller.sample_resources()
            except Exception:
                pass
        self._poll_id = self.root.after(80, self._poll)

    def run(self) -> None:
        self.root.mainloop()


def run() -> None:
    App().run()
