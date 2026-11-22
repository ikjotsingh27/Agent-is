"""The Memory screen: view, create, edit, and delete Pilot's plain Markdown memory notes."""
from __future__ import annotations

import os
import sys
import tkinter as tk

from .theme import FONT, FONT_BOLD, FONT_SMALL, FONT_TITLE, scroll_area


class MemoryView:
    def __init__(self, app, parent):
        self.app, t = app, app.theme
        self.frame = t.frame(parent)
        scroller, page = scroll_area(t, self.frame)
        scroller.pack(fill="both", expand=True)
        self.page = page

        head = t.frame(page)
        head.pack(fill="x", padx=28, pady=(22, 4))
        t.label(head, "Memory", font=FONT_TITLE).pack(side="left")
        t.button(head, "Open folder", self._open_folder, kind="secondary").pack(side="right")
        t.button(head, "New note", self.clear_editor, kind="secondary").pack(side="right", padx=(0, 8))

        t.label(
            page,
            "Plain Markdown notes stored in data/memory/. Pilot keeps these across restarts, and you can read, "
            "edit, or delete any note here.",
            fg="muted",
        ).pack(fill="x", padx=28, pady=(0, 12))

        # --- editor card ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 14))
        t.label(card, "Add or edit a note", bg="card", font=FONT_BOLD).pack(fill="x")
        title_row = t.frame(card, bg="card")
        title_row.pack(fill="x", pady=(8, 6))
        t.label(title_row, "Title", bg="card").pack(side="left", padx=(0, 12))
        self.title_entry = t.entry(title_row, width=48)
        self.title_entry.pack(side="left", ipady=4, fill="x", expand=True)

        self.body_input = tk.Text(
            card, height=4, width=1, wrap="word", relief="flat", bd=1, font=FONT,
            bg=t["field"], fg=t["text"], insertbackground=t["text"],
            highlightthickness=1, highlightbackground=t["border"], highlightcolor=t["accent"],
        )
        self.body_input.pack(fill="x", pady=(0, 8))

        btn_row = t.frame(card, bg="card")
        btn_row.pack(fill="x")
        t.button(btn_row, "Save note", self.save_note).pack(side="left")
        self.status = t.label(btn_row, "", bg="card", fg="ok", font=FONT_SMALL)
        self.status.pack(side="left", padx=(12, 0))

        # --- notes list ---
        self.list_frame = t.frame(page)
        self.list_frame.pack(fill="x", padx=28, pady=(0, 16))
        self.refresh()

    def _open_folder(self) -> None:
        folder = self.app.controller.memory.dir
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            import webbrowser
            webbrowser.open(folder.as_uri())

    def clear_editor(self) -> None:
        self.title_entry.delete(0, "end")
        self.body_input.delete("1.0", "end")
        self.status.configure(text="")
        self.title_entry.focus_set()

    def load_into_editor(self, note) -> None:
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, note.title)
        self.body_input.delete("1.0", "end")
        self.body_input.insert("1.0", note.content)
        self.status.configure(text=f"Editing {note.title}", fg=self.app.theme["muted"])

    def save_note(self) -> None:
        title = self.title_entry.get().strip()
        content = self.body_input.get("1.0", "end").strip()
        if not title:
            self.status.configure(text="Give the note a title first.", fg=self.app.theme["bad"])
            return
        note = self.app.controller.save_memory_note(title, content)
        self.title_entry.delete(0, "end")
        self.body_input.delete("1.0", "end")
        self.status.configure(text=f"Saved '{note.title}' ({note.slug}.md).", fg=self.app.theme["ok"])
        self.refresh()

    def delete_note(self, slug: str) -> None:
        self.app.controller.delete_memory_note(slug)
        self.status.configure(text="Note deleted.", fg=self.app.theme["muted"])
        self.refresh()

    def refresh(self) -> None:
        t = self.app.theme
        for w in self.list_frame.winfo_children():
            w.destroy()
        notes = self.app.controller.memory_notes()
        if not notes:
            outer, card = t.card(self.list_frame)
            outer.pack(fill="x")
            t.label(
                card,
                "No memory notes yet. Ask Pilot to remember something in Chat, or write a note above.",
                bg="card", fg="muted",
            ).pack(fill="x")
            return
        for note in notes:
            outer, card = t.card(self.list_frame)
            outer.pack(fill="x", pady=(0, 10))
            top = t.frame(card, bg="card")
            top.pack(fill="x")
            t.label(top, note.title, bg="card", font=FONT_BOLD).pack(side="left")
            t.label(top, f"  ({note.slug}.md)", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
            t.button(top, "Delete", lambda s=note.slug: self.delete_note(s), kind="secondary").pack(side="right", padx=(8, 0))
            t.button(top, "Edit", lambda n=note: self.load_into_editor(n), kind="secondary").pack(side="right")
            body = note.content or "(empty note)"
            t.label(card, body, bg="card", wraplength=620, pady=4).pack(fill="x", pady=(6, 0))
