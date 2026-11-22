"""The agent's own space on this computer: a folder with a disk cap, and a memory cap for what it runs.

data/agent/files    the agent's working folder (its terminal starts here, its files live here)
data/agent/browser  the hidden browser's own profile, separate from yours

The whole data/agent folder is capped (5 GB by default). Pilot checks before every write and every command,
and stops with a plain message when it is full.
"""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
import sys
from pathlib import Path

from .limits import RunResult, run_limited
from .settings import data_dir

GB = 1024 ** 3
MAX_INCOMING_FILE_BYTES = 50 * 1024 * 1024  # 50 MB per incoming connector attachment

WORKSPACE_DEFAULTS = {"max_disk_gb": 5, "max_ram_gb": 2, "command_seconds": 120}
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class WorkspaceError(Exception):
    """A plain-words reason the agent cannot do something in its workspace."""


def folder_size(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def human(n: int) -> str:
    return f"{n / GB:.2f} GB" if n >= GB / 10 else f"{n / 1024 ** 2:.1f} MB"


def file_size_label(n: int) -> str:
    if n >= GB:
        return f"{n / GB:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def safe_filename(name: str, fallback: str = "file") -> str:
    """Turn any untrusted file name into a safe single Windows file name."""
    raw = (name or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = _BAD_NAME_CHARS.sub("_", raw).strip(" .")
    if not cleaned or cleaned in (".", ".."):
        cleaned = fallback
    stem, dot, ext = cleaned.rpartition(".")
    if not dot or not stem:
        return cleaned[:120]
    return f"{stem[:100]}.{ext[:18]}"


class Workspace:
    def __init__(self, settings, root: Path | None = None):
        limits = {**WORKSPACE_DEFAULTS, **(settings.get("workspace", default={}) or {})}
        self.root = Path(root) if root else data_dir() / "agent"
        self.files = self.root / "files"
        self.browser_profile = self.root / "browser"
        self.venv_dir = self.root / "venv"
        self.max_bytes = int(float(limits["max_disk_gb"]) * GB)
        self.max_ram = int(float(limits["max_ram_gb"]) * GB)
        self.command_seconds = int(limits["command_seconds"])
        self.files.mkdir(parents=True, exist_ok=True)

    # --- disk -----------------------------------------------------------------------------------
    def used(self) -> int:
        return folder_size(self.root)

    def summary(self) -> str:
        return f"{human(self.used())} of {human(self.max_bytes)} used"

    def check_space(self, extra: int = 0) -> None:
        used = self.used()
        if used + extra > self.max_bytes:
            raise WorkspaceError(
                f"The agent's workspace is full ({human(used)} of its {human(self.max_bytes)} limit). "
                "Delete files in it, or raise the Workspace GB limit in Settings.")

    # --- paths ----------------------------------------------------------------------------------
    def path(self, relative: str) -> Path:
        """A path inside the workspace files folder. Anything that points outside it is refused."""
        relative = (relative or ".").strip().strip("'\"")
        candidate = (self.files / relative).resolve()
        base = self.files.resolve()
        if candidate != base and base not in candidate.parents:
            raise WorkspaceError("That path is outside the agent's workspace. Use a path inside it, like notes/todo.txt.")
        return candidate

    def show(self, path: Path) -> str:
        """How a workspace path is shown to the model and the user: relative, with forward slashes."""
        try:
            return path.resolve().relative_to(self.files.resolve()).as_posix() or "."
        except ValueError:
            return path.name

    # --- incoming user files --------------------------------------------------------------------
    def _unique_inbox_path(self, filename: str) -> Path:
        inbox = self.files / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        clean = safe_filename(filename)
        candidate = inbox / clean
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        n = 2
        while candidate.exists():
            candidate = inbox / f"{stem}-{n}{suffix}"
            n += 1
        return candidate

    def save_inbox_bytes(self, filename: str, data: bytes) -> Path:
        """Save an incoming file from a connector into workspace/inbox/, respecting the disk cap."""
        self.check_space(len(data))
        dest = self._unique_inbox_path(filename)
        dest.write_bytes(data)
        return dest

    def import_file(self, src: Path | str) -> Path:
        """Copy any local file from the user's PC into workspace/inbox/, respecting the disk cap."""
        source = Path(src)
        if not source.is_file():
            raise WorkspaceError(f"Could not find the file: {source.name or str(source)}")
        size = source.stat().st_size
        self.check_space(size)
        dest = self._unique_inbox_path(source.name)
        try:
            with open(source, "rb") as inp, open(dest, "wb") as out:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise WorkspaceError(f"Could not copy {source.name}: {exc}")
        return dest

    def describe_attachments(self, paths: list) -> str:
        """Compact workspace note prepended to a user message when files were attached."""
        if not paths:
            return ""
        lines = ["[Files the user sent you, saved in your workspace:]"]
        for p in paths:
            path = Path(p)
            rel = self.show(path)
            size = file_size_label(path.stat().st_size) if path.is_file() else "unknown size"
            mime = mimetypes.guess_type(path.name)[0] or "binary/file"
            lines.append(f"- {rel} ({size}, {mime})")
        lines.append("[Call read_file directly on text, PDF (.pdf), Word (.docx), Excel (.xlsx), or PowerPoint (.pptx) files. "
                     "Use send_file to send any file back to the user.]")
        return "\n".join(lines)

    # --- optional isolated Python environment (keeps pip installs inside the 5 GB cap) ----------
    def venv_scripts_dir(self) -> Path:
        return self.venv_dir / ("Scripts" if sys.platform == "win32" else "bin")

    def has_venv(self) -> bool:
        exe = self.venv_scripts_dir() / ("python.exe" if sys.platform == "win32" else "python")
        return exe.is_file()

    def create_venv(self) -> Path:
        """Create an isolated Python venv inside data/agent/venv so pip installs count toward the 5 GB cap."""
        self.check_space(80 * 1024 * 1024)
        self.root.mkdir(parents=True, exist_ok=True)
        res = run_limited([sys.executable, "-m", "venv", str(self.venv_dir)],
                          str(self.root), self.max_ram, max(120, self.command_seconds))
        if res.exit_code != 0 or not self.has_venv():
            raise WorkspaceError(f"Could not create the workspace Python environment: {res.output.strip()[:200]}")
        return self.venv_dir

    def remove_venv(self) -> None:
        if self.venv_dir.exists():
            shutil.rmtree(self.venv_dir, ignore_errors=True)

    def _run_env(self) -> dict | None:
        if not self.has_venv():
            return None
        env = dict(os.environ)
        scripts = str(self.venv_scripts_dir())
        env["VIRTUAL_ENV"] = str(self.venv_dir)
        env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
        env.pop("PYTHONHOME", None)
        return env

    # --- running programs -----------------------------------------------------------------------
    def run(self, argv: list, timeout: float | None = None, keep_errors: bool = True) -> RunResult:
        self.check_space()
        return run_limited(argv, str(self.files), self.max_ram, timeout or self.command_seconds,
                           env=self._run_env(), keep_errors=keep_errors)
