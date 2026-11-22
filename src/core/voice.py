"""Pure-stdlib voice note recording (Windows winmm MCI) and multimodal audio encoding.

No external packages required:
- On Windows, VoiceRecorder uses the built-in winmm.dll mciSendStringW API to record a .wav clip directly
  into the workspace inbox.
- encode_audio_block() encodes any supported audio attachment (.wav, .mp3, .ogg, .oga, .m4a, .flac, .aac,
  .webm) into the OpenAI-compatible input_audio block supported natively by Google Gemini.
"""
from __future__ import annotations

import base64
import ctypes
import sys
import uuid
from datetime import datetime
from pathlib import Path

AUDIO_FORMATS = {
    ".wav": "wav",
    ".mp3": "mp3",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".m4a": "mp4",
    ".flac": "flac",
    ".aac": "aac",
    ".webm": "webm",
}
MAX_INLINE_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB cap for inline base64 voice notes


class VoiceError(Exception):
    """Plain-language voice recording error."""


def is_audio_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in AUDIO_FORMATS


def encode_audio_block(path: Path | str, max_bytes: int = MAX_INLINE_AUDIO_BYTES) -> dict | None:
    """Turn a small audio file into an OpenAI-compatible input_audio content block, or None if unsupported/too big."""
    p = Path(path)
    fmt = AUDIO_FORMATS.get(p.suffix.lower())
    if not fmt or not p.is_file():
        return None
    try:
        size = p.stat().st_size
        if size <= 0 or size > max_bytes:
            return None
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return None
    return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}


def _mci_call(command: str) -> tuple[int, str]:
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(command, buf, len(buf), 0)
    if err:
        err_buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(err, err_buf, len(err_buf))
        return int(err), err_buf.value or f"MCI error {err}"
    return 0, buf.value


class VoiceRecorder:
    """Simple start/stop microphone recorder using Windows' built-in winmm MCI interface."""

    def __init__(self, mci_fn=None):
        self._mci = mci_fn or (_mci_call if sys.platform == "win32" else None)
        self._alias: str = ""
        self.recording: bool = False

    def start(self) -> None:
        if self.recording:
            return
        if self._mci is None:
            raise VoiceError("Voice recording is currently supported on Windows (or in the Web browser).")
        alias = f"pilotrec_{uuid.uuid4().hex[:8]}"
        code, msg = self._mci(f"open new Type waveaudio Alias {alias}")
        if code:
            raise VoiceError(f"Could not open your microphone ({msg}). Check Windows microphone permissions.")
        code, msg = self._mci(f"record {alias}")
        if code:
            self._mci(f"close {alias}")
            raise VoiceError(f"Could not start recording ({msg}).")
        self._alias = alias
        self.recording = True

    def stop(self, dest_dir: Path | str) -> Path:
        if not self.recording or not self._alias:
            raise VoiceError("Not currently recording.")
        alias = self._alias
        self.recording = False
        self._alias = ""
        folder = Path(dest_dir)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = folder / f"voice-{stamp}.wav"
        n = 2
        while out_path.exists():
            out_path = folder / f"voice-{stamp}-{n}.wav"
            n += 1
        try:
            self._mci(f"stop {alias}")
            code, msg = self._mci(f'save {alias} "{out_path}"')
            if code:
                raise VoiceError(f"Could not save the voice note ({msg}).")
        finally:
            self._mci(f"close {alias}")
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise VoiceError("The voice note was empty. Check that your microphone is not muted.")
        return out_path

    def cancel(self) -> None:
        if self._alias and self._mci is not None:
            try:
                self._mci(f"stop {self._alias}")
                self._mci(f"close {self._alias}")
            except Exception:
                pass
        self._alias = ""
        self.recording = False
