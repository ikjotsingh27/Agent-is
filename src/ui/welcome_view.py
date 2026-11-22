"""First-launch welcome: 1) pick a provider, 2) paste a key and test it, 3) optionally connect Telegram,
4) say hello.
"""
from __future__ import annotations

import webbrowser

from ..core.settings import APP_NAME
from .controller import KEY_PAGES
from .theme import FONT_BIG, FONT_BOLD, FONT_SMALL, paste_into

STEPS = {
    "gemini": [
        "Click 'Open the key page'. It opens Google AI Studio in your browser.",
        "Sign in with your Google account.",
        "Click 'Create API key', then copy the key.",
        "Come back here, click 'Paste', then 'Test key'.",
    ],
    "anthropic": [
        "Click 'Open the key page'. It opens the Anthropic Console in your browser.",
        "Sign in, and add a little credit under Billing (Anthropic is paid).",
        "Click 'Create Key', then copy the key.",
        "Come back here, click 'Paste', then 'Test key'.",
    ],
}
NOTES = {
    "gemini": "Free, no card needed. Google sets a daily limit on free use. "
              "On the free plan Google may use your chats to improve its products.",
    "anthropic": "Paid per use, billed by Anthropic. Pilot's daily limits keep the cost small.",
}


class WelcomeView:
    def __init__(self, app, parent):
        self.app = app
        self.provider = "gemini"
        self.model = ""
        self.frame = app.theme.frame(parent)
        self.holder = None
        self.step_choose()

    def _page(self, step: int, title: str, text: str):
        t = self.app.theme
        if self.holder is not None:
            self.holder.destroy()
        self.holder = t.frame(self.frame)
        self.holder.place(relx=0.5, rely=0.45, anchor="center", width=600)
        t.label(self.holder, f"Step {step} of 4", fg="muted", font=FONT_SMALL).pack(fill="x")
        t.label(self.holder, title, font=FONT_BIG).pack(fill="x", pady=(4, 6))
        t.label(self.holder, text, fg="muted", wraplength=580).pack(fill="x", pady=(0, 18))
        return self.holder

    # --- step 1 ---------------------------------------------------------------------------------
    def step_choose(self) -> None:
        t = self.app.theme
        page = self._page(1, f"Welcome to {APP_NAME}",
                          f"{APP_NAME} is your personal assistant. It runs on this computer and thinks using an "
                          "AI service. Pick the service it should use. You can change this later in Settings.")
        for name, title in (("gemini", "Google Gemini  (free, recommended)"), ("anthropic", "Anthropic Claude  (paid)")):
            outer, card = t.card(page)
            outer.pack(fill="x", pady=(0, 10))
            outer.configure(cursor="hand2")
            head = t.label(card, title, bg="card", font=FONT_BOLD)
            head.pack(fill="x")
            note = t.label(card, NOTES[name], bg="card", fg="muted", font=FONT_SMALL, wraplength=540)
            note.pack(fill="x", pady=(4, 0))
            for w in (outer, card, head, note):
                w.bind("<Button-1>", lambda e, n=name: self.step_key(n))
        t.label(
            page,
            "Privacy & trust: your API keys, chat history, and memory stay on this computer only. "
            "Pilot asks before risky actions and only connects to apps you switch on.",
            fg="muted", font=FONT_SMALL, wraplength=580,
        ).pack(fill="x", pady=(8, 0))

    # --- step 2 ---------------------------------------------------------------------------------
    def step_key(self, provider: str) -> None:
        t = self.app.theme
        self.provider = provider
        page = self._page(2, "Get your key",
                          "A key is like a password that lets Pilot use the AI service. "
                          "It is saved only on this computer.")
        for i, text in enumerate(STEPS[provider], 1):
            t.label(page, f"{i}.  {text}", wraplength=580).pack(fill="x", pady=2)
        t.button(page, "Open the key page", lambda: webbrowser.open(KEY_PAGES[provider]),
                 kind="secondary").pack(anchor="w", pady=(12, 14))
        row = t.frame(page)
        row.pack(fill="x")
        self.key = t.entry(row, show="•", width=44)
        self.key.pack(side="left", ipady=6)
        t.button(row, "Paste", lambda: paste_into(self.frame, self.key), kind="secondary").pack(side="left", padx=(8, 0))
        self.result = t.label(page, "", wraplength=580)
        self.result.pack(fill="x", pady=(10, 0))
        nav = t.frame(page)
        nav.pack(fill="x", pady=(16, 0))
        t.button(nav, "Back", self.step_choose, kind="secondary").pack(side="left")
        self.test_btn = t.button(nav, "Test key", self.test)
        self.test_btn.pack(side="right")
        self.key.focus_set()
        self.key.bind("<Return>", lambda e: self.test())

    def test(self) -> None:
        key = self.key.get()
        if not key.strip():
            self.result.configure(text="Paste your key first.", fg=self.app.theme["bad"])
            return
        self.result.configure(text="Testing...", fg=self.app.theme["muted"])
        self.test_btn.configure(state="disabled")
        c = self.app.controller
        self.app.run_in_background(lambda: c.check_key(self.provider, key), lambda check: self._tested(key, check))

    def _tested(self, key, check) -> None:
        self.test_btn.configure(state="normal")
        if not check.ok:
            self.result.configure(text=check.message, fg=self.app.theme["bad"])
            return
        problem = self.app.controller.save(self.provider, key, check.main, check.cheap)
        if problem:
            self.result.configure(text=problem, fg=self.app.theme["bad"])
            return
        self.model = check.main
        self.step_phone()

    # --- step 3 ---------------------------------------------------------------------------------
    def step_phone(self) -> None:
        t = self.app.theme
        page = self._page(3, "Chat from your phone?",
                          f"{APP_NAME} can connect to Telegram, Gmail, Google Calendar, or Discord — each one has "
                          "step-by-step setup and a test button. Pick one now or skip and set it up later from Connectors.")
        row = t.frame(page)
        row.pack(fill="x")
        t.button(row, "Set up Telegram", lambda: self.app.finish_welcome(open_connector="telegram")).pack(side="left")
        t.button(row, "Skip for now", lambda: self.step_done(self.model), kind="secondary").pack(side="left", padx=(8, 0))
        more = t.frame(page)
        more.pack(fill="x", pady=(12, 0))
        t.label(more, "Or connect:", fg="muted", font=FONT_SMALL).pack(side="left", padx=(0, 8))
        for cid, label in (("gmail", "Gmail"), ("calendar", "Google Calendar"), ("discord", "Discord")):
            t.button(more, label, lambda c=cid: self.app.finish_welcome(open_connector=c),
                     kind="secondary").pack(side="left", padx=(0, 6))

    # --- step 4 ---------------------------------------------------------------------------------
    def step_done(self, model: str) -> None:
        t = self.app.theme
        page = self._page(4, "You're all set",
                          f"Your key works and is saved on this computer. {APP_NAME} will use {model}. "
                          "Say hello to try it out.")
        t.button(page, "Start chatting", lambda: self.app.finish_welcome("Hello!")).pack(anchor="w")
