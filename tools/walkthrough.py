"""Button-pressing walkthrough: drives the real Pilot window like a new user would, and checks every step.

It uses a fake model and a fake Telegram, and a throwaway settings folder, so it costs nothing and never touches
your real settings, key or bot. Each step is checked; with --shots it also saves a screenshot of each step.

    python tools/walkthrough.py                      run it and print PASS or FAIL for each step
    python tools/walkthrough.py --shots some_folder  also save step screenshots there (Windows)

The clipboard is used for the Paste buttons and put back afterwards.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.settings import Settings  # noqa: E402
from src.ui import chat_view  # noqa: E402
from src.ui.app import App  # noqa: E402
from src.ui.controller import Controller  # noqa: E402
from tests.helpers import GOOD_TOKEN, FakeModelService, FakeTelegram  # noqa: E402

GOOD_KEY = "AIza-walkthrough-good-key-123"
BAD_KEY = "AIza-walkthrough-bad-key-123"


class StepFailed(Exception):
    pass


class Walkthrough:
    def __init__(self, shots: str | None = None, visible: bool = True):
        self.shots = Path(shots) if shots else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-walkthrough-"))
        self.tg = FakeTelegram()
        settings = Settings({}, path=self.tmp / "settings.json")
        self.controller = Controller(settings, provider_factory=FakeModelService, usage_dir=self.tmp / "usage",
                                     connector_options={"telegram_transport": self.tg.transport,
                                                        "telegram_poll_seconds": 1},
                                     startup_dir=self.tmp / "startup")
        self.visible = visible
        self.root = tk.Tk()
        if visible:
            self.root.attributes("-topmost", True)
        else:  # off screen (a hidden window ignores clicks), and never takes focus from what you are doing
            self.root.geometry("+-4000+0")
        self.app = App(self.controller, root=self.root)
        self.results: list = []
        self.asked: list = []
        try:
            self._clipboard = self.root.clipboard_get()
        except tk.TclError:
            self._clipboard = None

    # --- helpers --------------------------------------------------------------------------------
    def pump(self, seconds: float = 0.15) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def wait(self, condition, what: str, seconds: float = 4.0) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            if condition():
                return
            time.sleep(0.02)
        raise StepFailed(f"timed out waiting for: {what}")

    def find(self, text: str, within=None, contains: bool = False):
        """The widget showing this text (buttons and labels), preferring ones on screen."""
        hits, stack = [], [within or self.root]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            try:
                shown = str(w.cget("text"))
            except tk.TclError:
                continue
            if shown == text or (contains and text in shown):
                hits.append(w)
        if not hits:
            raise StepFailed(f"could not find '{text}' on screen")
        mapped = [w for w in hits if w.winfo_ismapped()]
        return (mapped or hits)[0]

    def click(self, text: str, within=None) -> None:
        widget = self.find(text, within)
        if isinstance(widget, tk.Button):
            if str(widget.cget("state")) == "disabled":
                raise StepFailed(f"'{text}' is greyed out")
            widget.invoke()
        else:
            widget.event_generate("<Button-1>")
        self.pump()

    def copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def shows(self, text: str, within=None) -> bool:
        try:
            self.find(text, within, contains=True)
            return True
        except StepFailed:
            return False

    def shot(self, name: str) -> None:
        if not self.shots:
            return
        from tools.screenshot import capture
        self.root.lift()
        self.pump(0.3)
        r = self.root
        capture(r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height(),
                str(self.shots / f"{len(self.results) + 1:02d}_{name}.png"))

    def step(self, name: str, action) -> None:
        try:
            action()
            self.shot(name)
            self.results.append((name, True, ""))
        except Exception as exc:  # record and carry on, so one failure does not hide the rest
            self.shot(name + "_FAILED")
            self.results.append((name, False, str(exc)))

    # --- the steps ------------------------------------------------------------------------------
    def welcome_shows(self):
        if not self.shows("Welcome to Pilot") or not self.shows("Privacy & trust"):
            raise StepFailed("the welcome page or privacy summary is not showing for a new user")

    def pick_gemini(self):
        self.click("Google Gemini  (free, recommended)")
        if not self.shows("Get your key"):
            raise StepFailed("clicking Gemini did not open the key step")

    def wrong_key_is_refused(self):
        self.copy(BAD_KEY)
        self.click("Paste")
        self.click("Test key")
        self.wait(lambda: self.shows("rejected"), "a 'rejected' message")
        if self.controller.settings.api_key("gemini"):
            raise StepFailed("a rejected key was saved")

    def good_key_is_saved(self):
        self.copy(GOOD_KEY)
        self.click("Paste")
        self.click("Test key")
        self.wait(lambda: self.shows("Chat from your phone?"), "the phone step")
        if Settings.load(self.tmp / "settings.json").api_key("gemini") != GOOD_KEY:
            raise StepFailed("the good key was not saved")

    def open_telegram_setup(self):
        self.click("Set up Telegram")
        if not self.shows("Set up Telegram"):
            raise StepFailed("the Telegram guide did not open")
        for title in ("Open BotFather", "Make your bot", "Copy the token", "Paste it here", "Say hi to your bot"):
            if not self.shows(title):
                raise StepFailed(f"guide step '{title}' is missing")

    def telegram_needs_a_message_first(self):
        guide = self.app.views["Connectors"].frame
        self.copy(GOOD_TOKEN)
        self.click("Paste", guide)
        self.click("Test connection", guide)
        self.wait(lambda: self.shows("has not had a message", guide), "the 'send it a message' hint")
        if self.controller.connector_status("telegram") != "Not set up":
            raise StepFailed("the card says connected before a passing test")

    def telegram_connects(self):
        guide = self.app.views["Connectors"].frame
        self.tg.user_says("hi", chat_id=42, first_name="Sam")
        self.click("Paste", guide)
        self.click("Test connection", guide)
        self.wait(lambda: self.shows("Connected to @my_pilot_bot", guide), "the Connected message")
        if not any("Pilot is connected" in t for _, t in self.tg.sent):
            raise StepFailed("the bot did not say hello on Telegram")

    def phone_message_is_answered(self):
        self.tg.user_says("hello from my phone", chat_id=42)
        self.tg.user_says("I am a stranger", chat_id=99)
        self.wait(lambda: (42, "Echo: hello from my phone") in self.tg.sent, "the reply on Telegram")
        self.pump(0.5)
        if any(chat == 99 for chat, _ in self.tg.sent):
            raise StepFailed("Pilot answered a stranger")
        self.click("Chat", self.app.sidebar)
        if not self.shows("hello from my phone", self.app.views["Chat"].frame):
            log = self.app.views["Chat"].log.get("1.0", "end")
            if "hello from my phone" not in log:
                raise StepFailed("the phone message is not shown in the window's chat")

    def press_enter(self, widget) -> None:
        """Like a person: click into the box, then press Enter.

        Key presses need real keyboard focus, which the off-screen run (used by the unit tests) must not take
        from whatever you are doing. So off screen it clicks Send instead; the visible run tests Enter itself.
        """
        if not self.visible:
            self.click("Send", self.app.views["Chat"].frame)
            return
        widget.focus_force()
        self.pump(0.1)
        widget.event_generate("<Return>")
        self.pump(0.1)

    def window_chat_works(self):
        chat = self.app.views["Chat"]
        chat.input.delete("1.0", "end")
        chat.input.insert("1.0", "hi from the window")
        self.press_enter(chat.input)
        self.wait(lambda: "Echo: hi from the window" in chat.log.get("1.0", "end"), "the reply in the window")
        if not self.shows("Thought & steps", chat.frame):
            raise StepFailed("the collapsible Thought & steps block did not appear")

    def chat_history_persists(self):
        if self.shows("SAVED SESSIONS", self.app.sidebar):
            raise StepFailed("sidebar should stay clean without a Saved Sessions list")
        reloaded = Controller(Settings.load(self.tmp / "settings.json"),
                              provider_factory=FakeModelService, usage_dir=self.tmp / "usage",
                              startup_dir=self.tmp / "startup")
        transcript = reloaded.saved_transcript()
        if not any("Echo: hi from the window" in str(row[1]) for row in transcript):
            raise StepFailed("saved chat history was not restored after reload")

    def connector_card_switches(self):
        self.click("Connectors", self.app.sidebar)
        grid = self.app.views["Connectors"].frame
        if not self.shows("Connected", grid):
            raise StepFailed("the Telegram card does not say Connected")
        self.click("Turn off", grid)
        if self.controller.connector_status("telegram") != "Off" or not self.shows("Off", grid):
            raise StepFailed("Turn off did not switch it off")
        self.click("Turn on", grid)
        if self.controller.connector_status("telegram") != "Connected":
            raise StepFailed("Turn on did not switch it back on")

    def settings_test_and_theme(self):
        self.click("Settings", self.app.sidebar)
        view = self.app.views["Settings"].frame
        self.click("Test connection", view)
        self.wait(lambda: self.shows("Connected. The model answered", view), "the settings test result")
        self.click("Light", view)
        if self.app.theme.name != "light":
            raise StepFailed("the theme did not change")
        self.click("Chat", self.app.sidebar)
        if "Echo: hi from the window" not in self.app.views["Chat"].log.get("1.0", "end"):
            raise StepFailed("the chat was lost when the theme changed")

    def daily_limit_asks_first(self):
        self.controller.settings.set("limits", "daily_tokens", 1)
        chat = self.app.views["Chat"]
        original = chat_view.messagebox.askyesno
        chat_view.messagebox.askyesno = lambda *a, **k: self.asked.append(a) or True
        try:
            chat.input.insert("1.0", "one more please")
            self.press_enter(chat.input)
            self.wait(lambda: "Echo: one more please" in chat.log.get("1.0", "end"), "the reply after saying yes")
        finally:
            chat_view.messagebox.askyesno = original
        if not self.asked:
            raise StepFailed("Pilot went past the daily limit without asking")

    def _tidy_up(self, button: str) -> str:
        """Ask for a tidy-up, check the approval card shows the exact command, answer it, return the reply."""
        chat = self.app.views["Chat"]
        before = chat.log.get("1.0", "end").count("Tidy-up result")
        (self.controller.workspace.files / "old.txt").write_text("old", encoding="utf-8")
        self.click("Chat", self.app.sidebar)
        chat.input.delete("1.0", "end")
        chat.input.insert("1.0", "please tidy up")
        self.press_enter(chat.input)
        self.wait(lambda: self.shows("Pilot wants to run this command", chat.frame), "the approval card")
        for must in ("Remove-Item old.txt", "Why: You asked me to tidy up", "Needs your OK because"):
            if not self.shows(must, chat.frame):
                raise StepFailed(f"the approval card does not show '{must}'")
        self.shot(f"approval_card_before_{button.split()[0].lower()}")
        self.click(button, chat.frame)
        self.wait(lambda: chat.log.get("1.0", "end").count("Tidy-up result") > before,
                  "the agent's answer after the card")
        return chat.log.get("1.0", "end")

    def deny_keeps_the_file(self):
        log = self._tidy_up("Deny")
        if not (self.controller.workspace.files / "old.txt").exists():
            raise StepFailed("the file was deleted although the user said Deny")
        if "said no" not in log:
            raise StepFailed("the agent was not told the user said no")

    def always_allow_saves_a_rule(self):
        self._tidy_up('Always allow "remove-item"')
        if (self.controller.workspace.files / "old.txt").exists():
            raise StepFailed("the file was not deleted after Always allow")
        if "remove-item" not in self.controller.always_allow_rules():
            raise StepFailed("the always-allow rule was not saved")

    def permissions_can_change(self):
        self.click("Settings", self.app.sidebar)
        view = self.app.views["Settings"]
        if not self.shows("remove-item", view.frame):
            raise StepFailed("the saved rule is not listed in Settings")
        install_row = self.find("Install or update software", view.perm_card).master
        self.click("Allowed", install_row)
        if dict((k, v) for k, _, v in self.controller.permission_rows())["install"] != "allow":
            raise StepFailed("Install did not switch to Allowed")
        self.click("Remove", view.perm_card)
        if self.controller.always_allow_rules():
            raise StepFailed("Remove did not delete the rule")
        if not self.shows("Agent workspace", view.frame) or not self.shows("Terminal: On", view.frame):
            raise StepFailed("the Agent workspace card is missing")
        if not self.shows("Web access (password-protected)", view.frame):
            raise StepFailed("the Web access card is missing in Settings")
        canvas = next(w for w in view.frame.winfo_children()[0].winfo_children() if isinstance(w, tk.Canvas))
        canvas.yview_moveto(0.45)
        self.pump(0.2)

    def file_sharing_in_chat(self):
        chat = self.app.views["Chat"]
        self.click("Chat", self.app.sidebar)
        if not self.shows("+", chat.frame) or not self.shows("Mic", chat.frame):
            raise StepFailed("the + file button or Mic voice note button is missing in Chat")
        local_src = self.tmp / "upload.txt"
        local_src.write_text("from user", encoding="utf-8")
        (self.controller.workspace.files / "reply_note.txt").write_text("from ai", encoding="utf-8")
        chat.add_attachments([local_src])
        if not self.shows("upload.txt", chat.frame):
            raise StepFailed("the attachment chip did not show the attached file")
        chat.input.delete("1.0", "end")
        chat.input.insert("1.0", "please share a file")
        self.press_enter(chat.input)
        self.wait(lambda: self.shows("reply_note.txt", chat.frame) and self.shows("Save as...", chat.frame),
                  "the AI file card in Chat")
        if not (self.controller.workspace.files / "inbox" / "upload.txt").is_file():
            raise StepFailed("the attached file was not copied into workspace/inbox/")

    def discord_guide_shows(self):
        self.click("Connectors", self.app.sidebar)
        view = self.app.views["Connectors"]
        if not self.shows("Discord", view.frame):
            raise StepFailed("the Discord connector card is missing")
        # Open Discord's guided setup and verify its fields and steps render cleanly
        view.show_guide("discord")
        self.pump(0.2)
        for must in ("Set up Discord", "Create a Discord app", "Turn on Message Content Intent",
                     "Your Discord User ID"):
            if not self.shows(must, view.frame):
                raise StepFailed(f"Discord setup is missing '{must}'")
        self.click("←  All connectors", view.frame)

    def gmail_calendar_and_health(self):
        self.click("Connectors", self.app.sidebar)
        view = self.app.views["Connectors"]
        for must in ("Connector health", "Gmail", "Google Calendar", "Check health"):
            if not self.shows(must, view.frame):
                raise StepFailed(f"Connectors screen is missing '{must}'")
        self.click("Check health", view.frame)
        self.wait(lambda: self.shows("passed their health check", view.frame),
                  "the connector health check result")
        view.show_guide("gmail")
        self.pump(0.15)
        for must in ("Set up Gmail", "Create a 16-letter App Password", "Read only (recommended)"):
            if not self.shows(must, view.frame):
                raise StepFailed(f"Gmail setup is missing '{must}'")
        view.show_guide("calendar")
        self.pump(0.15)
        for must in ("Set up Google Calendar", "Secret address in iCal format"):
            if not self.shows(must, view.frame):
                raise StepFailed(f"Google Calendar setup is missing '{must}'")
        self.click("←  All connectors", view.frame)

    def memory_screen_works(self):
        self.click("Memory", self.app.sidebar)
        view = self.app.views["Memory"]
        if not self.shows("Add or edit a note", view.frame):
            raise StepFailed("the Memory screen did not open")
        view.title_entry.insert(0, "About Sam")
        view.body_input.insert("1.0", "Studies criminology.")
        self.click("Save note", view.frame)
        if not self.shows("About Sam", view.list_frame) or not self.shows("Studies criminology.", view.list_frame):
            raise StepFailed("the saved memory note is not shown on the Memory screen")
        if not (self.controller.memory.dir / "about-sam.md").is_file():
            raise StepFailed("the memory note was not written to data/memory/about-sam.md")
        self.click("Edit", view.list_frame)
        if view.title_entry.get() != "About Sam":
            raise StepFailed("clicking Edit did not load the note into the editor")
        self.click("Delete", view.list_frame)
        if (self.controller.memory.dir / "about-sam.md").exists():
            raise StepFailed("clicking Delete did not remove the memory note file")

    def approvals_screen_and_badge_work(self):
        chat = self.app.views["Chat"]
        before = chat.log.get("1.0", "end").count("Tidy-up result")
        (self.controller.workspace.files / "old.txt").write_text("old", encoding="utf-8")
        self.click("Chat", self.app.sidebar)
        chat.input.delete("1.0", "end")
        chat.input.insert("1.0", "please tidy up")
        self.press_enter(chat.input)
        self.wait(lambda: "Approvals  (1)" in str(self.app.nav["Approvals"].cget("text")),
                  "the sidebar Approvals (1) badge")
        self.click("Approvals  (1)", self.app.sidebar)
        ap_view = self.app.views["Approvals"]
        if not self.shows("Remove-Item old.txt", ap_view.frame):
            raise StepFailed("the pending command is not shown on the Approvals screen")
        self.click("Deny", ap_view.frame)
        self.wait(lambda: self.controller.pending_approvals_count() == 0, "the approval to resolve")
        self.wait(lambda: str(self.app.nav["Approvals"].cget("text")) == "Approvals",
                  "the sidebar Approvals badge to clear")
        if not self.shows("Denied", ap_view.frame):
            raise StepFailed("the decision is not listed under Recent decisions")
        self.wait(lambda: chat.log.get("1.0", "end").count("Tidy-up result") > before and not chat.busy,
                  "the agent to finish the denied task")

    def activity_screen_works(self):
        self.click("Activity", self.app.sidebar)
        view = self.app.views["Activity"]
        if not self.shows("Today's daily summary", view.frame):
            raise StepFailed("the Activity screen did not open")
        if not self.shows("hi from the window", view.list_frame):
            raise StepFailed("the Activity log did not record earlier chat tasks")
        view.search_entry.delete(0, "end")
        view.search_entry.insert(0, "hi from the window")
        view.refresh()
        self.pump(0.1)
        if not self.shows("hi from the window", view.list_frame):
            raise StepFailed("Activity search did not return the matching entry")
        self.click("Clear", view.frame)

    def pause_all_and_resume_work(self):
        self.click("Pause All", self.app.sidebar)
        if not self.controller.is_paused():
            raise StepFailed("Pause All did not pause Pilot")
        if str(self.app.pause_btn.cget("text")) != "Resume Pilot":
            raise StepFailed("Pause All button did not change to Resume Pilot")
        chat = self.app.views["Chat"]
        self.click("Chat", self.app.sidebar)
        chat.input.delete("1.0", "end")
        chat.input.insert("1.0", "should not run while paused")
        self.press_enter(chat.input)
        self.wait(lambda: "Pilot is paused" in chat.log.get("1.0", "end") and not chat.busy,
                  "the paused notice in Chat")
        self.click("Resume Pilot", self.app.sidebar)
        if self.controller.is_paused():
            raise StepFailed("Resume Pilot did not unpause Pilot")

    def always_on_and_usage_settings(self):
        self.click("Settings", self.app.sidebar)
        view = self.app.views["Settings"]
        for must in ("Always-on & Schedules", "Usage & System Resources", "Save limits"):
            if not self.shows(must, view.frame):
                raise StepFailed(f"Settings is missing '{must}'")
        self.click("Add schedule", view.always_card)
        if not self.controller.scheduled_jobs():
            raise StepFailed("Add schedule did not add a scheduled job")
        self.click("Delete job", view.always_card)
        if self.controller.scheduled_jobs():
            raise StepFailed("Delete job did not remove the scheduled job")
        self.click("Start at login: Off", view.always_card)
        if not self.controller.start_with_windows():
            raise StepFailed("Start with Windows did not create the startup launcher")
        self.click("Start at login: On", view.always_card)
        if self.controller.start_with_windows():
            raise StepFailed("Start with Windows did not remove the startup launcher")

    def run(self) -> bool:
        steps = [("welcome", self.welcome_shows), ("pick_gemini", self.pick_gemini),
                 ("wrong_key_refused", self.wrong_key_is_refused), ("good_key_saved", self.good_key_is_saved),
                 ("telegram_guide", self.open_telegram_setup),
                 ("telegram_needs_message", self.telegram_needs_a_message_first),
                 ("telegram_connected", self.telegram_connects), ("phone_chat", self.phone_message_is_answered),
                 ("window_chat", self.window_chat_works), ("chat_history_persists", self.chat_history_persists),
                 ("file_sharing", self.file_sharing_in_chat),
                 ("connector_switch", self.connector_card_switches), ("discord_guide", self.discord_guide_shows),
                 ("gmail_calendar_health", self.gmail_calendar_and_health),
                 ("memory_screen", self.memory_screen_works),
                 ("approvals_screen", self.approvals_screen_and_badge_work),
                 ("deny_keeps_file", self.deny_keeps_the_file), ("always_allow", self.always_allow_saves_a_rule),
                 ("activity_screen", self.activity_screen_works),
                 ("pause_all_resume", self.pause_all_and_resume_work),
                 ("permissions", self.permissions_can_change),
                 ("always_on_usage", self.always_on_and_usage_settings),
                 ("settings_and_theme", self.settings_test_and_theme), ("daily_limit", self.daily_limit_asks_first)]
        self.pump(0.3)
        for name, action in steps:
            self.step(name, action)
        self.close()
        return all(ok for _, ok, _ in self.results)

    def close(self) -> None:
        if self._clipboard is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._clipboard)
            self.root.update()
        self.app.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shots", help="folder to save a screenshot of each step")
    args = parser.parse_args()
    walk = Walkthrough(shots=args.shots)
    ok = walk.run()
    for name, passed, why in walk.results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({why})" if why else ""))
    print("All steps passed." if ok else "Some steps failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
