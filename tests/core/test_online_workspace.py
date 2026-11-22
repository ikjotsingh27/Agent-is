"""Tests for Option 2: Cloudflare Tunnel, online Workspace & Terminal API, proxy IP rate-limiting, HTTPS Secure cookies."""
from __future__ import annotations

import http.cookiejar
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.cli import run_web
from src.core.settings import Settings
from src.core.tunnel import CloudflareTunnel, TunnelError
from src.ui.controller import Controller
from tests.helpers import FakeModelService


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeProc:
    def __init__(self, lines: list[str]):
        self.stdout = io.StringIO("\n".join(lines) + "\n")
        self._ended = False

    def poll(self):
        return 0 if self._ended else None

    def terminate(self):
        self._ended = True

    def wait(self, timeout=None):
        self._ended = True
        return 0

    def kill(self):
        self._ended = True


class CloudflareTunnelTests(unittest.TestCase):
    def test_quick_tunnel_parses_public_url_and_stops(self):
        logs = [
            "2026-11-21T20:00:00Z INF Requesting new quick Tunnel...",
            "2026-11-21T20:00:01Z INF +------------------------------------------------+",
            "2026-11-21T20:00:01Z INF |  https://cool-pilot-test.trycloudflare.com    |",
        ]
        tunnel = CloudflareTunnel(8765, exe_path="fake-cloudflared.exe", process_factory=lambda argv: FakeProc(logs))
        url = tunnel.start(timeout=3.0)
        self.assertEqual(url, "https://cool-pilot-test.trycloudflare.com")
        self.assertTrue(tunnel.running)
        tunnel.stop()
        self.assertFalse(tunnel.running)
        self.assertEqual(tunnel.public_url, "")

    def test_missing_binary_gives_plain_error(self):
        tunnel = CloudflareTunnel(8765, exe_path="")
        with self.assertRaises(TunnelError):
            tunnel.start()

    def test_controller_refuses_tunnel_without_web_password_and_stops_on_pause(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctrl = Controller(Settings({"provider": "mock"}, path=tmp / "settings.json"),
                              provider_factory=FakeModelService, startup_dir=tmp / "startup")
            self.addCleanup(ctrl.stop_web)

            # Cannot start tunnel before Web access is on
            url, err = ctrl.start_public_tunnel()
            self.assertEqual(url, "")
            self.assertIn("Turn on Web access", err)

            port = free_port()
            self.assertEqual(ctrl.configure_web(True, "127.0.0.1", port, "secretpass"), "")

            def fake_factory(p, tok="", custom=""):
                return CloudflareTunnel(
                    p, exe_path="fake.exe", tunnel_token=tok, custom_url=custom,
                    process_factory=lambda argv: FakeProc(["https://demo-abc.trycloudflare.com"]),
                )

            url2, err2 = ctrl.start_public_tunnel(tunnel_factory=fake_factory)
            self.assertEqual(err2, "")
            self.assertEqual(url2, "https://demo-abc.trycloudflare.com")
            self.assertTrue(ctrl.web_config()["tunnel_running"])

            # Pause All shuts down the public tunnel immediately
            ctrl.pause_all()
            self.assertFalse(ctrl.web_config()["tunnel_running"])
            self.assertEqual(ctrl.web_config()["public_url"], "")
            ctrl.resume_all()

            # Configure permanent custom domain + secret named tunnel token (redacted everywhere)
            fake_tok = "fake-tunnel-" + "x" * 24
            self.assertEqual(ctrl.configure_tunnel("app.example.com", fake_tok, auto_tunnel=True), "")
            self.assertIn("[hidden]", ctrl.settings.redact(f"tok={fake_tok}"))
            cfg = ctrl.web_config()
            self.assertEqual(cfg["custom_url"], "https://app.example.com")
            self.assertTrue(cfg["has_tunnel_token"])
            self.assertTrue(cfg["auto_tunnel"])
            url3, err3 = ctrl.start_public_tunnel(tunnel_factory=fake_factory)
            self.assertEqual(err3, "")
            self.assertEqual(url3, "https://app.example.com")


class OnlineWorkspaceAndProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-online-test-"))
        self.port = free_port()
        settings = Settings({"provider": "mock"}, path=self.tmp / "settings.json")
        self.ctrl = Controller(settings, provider_factory=FakeModelService,
                               usage_dir=self.tmp / "usage", startup_dir=self.tmp / "startup")
        self.addCleanup(self.ctrl.stop_web)
        self.assertEqual(self.ctrl.configure_web(True, "127.0.0.1", self.port, "my-web-pass"), "")
        self.base = f"http://127.0.0.1:{self.ctrl.web_server.actual_port}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method: str, path: str, body=None, headers: dict | None = None):
        hdrs = dict(headers or {})
        data = None
        if isinstance(body, dict):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(request, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {"raw": raw}
            return err.code, parsed, dict(err.headers)

    def test_workspace_terminal_files_limits_and_state_contract(self):
        st, _, hdrs = self.req("POST", "/api/login", {"password": "my-web-pass"},
                               {"X-Pilot-Client": "web", "X-Forwarded-Proto": "https"})
        self.assertEqual(st, 200)
        self.assertIn("Secure", hdrs.get("Set-Cookie", ""))

        # Re-login without X-Forwarded-Proto so local http cookiejar sends cookie
        st, _, _ = self.req("POST", "/api/login", {"password": "my-web-pass"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)

        # Workspace terminal runs safe command inside isolated workspace
        st, res, _ = self.req("POST", "/api/workspace/terminal", {"command": "Write-Output online_term_ok"},
                              {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertTrue(res["ok"])
        self.assertIn("online_term_ok", res["output"])

        # Workspace terminal still enforces NEVER-allowed block
        st, bad, _ = self.req("POST", "/api/workspace/terminal", {"command": "type ..\\..\\settings.json"},
                              {"X-Pilot-Client": "web"})
        self.assertEqual(st, 400)
        self.assertIn("Never allowed", bad["error"])

        # Files listing & safe delete (blocking traversal)
        (self.ctrl.workspace.files / "demo.txt").write_text("hi", encoding="utf-8")
        st, fl, _ = self.req("GET", "/api/workspace/files")
        self.assertEqual(st, 200)
        self.assertTrue(any(f["name"] == "demo.txt" for f in fl["files"]))

        st, _, _ = self.req("POST", "/api/workspace/delete", {"path": "../../settings.json"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 400)

        st, dl, _ = self.req("POST", "/api/workspace/delete", {"path": "demo.txt"}, {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertTrue(dl["ok"])
        self.assertFalse((self.ctrl.workspace.files / "demo.txt").exists())

        # Settings limits API + full state contract
        st, lim, _ = self.req("POST", "/api/settings/limits",
                              {"daily_tokens": 400000, "daily_usd": 4.0, "max_disk_gb": 6},
                              {"X-Pilot-Client": "web"})
        self.assertEqual(st, 200)
        self.assertTrue(lim["ok"])

        st, state, _ = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        for key in ("workspace", "usage", "web", "provider", "connectors", "activity_log", "memory"):
            self.assertIn(key, state)
        self.assertEqual(state["usage"]["limits"]["daily_tokens"], 400000)

    def test_cloudflare_connecting_ip_isolates_brute_force_lockout(self):
        # 5 bad logins from attacker IP 198.51.100.7 lock ONLY that IP, not owner IP 203.0.113.9
        for _ in range(5):
            st, _, _ = self.req("POST", "/api/login", {"password": "wrong"},
                                {"X-Pilot-Client": "web", "CF-Connecting-IP": "198.51.100.7"})
            self.assertEqual(st, 401)
        st, _, _ = self.req("POST", "/api/login", {"password": "my-web-pass"},
                            {"X-Pilot-Client": "web", "CF-Connecting-IP": "198.51.100.7"})
        self.assertEqual(st, 429)
        st_owner, _, _ = self.req("POST", "/api/login", {"password": "my-web-pass"},
                                  {"X-Pilot-Client": "web", "CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(st_owner, 200)

    def test_cli_run_web_headless_mode(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = Settings({"provider": "mock"}, path=tmp / "settings.json")
            stop = threading.Event()
            lines = []
            port = free_port()
            old_pw = os.environ.get("PILOT_WEB_PASSWORD")
            os.environ["PILOT_WEB_PASSWORD"] = "envpass123"
            try:
                t = threading.Thread(
                    target=lambda: run_web(settings, host="127.0.0.1", port=port, tunnel=False,
                                           stop_event=stop, out=lines.append),
                    daemon=True,
                )
                t.start()
                time.sleep(0.25)
                stop.set()
                t.join(timeout=3.0)
            finally:
                if old_pw is None:
                    os.environ.pop("PILOT_WEB_PASSWORD", None)
                else:
                    os.environ["PILOT_WEB_PASSWORD"] = old_pw
            self.assertFalse(t.is_alive())
            self.assertTrue(any(f"127.0.0.1:{port}" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
