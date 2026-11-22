"""The Connectors screen: a card per connector, and the guided setup page drawn from its guide.json."""
from __future__ import annotations

import tkinter as tk
import webbrowser

from .theme import FONT_BOLD, FONT_SMALL, FONT_TITLE, paste_into, scroll_area

STATUS_COLOR = {"Connected": "ok", "Problem": "bad", "Off": "warn", "Not set up": "muted"}
HEALTH_COLOR = {"green": "ok", "yellow": "warn", "red": "bad", "none": "muted"}
HEALTH_LABEL = {"green": "Healthy", "yellow": "Off / Paused", "red": "Problem", "none": "Not set up"}
COMING = "Each connector is self-contained under src/connectors/ — connect only what you need."


class ConnectorsView:
    def __init__(self, app, parent):
        self.app = app
        self.frame = app.theme.frame(parent)
        self.body = None
        self.page = "grid"
        self.fields: dict = {}
        self._choice_vars: dict = {}
        self._health_status = ""
        self.show_grid()

    def _clear(self, scrollable: bool = False):
        if self.body is not None:
            self.body.destroy()
        if scrollable:
            scroller, inner = scroll_area(self.app.theme, self.frame)
            scroller.pack(fill="both", expand=True)
            self.body = scroller
            page = self.app.theme.frame(inner)
            page.pack(fill="both", expand=True, padx=28, pady=(22, 16))
            return page
        self.body = self.app.theme.frame(self.frame)
        self.body.pack(fill="both", expand=True, padx=28, pady=(22, 12))
        return self.body

    # --- the grid of cards + Connector Health summary -------------------------------------------
    def show_grid(self) -> None:
        t, c = self.app.theme, self.app.controller
        self.page = "grid"
        body = self._clear(scrollable=True)
        head = t.frame(body)
        head.pack(fill="x")
        t.label(head, "Connectors", font=FONT_TITLE).pack(side="left")
        self.health_btn = t.button(head, "Check health", self._check_health, kind="secondary")
        self.health_btn.pack(side="right")
        t.label(body, "Connect apps so Pilot can do more. Each one has step-by-step setup and a test.",
                fg="muted").pack(fill="x", pady=(4, 12))

        cards = c.connector_cards()
        # --- Connector Health overview bar (Green / Yellow / Red) ---
        h_outer, h_card = t.card(body)
        h_outer.pack(fill="x", pady=(0, 14))
        t.label(h_card, "Connector health", bg="card", font=FONT_BOLD).pack(fill="x")
        pills = t.frame(h_card, bg="card")
        pills.pack(fill="x", pady=(6, 0))
        for card in cards:
            hl = card.get("health", "none")
            dot = "●" if hl != "none" else "○"
            t.label(pills, f"{dot} {card['name']}: {HEALTH_LABEL[hl]}", bg="card",
                    fg=HEALTH_COLOR[hl], font=FONT_SMALL).pack(side="left", padx=(0, 16))
        if self._health_status:
            t.label(h_card, self._health_status, bg="card", fg="muted", font=FONT_SMALL,
                    wraplength=640).pack(fill="x", pady=(6, 0))

        grid = t.frame(body)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="cards")
        for i, card in enumerate(cards):
            outer, inner = t.card(grid)
            outer.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0, 12) if i % 2 == 0 else 0, pady=(0, 12))
            t.label(inner, card["name"], bg="card", font=FONT_BOLD).pack(fill="x")
            t.label(inner, card["summary"], bg="card", fg="muted", font=FONT_SMALL, wraplength=310).pack(fill="x", pady=(2, 8))
            t.label(inner, f"●  {card['status']}", bg="card", fg=STATUS_COLOR[card["status"]],
                    font=FONT_SMALL).pack(fill="x")
            if card["problem"]:
                t.label(inner, card["problem"], bg="card", fg="bad", font=FONT_SMALL, wraplength=320).pack(fill="x", pady=(2, 0))
            elif card.get("health_note") and card["connected"]:
                t.label(inner, card["health_note"], bg="card", fg="muted", font=FONT_SMALL, wraplength=320).pack(fill="x", pady=(2, 0))
            row = t.frame(inner, bg="card")
            row.pack(fill="x", pady=(12, 0))
            setup_text = "Set up again" if card["connected"] else "Set up"
            t.button(row, setup_text, lambda cid=card["id"]: self.show_guide(cid),
                     kind="secondary" if card["connected"] else "primary").pack(side="left")
            if card["connected"]:
                on = card["enabled"]
                t.button(row, "Turn off" if on else "Turn on",
                         lambda cid=card["id"], on=on: self._toggle(cid, not on), kind="secondary").pack(side="left", padx=(8, 0))
        t.label(body, COMING, fg="muted", font=FONT_SMALL).pack(fill="x", pady=(4, 0))

    def _check_health(self) -> None:
        self._health_status = "Checking connected services..."
        self.show_grid()
        self.health_btn.configure(state="disabled")
        self.app.run_in_background(self.app.controller.check_connectors_health, self._health_done)

    def _health_done(self, results) -> None:
        bad = [r for r in results if r["health"] == "red"]
        good = [r for r in results if r["health"] == "green"]
        if bad:
            self._health_status = "Problems found: " + "; ".join(f"{r['name']} — {r['message']}" for r in bad)
        elif good:
            self._health_status = f"All {len(good)} active connector(s) passed their health check."
        else:
            self._health_status = "No active connectors to test yet. Set up one below."
        if self.page == "grid":
            self.show_grid()

    def _toggle(self, cid: str, enabled: bool) -> None:
        self.app.controller.set_connector_enabled(cid, enabled)
        self.show_grid()

    def refresh(self) -> None:
        if self.page == "grid":
            self.show_grid()

    # --- the guided setup page ------------------------------------------------------------------
    def show_guide(self, cid: str) -> None:
        t, c = self.app.theme, self.app.controller
        self.page, self.cid = "guide", cid
        guide = c.connectors[cid].guide
        body = self._clear(scrollable=True)
        t.button(body, "←  All connectors", self.show_grid, kind="secondary").pack(anchor="w")
        t.label(body, f"Set up {guide['name']}", font=FONT_TITLE).pack(fill="x", pady=(14, 2))
        t.label(body, guide.get("summary", ""), fg="muted").pack(fill="x", pady=(0, 12))

        for n, step in enumerate(guide["steps"], 1):
            row = t.frame(body)
            row.pack(fill="x", pady=4)
            badge = tk.Label(row, text=str(n), width=2, font=FONT_BOLD, bg=t["accent"], fg=t["on_accent"])
            badge.pack(side="left", anchor="n", padx=(0, 12), ipady=1)
            text = t.frame(row)
            text.pack(side="left", fill="x", expand=True)
            t.label(text, step["title"], font=FONT_BOLD).pack(fill="x")
            t.label(text, step["text"], fg="muted", wraplength=540).pack(fill="x")
            if step.get("link"):
                t.button(row, step.get("link_label", "Open link"), lambda url=step["link"]: webbrowser.open(url),
                         kind="secondary").pack(side="right", anchor="n")

        self.fields = {}
        self._choice_vars = {}
        saved_data = c.connector_data(cid)
        for field in guide.get("fields", []):
            row = t.frame(body)
            row.pack(fill="x", pady=(12, 0))
            t.label(row, field["label"]).pack(side="left", padx=(0, 12))
            if field.get("choices"):
                current = str(saved_data.get(field["key"]) or field.get("default") or field["choices"][0]["value"])
                var = tk.StringVar(value=current)
                self._choice_vars[field["key"]] = var
                btns: list = []

                def _select(val: str, v=var, btn_list=btns):
                    v.set(val)
                    for b, bval in btn_list:
                        k = "primary" if bval == val else "secondary"
                        bg = t["accent"] if k == "primary" else t["nav_active"]
                        fg = t["on_accent"] if k == "primary" else t["text"]
                        b.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg)

                for opt in field["choices"]:
                    b = t.button(row, opt["label"], lambda v=opt["value"]: _select(v),
                                 kind="primary" if opt["value"] == current else "secondary")
                    b.pack(side="left", padx=(0, 8))
                    btns.append((b, opt["value"]))
            else:
                entry = t.entry(row, show="•" if field.get("secret") else None, width=46)
                prefill = saved_data.get(field["key"]) if not field.get("secret") else None
                if prefill is None and not field.get("secret") and field.get("default"):
                    prefill = field["default"]
                if prefill:
                    entry.insert(0, str(prefill))
                entry.pack(side="left", ipady=5)
                t.button(row, "Paste", lambda e=entry: paste_into(self.frame, e), kind="secondary").pack(side="left", padx=(8, 0))
                self.fields[field["key"]] = entry
        if saved_data.get("connected"):
            t.label(body, "Already connected. Leave secret boxes empty to test your saved settings again.",
                    fg="muted", font=FONT_SMALL).pack(fill="x", pady=(4, 0))

        actions = t.frame(body)
        actions.pack(fill="x", pady=(14, 0))
        self.test_btn = t.button(actions, guide.get("test_button", "Test connection"), self.test)
        self.test_btn.pack(side="left")
        self.result = t.label(body, "", wraplength=680)
        self.result.pack(fill="x", pady=(10, 0))
        if guide.get("privacy"):
            t.label(body, guide["privacy"], fg="muted", font=FONT_SMALL, wraplength=680).pack(fill="x", pady=(12, 0))

    def test(self) -> None:
        values = {key: entry.get() for key, entry in self.fields.items()}
        for key, var in self._choice_vars.items():
            values[key] = var.get()
        self.result.configure(text="Testing...", fg=self.app.theme["muted"])
        self.test_btn.configure(state="disabled")
        cid = self.cid
        self.app.run_in_background(lambda: self.app.controller.check_connector(cid, values), self._tested)

    def _tested(self, result) -> None:
        if self.page != "guide":
            return
        self.test_btn.configure(state="normal")
        if result.ok:
            for entry in self.fields.values():
                entry.delete(0, "end")
            extra = self.app.controller.connectors[self.cid].guide.get("connected_text", "")
            self.result.configure(text=f"{result.message} {extra}".strip(), fg=self.app.theme["ok"])
            self.app.refresh_sidebar()
        else:
            color = "warn" if result.nearly else "bad"
            self.result.configure(text=f"{result.message}\n{result.fix}".strip(), fg=self.app.theme[color])
