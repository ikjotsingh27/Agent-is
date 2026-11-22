"""The Activity screen: searchable read-only timeline of everything Pilot did, plus Today's plain summary."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox

from ..core.settings import APP_NAME
from .theme import FONT_BOLD, FONT_SMALL, FONT_TITLE, scroll_area


class ActivityView:
    def __init__(self, app, parent):
        self.app, t = app, app.theme
        self.frame = t.frame(parent)
        scroller, page = scroll_area(t, self.frame)
        scroller.pack(fill="both", expand=True)
        self.page = page

        head = t.frame(page)
        head.pack(fill="x", padx=28, pady=(22, 4))
        t.label(head, "Activity", font=FONT_TITLE).pack(side="left")
        t.button(head, "Export log", self.export_log, kind="secondary").pack(side="right")
        t.button(head, "Refresh", self.refresh, kind="secondary").pack(side="right", padx=(0, 8))

        t.label(
            page,
            "Read-only log of everything Pilot did, with run times and token counts. Long runs are highlighted.",
            fg="muted",
        ).pack(fill="x", padx=28, pady=(0, 12))

        # --- search bar ---
        search_row = t.frame(page)
        search_row.pack(fill="x", padx=28, pady=(0, 12))
        self.search_entry = t.entry(search_row, width=44)
        self.search_entry.pack(side="left", ipady=5, fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh())
        t.button(search_row, "Clear", self.clear_search, kind="secondary").pack(side="left", padx=(8, 0))

        # --- today's plain-language summary ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        self.summary_card = card

        # --- timeline ---
        self.list_frame = t.frame(page)
        self.list_frame.pack(fill="x", padx=28, pady=(0, 16))
        self.refresh()

    def clear_search(self) -> None:
        self.search_entry.delete(0, "end")
        self.refresh()

    def export_log(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Export activity log",
            initialfile="pilot-activity.txt",
            defaultextension=".txt",
            parent=self.frame,
        )
        if not dest:
            return
        try:
            self.app.controller.export_activity(dest, query=self.search_entry.get().strip())
            messagebox.showinfo(APP_NAME, f"Exported activity log to {dest}", parent=self.frame)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not export log: {exc}", parent=self.frame)

    def refresh(self) -> None:
        t, c = self.app.theme, self.app.controller
        query = self.search_entry.get().strip()

        for w in self.summary_card.winfo_children():
            w.destroy()
        t.label(self.summary_card, "Today's daily summary", bg="card", font=FONT_BOLD).pack(fill="x")
        summary_lines = c.daily_summary_lines()
        if not summary_lines:
            t.label(self.summary_card, "Nothing recorded today yet.", bg="card", fg="muted",
                    font=FONT_SMALL).pack(fill="x", pady=(4, 0))
        else:
            shown = summary_lines[-8:]
            t.label(self.summary_card, "\n".join(f"• {ln}" for ln in reversed(shown)),
                    bg="card", fg="muted", font=FONT_SMALL, wraplength=620).pack(fill="x", pady=(4, 0))

        for w in self.list_frame.winfo_children():
            w.destroy()
        entries = c.activity_entries(query=query, limit=120)
        if not entries:
            outer, card = t.card(self.list_frame)
            outer.pack(fill="x")
            msg = f"No activity matched '{query}'." if query else "No activity recorded yet."
            t.label(card, msg, bg="card", fg="muted").pack(fill="x")
            return
        for e in entries:
            outer, card = t.card(self.list_frame)
            if e.long_run:
                outer.configure(highlightbackground=t["warn"], highlightthickness=2)
            elif e.kind == "error" or e.kind == "pause":
                outer.configure(highlightbackground=t["bad"], highlightthickness=1)
            outer.pack(fill="x", pady=(0, 8))
            top = t.frame(card, bg="card")
            top.pack(fill="x")
            time_str = f"{e.start_time} to {e.end_time}" if e.start_time != e.end_time else e.start_time
            t.label(top, f"{time_str}  ·  {e.source}", bg="card", font=FONT_BOLD).pack(side="left")
            meta_bits = []
            if e.seconds > 0:
                meta_bits.append(f"{e.seconds:.1f}s")
            if e.tokens:
                meta_bits.append(f"{e.tokens:,} tokens")
            if e.quiet:
                meta_bits.append("quiet run")
            if e.long_run:
                meta_bits.append("ran long")
            if meta_bits:
                t.label(top, "  ·  ".join(meta_bits), bg="card",
                        fg="warn" if e.long_run else "muted", font=FONT_SMALL).pack(side="right")
            t.label(card, e.summary, bg="card", wraplength=620).pack(fill="x", pady=(4, 0))
