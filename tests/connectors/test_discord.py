import base64
import hashlib
import json
import queue
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.connectors import load_connectors
from src.connectors.discord import test as discord_test
from src.connectors.discord.connector import (INTENTS, USER_AGENT, DiscordClient, DiscordRunner)
from src.connectors.discord.gateway import WebSocketClient, WebSocketClosed
from src.core.agent_tools import SharedFile
from src.core.settings import Settings
from src.core.workspace import Workspace
from src.ui.controller import Controller
from tests.helpers import FakeModelService, text_response, tool_response

# Built dynamically so GitHub Push Protection never mistakes this fake unit-test placeholder for a real bot token.
GOOD_DISCORD_TOKEN = ".".join(["FAKE" + "A" * 20, "Test01", "x" * 27])
GOOD_USER_ID = "123456789012345678"


def wait_for(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class FakeDiscordAPI:
    """Stands in for Discord REST v10 + CDN downloads."""

    def __init__(self, token=GOOD_DISCORD_TOKEN, bot_username="PilotBot", dm_blocked=False):
        self.token = token
        self.bot_username = bot_username
        self.dm_blocked = dm_blocked
        self.requests = []
        self.sent_messages = []
        self.sent_uploads = []
        self.cdn_files = {}

    def transport(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body))
        if headers.get("Authorization") != f"Bot {self.token}":
            return 401, json.dumps({"message": "401: Unauthorized"}).encode()
        path = url.split("/api/v10", 1)[1]
        if method == "GET" and path == "/users/@me":
            return 200, json.dumps({"id": "999000111222333444", "username": self.bot_username, "bot": True}).encode()
        if method == "GET" and path == "/gateway/bot":
            return 200, json.dumps({"url": "wss://gateway.discord.gg"}).encode()
        if method == "POST" and path == "/users/@me/channels":
            payload = json.loads(body.decode("utf-8"))
            rid = payload.get("recipient_id", "")
            if rid == "999999999999999999":
                return 404, json.dumps({"code": 10013, "message": "Unknown User"}).encode()
            return 200, json.dumps({
                "id": f"dm_{rid}",
                "recipients": [{"id": rid, "username": "sam", "global_name": "Sam"}],
            }).encode()
        if method == "POST" and path.endswith("/typing"):
            return 204, b""
        if method == "POST" and path.endswith("/messages"):
            channel_id = path.split("/")[2]
            if self.dm_blocked:
                return 403, json.dumps({"code": 50007, "message": "Cannot send messages to this user"}).encode()
            ctype = headers.get("Content-Type", "")
            if ctype.startswith("multipart/form-data"):
                self.sent_uploads.append((channel_id, ctype, body))
            else:
                payload = json.loads(body.decode("utf-8"))
                self.sent_messages.append((channel_id, payload.get("content", "")))
            return 200, json.dumps({"id": "msg_1", "channel_id": channel_id}).encode()
        return 404, json.dumps({"message": "Not Found"}).encode()

    def download_transport(self, url, headers, timeout, max_bytes):
        if url in self.cdn_files:
            return 200, self.cdn_files[url][:max_bytes + 1]
        return 404, b""


class FakeGateway:
    """Stands in for WebSocketClient so DiscordRunner can be tested without real sockets."""

    def __init__(self, incoming=None, close_after=None):
        self.incoming = queue.Queue()
        self.sent = []
        self.connected = False
        self.closed = False
        self.close_after = close_after
        self.incoming.put({"op": 10, "d": {"heartbeat_interval": 60000}})
        for item in incoming or []:
            self.incoming.put(item)

    def connect(self):
        self.connected = True

    def close(self, code=1000):
        self.closed = True

    def send_json(self, obj):
        self.sent.append(obj)
        if self.close_after is not None and obj.get("op") == 2:
            self.incoming.put(WebSocketClosed(self.close_after, "closed by test"))

    def push(self, frame):
        self.incoming.put(frame)

    def recv_json(self, timeout=1.0):
        try:
            item = self.incoming.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            raise item
        return item


class DiscordCheckTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeDiscordAPI()

    def run_check(self, token=GOOD_DISCORD_TOKEN, user_id=GOOD_USER_ID, saved=None):
        return discord_test.run(token, user_id, saved or {}, transport=self.api.transport)

    def test_registry_finds_discord_in_order(self):
        connectors = load_connectors()
        self.assertIn("discord", connectors)
        names = list(connectors)
        self.assertLess(names.index("telegram"), names.index("discord"))

    def test_wrong_token_and_user_id_caught_before_network(self):
        r1 = self.run_check(token="not-a-discord-token")
        self.assertFalse(r1.ok)
        self.assertIn("bot token", r1.message)
        self.assertEqual(self.api.requests, [])

        r2 = self.run_check(user_id="@sam")
        self.assertFalse(r2.ok)
        self.assertIn("User ID", r2.message)
        self.assertIn("Developer Mode", r2.fix)
        self.assertEqual(self.api.requests, [])

    def test_rejected_token_never_leaks(self):
        bad = "OTk5OTk5OTk5OTk5OTk5OTk5.BadTok.WrongWrongWrongWrongWrongWrong00"
        r = self.run_check(token=bad)
        self.assertFalse(r.ok)
        self.assertIn("did not accept", r.message)
        self.assertNotIn(bad, r.message)

    def test_dm_blocked_returns_nearly_with_clear_fix(self):
        self.api.dm_blocked = True
        r = self.run_check()
        self.assertFalse(r.ok)
        self.assertTrue(r.nearly)
        self.assertIn("private server", r.fix)

    def test_pass_opens_dm_says_hello_and_sends_bot_user_agent(self):
        r = self.run_check()
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.data["owner_user_id"], GOOD_USER_ID)
        self.assertEqual(r.data["dm_channel_id"], f"dm_{GOOD_USER_ID}")
        self.assertEqual(r.secrets, {"bot_token": GOOD_DISCORD_TOKEN})
        self.assertNotIn("bot_token", r.data)
        self.assertIn("Sam", r.message)
        self.assertTrue(any("Pilot is connected" in msg for _, msg in self.api.sent_messages))
        self.assertTrue(all(headers.get("User-Agent") == USER_AGENT for _, _, headers, _ in self.api.requests))


class DiscordRunnerTests(unittest.TestCase):
    def make_ws(self):
        tmp = Path(tempfile.mkdtemp(prefix="pilot-dc-ws-"))
        return Workspace(Settings({}), root=tmp / "agent")

    def test_answers_only_owner_dms_and_exchanges_files(self):
        api = FakeDiscordAPI()
        ws = self.make_ws()
        url = "https://cdn.discordapp.com/attachments/1/2/notes.pdf"
        api.cdn_files[url] = b"%PDF-1.4\nhello"
        out_file = ws.files / "reply.txt"
        out_file.write_text("reply content", encoding="utf-8")
        shared = SharedFile(path=out_file, rel_path="reply.txt", name="reply.txt", size=len("reply content"), note="Here you go")

        gw = FakeGateway()
        asked, events = [], []

        def ask(text, attachments=None):
            asked.append((text, [p.name for p in (attachments or [])]))
            return (f"Got {text or 'file'}", [shared] if attachments else [])

        client = DiscordClient(GOOD_DISCORD_TOKEN, transport=api.transport, download_transport=api.download_transport)
        runner = DiscordRunner(client, GOOD_DISCORD_TOKEN, GOOD_USER_ID, f"dm_{GOOD_USER_ID}",
                               ask=ask, on_event=lambda k, i: events.append((k, i)),
                               save_data=lambda d: None, workspace=ws, gateway_factory=lambda u: gw)
        # Push: 1) stranger DM, 2) guild channel message from owner, 3) bot message, 4) owner DM with file
        gw.push({"op": 0, "s": 1, "t": "MESSAGE_CREATE", "d": {
            "channel_id": "dm_999", "author": {"id": "999", "bot": False}, "content": "stranger"}})
        gw.push({"op": 0, "s": 2, "t": "MESSAGE_CREATE", "d": {
            "guild_id": "guild_1", "channel_id": "chan_1", "author": {"id": GOOD_USER_ID}, "content": "in server"}})
        gw.push({"op": 0, "s": 3, "t": "MESSAGE_CREATE", "d": {
            "channel_id": f"dm_{GOOD_USER_ID}", "author": {"id": GOOD_USER_ID, "bot": True}, "content": "self"}})
        gw.push({"op": 0, "s": 4, "t": "MESSAGE_CREATE", "d": {
            "channel_id": f"dm_{GOOD_USER_ID}", "author": {"id": GOOD_USER_ID}, "content": "check this",
            "attachments": [{"filename": "notes.pdf", "size": 14, "url": url}]}})
        runner.start()
        try:
            self.assertTrue(wait_for(lambda: len(api.sent_uploads) == 1))
        finally:
            runner.stop()
        # Identify sent right intents
        self.assertEqual(gw.sent[0]["op"], 2)
        self.assertEqual(gw.sent[0]["d"]["intents"], INTENTS)
        # Only the owner's DM reached ask()
        self.assertEqual(asked, [("check this", ["notes.pdf"])])
        self.assertTrue((ws.files / "inbox" / "notes.pdf").is_file())
        self.assertEqual((ws.files / "inbox" / "notes.pdf").read_bytes(), b"%PDF-1.4\nhello")
        # Both text reply and multipart upload went to owner's DM channel
        self.assertEqual(api.sent_messages, [(f"dm_{GOOD_USER_ID}", "Got check this")])
        ch, ctype, raw_upload = api.sent_uploads[0]
        self.assertEqual(ch, f"dm_{GOOD_USER_ID}")
        self.assertIn("multipart/form-data", ctype)
        self.assertIn(b'filename="reply.txt"', raw_upload)
        self.assertIn(b"reply content", raw_upload)

    def test_close_code_4014_reports_message_content_intent_fix(self):
        api = FakeDiscordAPI()
        gw = FakeGateway(close_after=4014)
        events = []
        client = DiscordClient(GOOD_DISCORD_TOKEN, transport=api.transport)
        runner = DiscordRunner(client, GOOD_DISCORD_TOKEN, GOOD_USER_ID, f"dm_{GOOD_USER_ID}",
                               ask=lambda t: "ok", on_event=lambda k, i: events.append((k, i)),
                               save_data=lambda d: None, gateway_factory=lambda u: gw)
        runner.start()
        try:
            self.assertTrue(wait_for(lambda: any(k == "problem" for k, _ in events)))
        finally:
            runner.stop()
        problems = [i for k, i in events if k == "problem"]
        self.assertIn("Message Content Intent", problems[0])

    def test_long_replies_are_split_under_discord_limit(self):
        api = FakeDiscordAPI()
        DiscordClient(GOOD_DISCORD_TOKEN, transport=api.transport).send_message("ch1", "x" * 4500)
        self.assertEqual([len(m) for _, m in api.sent_messages], [1950, 1950, 600])

    def test_untrusted_attachment_host_is_refused(self):
        api = FakeDiscordAPI()
        client = DiscordClient(GOOD_DISCORD_TOKEN, transport=api.transport, download_transport=api.download_transport)
        with self.assertRaises(Exception) as ctx:
            client.download_attachment("http://127.0.0.1/secret.txt")
        self.assertIn("untrusted", str(ctx.exception))


class DiscordControllerTests(unittest.TestCase):
    def test_setup_and_two_way_files_through_controller(self):
        api = FakeDiscordAPI()
        gw = FakeGateway()
        url = "https://cdn.discordapp.com/attachments/1/2/data.csv"
        api.cdn_files[url] = b"a,b\n1,2\n"
        tmp = Path(tempfile.mkdtemp(prefix="pilot-dc-ctrl-"))
        settings = Settings({"api_keys": {"gemini": "AIza-good-key-1234567890"}}, path=tmp / "settings.json")
        c = Controller(settings, provider_factory=FakeModelService, usage_dir=tmp / "usage",
                       connector_options={"discord_transport": api.transport,
                                          "discord_download_transport": api.download_transport,
                                          "discord_gateway_factory": lambda u: gw})
        self.addCleanup(c.stop_connectors)
        self.assertEqual(c.connector_status("discord"), "Not set up")
        res = c.check_connector("discord", {"bot_token": GOOD_DISCORD_TOKEN, "owner_user_id": GOOD_USER_ID})
        self.assertTrue(res.ok, res.message)
        self.assertEqual(c.connector_status("discord"), "Connected")
        # Prepare a file for the scripted AI to send back via send_file
        (c.workspace.files / "summary.txt").write_text("summary ok", encoding="utf-8")
        c.agent.provider.complete = lambda **kw: (
            tool_response(("t1", "load_tool", {"names": ["send_file"]}))
            if not any(isinstance(m.get("content"), list) for m in kw["messages"])
            else (tool_response(("t2", "send_file", {"path": "summary.txt", "note": "Summary"}))
                  if kw["messages"][-1]["content"][0].get("tool_use_id") == "t1"
                  else text_response("Sent summary.txt!"))
        )
        gw.push({"op": 0, "s": 1, "t": "MESSAGE_CREATE", "d": {
            "channel_id": f"dm_{GOOD_USER_ID}", "author": {"id": GOOD_USER_ID}, "content": "summarize",
            "attachments": [{"filename": "data.csv", "size": 8, "url": url}]}})
        self.assertTrue(wait_for(lambda: len(api.sent_uploads) == 1))
        self.assertTrue((c.workspace.files / "inbox" / "data.csv").is_file())
        self.assertIn(b"summary ok", api.sent_uploads[0][2])
        c.set_connector_enabled("discord", False)
        self.assertEqual(c.connector_status("discord"), "Off")


class WebSocketLoopbackTests(unittest.TestCase):
    def test_handshake_send_recv_fragment_ping_and_close_code(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        port = server_sock.getsockname()[1]
        server_sock.listen(1)
        received_from_client = []

        def send_server_frame(conn, opcode, payload, fin=True):
            b1 = (0x80 if fin else 0) | opcode
            n = len(payload)
            hdr = bytes([b1, n]) if n < 126 else struct.pack("!BBH", b1, 126, n)
            conn.sendall(hdr + payload)

        def read_client_frame(conn):
            b1, b2 = conn.recv(2)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", conn.recv(2))[0]
            mask = conn.recv(4)
            data = bytearray()
            while len(data) < length:
                data.extend(conn.recv(length - len(data)))
            return b1 & 0x0F, bytes(b ^ mask[i & 3] for i, b in enumerate(data))

        def serve():
            conn, _ = server_sock.accept()
            with conn:
                req = conn.recv(4096).decode("ascii")
                key = next(line.split(":", 1)[1].strip() for line in req.splitlines() if line.lower().startswith("sec-websocket-key:"))
                accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
                # Send ping, fragmented JSON, then wait for client message, then close with 4014
                send_server_frame(conn, 0x9, b"hi")
                send_server_frame(conn, 0x1, b'{"op":', fin=False)
                send_server_frame(conn, 0x0, b'10}', fin=True)
                op1, data1 = read_client_frame(conn)  # Pong
                op2, data2 = read_client_frame(conn)  # Client JSON
                received_from_client.append((op1, data1, op2, data2))
                send_server_frame(conn, 0x8, struct.pack("!H", 4014) + b"disallowed")

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            ws = WebSocketClient(f"ws://127.0.0.1:{port}/?v=10")
            ws.connect()
            self.assertEqual(ws.recv_json(timeout=2.0), {"op": 10})
            ws.send_json({"op": 2})
            with self.assertRaises(WebSocketClosed) as ctx:
                ws.recv_json(timeout=2.0)
            self.assertEqual(ctx.exception.code, 4014)
            ws.close()
        finally:
            t.join(timeout=2.0)
            server_sock.close()
        self.assertEqual(received_from_client[0][0], 0xA)  # Pong reply
        self.assertEqual(json.loads(received_from_client[0][3].decode()), {"op": 2})


if __name__ == "__main__":
    unittest.main()
