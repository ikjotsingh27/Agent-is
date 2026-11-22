"""Counts tokens and money for every model call, one log file per day, plus daily limits.

Log files: data/usage/YYYY-MM-DD.jsonl (one line per model call). A plain-language daily summary and
the RAM/CPU/GPU graphs come later (Session 5). Costs are estimates built from the prices in
settings; if a model has no price listed, its cost is left blank instead of guessed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .settings import data_dir

MAX_RESOURCE_SAMPLES = 1000
_LAST_CPU: tuple[float, float] = (0.0, 0.0)
_NVIDIA_SMI: str | None = None
_NVIDIA_CHECKED = False


def _sample_gpu_pct() -> float | None:
    """Optional NVIDIA GPU utilization percentage via nvidia-smi (zero overhead if absent)."""
    global _NVIDIA_SMI, _NVIDIA_CHECKED
    if not _NVIDIA_CHECKED:
        _NVIDIA_SMI = shutil.which("nvidia-smi")
        _NVIDIA_CHECKED = True
    if not _NVIDIA_SMI:
        return None
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0
        out = subprocess.check_output(
            [_NVIDIA_SMI, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=1.0, creationflags=flags,
        ).decode("ascii", errors="ignore").strip()
        first = out.splitlines()[0].strip() if out else ""
        return round(max(0.0, min(100.0, float(first))), 1) if first else None
    except Exception:
        return None


def cleanup_old_data(base_dir: Path | str, max_age_days: int = 14) -> int:
    """Automatic cleanup of old trimmed tool outputs and stray temp PDF files so disk never silently fills."""
    root = Path(base_dir)
    now_ts = time.time()
    removed = 0
    tool_dir = root / "tool_output"
    if tool_dir.is_dir():
        cutoff = now_ts - max(1, int(max_age_days)) * 86400.0
        for p in tool_dir.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    agent_dir = root / "agent"
    if agent_dir.is_dir():
        tmp_cutoff = now_ts - 86400.0
        for p in agent_dir.iterdir():
            try:
                if p.is_file() and p.name.startswith(".tmp-pdf-") and p.stat().st_mtime < tmp_cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    return removed


def _process_ram_mb() -> float:
    """Current process RAM usage (Working Set) in MB using stdlib ctypes on Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            k32 = ctypes.WinDLL("kernel32")
            psapi = ctypes.WinDLL("psapi")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return round(counters.WorkingSetSize / (1024 * 1024), 1)
        except Exception:
            pass
    return 0.0


def _process_cpu_pct() -> float:
    global _LAST_CPU
    wall = time.monotonic()
    cpu = time.process_time()
    prev_wall, prev_cpu = _LAST_CPU
    _LAST_CPU = (wall, cpu)
    if prev_wall <= 0.0 or wall <= prev_wall:
        return 0.0
    pct = ((cpu - prev_cpu) / (wall - prev_wall)) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def sample_resources(workspace=None) -> dict:
    """Take one lightweight snapshot of Pilot's RAM, CPU, optional GPU, and workspace disk use."""
    ram_mb = _process_ram_mb()
    cpu_pct = _process_cpu_pct()
    disk_bytes = workspace.used() if workspace is not None else 0
    max_disk_bytes = workspace.max_bytes if workspace is not None else (5 * 1024 ** 3)
    max_ram_bytes = workspace.max_ram if workspace is not None else (2 * 1024 ** 3)
    disk_mb = round(disk_bytes / (1024 * 1024), 2)
    disk_pct = round((disk_bytes / max_disk_bytes) * 100.0, 1) if max_disk_bytes > 0 else 0.0
    ram_pct = round(((ram_mb * 1024 * 1024) / max_ram_bytes) * 100.0, 1) if max_ram_bytes > 0 else 0.0
    return {
        "time": _now().isoformat(timespec="seconds"),
        "ts": time.time(),
        "ram_mb": ram_mb,
        "ram_pct": min(100.0, ram_pct),
        "cpu_pct": cpu_pct,
        "gpu_pct": _sample_gpu_pct(),
        "disk_mb": disk_mb,
        "disk_pct": min(100.0, disk_pct),
    }


class CapReached(Exception):
    """A daily limit was reached. The caller must ask the user before going on."""


def _now() -> datetime:
    return datetime.now().astimezone()


class UsageTracker:
    def __init__(self, settings, usage_dir: Path | str | None = None, now=None):
        self.settings = settings
        self.dir = Path(usage_dir) if usage_dir else data_dir() / "usage"
        self._now = now or _now
        self.session = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                        "cache_write_tokens": 0, "usd": 0.0}

    def log_path(self, day=None) -> Path:
        day = day or self._now().date()
        return self.dir / f"{day.isoformat()}.jsonl"

    def cost(self, model: str, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0):
        """Estimated USD for one call, or None when the model has no price in settings."""
        price = self.settings.get("prices_per_million_tokens", model)
        if not price or price.get("input") is None or price.get("output") is None:
            return None
        rate_in = price["input"]
        rate_read = price.get("cache_read")
        rate_write = price.get("cache_write")
        rate_read = rate_in if rate_read is None else rate_read
        rate_write = rate_in if rate_write is None else rate_write
        total = (input_tokens * rate_in + output_tokens * price["output"]
                 + cache_read * rate_read + cache_write * rate_write)
        return total / 1_000_000

    def record(self, *, task: str, model: str, response, started: float, ended: float,
               source: str = "window") -> dict:
        usd = self.cost(model, response.input_tokens, response.output_tokens,
                        response.cache_read_tokens, response.cache_write_tokens)
        line = {
            "time": self._now().isoformat(timespec="seconds"),
            "task": (task or "")[:120],
            "source": source or "window",
            "model": model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cache_read_tokens": response.cache_read_tokens,
            "cache_write_tokens": response.cache_write_tokens,
            "usd": usd,
            "seconds": round(max(0.0, ended - started), 2),
            "stop_reason": response.stop_reason,
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        self.session["calls"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            self.session[key] += line[key]
        if usd is not None:
            self.session["usd"] += usd
        return line

    def totals(self, day=None) -> dict:
        out = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
               "cache_write_tokens": 0, "total_tokens": 0, "usd": 0.0, "usd_complete": True}
        path = self.log_path(day)
        if not path.exists():
            return out
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            out["calls"] += 1
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                out[key] += int(line.get(key, 0) or 0)
            if line.get("usd") is None:
                out["usd_complete"] = False
            else:
                out["usd"] += line["usd"]
        out["total_tokens"] = (out["input_tokens"] + out["output_tokens"]
                               + out["cache_read_tokens"] + out["cache_write_tokens"])
        return out

    def check_caps(self) -> None:
        limits = self.settings.get("limits", default={}) or {}
        totals = self.totals()
        cap_tokens = limits.get("daily_tokens")
        if cap_tokens and totals["total_tokens"] >= cap_tokens:
            raise CapReached(
                f"Today's token limit is reached ({totals['total_tokens']:,} of {cap_tokens:,} tokens)."
            )
        cap_usd = limits.get("daily_usd")
        if cap_usd and totals["usd"] >= cap_usd:
            raise CapReached(
                f"Today's spending limit is reached (about ${totals['usd']:.2f} of ${cap_usd:.2f})."
            )

    # --- period totals, resource history, alerts, export (Session 5) ----------------------------
    def totals_for_days(self, days: int = 1) -> dict:
        today = self._now().date()
        agg = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
               "cache_write_tokens": 0, "total_tokens": 0, "usd": 0.0, "usd_complete": True,
               "by_source": {}}
        for offset in range(max(1, int(days))):
            day = today - timedelta(days=offset)
            path = self.log_path(day)
            if not path.exists():
                continue
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                agg["calls"] += 1
                toks = 0
                for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                    v = int(line.get(key, 0) or 0)
                    agg[key] += v
                    toks += v
                agg["total_tokens"] += toks
                if line.get("usd") is None:
                    agg["usd_complete"] = False
                else:
                    agg["usd"] += float(line["usd"])
                src = str(line.get("source") or "window")
                bucket = agg["by_source"].setdefault(src, {"calls": 0, "tokens": 0, "usd": 0.0})
                bucket["calls"] += 1
                bucket["tokens"] += toks
                if line.get("usd") is not None:
                    bucket["usd"] += float(line["usd"])
        return agg

    def period_summary(self) -> dict:
        return {
            "today": self.totals_for_days(1),
            "week": self.totals_for_days(7),
            "month": self.totals_for_days(30),
        }

    def resources_path(self) -> Path:
        return self.dir / "resources.jsonl"

    def record_resource_sample(self, workspace=None, sample: dict | None = None) -> dict:
        row = sample or sample_resources(workspace)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.resources_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        # Keep the resource log small so it never fills the disk.
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > MAX_RESOURCE_SAMPLES:
                path.write_text("\n".join(lines[-MAX_RESOURCE_SAMPLES:]) + "\n", encoding="utf-8")
        except OSError:
            pass
        return row

    def resource_samples(self, window: str = "hour", now_ts: float | None = None) -> list[dict]:
        path = self.resources_path()
        if not path.is_file():
            return []
        span = {"hour": 3600.0, "day": 86400.0, "week": 7 * 86400.0}.get(window, 3600.0)
        cutoff = (now_ts if now_ts is not None else time.time()) - span
        out = []
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if float(row.get("ts", 0.0) or 0.0) >= cutoff:
                out.append(row)
        return out

    def alerts(self, workspace=None, latest_sample: dict | None = None) -> list[str]:
        out: list[str] = []
        limits = self.settings.get("limits", default={}) or {}
        today = self.totals()
        cap_tok = limits.get("daily_tokens")
        if cap_tok and today["total_tokens"] >= cap_tok * 0.8:
            out.append(f"Daily tokens near limit: {today['total_tokens']:,} of {cap_tok:,}.")
        cap_usd = limits.get("daily_usd")
        if cap_usd and today["usd"] >= cap_usd * 0.8:
            out.append(f"Daily spending near limit: about ${today['usd']:.2f} of ${cap_usd:.2f}.")
        sample = latest_sample or (sample_resources(workspace) if workspace is not None else None)
        if sample:
            if sample.get("disk_pct", 0) >= 80.0:
                out.append(f"Agent workspace disk is {sample['disk_pct']:.0f}% full ({sample['disk_mb']:.1f} MB).")
            if sample.get("ram_pct", 0) >= 80.0:
                out.append(f"Memory use is {sample['ram_pct']:.0f}% of the limit ({sample['ram_mb']:.0f} MB).")
        return out

    def export_report(self, dest: Path | str, workspace=None) -> Path:
        summary = self.period_summary()
        sample = sample_resources(workspace) if workspace is not None else None
        lines = ["Pilot Usage Report", "==================", ""]
        for label, key in (("Today", "today"), ("Last 7 days", "week"), ("Last 30 days", "month")):
            d = summary[key]
            usd = f"${d['usd']:.4f}" + ("" if d["usd_complete"] else " (incomplete)")
            lines.append(f"{label}: {d['calls']} calls, {d['total_tokens']:,} tokens, {usd}")
            for src, b in sorted(d["by_source"].items()):
                lines.append(f"  - {src}: {b['calls']} calls, {b['tokens']:,} tokens, ${b['usd']:.4f}")
        if sample:
            lines.extend(["", f"Current resources: RAM {sample['ram_mb']} MB ({sample['ram_pct']}%), "
                              f"CPU {sample['cpu_pct']}%, Workspace disk {sample['disk_mb']} MB ({sample['disk_pct']}%)."])
        out_path = Path(dest)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path
