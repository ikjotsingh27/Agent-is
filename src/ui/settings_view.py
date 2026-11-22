"""The Settings screen: provider, API key with a Test button, models, the agent's workspace and tools,
permissions, password-protected web access, always-on schedules & Pause All, theme, and live usage dashboard.

Saving switches to the new choices at once. No restart, and the chat is kept.
"""
from __future__ import annotations

import os
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from ..core.settings import APP_NAME
from .controller import KEY_PAGES
from .theme import FONT_BOLD, FONT_SMALL, FONT_TITLE, paste_into, scroll_area

GROUP_LABELS = {"files": "Files", "memory": "Memory", "terminal": "Terminal", "browser": "Browser"}
NEVER_TEXT = ("Never allowed, whatever you choose here: reading saved passwords or Pilot's own keys, turning off "
              "Windows security, moving money and trading.")


class SettingsView:
    def __init__(self, app, parent):
        self.app, t = app, app.theme
        c = app.controller
        self.frame = t.frame(parent)
        scroller, page = scroll_area(t, self.frame)
        scroller.pack(fill="both", expand=True)
        self.page = page
        t.label(page, "Settings", font=FONT_TITLE).pack(fill="x", padx=28, pady=(22, 12))

        # --- model service ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        t.label(card, "Model service", bg="card", font=FONT_BOLD).grid(row=0, column=0, columnspan=4, sticky="w")
        self.labels = {c.provider_label(p): p for p in c.providers}
        self.provider = ttk.Combobox(card, state="readonly", width=38, values=list(self.labels))
        self.provider.set(c.provider_label(c.settings.provider))
        self.provider.bind("<<ComboboxSelected>>", lambda e: self._provider_changed())
        self._row(card, 1, "Provider", self.provider)

        key_row = t.frame(card, bg="card")
        self.key = t.entry(key_row, show="•", width=40)
        self.key.pack(side="left", ipady=5)
        t.button(key_row, "Paste", lambda: paste_into(self.frame, self.key), kind="secondary").pack(side="left", padx=(8, 0))
        self.key_page_btn = t.button(key_row, "Get a key", self._open_key_page, kind="secondary")
        self.key_page_btn.pack(side="left", padx=(8, 0))
        self._row(card, 2, "API key", key_row)
        self.key_note = t.label(card, "", bg="card", fg="muted", font=FONT_SMALL)
        self.key_note.grid(row=3, column=1, columnspan=3, sticky="w", pady=(2, 8))

        self.main_model = ttk.Combobox(card, width=38)
        self.cheap_model = ttk.Combobox(card, width=38)
        self._row(card, 4, "Main model", self.main_model)
        self._row(card, 5, "Cheap model", self.cheap_model)
        t.label(card, "The main model answers your messages. The cheap one is for small jobs later on.",
                bg="card", fg="muted", font=FONT_SMALL).grid(row=6, column=1, columnspan=3, sticky="w", pady=(2, 10))

        actions = t.frame(card, bg="card")
        actions.grid(row=7, column=1, columnspan=3, sticky="w")
        self.test_btn = t.button(actions, "Test connection", self.test, kind="secondary")
        self.test_btn.pack(side="left")
        self.save_btn = t.button(actions, "Save", self.save)
        self.save_btn.pack(side="left", padx=(8, 0))
        self.result = t.label(card, "", bg="card", wraplength=520)
        self.result.grid(row=8, column=1, columnspan=3, sticky="w", pady=(10, 0))

        # --- the agent's workspace and tools ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        head = t.frame(card, bg="card")
        head.pack(fill="x")
        t.label(head, "Agent workspace", bg="card", font=FONT_BOLD).pack(side="left")
        t.button(head, "Open folder", lambda: os.startfile(c.workspace.files), kind="secondary").pack(side="right")
        self.ws_label = t.label(card, "", bg="card", fg="muted", font=FONT_SMALL, wraplength=600)
        self.ws_label.pack(fill="x", pady=(4, 2))
        ws = c.workspace
        t.label(card, f"Programs the agent runs can use up to {ws.max_ram // 1024 ** 3} GB of memory and "
                      f"{ws.command_seconds} seconds each. You can adjust the workspace disk limit below under Usage.",
                bg="card", fg="muted", font=FONT_SMALL, wraplength=600).pack(fill="x")
        self.groups_row = t.frame(card, bg="card")
        self.groups_row.pack(fill="x", pady=(10, 0))
        self.group_note = t.label(card, "", bg="card", fg="bad", font=FONT_SMALL)
        self.group_note.pack(fill="x")
        self._draw_groups()
        self.venv_row = t.frame(card, bg="card")
        self.venv_row.pack(fill="x", pady=(8, 0))
        self.venv_note = t.label(card, "", bg="card", fg="muted", font=FONT_SMALL, wraplength=600)
        self.venv_note.pack(fill="x")
        self._draw_venv()

        # --- permissions ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        self.perm_card = card
        self._draw_permissions()

        # --- password-protected web access ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        self.web_card = card
        self._web_host = c.web_config()["host"]
        self._draw_web_access()

        # --- always-on, schedules & pause all ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        self.always_card = card
        self._always_msg = ""
        self._draw_always_on()

        # --- look ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 12))
        t.label(card, "Look", bg="card", font=FONT_BOLD).pack(side="left")
        for name in ("light", "dark"):
            kind = "primary" if app.theme.name == name else "secondary"
            t.button(card, name.capitalize(), lambda n=name: app.change_theme(n), kind=kind).pack(side="right", padx=(8, 0))

        # --- usage & live resources dashboard ---
        outer, card = t.card(page)
        outer.pack(fill="x", padx=28, pady=(0, 18))
        self.usage_card = card
        self._usage_window = "hour"
        self.usage = t.label(card, "", bg="card", fg="muted")
        self._draw_usage()

        self._provider_changed()

    def _row(self, card, row, label, widget) -> None:
        self.app.theme.label(card, label, bg="card").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=4)
        widget.grid(row=row, column=1, columnspan=3, sticky="w", pady=4)

    # --- actions --------------------------------------------------------------------------------
    @property
    def chosen(self) -> str:
        return self.labels.get(self.provider.get(), self.app.controller.settings.provider)

    def _provider_changed(self) -> None:
        c, p = self.app.controller, self.chosen
        models = c.default_models(p)
        self.main_model.set(models["main"])
        self.cheap_model.set(models["cheap"])
        self.main_model.configure(values=[models["main"]])
        self.cheap_model.configure(values=[models["cheap"]])
        self.key.delete(0, "end")
        if p == "mock":
            note = "The mock provider gives fake replies and needs no key."
        elif c.has_key(p):
            note = "A key is saved. Leave this empty to keep it, or paste a new one."
        else:
            note = c.key_help(p)
        self.key_note.configure(text=note)
        self.key_page_btn.configure(state="normal" if p in KEY_PAGES else "disabled")
        self.result.configure(text="")

    def _open_key_page(self) -> None:
        if self.chosen in KEY_PAGES:
            webbrowser.open(KEY_PAGES[self.chosen])

    def _say(self, text: str, color: str = "text") -> None:
        self.result.configure(text=text, fg=self.app.theme[color])

    def test(self) -> None:
        self._say("Testing...", "muted")
        self.test_btn.configure(state="disabled")
        args = (self.chosen, self.key.get(), self.main_model.get().strip(), self.cheap_model.get().strip())
        self.app.run_in_background(lambda: self.app.controller.check_key(*args), self._tested)

    def _tested(self, check) -> None:
        self.test_btn.configure(state="normal")
        if check.ok:
            self.main_model.configure(values=check.models)
            self.cheap_model.configure(values=check.models)
            self.main_model.set(check.main)
            self.cheap_model.set(check.cheap)
            self._say(check.message + " Press Save to use it.", "ok")
        else:
            self._say(check.message, "bad")

    def save(self) -> None:
        problem = self.app.controller.save(self.chosen, self.key.get(),
                                           self.main_model.get().strip(), self.cheap_model.get().strip())
        if problem:
            self._say(problem, "bad")
            return
        self._provider_changed()
        self._say("Saved. Pilot is using these settings now.", "ok")
        self.refresh()

    def refresh(self) -> None:
        self.ws_label.configure(text=self.app.controller.workspace_summary())
        self._draw_venv()
        self._draw_permissions()
        self._draw_web_access()
        self._draw_always_on()
        self._draw_usage()

    def _draw_venv(self) -> None:
        t, c = self.app.theme, self.app.controller
        for w in self.venv_row.winfo_children():
            w.destroy()
        has_v = c.has_workspace_venv()
        t.label(self.venv_row, "Isolated workspace Python (keeps pip installs inside the 5 GB cap)",
                bg="card", font=FONT_SMALL).pack(side="left")
        if has_v:
            t.button(self.venv_row, "Remove workspace Python", self._remove_venv,
                     kind="secondary").pack(side="right")
        else:
            self.venv_btn = t.button(self.venv_row, "Create workspace Python", self._create_venv,
                                     kind="secondary")
            self.venv_btn.pack(side="right")

    def _create_venv(self) -> None:
        self.venv_btn.configure(state="disabled")
        self.venv_note.configure(text="Creating isolated Python inside data/agent/venv (this takes ~10 seconds)...",
                                 fg=self.app.theme["muted"])
        self.app.run_in_background(self.app.controller.create_workspace_venv, self._venv_created)

    def _venv_created(self, err: str) -> None:
        if err:
            self.venv_note.configure(text=err, fg=self.app.theme["bad"])
        else:
            self.venv_note.configure(text="Isolated workspace Python is ready. Future pip installs stay inside data/agent.",
                                     fg=self.app.theme["ok"])
        self.ws_label.configure(text=self.app.controller.workspace_summary())
        self._draw_venv()

    def _remove_venv(self) -> None:
        self.app.controller.remove_workspace_venv()
        self.venv_note.configure(text="Removed isolated workspace Python.", fg=self.app.theme["muted"])
        self.ws_label.configure(text=self.app.controller.workspace_summary())
        self._draw_venv()

    # --- agent tools and permissions ------------------------------------------------------------
    def _draw_groups(self) -> None:
        t, c = self.app.theme, self.app.controller
        for w in self.groups_row.winfo_children():
            w.destroy()
        t.label(self.groups_row, "Tools", bg="card").pack(side="left", padx=(0, 12))
        for group, on in c.tool_groups().items():
            t.button(self.groups_row, f"{GROUP_LABELS[group]}: {'On' if on else 'Off'}",
                     lambda g=group, on=on: self._toggle_group(g, not on),
                     kind="primary" if on else "secondary").pack(side="left", padx=(0, 8))

    def _toggle_group(self, group: str, on: bool) -> None:
        problem = self.app.controller.set_tool_group(group, on)
        self.group_note.configure(text=problem)
        self._draw_groups()

    def _draw_permissions(self) -> None:
        t, c = self.app.theme, self.app.controller
        card = self.perm_card
        for w in card.winfo_children():
            w.destroy()
        t.label(card, "Permissions", bg="card", font=FONT_BOLD).pack(fill="x")
        t.label(card, "Pilot asks before risky actions. As you come to trust it, you can let it do some of them "
                      "on its own, and change your mind any time.", bg="card", fg="muted", font=FONT_SMALL,
                wraplength=600).pack(fill="x", pady=(2, 8))
        for kind, label, value in c.permission_rows():
            row = t.frame(card, bg="card")
            row.pack(fill="x", pady=2)
            t.label(row, label, bg="card").pack(side="left")
            for choice, text in (("allow", "Allowed"), ("ask", "Ask first")):
                t.button(row, text, lambda k=kind, ch=choice: self._set_permission(k, ch),
                         kind="primary" if value == choice else "secondary").pack(side="right", padx=(6, 0))
        t.label(card, NEVER_TEXT, bg="card", fg="muted", font=FONT_SMALL, wraplength=600).pack(fill="x", pady=(10, 6))
        t.label(card, "Always allowed commands", bg="card", font=FONT_BOLD).pack(fill="x", pady=(4, 2))
        rules = c.always_allow_rules()
        if not rules:
            t.label(card, "None yet. Press \"Always allow\" on an approval card to add one.", bg="card", fg="muted",
                    font=FONT_SMALL).pack(fill="x")
        for rule in rules:
            row = t.frame(card, bg="card")
            row.pack(fill="x", pady=2)
            t.label(row, rule, bg="card").pack(side="left")
            t.button(row, "Remove", lambda r=rule: self._remove_rule(r), kind="secondary").pack(side="right")

    def _set_permission(self, kind: str, value: str) -> None:
        self.app.controller.set_permission(kind, value)
        self._draw_permissions()

    def _remove_rule(self, rule: str) -> None:
        self.app.controller.remove_rule(rule)
        self._draw_permissions()

    # --- password-protected web access ----------------------------------------------------------
    def _draw_web_access(self) -> None:
        t, c = self.app.theme, self.app.controller
        card = self.web_card
        for w in card.winfo_children():
            w.destroy()
        cfg = c.web_config()
        self._web_host = cfg["host"]

        head = t.frame(card, bg="card")
        head.pack(fill="x")
        t.label(head, "Web access (password-protected)", bg="card", font=FONT_BOLD).pack(side="left")
        if cfg["running"] and cfg["urls"]:
            t.button(head, "Open web page", lambda u=cfg["urls"][0]: webbrowser.open(u),
                     kind="secondary").pack(side="right")

        status_text = ("Running at " + "  |  ".join(cfg["urls"])) if cfg["running"] else "Off. Set a password and turn it on to use Pilot from any browser."
        t.label(card, status_text, bg="card", fg="ok" if cfg["running"] else "muted",
                font=FONT_SMALL, wraplength=600).pack(fill="x", pady=(4, 8))

        pw_row = t.frame(card, bg="card")
        pw_row.pack(fill="x", pady=3)
        t.label(pw_row, "Password", bg="card").pack(side="left", padx=(0, 12))
        self.web_pw = t.entry(pw_row, show="•", width=28)
        self.web_pw.pack(side="left", ipady=4)
        pw_hint = "Saved (leave blank to keep)" if cfg["has_password"] else "Required (4+ characters)"
        t.label(pw_row, pw_hint, bg="card", fg="muted", font=FONT_SMALL).pack(side="left", padx=(10, 0))

        net_row = t.frame(card, bg="card")
        net_row.pack(fill="x", pady=6)
        t.label(net_row, "Reachable from", bg="card").pack(side="left", padx=(0, 10))
        t.button(net_row, "This computer only", lambda: self._set_web_host("127.0.0.1"),
                 kind="primary" if self._web_host == "127.0.0.1" else "secondary").pack(side="left", padx=(0, 6))
        t.button(net_row, "Same Wi-Fi network", lambda: self._set_web_host("0.0.0.0"),
                 kind="primary" if self._web_host == "0.0.0.0" else "secondary").pack(side="left", padx=(0, 14))
        t.label(net_row, "Port", bg="card").pack(side="left", padx=(0, 6))
        self.web_port = t.entry(net_row, width=7)
        self.web_port.insert(0, str(cfg["port"]))
        self.web_port.pack(side="left", ipady=4)

        act = t.frame(card, bg="card")
        act.pack(fill="x", pady=(8, 0))
        t.button(act, "Save & Turn on" if not cfg["running"] else "Save & Restart",
                 lambda: self._save_web(True)).pack(side="left")
        if cfg["running"]:
            t.button(act, "Turn off", lambda: self._save_web(False), kind="secondary").pack(side="left", padx=(8, 0))
            if cfg["tunnel_running"]:
                t.button(act, "Stop public link", self._stop_tunnel, kind="secondary").pack(side="left", padx=(8, 0))
            else:
                self.tunnel_btn = t.button(act, "Put online (free HTTPS link)", self._start_tunnel, kind="secondary")
                self.tunnel_btn.pack(side="left", padx=(8, 0))

        if cfg["tunnel_running"] and cfg["public_url"]:
            pub = t.frame(card, bg="card")
            pub.pack(fill="x", pady=(8, 0))
            t.label(pub, f"Public HTTPS link: {cfg['public_url']}", bg="card", fg="ok",
                    font=FONT_SMALL, wraplength=440).pack(side="left")
            t.button(pub, "Open public link", lambda u=cfg["public_url"]: webbrowser.open(u),
                     kind="secondary").pack(side="right", padx=(6, 0))
            t.button(pub, "Copy link", lambda u=cfg["public_url"]: self._copy_text(u),
                     kind="secondary").pack(side="right")

        self.web_msg = t.label(card, "", bg="card", fg="muted", font=FONT_SMALL, wraplength=600)
        self.web_msg.pack(fill="x", pady=(6, 0))
        t.label(card, "Tip: Click 'Put online (free HTTPS link)' to open Pilot from anywhere on the internet for $0 "
                      "via Cloudflare Tunnel (protected by your password). Your password is stored only as a salted hash.",
                bg="card", fg="muted", font=FONT_SMALL, wraplength=600).pack(fill="x", pady=(4, 0))

    def _copy_text(self, text: str) -> None:
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self.web_msg.configure(text="Copied link to clipboard.", fg=self.app.theme["ok"])

    def _start_tunnel(self) -> None:
        if hasattr(self, "tunnel_btn"):
            self.tunnel_btn.configure(state="disabled")
        self.web_msg.configure(text="Starting free Cloudflare HTTPS tunnel...", fg=self.app.theme["muted"])
        self.app.run_in_background(self.app.controller.start_public_tunnel, self._tunnel_done)

    def _tunnel_done(self, result) -> None:
        url, err = result
        self._draw_web_access()
        self.app._update_web_badge()
        if err:
            self.web_msg.configure(text=err, fg=self.app.theme["bad"])
        else:
            self.web_msg.configure(text=f"Online! Open {url} from any phone or computer.", fg=self.app.theme["ok"])

    def _stop_tunnel(self) -> None:
        self.app.controller.stop_public_tunnel()
        self._draw_web_access()
        self.app._update_web_badge()
        self.web_msg.configure(text="Public HTTPS link stopped.", fg=self.app.theme["muted"])

    def _set_web_host(self, host: str) -> None:
        self._web_host = host
        cfg = dict(self.app.controller.settings.get("web_access", default={}) or {})
        cfg["host"] = host
        self.app.controller.settings.set("web_access", cfg)
        self._draw_web_access()

    def _save_web(self, enabled: bool) -> None:
        err = self.app.controller.configure_web(
            enabled=enabled,
            host=self._web_host,
            port=self.web_port.get().strip(),
            new_password=self.web_pw.get(),
        )
        if err:
            self.web_msg.configure(text=err, fg=self.app.theme["bad"])
            return
        self._draw_web_access()
        self.app._update_web_badge()
        self.web_msg.configure(
            text="Web access is on." if enabled else "Web access is off.",
            fg=self.app.theme["ok"] if enabled else self.app.theme["muted"],
        )

    # --- always-on, scheduled tasks & Pause All (Session 5) -------------------------------------
    def _draw_always_on(self) -> None:
        t, c = self.app.theme, self.app.controller
        card = self.always_card
        for w in card.winfo_children():
            w.destroy()
        paused = c.is_paused()

        head = t.frame(card, bg="card")
        head.pack(fill="x")
        t.label(head, "Always-on & Schedules", bg="card", font=FONT_BOLD).pack(side="left")
        btn_bg = t["ok"] if paused else t["bad"]
        btn_fg = "#0f1115" if paused else "#ffffff"
        tk.Button(
            head, text="Resume everything" if paused else "Pause everything",
            command=self.app.toggle_pause, relief="flat", bd=0, cursor="hand2",
            font=FONT_BOLD, padx=12, pady=5, bg=btn_bg, fg=btn_fg,
            activebackground=btn_bg, activeforeground=btn_fg,
        ).pack(side="right")

        state_txt = ("Pilot is currently paused. Connectors, schedules, and chat are stopped."
                     if paused else "Pilot runs quietly in the background and only speaks when something needs you.")
        t.label(card, state_txt, bg="card", fg="bad" if paused else "muted",
                font=FONT_SMALL, wraplength=600).pack(fill="x", pady=(4, 8))

        # Start with Windows
        boot_row = t.frame(card, bg="card")
        boot_row.pack(fill="x", pady=3)
        boot_on = c.start_with_windows()
        t.label(boot_row, "Start with Windows (background)", bg="card").pack(side="left")
        t.button(boot_row, f"Start at login: {'On' if boot_on else 'Off'}",
                 lambda on=not boot_on: self._toggle_start_with_windows(on),
                 kind="primary" if boot_on else "secondary").pack(side="right")

        # When check-ins are ignored
        ign_row = t.frame(card, bg="card")
        ign_row.pack(fill="x", pady=4)
        policy = c.when_ignored_policy()
        t.label(ign_row, "If scheduled check-ins go unanswered", bg="card").pack(side="left")
        for mode, label in (("pause", "Pause after 6"), ("keep_going", "Keep going quietly")):
            t.button(ign_row, label, lambda m=mode: self._set_ignored_policy(m),
                     kind="primary" if policy == mode else "secondary").pack(side="right", padx=(6, 0))

        # Add a scheduled job
        t.label(card, "Scheduled checks", bg="card", font=FONT_BOLD).pack(fill="x", pady=(10, 4))
        add_row = t.frame(card, bg="card")
        add_row.pack(fill="x", pady=(0, 4))
        self.sched_name = t.entry(add_row, width=16)
        self.sched_name.insert(0, "Daily check")
        self.sched_name.pack(side="left", ipady=4, padx=(0, 6))
        self.sched_prompt = t.entry(add_row, width=30)
        self.sched_prompt.insert(0, "Check workspace notes and summarize anything urgent.")
        self.sched_prompt.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        t.label(add_row, "Every", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
        self.sched_mins = t.entry(add_row, width=5)
        self.sched_mins.insert(0, "60")
        self.sched_mins.pack(side="left", ipady=4, padx=4)
        t.label(add_row, "min", bg="card", fg="muted", font=FONT_SMALL).pack(side="left", padx=(0, 6))
        t.button(add_row, "Add schedule", self._add_schedule, kind="secondary").pack(side="left")

        if self._always_msg:
            t.label(card, self._always_msg, bg="card", fg="muted", font=FONT_SMALL,
                    wraplength=600).pack(fill="x", pady=(2, 4))

        jobs = c.scheduled_jobs()
        if not jobs:
            t.label(card, "No scheduled tasks yet. Add one above so Pilot checks in automatically.",
                    bg="card", fg="muted", font=FONT_SMALL).pack(fill="x", pady=(2, 0))
        for job in jobs:
            jrow = t.frame(card, bg="card")
            jrow.pack(fill="x", pady=3)
            status_tag = "On" if job.enabled else "Paused"
            extra = f" · every {job.interval_minutes} min · {status_tag}"
            t.label(jrow, f"{job.name}{extra}", bg="card", font=FONT_SMALL).pack(side="left")
            t.button(jrow, "Delete job", lambda jid=job.id: self._delete_schedule(jid),
                     kind="secondary").pack(side="right", padx=(6, 0))
            t.button(jrow, "Pause job" if job.enabled else "Resume job",
                     lambda jid=job.id, en=not job.enabled: self._toggle_schedule(jid, en),
                     kind="secondary").pack(side="right")

    def _toggle_start_with_windows(self, enabled: bool) -> None:
        err = self.app.controller.set_start_with_windows(enabled)
        self._always_msg = err or ("Pilot will start quietly when you sign in to Windows."
                                   if enabled else "Start with Windows turned off.")
        self._draw_always_on()

    def _set_ignored_policy(self, mode: str) -> None:
        self.app.controller.set_when_ignored_policy(mode)
        self._always_msg = ""
        self._draw_always_on()

    def _add_schedule(self) -> None:
        name = self.sched_name.get().strip()
        prompt = self.sched_prompt.get().strip()
        try:
            mins = int(self.sched_mins.get().strip())
        except ValueError:
            mins = 60
        if not name or not prompt:
            self._always_msg = "Enter a name and what Pilot should check."
            self._draw_always_on()
            return
        job = self.app.controller.add_scheduled_job(name, prompt, mins)
        self._always_msg = f"Added schedule '{job.name}' (every {job.interval_minutes} min)."
        self._draw_always_on()

    def _toggle_schedule(self, job_id: str, enabled: bool) -> None:
        self.app.controller.set_scheduled_job_enabled(job_id, enabled)
        self._draw_always_on()

    def _delete_schedule(self, job_id: str) -> None:
        self.app.controller.remove_scheduled_job(job_id)
        self._draw_always_on()

    # --- usage & live system resources dashboard (Session 5) ------------------------------------
    def _meter_color(self, pct: float) -> str:
        t = self.app.theme
        if pct >= 95.0:
            return t["bad"]
        if pct >= 80.0:
            return t["warn"]
        return t["ok"]

    def _draw_usage(self) -> None:
        t, c = self.app.theme, self.app.controller
        card = self.usage_card
        for w in card.winfo_children():
            w.destroy()

        head = t.frame(card, bg="card")
        head.pack(fill="x")
        t.label(head, "Usage & System Resources", bg="card", font=FONT_BOLD).pack(side="left")
        t.button(head, "Export report", self._export_usage, kind="secondary").pack(side="right")

        self.usage = t.label(card, c.usage_line(), bg="card", fg="muted")
        self.usage.pack(fill="x", pady=(4, 6))

        # Alerts (if approaching caps or RAM/Disk >= 80%)
        alerts = c.usage_alerts()
        if alerts:
            alert_box = tk.Frame(card, bg=t["warn"], padx=10, pady=6)
            alert_box.pack(fill="x", pady=(0, 8))
            for msg in alerts:
                tk.Label(alert_box, text="⚠  " + msg, bg=t["warn"], fg="#0f1115",
                         font=FONT_SMALL, anchor="w", justify="left", wraplength=580).pack(fill="x")

        # Live resource snapshot & colored bars
        snap = c.sample_resources()
        rows = [
            ("Memory (RAM)", snap["ram_pct"], f"{snap['ram_mb']:.0f} MB ({snap['ram_pct']:.0f}%)"),
            ("Processor (CPU)", snap["cpu_pct"], f"{snap['cpu_pct']:.1f}%"),
            ("Workspace disk", snap["disk_pct"], f"{snap['disk_mb']:.1f} MB ({snap['disk_pct']:.1f}%)"),
        ]
        if snap.get("gpu_pct") is not None:
            rows.append(("Graphics (GPU)", snap["gpu_pct"], f"{snap['gpu_pct']:.0f}%"))
        bars_cv = tk.Canvas(card, width=540, height=12 + len(rows) * 25, bg=t["card"], highlightthickness=0, bd=0)
        bars_cv.pack(fill="x", pady=(2, 4))
        for idx, (label, pct, val_txt) in enumerate(rows):
            y = 10 + idx * 25
            bars_cv.create_text(2, y + 6, text=label, anchor="w", fill=t["text"], font=FONT_SMALL)
            bar_x0, bar_x1 = 130, 385
            bars_cv.create_rectangle(bar_x0, y, bar_x1, y + 12, fill=t["field"], outline=t["border"])
            fill_w = max(2, int((bar_x1 - bar_x0) * max(0.0, min(100.0, pct)) / 100.0))
            bars_cv.create_rectangle(bar_x0, y, bar_x0 + fill_w, y + 12,
                                     fill=self._meter_color(pct), outline="")
            bars_cv.create_text(bar_x1 + 10, y + 6, text=val_txt, anchor="w", fill=t["muted"], font=FONT_SMALL)
        if snap.get("gpu_pct") is None:
            t.label(card, "GPU: no supported NVIDIA GPU detected on this PC.", bg="card", fg="muted",
                    font=FONT_SMALL).pack(fill="x", pady=(0, 4))

        # History sparkline + timeframe buttons
        win_row = t.frame(card, bg="card")
        win_row.pack(fill="x", pady=(2, 4))
        t.label(win_row, "Recent memory & CPU trend", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
        for key, lbl in (("week", "Last week"), ("day", "Last day"), ("hour", "Last hour")):
            t.button(win_row, lbl, lambda k=key: self._set_usage_window(k),
                     kind="primary" if self._usage_window == key else "secondary").pack(side="right", padx=(4, 0))

        samples = c.resource_samples(self._usage_window)
        spark = tk.Canvas(card, width=540, height=56, bg=t["field"],
                          highlightthickness=1, highlightbackground=t["border"])
        spark.pack(fill="x", pady=(0, 8))
        if len(samples) >= 2:
            w_px, h_px, pad = 520, 44, 6
            step_x = w_px / max(1, len(samples) - 1)
            ram_pts, cpu_pts = [], []
            for i, s in enumerate(samples):
                x = pad + i * step_x
                y_ram = pad + h_px - (min(100.0, float(s.get("ram_pct", 0))) / 100.0) * h_px
                y_cpu = pad + h_px - (min(100.0, float(s.get("cpu_pct", 0))) / 100.0) * h_px
                ram_pts.extend([x, y_ram])
                cpu_pts.extend([x, y_cpu])
            spark.create_line(*ram_pts, fill=t["accent"], width=2)
            spark.create_line(*cpu_pts, fill=t["ok"], width=2)
            spark.create_text(10, 10, text="RAM", anchor="w", fill=t["accent"], font=FONT_SMALL)
            spark.create_text(52, 10, text="CPU", anchor="w", fill=t["ok"], font=FONT_SMALL)
        else:
            spark.create_text(270, 28, text="Collecting samples once per minute while Pilot runs...",
                              fill=t["muted"], font=FONT_SMALL)

        # Period breakdown (Today / Week / Month) & per-source usage
        summary = c.period_usage()
        for label, key in (("Today", "today"), ("Last 7 days", "week"), ("Last 30 days", "month")):
            d = summary[key]
            usd = f"${d['usd']:.4f}" + ("" if d["usd_complete"] else " (partial)")
            t.label(card, f"{label}: {d['calls']} calls · {d['total_tokens']:,} tokens · {usd}",
                    bg="card", fg="text" if key == "today" else "muted", font=FONT_SMALL).pack(fill="x", pady=1)

        by_src = summary["week"]["by_source"]
        if by_src:
            parts = [f"{src}: {b['calls']} ({b['tokens']:,} tok)" for src, b in sorted(by_src.items())]
            t.label(card, "By source (7d): " + "  |  ".join(parts),
                    bg="card", fg="muted", font=FONT_SMALL, wraplength=580).pack(fill="x", pady=(4, 0))

        # Beginner-friendly Daily & workspace limits controls (no editing JSON)
        lim = c.limits_config()
        t.label(card, "Daily & workspace limits", bg="card", font=FONT_BOLD).pack(fill="x", pady=(10, 4))
        lim_row = t.frame(card, bg="card")
        lim_row.pack(fill="x")
        t.label(lim_row, "Daily tokens", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
        self.lim_tokens = t.entry(lim_row, width=10)
        self.lim_tokens.insert(0, str(lim["daily_tokens"]))
        self.lim_tokens.pack(side="left", ipady=3, padx=(4, 10))
        t.label(lim_row, "Daily $", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
        self.lim_usd = t.entry(lim_row, width=6)
        self.lim_usd.insert(0, f"{lim['daily_usd']:.2f}")
        self.lim_usd.pack(side="left", ipady=3, padx=(4, 10))
        t.label(lim_row, "Workspace GB", bg="card", fg="muted", font=FONT_SMALL).pack(side="left")
        self.lim_disk = t.entry(lim_row, width=5)
        self.lim_disk.insert(0, f"{lim['max_disk_gb']:g}")
        self.lim_disk.pack(side="left", ipady=3, padx=(4, 10))
        t.button(lim_row, "Save limits", self._save_limits, kind="secondary").pack(side="left")
        self.lim_msg = t.label(card, getattr(self, "_lim_msg_text", ""), bg="card", fg="muted", font=FONT_SMALL)
        self.lim_msg.pack(fill="x", pady=(4, 0))

    def _save_limits(self) -> None:
        err = self.app.controller.save_limits(
            self.lim_tokens.get(), self.lim_usd.get(), self.lim_disk.get()
        )
        if err:
            self._lim_msg_text = err
            self.lim_msg.configure(text=err, fg=self.app.theme["bad"])
            return
        self._lim_msg_text = "Limits saved."
        self.ws_label.configure(text=self.app.controller.workspace_summary())
        self._draw_usage()
        self.lim_msg.configure(text="Limits saved.", fg=self.app.theme["ok"])

    def _set_usage_window(self, window: str) -> None:
        self._usage_window = window
        self._draw_usage()

    def _export_usage(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Export Pilot usage report",
            defaultextension=".txt",
            initialfile="pilot-usage-report.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not dest:
            return
        try:
            self.app.controller.export_usage(dest)
            messagebox.showinfo(APP_NAME, f"Saved usage report to:\n{dest}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save report: {exc}")
