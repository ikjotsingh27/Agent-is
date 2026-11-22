"""Discord: talk to Pilot in private Discord DMs and exchange any kind of file.

Uses Discord's REST API v10 and Gateway WebSocket v10 with Python's standard library only (no packages to install).
While Pilot is open, a background thread holds a lightweight Gateway WebSocket so DMs arrive right away with zero
polling traffic and zero model tokens while idle.

Safety: Pilot answers only the owner's Discord User ID in direct messages (never server channels, never other users).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ...core.workspace import WorkspaceError
from ..base import CheckResult, Connector, call_ask, encode_multipart
from .gateway import WebSocketClient, WebSocketClosed, WebSocketError

API = "https://discord.com/api/v10"
DEFAULT_GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
USER_AGENT = "DiscordBot (https://github.com/pilot-agent, 1.0)"
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}$")
USER_ID_SHAPE = re.compile(r"^\d{15,22}$")
INTENTS = (1 << 12) | (1 << 15)  # DIRECT_MESSAGES | MESSAGE_CONTENT
MAX_TEXT = 1950  # Discord's limit is 2000 characters per message
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # Discord's standard bot upload limit
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_ATTACHMENT_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")


class DiscordError(Exception):
    def __init__(self, kind: str, message: str, fix: str = "", retry_after: float = 0.0):
        super().__init__(message)
        self.kind, self.message, self.fix, self.retry_after = kind, message, fix, retry_after


def _default_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DiscordError("network", "Could not reach Discord.", "Check your internet connection and try again.")


def _default_download_transport(url: str, headers: dict, timeout: float, max_bytes: int):
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read(max_bytes + 1)
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DiscordError("network", "Could not download the file from Discord.",
                           "Check your internet connection and try again.")


class DiscordClient:
    """Small Discord Bot API v10 REST client using stdlib only."""

    def __init__(self, token: str, transport=None, download_transport=None):
        self._token = token
        self._transport = transport or _default_transport
        self._download = download_transport or _default_download_transport

    def _headers(self, content_type: str = "application/json") -> dict:
        headers = {"Authorization": f"Bot {self._token}", "User-Agent": USER_AGENT}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _parse(self, status: int, raw: bytes):
        if status == 204:
            return None
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {}
        if 200 <= status < 300:
            return data
        code = data.get("code") if isinstance(data, dict) else None
        if status == 401:
            raise DiscordError("token", "Discord did not accept this bot token.",
                               "Open the Discord Developer Portal -> Bot -> Reset Token, copy it, and Paste again.")
        if code == 50007 or status == 403:
            raise DiscordError(
                "dm_blocked", "Discord blocked the DM to your account.",
                "Make sure the bot is in a private server with you (step 4) and that your server privacy settings "
                "allow Direct Messages from server members, then press Test connection again.",
            )
        if code == 10013 or (status == 404 and "user" in str(data.get("message", "")).lower()):
            raise DiscordError("user_id", "Discord could not find that User ID.",
                               "Turn on Developer Mode in Discord (Settings -> Advanced), right-click your own name, "
                               "click Copy User ID, and paste the number here.")
        if status == 429:
            wait_s = float((data.get("retry_after") if isinstance(data, dict) else 5) or 5)
            raise DiscordError("busy", "Discord asked Pilot to slow down.", f"Try again in {int(wait_s) + 1} seconds.", wait_s)
        detail = str(data.get("message", "") if isinstance(data, dict) else "").replace(self._token, "[hidden]")
        raise DiscordError("other", f"Discord answered with a problem ({status}){': ' + detail if detail else ''}.",
                           "Try again in a moment.")

    def call(self, method: str, path: str, payload: dict | None = None, timeout: float = 20.0):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        ctype = "application/json" if payload is not None else ""
        status, raw = self._transport(method, f"{API}{path}", self._headers(ctype), body, timeout)
        return self._parse(status, raw)

    def get_me(self) -> dict:
        return self.call("GET", "/users/@me") or {}

    def get_gateway_url(self) -> str:
        try:
            data = self.call("GET", "/gateway/bot") or {}
            base = str(data.get("url") or "").rstrip("/")
            if base.startswith("wss://"):
                return f"{base}/?v=10&encoding=json"
        except DiscordError:
            pass
        return DEFAULT_GATEWAY

    def open_dm(self, user_id: str) -> dict:
        return self.call("POST", "/users/@me/channels", {"recipient_id": str(user_id)}) or {}

    def send_message(self, channel_id: str, text: str) -> None:
        text = text or "(empty reply)"
        for start in range(0, len(text), MAX_TEXT):
            self.call("POST", f"/channels/{channel_id}/messages", {"content": text[start:start + MAX_TEXT]})

    def send_file(self, channel_id: str, path: Path | str, caption: str = "") -> None:
        p = Path(path)
        data = p.read_bytes()
        if len(data) > MAX_UPLOAD_BYTES:
            raise DiscordError("other", f"{p.name} is over Discord's 25 MB upload limit.",
                               "Open it from Pilot on your computer instead.")
        fields = {}
        if caption:
            fields["payload_json"] = json.dumps({"content": caption[:MAX_TEXT]})
        body, content_type = encode_multipart(fields, [("files[0]", p.name, data, "")])
        status, raw = self._transport("POST", f"{API}/channels/{channel_id}/messages",
                                      self._headers(content_type), body, 60.0)
        self._parse(status, raw)

    def download_attachment(self, url: str, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
        parts = urlparse(url or "")
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in ALLOWED_ATTACHMENT_HOSTS:
            raise DiscordError("other", "Skipped a file attachment from an untrusted address.")
        status, data = self._download(url, {"User-Agent": USER_AGENT}, 45.0, max_bytes)
        if status != 200:
            raise DiscordError("other", f"Could not download the file from Discord ({status}).")
        if len(data) > max_bytes:
            raise DiscordError("other", "That file is over Pilot's 50 MB connector download limit.",
                               "Attach it directly in the Pilot window on your computer instead.")
        return data

    def typing(self, channel_id: str) -> None:
        try:
            self.call("POST", f"/channels/{channel_id}/typing")
        except DiscordError:
            pass


class DiscordRunner:
    """Keeps a Discord Gateway WebSocket open and answers only the owner's DMs."""

    def __init__(self, client: DiscordClient, token: str, owner_user_id: str, dm_channel_id: str,
                 ask, on_event, save_data, workspace=None, gateway_factory=None, sleep=None, clock=None):
        self.client = client
        self._token = token
        self.owner_id = str(owner_user_id)
        self.dm_channel_id = str(dm_channel_id or "")
        self.ask, self.on_event, self.save_data = ask, on_event, save_data
        self.workspace = workspace
        self._gateway_factory = gateway_factory or (lambda url: WebSocketClient(url, user_agent=USER_AGENT))
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._clock = clock or time.monotonic
        self._thread = None
        self._problem = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="discord")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _report(self, kind: str, info="") -> None:
        if kind == "problem" and info == self._problem:
            return
        self._problem = info if kind == "problem" else ""
        self.on_event(kind, info)

    def _loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            ws = None
            try:
                url = self.client.get_gateway_url()
                ws = self._gateway_factory(url)
                ws.connect()
                backoff = 2
                if self._problem:
                    self._report("ok")
                self._run_session(ws)
            except WebSocketClosed as exc:
                if exc.code == 4004:
                    self._report("problem", "Discord rejected the bot token. Open Connectors -> Discord to paste a new token.")
                    return
                if exc.code == 4014:
                    self._report(
                        "problem",
                        "Discord closed the connection because Message Content Intent is off. "
                        "Turn it ON in the Discord Developer Portal -> Bot, save, then turn Discord off and on again.",
                    )
                    return
                if not self._stop.is_set():
                    self._sleep(backoff)
                    backoff = min(backoff * 2, 60)
            except (WebSocketError, DiscordError, OSError) as exc:
                if not self._stop.is_set():
                    self._report("problem", f"Lost connection to Discord ({exc}). Reconnecting...")
                    self._sleep(backoff)
                    backoff = min(backoff * 2, 60)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _run_session(self, ws) -> None:
        hello = None
        deadline = self._clock() + 15.0
        while hello is None and not self._stop.is_set():
            msg = ws.recv_json(timeout=0.5)
            if msg and msg.get("op") == 10:
                hello = msg
                break
            if self._clock() > deadline:
                raise WebSocketError("Timed out waiting for Discord Gateway Hello.")
        if self._stop.is_set() or hello is None:
            return
        interval = max(1.0, float((hello.get("d") or {}).get("heartbeat_interval", 41250)) / 1000.0)
        ws.send_json({
            "op": 2,
            "d": {
                "token": self._token,
                "intents": INTENTS,
                "properties": {"os": "windows", "browser": "pilot", "device": "pilot"},
            },
        })
        seq = None
        next_hb = self._clock() + interval
        while not self._stop.is_set():
            now = self._clock()
            if now >= next_hb:
                ws.send_json({"op": 1, "d": seq})
                next_hb = now + interval
            wait_s = min(0.5, max(0.05, next_hb - self._clock()))
            frame = ws.recv_json(timeout=wait_s)
            if frame is None:
                continue
            if frame.get("s") is not None:
                seq = frame["s"]
            op = frame.get("op")
            if op == 1:  # server requested immediate heartbeat
                ws.send_json({"op": 1, "d": seq})
                next_hb = self._clock() + interval
            elif op in (7, 9):  # Reconnect / Invalid Session
                return
            elif op == 0 and frame.get("t") == "MESSAGE_CREATE":
                self.process_message(frame.get("d") or {})

    def _download_attachments(self, channel_id: str, msg: dict) -> list:
        raw_items = msg.get("attachments") or []
        if not raw_items or self.workspace is None:
            return []
        saved = []
        for item in raw_items:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            name = item.get("filename") or "attachment"
            size = int(item.get("size") or 0)
            if size > MAX_DOWNLOAD_BYTES:
                self.client.send_message(
                    channel_id,
                    f"I skipped {name} because it is over Pilot's 50 MB connector limit. "
                    "Attach it in the Pilot window on your computer instead.",
                )
                continue
            data = self.client.download_attachment(item["url"], MAX_DOWNLOAD_BYTES)
            saved.append(self.workspace.save_inbox_bytes(name, data))
        return saved

    def process_message(self, msg: dict) -> None:
        if msg.get("guild_id"):
            return  # ignore server channels; private DMs only
        author = msg.get("author") or {}
        if author.get("bot"):
            return
        if str(author.get("id") or "") != self.owner_id:
            return  # not the owner: ignored on purpose
        channel_id = str(msg.get("channel_id") or self.dm_channel_id)
        if not channel_id:
            return
        if channel_id != self.dm_channel_id:
            self.dm_channel_id = channel_id
            self.save_data({"dm_channel_id": channel_id})
        text = (msg.get("content") or "").strip()
        has_files = bool(msg.get("attachments"))
        try:
            if not text and (not has_files or self.workspace is None):
                return
            attachments = []
            if has_files and self.workspace is not None:
                try:
                    attachments = self._download_attachments(channel_id, msg)
                except WorkspaceError as exc:
                    self.client.send_message(channel_id, str(exc))
                    return
                if not attachments and not text:
                    return
            self.client.typing(channel_id)
            reply, sent_files = call_ask(self.ask, text, attachments or None)
            self.client.send_message(channel_id, reply)
            for shared in sent_files:
                try:
                    self.client.send_file(channel_id, shared.path, caption=shared.note)
                except DiscordError as exc:
                    self.client.send_message(channel_id, f"Could not send {shared.name}: {exc.message}")
            shown_user = text or f"[Sent file: {', '.join(p.name for p in attachments)}]"
            if attachments and text:
                shown_user = f"{text}\n[Attached: {', '.join(p.name for p in attachments)}]"
            if sent_files or attachments:
                self.on_event("message", (shown_user, reply, sent_files))
            else:
                self.on_event("message", (text, reply))
        except DiscordError as exc:
            self._report("problem", f"Could not reply on Discord. {exc.message} {exc.fix}")


class DiscordConnector(Connector):
    def __init__(self, guide: dict, transport=None, download_transport=None, gateway_factory=None):
        super().__init__(guide)
        self.transport = transport
        self.download_transport = download_transport
        self.gateway_factory = gateway_factory

    def check(self, values: dict, saved_secrets: dict, saved_data: dict) -> CheckResult:
        from . import test
        token = values.get("bot_token") or saved_secrets.get("bot_token", "")
        owner_id = values.get("owner_user_id") or str(saved_data.get("owner_user_id", "") or "")
        if not token:
            return CheckResult(False, "Paste your Discord bot token first.",
                               "Follow steps 1 to 3 in the Discord Developer Portal to get one.")
        if not owner_id:
            return CheckResult(False, "Paste your Discord User ID first.",
                               "Turn on Developer Mode in Discord (Settings -> Advanced), right-click your own name, "
                               "and click Copy User ID.")
        return test.run(token, owner_id, saved_data, transport=self.transport)

    def make_runner(self, secrets: dict, data: dict, ask, on_event, save_data, workspace=None):
        token = secrets.get("bot_token")
        owner_id = data.get("owner_user_id")
        if not token or not owner_id:
            return None
        client = DiscordClient(token, transport=self.transport, download_transport=self.download_transport)
        return DiscordRunner(client, token, owner_id, data.get("dm_channel_id", ""), ask, on_event, save_data,
                             workspace=workspace, gateway_factory=self.gateway_factory)

    def health_check(self, secrets: dict, data: dict) -> tuple[bool, str]:
        token = secrets.get("bot_token", "")
        if not token:
            return False, "No Discord bot token saved."
        try:
            me = DiscordClient(token, transport=self.transport).get_me()
            return True, f"Healthy ({me.get('username', 'bot')})."
        except DiscordError as exc:
            return False, f"{exc.message} {exc.fix}".strip()


def create(guide: dict, discord_transport=None, discord_download_transport=None,
           discord_gateway_factory=None, **_) -> DiscordConnector:
    return DiscordConnector(guide, transport=discord_transport,
                            download_transport=discord_download_transport,
                            gateway_factory=discord_gateway_factory)
