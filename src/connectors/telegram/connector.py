"""Telegram: talk to Pilot from your phone through your own Telegram bot.

Uses the Telegram Bot API over HTTPS with Python's standard library. While Pilot is open, a background
thread waits for new messages ("long polling"). Waiting costs no model tokens: the model is only called
when the owner sends a text message.

Safety: Pilot answers only the owner's chat (learned during setup). Messages from anyone else are ignored,
so a stranger who finds the bot cannot use the owner's agent or quota.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

from ...core.workspace import WorkspaceError
from ..base import CheckResult, Connector, call_ask, encode_multipart

API = "https://api.telegram.org"
TOKEN_SHAPE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$")
MAX_TEXT = 4000  # Telegram's limit is 4096 characters per message
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram Bot API getFile limit
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # Telegram Bot API sendDocument limit


class TelegramError(Exception):
    def __init__(self, kind: str, message: str, fix: str = "", retry_after: int = 0):
        super().__init__(message)
        self.kind, self.message, self.fix, self.retry_after = kind, message, fix, retry_after


def _default_transport(url: str, body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TelegramError("network", "Could not reach Telegram.", "Check your internet connection and try again.")


def _default_multipart_transport(url: str, body: bytes, content_type: str, timeout: float):
    request = urllib.request.Request(url, data=body, method="POST", headers={"content-type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TelegramError("network", "Could not reach Telegram.", "Check your internet connection and try again.")


def _default_download_transport(url: str, timeout: float, max_bytes: int):
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = reply.read(max_bytes + 1)
            return reply.status, data
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TelegramError("network", "Could not download the file from Telegram.",
                            "Check your internet connection and try again.")


def _incoming_file_info(msg: dict) -> tuple[str, str, int] | None:
    """Return (file_id, filename, file_size) for any file/media in a Telegram message, or None."""
    if msg.get("document"):
        d = msg["document"]
        if d.get("file_id"):
            return d["file_id"], d.get("file_name") or "document", int(d.get("file_size") or 0)
    if msg.get("photo"):
        photos = [p for p in msg["photo"] if isinstance(p, dict) and p.get("file_id")]
        if photos:
            p = photos[-1]
            return p["file_id"], f"photo_{p.get('file_unique_id') or 'image'}.jpg", int(p.get("file_size") or 0)
    for key, default_name in (("audio", "audio.mp3"), ("video", "video.mp4"), ("voice", "voice.ogg"),
                              ("animation", "animation.mp4"), ("sticker", "sticker.webp")):
        item = msg.get(key)
        if isinstance(item, dict) and item.get("file_id"):
            return item["file_id"], item.get("file_name") or default_name, int(item.get("file_size") or 0)
    return None


class TelegramClient:
    """A tiny Bot API client. The token is part of Telegram's web address, so it is never put in any message."""

    def __init__(self, token: str, transport=None, multipart_transport=None, download_transport=None):
        self._token = token
        self._transport = transport or _default_transport
        self._multipart = multipart_transport or _default_multipart_transport
        self._download = download_transport or _default_download_transport

    def _parse_reply(self, status: int, raw: bytes):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, AttributeError):
            data = {}
        if status == 200 and data.get("ok"):
            return data.get("result")
        if status in (401, 404):
            raise TelegramError("token", "Telegram did not accept this bot token.",
                                "Copy the token again from BotFather (send /token to BotFather), then Paste.")
        if status == 409:
            raise TelegramError("conflict", "This bot is already being read by another program.",
                                "Close any other program or copy of Pilot that uses this bot, then try again.")
        if status == 429:
            wait_s = int((data.get("parameters") or {}).get("retry_after", 5))
            raise TelegramError("busy", "Telegram asked Pilot to slow down.", f"Try again in {wait_s} seconds.", wait_s)
        detail = str(data.get("description", "")).replace(self._token, "[hidden]")
        raise TelegramError("other", f"Telegram answered with a problem ({status}){': ' + detail if detail else ''}.",
                            "Try again in a moment.")

    def call(self, method: str, params: dict | None = None, wait: float = 0):
        url = f"{API}/bot{self._token}/{method}"
        status, raw = self._transport(url, json.dumps(params or {}).encode("utf-8"), wait + 15)
        return self._parse_reply(status, raw)

    def get_me(self) -> dict:
        return self.call("getMe") or {}

    def get_updates(self, offset, timeout: int = 20) -> list:
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", params, wait=timeout) or []

    def get_file(self, file_id: str) -> dict:
        return self.call("getFile", {"file_id": file_id}) or {}

    def download_file(self, file_path: str, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
        url = f"{API}/file/bot{self._token}/{file_path.lstrip('/')}"
        status, data = self._download(url, 45, max_bytes)
        if status != 200:
            raise TelegramError("other", f"Could not download the file from Telegram ({status}).", "Try sending it again.")
        if len(data) > max_bytes:
            raise TelegramError("other", "That file is larger than Telegram's 20 MB bot download limit.",
                                "Attach it directly in the Pilot window on your computer instead.")
        return data

    def send_message(self, chat_id, text: str) -> None:
        text = text or "(empty reply)"
        for start in range(0, len(text), MAX_TEXT):
            self.call("sendMessage", {"chat_id": chat_id, "text": text[start:start + MAX_TEXT]})

    def send_document(self, chat_id, path: Path | str, caption: str = "") -> None:
        p = Path(path)
        data = p.read_bytes()
        if len(data) > MAX_UPLOAD_BYTES:
            raise TelegramError("other", f"{p.name} is over Telegram's 50 MB upload limit.",
                                "Open it from Pilot on your computer instead.")
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:1024]
        body, content_type = encode_multipart(fields, [("document", p.name, data, "")])
        url = f"{API}/bot{self._token}/sendDocument"
        status, raw = self._multipart(url, body, content_type, 60)
        self._parse_reply(status, raw)

    def typing(self, chat_id) -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except TelegramError:
            pass  # only a nicety


class TelegramRunner:
    """Waits for the owner's messages and answers them with ask(text[, attachments]) -> reply (or (reply, files)).

    on_event(kind, info) reports to the window: ("message", (text, reply[, files])), ("problem", words), ("ok", "").
    save_data(dict) stores the read position, so no message is answered twice after a restart.
    """

    def __init__(self, client, owner_chat_id, offset, ask, on_event, save_data, poll_seconds: int = 20,
                 sleep=None, workspace=None):
        self.client, self.owner, self.offset = client, owner_chat_id, offset
        self.ask, self.on_event, self.save_data = ask, on_event, save_data
        self.poll_seconds = poll_seconds
        self.workspace = workspace
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._thread = None
        self._problem = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _report(self, kind: str, info="") -> None:
        if kind == "problem" and info == self._problem:
            return  # say each problem once, not on every retry
        self._problem = info if kind == "problem" else ""
        self.on_event(kind, info)

    def _loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                updates = self.client.get_updates(self.offset, timeout=self.poll_seconds)
            except TelegramError as exc:
                if exc.kind == "token":
                    self._report("problem", f"{exc.message} {exc.fix}")
                    return  # nothing will work until the token is fixed
                if exc.kind != "conflict" or backoff > 8:  # a short overlap after a restart is normal
                    self._report("problem", f"{exc.message} {exc.fix}")
                self._sleep(exc.retry_after or backoff)
                backoff = min(backoff * 2, 60)
                continue
            backoff = 2
            if self._problem:
                self._report("ok")
            if self._stop.is_set():
                return  # stopped while waiting: leave these messages for the next runner
            for update in updates:
                self.process(update)

    def _download_attachment(self, chat_id, msg: dict) -> list:
        info = _incoming_file_info(msg)
        if not info or self.workspace is None:
            return []
        file_id, filename, size = info
        if size > MAX_DOWNLOAD_BYTES:
            self.client.send_message(
                chat_id,
                f"I could not download {filename} because it is over Telegram's 20 MB bot limit. "
                "Attach it in the Pilot window on your computer instead.",
            )
            return []
        meta = self.client.get_file(file_id)
        remote_path = meta.get("file_path") or ""
        if not remote_path:
            raise TelegramError("other", f"Telegram did not return a download path for {filename}.")
        if filename == "document" and "/" in remote_path:
            filename = remote_path.rsplit("/", 1)[-1] or filename
        data = self.client.download_file(remote_path)
        return [self.workspace.save_inbox_bytes(filename, data)]

    def process(self, update: dict) -> None:
        self.offset = update.get("update_id", 0) + 1
        self.save_data({"offset": self.offset})
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id != self.owner:
            return  # not the owner: ignored on purpose
        text = (msg.get("text") or msg.get("caption") or "").strip()
        file_info = _incoming_file_info(msg)
        try:
            if not text and (not file_info or self.workspace is None):
                self.client.send_message(chat_id, "I can only read text messages for now.")
                return
            if text and not file_info and text.split()[0].lower() == "/start":
                self.client.send_message(chat_id, "Hi! I'm Pilot. Send me a message or any file any time Pilot is open on your computer.")
                return
            attachments = []
            if file_info and self.workspace is not None:
                try:
                    attachments = self._download_attachment(chat_id, msg)
                except WorkspaceError as exc:
                    self.client.send_message(chat_id, str(exc))
                    return
                if not attachments and not text:
                    return
            self.client.typing(chat_id)
            reply, sent_files = call_ask(self.ask, text, attachments or None)
            self.client.send_message(chat_id, reply)
            for shared in sent_files:
                try:
                    self.client.send_document(chat_id, shared.path, caption=shared.note)
                except TelegramError as exc:
                    self.client.send_message(chat_id, f"Could not send {shared.name}: {exc.message}")
            shown_user = text or f"[Sent file: {', '.join(p.name for p in attachments)}]"
            if attachments and text:
                shown_user = f"{text}\n[Attached: {', '.join(p.name for p in attachments)}]"
            if sent_files or attachments:
                self.on_event("message", (shown_user, reply, sent_files))
            else:
                self.on_event("message", (text, reply))
        except TelegramError as exc:
            self._report("problem", f"Could not send a reply on Telegram. {exc.message} {exc.fix}")


class TelegramConnector(Connector):
    def __init__(self, guide: dict, transport=None, poll_seconds: int = 20,
                 multipart_transport=None, download_transport=None):
        super().__init__(guide)
        self.transport = transport
        self.poll_seconds = poll_seconds
        self.multipart_transport = multipart_transport
        self.download_transport = download_transport

    def check(self, values: dict, saved_secrets: dict, saved_data: dict) -> CheckResult:
        from . import test  # the connection check lives in test.py, as for every connector
        token = values.get("bot_token") or saved_secrets.get("bot_token", "")
        if not token:
            return CheckResult(False, "Paste your bot token first.", "Follow steps 1 to 3 to get one from BotFather.")
        return test.run(token, saved_data, transport=self.transport)

    def make_runner(self, secrets: dict, data: dict, ask, on_event, save_data, workspace=None):
        token, owner = secrets.get("bot_token"), data.get("owner_chat_id")
        if not token or owner is None:
            return None
        client = TelegramClient(token, transport=self.transport, multipart_transport=self.multipart_transport,
                                download_transport=self.download_transport)
        return TelegramRunner(client, owner, data.get("offset"), ask, on_event, save_data, self.poll_seconds,
                              workspace=workspace)

    def health_check(self, secrets: dict, data: dict) -> tuple[bool, str]:
        token = secrets.get("bot_token", "")
        if not token:
            return False, "No bot token saved."
        try:
            me = TelegramClient(token, transport=self.transport).get_me()
            return True, f"Healthy (@{me.get('username', 'bot')})."
        except TelegramError as exc:
            return False, f"{exc.message} {exc.fix}".strip()


def create(guide: dict, telegram_transport=None, telegram_poll_seconds: int = 20,
           telegram_multipart_transport=None, telegram_download_transport=None, **_) -> TelegramConnector:
    return TelegramConnector(guide, transport=telegram_transport, poll_seconds=telegram_poll_seconds,
                             multipart_transport=telegram_multipart_transport,
                             download_transport=telegram_download_transport)
