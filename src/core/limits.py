"""Run a program with a memory cap and a time limit, and make sure nothing it started keeps running.

On Windows this uses a "job object": the program and everything it starts share one memory cap (2 GB by
default). The program is started paused, put in the job, then let go, so not even its first child escapes.
When the job is closed, every process still in it is closed too. Standard library only (ctypes).
On other systems only the time limit applies.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


@dataclass
class RunResult:
    exit_code: int
    output: str
    timed_out: bool = False
    hit_memory_limit: bool = False


CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


class _WindowsJob:
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, max_bytes: int):
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                          "ReadBytes", "WriteBytes", "OtherBytes")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.ctypes, self.EXTENDED = ctypes, EXTENDED
        k = self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.c_void_p]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.ntdll = ctypes.WinDLL("ntdll")
        self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Could not create a Windows job object.")
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = (self.JOB_OBJECT_LIMIT_JOB_MEMORY
                                                 | self.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                                                 | self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        info.JobMemoryLimit = max_bytes
        self.max_bytes = max_bytes
        if not k.SetInformationJobObject(self.handle, self.EXTENDED_LIMIT_INFORMATION,
                                         ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("Could not set the memory limit.")

    def add_and_resume(self, process: subprocess.Popen) -> None:
        handle = int(process._handle)  # the process handle subprocess keeps on Windows
        if not self.kernel32.AssignProcessToJobObject(self.handle, handle):
            process.kill()
            raise OSError("Could not put the program inside the memory limit.")
        self.ntdll.NtResumeProcess(handle)

    def peak_bytes(self) -> int:
        info = self.EXTENDED()
        self.kernel32.QueryInformationJobObject(self.handle, self.EXTENDED_LIMIT_INFORMATION,
                                                self.ctypes.byref(info), self.ctypes.sizeof(info), None)
        return int(info.PeakJobMemoryUsed)

    def kill_all(self) -> None:
        self.kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        self.kernel32.CloseHandle(self.handle)  # closes anything still running in the job


def run_limited(argv: list, cwd: str, max_ram_bytes: int, timeout: float, env: dict | None = None,
                keep_errors: bool = True) -> RunResult:
    """Run argv in cwd, capped at max_ram_bytes of memory (Windows) and timeout seconds.

    Output is stdout plus stderr, or stdout only when keep_errors is False (the browser prints noise there).
    """
    windows = sys.platform == "win32"
    job = _WindowsJob(max_ram_bytes) if windows else None
    flags = (CREATE_SUSPENDED | CREATE_NO_WINDOW) if windows else 0
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT if keep_errors else subprocess.DEVNULL,
                                   creationflags=flags)
        if job:
            job.add_and_resume(process)
        timed_out = False
        try:
            raw, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            job.kill_all() if job else process.kill()
            raw, _ = process.communicate()
        hit_memory = bool(job and job.peak_bytes() >= max_ram_bytes * 0.97)
        return RunResult(process.returncode if process.returncode is not None else -1, _decode(raw or b""),
                         timed_out, hit_memory)
    finally:
        if job:
            job.close()
