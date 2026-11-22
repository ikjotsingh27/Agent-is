"""The Chat screen: the conversation, a message box at the bottom, and a status dot."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from ..core.settings import APP_NAME
from ..core.voice import VoiceError, VoiceRecorder
from ..core.workspace import file_size_label
from .theme import FONT, FONT_BOLD, FONT_SMALL, FONT_TITLE


class ChatView:
    def __init__(self, app, parent):
        self.app, t = app, app.theme
        self.busy = False
        self.pending_files: list[Path] = []
        self._live_steps: list[dict] = []
        self.recorder = VoiceRecorder()
        self.frame = t.frame(parent)

        header = t.frame(self.frame)
        header.pack(fill="x", padx=28, pady=(22, 8))
        t.label(header, "Chat", font=FONT_TITLE).pack(side="left")
        self.dot = tk.Canvas(header, width=12, height=12, bg=t["bg"], highlightthickness=0)
        self.dot.pack(side="left", padx=(16, 6), pady=(4, 0))
        self.status = t.label(header, "", fg="muted")
        self.status.pack(side="left", pady=(4, 0))
        t.button(header, "New chat", self.new_chat, kind="secondary").pack(side="right")

        # The message box is packed from the bottom first, so the chat above can never push it off screen.
        t.label(self.frame, "Enter sends. Shift+Enter starts a new line. Click + to attach files or Mic for a voice note.", fg="muted",
                font=FONT_SMALL).pack(side="bottom", fill="x", padx=30, pady=(0, 14))
        box_outer = tk.Frame(self.frame, bg=t["card"], highlightthickness=1, highlightbackground=t["border"])
        box_outer.pack(side="bottom", fill="x", padx=28, pady=(8, 6))
        box = tk.Frame(box_outer, bg=t["card"])
        box.pack(fill="x", padx=10, pady=8)
        self.send_btn = t.button(box, "Send", self.send, bg="card")
        self.send_btn.pack(side="right", padx=(10, 0))  # placed first so the text box cannot hide it
        self.attach_btn = tk.Button(
            box, text="+", command=self.pick_files, relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI Semibold", 13), padx=12, pady=3,
            bg=t["nav_active"], fg=t["text"], activebackground=t["border"], activeforeground=t["text"],
            disabledforeground=t["muted"],
        )
        self.attach_btn.pack(side="left", padx=(0, 6))
        self.mic_btn = tk.Button(
            box, text="Mic", command=self.toggle_voice, relief="flat", bd=0, cursor="hand2",
            font=FONT_SMALL, padx=10, pady=6,
            bg=t["nav_active"], fg=t["text"], activebackground=t["border"], activeforeground=t["text"],
            disabledforeground=t["muted"],
        )
        self.mic_btn.pack(side="left", padx=(0, 10))
        self.input = tk.Text(box, height=1, width=1, wrap="word", relief="flat", bd=0, font=FONT, bg=t["card"],
                             fg=t["text"], insertbackground=t["text"], highlightthickness=0)
        self.input.pack(side="left", fill="x", expand=True, pady=(5, 3))
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<KeyRelease>", self._auto_resize_input)
        self.attach_bar = t.frame(self.frame)
        self.live_panel = t.frame(self.frame)

        # No scrollbar (tkinter cannot color it on Windows); the mouse wheel scrolls the chat.
        self.log = tk.Text(self.frame, wrap="word", relief="flat", bd=0, font=FONT, bg=t["bg"], fg=t["text"],
                           height=1, padx=4, pady=8, cursor="arrow", spacing1=2, spacing3=2, highlightthickness=0,
                           state="disabled")
        self.log.pack(fill="both", expand=True, padx=28)
        self.log.tag_configure("you_name", foreground=t["you"], font=FONT_BOLD, spacing1=14)
        self.log.tag_configure("pilot_name", foreground=t["accent"], font=FONT_BOLD, spacing1=14)
        self.log.tag_configure("msg", lmargin1=2, lmargin2=2, rmargin=24)
        self.log.tag_configure("meta", foreground=t["muted"], font=FONT_SMALL)
        self.log.tag_configure("problem", foreground=t["bad"], lmargin1=2, lmargin2=2)

        for entry in app.transcript:  # redraw the chat after a theme change or restart
            who, text, extra = entry[0], entry[1], entry[2]
            files = entry[3] if len(entry) > 3 else []
            steps = entry[4] if len(entry) > 4 else []
            self._write(who, text, extra, files, steps)
        self.set_status("ready")

    # --- status ---------------------------------------------------------------------------------
    def set_status(self, state: str, text: str = "") -> None:
        t = self.app.theme
        color = {"ready": t["ok"], "thinking": t["warn"], "problem": t["bad"]}[state]
        default = {"ready": f"{APP_NAME} is running. Ready.", "thinking": "Thinking...",
                   "problem": "There was a problem. See the message below."}[state]
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 11, 11, fill=color, outline=color)
        self.status.configure(text=text or default)

    # --- transcript -----------------------------------------------------------------------------
    def reload_transcript(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        for entry in self.app.transcript:
            who, text, extra = entry[0], entry[1], entry[2]
            files = entry[3] if len(entry) > 3 else []
            steps = entry[4] if len(entry) > 4 else []
            self._write(who, text, extra, files, steps)

    def _write(self, who: str, text: str, extra: str = "", files: list | None = None,
               steps: list | None = None) -> None:
        self.log.configure(state="normal")
        if who in ("you", "you_phone"):
            self.log.insert("end", "You" + (f" (on {extra})" if who == "you_phone" else "") + "\n", "you_name")
            self.log.insert("end", text + "\n", "msg")
        elif who == "pilot":
            self.log.insert("end", f"{APP_NAME}\n", "pilot_name")
            if steps:
                steps_card = self._build_steps_card(steps)
                self.log.window_create("end", window=steps_card, padx=2, pady=4)
                self.log.insert("end", "\n")
            self.log.insert("end", text + "\n", "msg")
            if extra:
                self.log.insert("end", extra + "\n", "meta")
        else:
            self.log.insert("end", text + "\n", "problem")
        for shared in files or []:
            card = self._build_file_card(shared)
            self.log.window_create("end", window=card, padx=2, pady=4)
            self.log.insert("end", "\n")
        self.log.configure(state="disabled")
        if files or steps:
            self.log.update_idletasks()
            self.log.see("end")
            self.log.after(80, lambda: self.log.see("end"))
        else:
            self.log.see("end")

    def _build_steps_card(self, steps: list):
        """Collapsible 'Thought & steps' accordion shown above a Pilot reply."""
        t = self.app.theme
        outer = tk.Frame(self.log, bg=t["card"], highlightthickness=1, highlightbackground=t["border"])
        inner = tk.Frame(outer, bg=t["card"])
        inner.pack(fill="both", expand=True, padx=12, pady=7)
        total_sec = sum(float(s.get("seconds", 0.0) or 0.0) for s in steps)
        dur = f" · {total_sec:.1f}s" if total_sec > 0 else ""
        label_base = f"Thought & steps ({len(steps)} step{'s' if len(steps) != 1 else ''}{dur})"
        state = {"open": False}
        body = tk.Frame(inner, bg=t["card"])

        header = tk.Label(
            inner, text=f"▸  {label_base}", bg=t["card"], fg=t["muted"], font=FONT_BOLD,
            anchor="w", cursor="hand2",
        )
        header.pack(fill="x")

        def toggle(_event=None) -> None:
            state["open"] = not state["open"]
            header.configure(text=f"{'▾' if state['open'] else '▸'}  {label_base}")
            if state["open"]:
                body.pack(fill="x", pady=(6, 2))
            else:
                body.pack_forget()
            self.log.update_idletasks()

        header.bind("<Button-1>", toggle)
        for s in steps:
            row = tk.Frame(body, bg=t["card"])
            row.pack(fill="x", pady=2)
            st = s.get("status", "done")
            icon, color = {"running": ("●", "warn"), "error": ("✕", "bad")}.get(st, ("✓", "ok"))
            sec = f"  ({float(s.get('seconds', 0)):.1f}s)" if s.get("seconds") else ""
            tk.Label(row, text=icon, bg=t["card"], fg=t[color], font=FONT_BOLD).pack(side="left", anchor="n", padx=(0, 6))
            col = tk.Frame(row, bg=t["card"])
            col.pack(side="left", fill="x", expand=True)
            t.label(col, f"{s.get('label', '')}{sec}", bg="card", font=FONT_SMALL, wraplength=520).pack(fill="x")
            if s.get("detail"):
                t.label(col, str(s["detail"]), bg="card", fg="muted", font=FONT_SMALL, wraplength=520).pack(fill="x")
        return outer

    def update_live_step(self, step: dict) -> None:
        """Render live thinking/working steps one by one right above the message box while Pilot works."""
        if not self.busy:
            return
        for idx, existing in enumerate(self._live_steps):
            if existing.get("id") == step.get("id"):
                self._live_steps[idx] = dict(step)
                break
        else:
            self._live_steps.append(dict(step))
        self.set_status("thinking", step.get("label") or "Thinking...")
        self._draw_live_steps()

    def _clear_live_steps(self) -> None:
        self._live_steps.clear()
        for w in self.live_panel.winfo_children():
            w.destroy()
        self.live_panel.pack_forget()

    def _draw_live_steps(self) -> None:
        for w in self.live_panel.winfo_children():
            w.destroy()
        if not self._live_steps:
            self.live_panel.pack_forget()
            return
        t = self.app.theme
        outer = tk.Frame(self.live_panel, bg=t["card"], highlightthickness=1, highlightbackground=t["border"])
        outer.pack(fill="x")
        inner = tk.Frame(outer, bg=t["card"])
        inner.pack(fill="x", padx=12, pady=8)
        t.label(inner, f"Thinking & steps ({len(self._live_steps)})", bg="card", fg="muted", font=FONT_BOLD).pack(fill="x", pady=(0, 4))
        for s in self._live_steps[-6:]:
            row = tk.Frame(inner, bg=t["card"])
            row.pack(fill="x", pady=1)
            st = s.get("status", "running")
            icon, color = {"running": ("●", "warn"), "error": ("✕", "bad")}.get(st, ("✓", "ok"))
            sec = f"  ({float(s.get('seconds', 0)):.1f}s)" if s.get("seconds") else ""
            tk.Label(row, text=icon, bg=t["card"], fg=t[color], font=FONT_BOLD).pack(side="left", anchor="n", padx=(0, 6))
            t.label(row, f"{s.get('label', '')}{sec}", bg="card", font=FONT_SMALL, wraplength=560).pack(side="left", fill="x", expand=True)
        self.live_panel.pack(side="bottom", fill="x", padx=28, pady=(0, 4), before=self.log)

    def _build_file_card(self, shared):
        t = self.app.theme
        outer, card = t.card(self.log)
        t.label(card, f"{shared.name}  ({file_size_label(shared.size)})", bg="card", font=FONT_BOLD).pack(fill="x")
        if shared.note:
            t.label(card, shared.note, bg="card", fg="muted", font=FONT_SMALL, wraplength=540).pack(fill="x", pady=(2, 0))
        row = t.frame(card, bg="card")
        row.pack(fill="x", pady=(8, 0))
        t.button(row, "Open", lambda p=shared.path: self._open_file(p), bg="card").pack(side="left")
        t.button(row, "Save as...", lambda s=shared: self._save_file_as(s), kind="secondary", bg="card").pack(side="left", padx=(8, 0))
        t.button(row, "Show in folder", lambda p=shared.path: self._reveal_file(p), kind="secondary", bg="card").pack(side="left", padx=(8, 0))
        return outer

    def _open_file(self, path: Path) -> None:
        if not path.is_file():
            messagebox.showerror(APP_NAME, f"{path.name} is no longer in the workspace.", parent=self.frame)
            return
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import webbrowser
            webbrowser.open(path.as_uri())

    def _save_file_as(self, shared) -> None:
        if not shared.path.is_file():
            messagebox.showerror(APP_NAME, f"{shared.name} is no longer in the workspace.", parent=self.frame)
            return
        dest = filedialog.asksaveasfilename(title="Save file", initialfile=shared.name, parent=self.frame)
        if not dest:
            return
        try:
            shutil.copy2(shared.path, dest)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save the file: {exc}", parent=self.frame)

    def _reveal_file(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror(APP_NAME, f"{path.name} is no longer in the workspace.", parent=self.frame)
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            import webbrowser
            webbrowser.open(path.parent.as_uri())

    def add(self, who: str, text: str, extra: str = "", files: list | None = None,
            steps: list | None = None) -> None:
        entry = (who, text, extra, list(files or []), list(steps or [])) if (files or steps) else (who, text, extra)
        self.app.transcript.append(entry)
        self._write(who, text, extra, files, steps)

    # --- attachments ----------------------------------------------------------------------------
    def pick_files(self) -> None:
        if self.busy:
            return
        chosen = filedialog.askopenfilenames(title="Attach any file", parent=self.frame)
        if chosen:
            self.add_attachments(list(chosen))

    def add_attachments(self, paths: list) -> None:
        for p in paths:
            path = Path(p)
            if path not in self.pending_files:
                self.pending_files.append(path)
        self._draw_attachments()

    def remove_attachment(self, path: Path) -> None:
        self.pending_files = [p for p in self.pending_files if p != path]
        self._draw_attachments()

    def clear_attachments(self) -> None:
        self.pending_files.clear()
        self._draw_attachments()

    def _draw_attachments(self) -> None:
        for w in self.attach_bar.winfo_children():
            w.destroy()
        if not self.pending_files:
            self.attach_bar.pack_forget()
            return
        t = self.app.theme
        for p in self.pending_files:
            size_str = f" ({file_size_label(p.stat().st_size)})" if p.is_file() else ""
            chip = tk.Frame(self.attach_bar, bg=t["card"], highlightthickness=1, highlightbackground=t["border"])
            chip.pack(side="left", padx=(0, 8), pady=2)
            t.label(chip, f"{p.name}{size_str}", bg="card", font=FONT_SMALL).pack(side="left", padx=(10, 4), pady=4)
            tk.Button(
                chip, text="×", command=lambda target=p: self.remove_attachment(target),
                relief="flat", bd=0, cursor="hand2", font=FONT_BOLD, padx=6, pady=1,
                bg=t["card"], fg=t["muted"], activebackground=t["border"], activeforeground=t["text"],
            ).pack(side="left", padx=(0, 4))
        if len(self.pending_files) > 1:
            t.button(self.attach_bar, "Clear all", self.clear_attachments, kind="secondary").pack(side="right", padx=(8, 0))
        self.attach_bar.pack(side="bottom", fill="x", padx=28, pady=(0, 2), before=self.log)

    # --- sending --------------------------------------------------------------------------------
    def _auto_resize_input(self, _event=None) -> None:
        text = self.input.get("1.0", "end-1c")
        lines = max(1, min(4, text.count("\n") + 1 + len(text) // 75))
        if int(self.input.cget("height")) != lines:
            self.input.configure(height=lines)

    def _on_enter(self, event):
        if event.state & 0x0001:  # Shift held: new line
            return None
        self.send()
        return "break"

    def prefill(self, text: str) -> None:
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self._auto_resize_input()
        self.input.focus_set()

    def send(self, text: str | None = None, allow_over_cap: bool = False, attachments: list | None = None) -> None:
        if self.busy:
            return
        if text is None:
            text = self.input.get("1.0", "end").strip()
            chosen = list(self.pending_files)
            if not text and not chosen:
                return
            saved = []
            if chosen:
                saved, err = self.app.controller.attach_files(chosen)
                if err:
                    self.add("problem", err)
                    return
            self.input.delete("1.0", "end")
            self.input.configure(height=1)
            self.clear_attachments()
            attachments = saved or None
            shown = text or f"[Attached: {', '.join(p.name for p in saved)}]"
            if saved and text:
                shown = f"{text}\n[Attached: {', '.join(p.name for p in saved)}]"
            self.add("you", shown)
        self._set_busy(True)
        self.app.run_in_background(lambda: self.app.controller.send(text, allow_over_cap, attachments=attachments),
                                   lambda reply: self._done(text, reply, attachments))

    def _done(self, text, reply, attachments=None) -> None:
        self._clear_live_steps()
        self._set_busy(False)
        if reply.kind == "ok":
            self.add("pilot", reply.text, reply.detail, files=reply.files, steps=reply.steps)
            self.set_status("ready")
        elif reply.kind == "cap":
            self.set_status("problem", "A daily limit was reached.")
            if messagebox.askyesno(APP_NAME, f"{reply.text}\n\nGo past the limit for the rest of this session?",
                                   parent=self.frame):
                self.send(text, allow_over_cap=True, attachments=attachments)
            else:
                self.add("problem", reply.text + " Raise the daily limit in Settings, or try again tomorrow.")
        else:
            self.add("problem", reply.text)
            self.set_status("problem")

    def toggle_voice(self) -> None:
        if self.busy:
            return
        t = self.app.theme
        if not self.recorder.recording:
            try:
                self.recorder.start()
            except VoiceError as exc:
                self.add("problem", str(exc))
                return
            self.mic_btn.configure(text="Stop", bg=t["warn"], fg="#0f1115",
                                   activebackground=t["warn"], activeforeground="#0f1115")
            self.set_status("thinking", "Recording voice note... click Stop when done.")
        else:
            try:
                wav = self.recorder.stop(self.app.controller.workspace.files / "inbox")
            except VoiceError as exc:
                self.mic_btn.configure(text="Mic", bg=t["nav_active"], fg=t["text"],
                                       activebackground=t["border"], activeforeground=t["text"])
                self.set_status("ready")
                self.add("problem", str(exc))
                return
            self.mic_btn.configure(text="Mic", bg=t["nav_active"], fg=t["text"],
                                   activebackground=t["border"], activeforeground=t["text"])
            self.set_status("ready", "Voice note attached. Press Send.")
            self.add_attachments([wav])

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.send_btn.configure(state=state)
        self.attach_btn.configure(state=state)
        self.mic_btn.configure(state=state)
        if busy:
            self._clear_live_steps()
            self.set_status("thinking")
        else:
            self.input.focus_set()

    # --- approvals and activity --------------------------------------------------------------------
    def show_activity(self, text: str) -> None:
        if self.busy:
            self.set_status("thinking", text)

    def show_approval(self, request, answer) -> None:
        """A card in the chat with the exact command, why, what makes it risky, and Approve / Deny."""
        t = self.app.theme
        self.set_status("thinking", "Waiting for your OK...")
        outer, card = t.card(self.log)
        outer.configure(highlightbackground=t["warn"], highlightthickness=2)
        conn = getattr(request, "connector", "") or "Terminal"
        title = "Pilot wants to send an email (Gmail)" if conn == "Gmail" else "Pilot wants to run this command"
        head = t.frame(card, bg="card")
        head.pack(fill="x")
        t.label(head, title, bg="card", font=FONT_BOLD).pack(side="left")
        t.label(head, f"Via: {conn}", bg="card", fg="muted", font=FONT_SMALL).pack(side="right")
        cmd = tk.Label(card, text=request.action, bg=t["field"], fg=t["text"], font=("Consolas", 10),
                       anchor="w", justify="left", wraplength=560, padx=10, pady=6)
        cmd.pack(fill="x", pady=(6, 6))
        t.label(card, f"Why: {request.reason}", bg="card", wraplength=560).pack(fill="x")
        if request.risks:
            t.label(card, "Needs your OK because: " + "; ".join(request.risks) + ".", bg="card", fg="muted",
                    font=FONT_SMALL, wraplength=560).pack(fill="x", pady=(2, 0))
        if getattr(request, "flag", ""):
            t.label(card, request.flag, bg="card", fg="warn", font=FONT_BOLD, wraplength=560).pack(fill="x", pady=(6, 0))
        row = t.frame(card, bg="card")
        row.pack(fill="x", pady=(10, 0))

        def mark_done(choice: str) -> None:
            if not row.winfo_exists():
                return
            for w in row.winfo_children():
                w.destroy()
            words = {"once": "Approved.", "always": f"Approved. Always allowed from now on: {request.rule}",
                     "deny": "Denied."}.get(choice, "Denied.")
            t.label(row, words, bg="card", fg="ok" if choice != "deny" else "bad").pack(side="left")

        def choose(choice: str) -> None:
            mark_done(choice)
            answer(choice)

        if getattr(request, "item_id", ""):
            self.app.controller.approvals.add_listener(
                request.item_id,
                lambda choice: self.app._results.put((mark_done, choice)),
            )

        t.button(row, "Approve", lambda: choose("once"), bg="card").pack(side="left")
        t.button(row, "Deny", lambda: choose("deny"), kind="secondary", bg="card").pack(side="left", padx=(8, 0))
        if request.rule:
            t.button(row, f"Always allow \"{request.rule}\"", lambda: choose("always"), kind="secondary",
                     bg="card").pack(side="left", padx=(8, 0))
        self.log.configure(state="normal")
        self.log.insert("end", "\n")
        self.log.window_create("end", window=outer, padx=2, pady=6)
        self.log.insert("end", "\n")
        self.log.configure(state="disabled")
        # The card only knows its height once drawn: scroll after that, so the buttons are never off screen.
        self.log.update_idletasks()
        self.log.see("end")
        self.log.after(100, lambda: self.log.see("end"))

    def new_chat(self) -> None:
        if self.busy:
            return
        self.app.clear_chat()
