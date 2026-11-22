"""A tiny, pure-stdlib RFC 6455 TLS WebSocket client for Discord's Gateway.

Supports text frames, fragmented frames, Ping/Pong, and close-code parsing with zero third-party packages.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
from urllib.parse import urlparse


class WebSocketError(Exception):
    pass


class WebSocketClosed(Exception):
    def __init__(self, code: int = 1000, reason: str = ""):
        super().__init__(f"WebSocket closed ({code}): {reason}" if reason else f"WebSocket closed ({code})")
        self.code = code
        self.reason = reason


class WebSocketClient:
    def __init__(self, url: str, user_agent: str = "Pilot/1.0", connect_timeout: float = 15.0):
        self.url = url
        self.user_agent = user_agent
        self.connect_timeout = connect_timeout
        self._sock: ssl.SSLSocket | socket.socket | None = None
        self._buf = bytearray()

    def connect(self) -> None:
        parts = urlparse(self.url)
        if parts.scheme not in ("wss", "ws") or not parts.hostname:
            raise WebSocketError(f"Invalid WebSocket address: {self.url}")
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        raw_sock = socket.create_connection((host, port), timeout=self.connect_timeout)
        try:
            if parts.scheme == "wss":
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(raw_sock, server_hostname=host)
            else:
                sock = raw_sock
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host_header = f"{host}:{port}" if parts.port else host
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"User-Agent: {self.user_agent}\r\n\r\n"
            ).encode("ascii")
            sock.sendall(req)
            header_data = bytearray()
            while b"\r\n\r\n" not in header_data:
                chunk = sock.recv(4096)
                if not chunk:
                    raise WebSocketError("Connection closed during WebSocket handshake.")
                header_data.extend(chunk)
                if len(header_data) > 32768:
                    raise WebSocketError("Handshake response headers too large.")
            sep = header_data.index(b"\r\n\r\n") + 4
            status_line = header_data[:sep].split(b"\r\n", 1)[0].decode("latin-1", "replace")
            if " 101 " not in status_line:
                raise WebSocketError(f"WebSocket upgrade refused: {status_line}")
            self._buf = bytearray(header_data[sep:])
            self._sock = sock
        except Exception:
            raw_sock.close()
            raise

    def close(self, code: int = 1000) -> None:
        if self._sock is None:
            return
        try:
            self._send_frame(0x8, struct.pack("!H", code))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None

    def send_json(self, obj: dict) -> None:
        self._send_frame(0x1, json.dumps(obj).encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None:
            raise WebSocketError("WebSocket is not connected.")
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            if self._sock is None:
                raise WebSocketClosed(1006, "connection closed")
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WebSocketClosed(1006, "connection closed")
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = self._read_exact(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv_json(self, timeout: float = 1.0) -> dict | None:
        """Read the next JSON text message, or return None on timeout."""
        if self._sock is None:
            raise WebSocketClosed(1006, "not connected")
        self._sock.settimeout(timeout)
        fragments: list[bytes] = []
        while True:
            try:
                fin, opcode, payload = self._recv_frame()
            except socket.timeout:
                if not fragments:
                    return None
                continue
            except OSError as exc:
                raise WebSocketClosed(1006, str(exc))
            if opcode == 0x9:  # Ping -> Pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # Pong
                continue
            if opcode == 0x8:  # Close
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000
                reason = payload[2:].decode("utf-8", "replace") if len(payload) > 2 else ""
                raise WebSocketClosed(code, reason)
            if opcode in (0x1, 0x0):
                fragments.append(payload)
                if fin:
                    try:
                        return json.loads(b"".join(fragments).decode("utf-8"))
                    except ValueError as exc:
                        raise WebSocketError(f"Could not decode Gateway JSON: {exc}")
