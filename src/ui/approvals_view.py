"""The Approvals screen: waiting risky actions with Approve/Deny buttons, second-check warnings, and history."""
from __future__ import annotations

import tkinter as tk

from .theme import FONT_BOLD, FONT_SMALL, FONT_TITLE, scroll_area

STATUS_WORDS = {
    "once": ("Approved", "ok"),
    "always": ("Always allowed", "ok"),
    "deny": ("Denied", "bad"),
}


class ApprovalsView:
    def __init__(self, app, parent):
        self.app, t = app, app.theme
        self.frame = t.frame(parent)
        scroller, page = scroll_area(t, self.frame)
        scroller.pack(fill="both", expand=True)
        self.page = page

        t.label(page, "Approvals", font=FONT_TITLE).pack(fill="x", padx=28, pady=(22, 4))
        t.label(
            page,
            "Risky actions wait here for your OK. Before an action is shown, a separate second check flags "
            "anything unusual in plain words.",
            fg="muted", wraplength=660,
        ).pack(fill="x", padx=28, pady=(0, 14))

        self.body = t.frame(page)
        self.body.pack(fill="x", padx=28, pady=(0, 16))
        self.refresh()

    def refresh(self) -> None:
        t, c = self.app.theme, self.app.controller
        for w in self.body.winfo_children():
            w.destroy()

        pending = c.pending_approvals()
        t.label(self.body, f"Waiting for your OK ({len(pending)})", font=FONT_BOLD).pack(fill="x", pady=(0, 8))
        if not pending:
            outer, card = t.card(self.body)
            outer.pack(fill="x", pady=(0, 16))
            t.label(card, "Nothing is waiting right now.", bg="card", fg="muted").pack(fill="x")
        for item in pending:
            outer, card = t.card(self.body)
            outer.configure(highlightbackground=t["warn"], highlightthickness=2)
            outer.pack(fill="x", pady=(0, 12))
            head = t.frame(card, bg="card")
            head.pack(fill="x")
            conn = getattr(item, "connector", "") or "Terminal"
            title = "Pilot wants to send an email" if conn == "Gmail" else "Pilot wants to run this command"
            t.label(head, f"{title}  ({conn})", bg="card", font=FONT_BOLD).pack(side="left")
            if item.created_at:
                t.label(head, item.created_at, bg="card", fg="muted", font=FONT_SMALL).pack(side="right")
            cmd = tk.Label(
                card, text=item.action, bg=t["field"], fg=t["text"], font=("Consolas", 10),
                anchor="w", justify="left", wraplength=580, padx=10, pady=6,
            )
            cmd.pack(fill="x", pady=(6, 6))
            t.label(card, f"Why: {item.reason}", bg="card", wraplength=580).pack(fill="x")
            if item.risks:
                t.label(
                    card, "Needs your OK because: " + "; ".join(item.risks) + ".",
                    bg="card", fg="muted", font=FONT_SMALL, wraplength=580,
                ).pack(fill="x", pady=(2, 0))
            if item.flag:
                t.label(card, item.flag, bg="card", fg="warn", font=FONT_BOLD, wraplength=580).pack(fill="x", pady=(6, 0))
            row = t.frame(card, bg="card")
            row.pack(fill="x", pady=(10, 0))
            t.button(row, "Approve", lambda iid=item.id: c.resolve_approval(iid, "once"), bg="card").pack(side="left")
            t.button(row, "Deny", lambda iid=item.id: c.resolve_approval(iid, "deny"), kind="secondary", bg="card").pack(side="left", padx=(8, 0))
            if item.rule:
                t.button(
                    row, f'Always allow "{item.rule}"',
                    lambda iid=item.id: c.resolve_approval(iid, "always"),
                    kind="secondary", bg="card",
                ).pack(side="left", padx=(8, 0))

        history = c.approval_history()
        t.label(self.body, "Recent decisions", font=FONT_BOLD).pack(fill="x", pady=(8, 8))
        if not history:
            outer, card = t.card(self.body)
            outer.pack(fill="x")
            t.label(card, "No decisions yet in this session.", bg="card", fg="muted").pack(fill="x")
            return
        for item in history[:15]:
            outer, card = t.card(self.body)
            outer.pack(fill="x", pady=(0, 8))
            top = t.frame(card, bg="card")
            top.pack(fill="x")
            word, color = STATUS_WORDS.get(item.status, (item.status, "muted"))
            conn = getattr(item, "connector", "") or "Terminal"
            t.label(top, f"{word} · {conn}", bg="card", fg=color, font=FONT_BOLD).pack(side="left")
            if item.created_at:
                t.label(top, f"  ({item.created_at})", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
            t.label(card, item.action, bg="card", font=("Consolas", 9), wraplength=580).pack(fill="x", pady=(4, 0))
            t.label(card, f"Why: {item.reason}", bg="card", fg="muted", font=FONT_SMALL, wraplength=580).pack(fill="x")
            if item.flag:
                t.label(card, item.flag, bg="card", fg="warn", font=FONT_SMALL, wraplength=580).pack(fill="x", pady=(2, 0))
