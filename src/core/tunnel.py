"""Free public HTTPS access via Cloudflare Tunnel (cloudflared) — zero port forwarding, $0.

Quick Tunnels give Pilot a free public https://<random>.trycloudflare.com URL backed by your own computer's
password-protected web server (127.0.0.1:<port>). When you buy a custom domain later, you can connect a
named Cloudflare Tunnel token without changing anything in Pilot.

Security:
- Controller refuses to start a tunnel unless Web access is ON with a password hash saved.
- Pilot's web server still enforces PBKDF2 password login, HttpOnly/SameSite=Strict/Secure cookies,
  CSRF headers, and per-visitor rate limiting (via CF-Connecting-IP).
- Stopping Pilot or clicking Pause All shuts down the tunnel process immediately.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

TRY_CF_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

CANDIDATE_PATHS = [
    r"%LocalAppData%\Microsoft\WinGet\Links\cloudflared.exe",
    r"%LocalAppData%\Pilot\cloudflared.exe",
    r"%ProgramFiles%\cloudflared\cloudflared.exe",
    r"%ProgramFiles(x86)%\cloudflared\cloudflared.exe",
]


class TunnelError(Exception):
    """Plain-language error starting or running the public tunnel."""


def find_cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    for raw in CANDIDATE_PATHS:
        expanded = os.path.expandvars(raw)
        if os.path.isfile(expanded):
            return expanded
    # Also scan winget packages folder in case Links shim was not added to PATH yet
    wg_pkg = Path(os.path.expandvars(r"%LocalAppData%\Microsoft\WinGet\Packages"))
    if wg_pkg.is_dir():
        for match in wg_pkg.glob("Cloudflare.cloudflared*\\*cloudflared*.exe"):
            if match.is_file():
                return str(match)
    return ""


class CloudflareTunnel:
    """Manages one background cloudflared process exposing a local HTTP port over HTTPS."""

    def __init__(self, local_port: int, exe_path: str | None = None, process_factory=None,
                 tunnel_token: str = "", custom_url: str = ""):
        self.local_port = int(local_port)
        self.exe_path = exe_path if exe_path is not None else find_cloudflared()
        self._process_factory = process_factory
        self._tunnel_token = (tunnel_token or "").strip()
        self._custom_url = (custom_url or "").strip()
        self._proc = None
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self.public_url: str = ""
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 20.0) -> str:
        if self.running and self.public_url:
            return self.public_url
        if not self.exe_path:
            raise TunnelError(
                "cloudflared is not installed yet. Run 'winget install Cloudflare.cloudflared' "
                "or download it from developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/."
            )
        self._ready.clear()
        self.public_url = ""
        self.last_error = ""

        if self._tunnel_token:
            argv = [self.exe_path, "tunnel", "--no-autoupdate", "run", "--token", self._tunnel_token]
        else:
            argv = [
                self.exe_path, "tunnel", "--no-autoupdate",
                "--url", f"http://127.0.0.1:{self.local_port}",
            ]

        try:
            if self._process_factory is not None:
                self._proc = self._process_factory(argv)
            else:
                self._proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
        except OSError as exc:
            self._proc = None
            raise TunnelError(f"Could not start cloudflared: {exc}") from exc

        if self._tunnel_token:
            self.public_url = self._custom_url or "Named Cloudflare Tunnel (connected to your domain)"
            self._ready.set()

        self._reader = threading.Thread(target=self._watch_output, daemon=True, name="pilot-tunnel")
        self._reader.start()

        if not self._tunnel_token:
            self._ready.wait(timeout=timeout)
            if not self.public_url:
                err = self.last_error or "Timed out waiting for Cloudflare to assign a public HTTPS link."
                self.stop()
                raise TunnelError(err)
        else:
            time.sleep(0.2)
            if not self.running:
                err = self.last_error or "Named tunnel exited right away. Double-check your tunnel token."
                self.stop()
                raise TunnelError(err)
        return self.public_url

    def _watch_output(self) -> None:
        proc = self._proc
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        try:
            for line in stream:
                text = (line or "").strip()
                if not text:
                    continue
                m = TRY_CF_RE.search(text)
                if m and not self.public_url:
                    with self._lock:
                        self.public_url = m.group(0)
                    self._ready.set()
                if "ERR" in text or "failed" in text.lower():
                    with self._lock:
                        self.last_error = text[:200]
        except Exception:
            pass
        finally:
            self._ready.set()

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._lock:
            self.public_url = ""
