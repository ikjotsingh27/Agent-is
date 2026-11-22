"""What every connector has in common.

A connector folder holds:
  guide.json    the setup steps shown in the app (title, text, optional link), the fields to fill in,
                and a privacy note, all in plain words
  connector.py  the logic, with a create(guide) function that returns a Connector
  test.py       the connection check, returning a CheckResult
  README.md     notes for whoever works on the code next
"""
from __future__ import annotations

import inspect
import mimetypes
import uuid
from dataclasses import dataclass, field


def encode_multipart(fields: dict | None = None, files: list | None = None) -> tuple[bytes, str]:
    """Build a multipart/form-data body using the standard library only.

    files is a list of (field_name, filename, data_bytes, content_type_or_empty) tuples.
    Returns (body_bytes, content_type_header_value).
    """
    boundary = f"----PilotBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in (fields or {}).items():
        safe_name = str(name).replace('"', "_")
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for field_name, filename, data, content_type in (files or []):
        safe_field = str(field_name).replace('"', "_")
        safe_file = str(filename or "file").replace("\\", "_").replace("/", "_").replace('"', "_")
        ctype = content_type or mimetypes.guess_type(safe_file)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            f'Content-Disposition: form-data; name="{safe_field}"; filename="{safe_file}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
        )
        chunks.append(bytes(data))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def call_ask(ask, text: str, attachments: list | None = None) -> tuple[str, list]:
    """Call ask() supporting both legacy ask(text) -> str and ask(text, attachments) -> (reply, files)."""
    try:
        sig = inspect.signature(ask)
        params = list(sig.parameters.values())
        accepts_attachments = len(params) >= 2 or any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
    except (TypeError, ValueError):
        accepts_attachments = False
    result = ask(text, attachments) if accepts_attachments else ask(text)
    if isinstance(result, tuple) and len(result) == 2:
        return str(result[0]), list(result[1] or [])
    return str(result), []


@dataclass
class CheckResult:
    ok: bool
    message: str  # what happened, in plain words
    fix: str = ""  # what to do about it, when it failed
    data: dict = field(default_factory=dict)  # what the test learned, saved to settings (never secrets)
    secrets: dict = field(default_factory=dict)  # secrets to save, only when the test passed
    nearly: bool = False  # not passed yet, but nothing is wrong: one more user step to go (shown in amber, not red)


class Connector:
    """Base class. A connector checks its setup and, once connected, may run in the background."""

    def __init__(self, guide: dict):
        self.guide = guide
        self.id = guide["id"]
        self.name = guide["name"]

    @property
    def fields(self) -> list:
        return self.guide.get("fields", [])

    def check(self, values: dict, saved_secrets: dict, saved_data: dict) -> CheckResult:
        """Run the connection test with the values typed in (or the saved ones when a field is left empty)."""
        raise NotImplementedError

    def make_runner(self, secrets: dict, data: dict, ask, on_event, save_data, workspace=None):
        """Return a background runner with start() and stop(), or None if this connector does not run."""
        return None

    def make_tools(self, secrets: dict, data: dict, approve, on_activity) -> list:
        """Return lazy Tool objects exposed to the agent when this connector is connected and enabled."""
        return []

    def health_check(self, secrets: dict, data: dict) -> tuple[bool, str]:
        """Lightweight live health check. Returns (ok, plain_message)."""
        return True, "Ready."
