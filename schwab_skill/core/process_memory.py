"""Release scan working memory back to the OS and report process RSS.

Stage A used to materialize every kept price history as Python dicts. Those
objects are freed when the checkpoint write finishes, but the allocator keeps
the arenas, so a long universe scan stays hundreds of megabytes above the
idle baseline. ``release_process_memory`` collects that garbage and asks the
C allocator to return the unused heap.
"""

from __future__ import annotations

import gc
import os

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def read_rss_bytes() -> int | None:
    """Resident set size in bytes, or None when the platform does not expose it."""
    if os.name == "nt":
        return _read_windows_rss_bytes()
    return _read_proc_rss_bytes()


def read_rss_mb() -> float | None:
    """Resident set size in mebibytes, rounded to one decimal, or None."""
    raw = read_rss_bytes()
    if raw is None:
        return None
    return round(raw / (1024 * 1024), 1)


def release_process_memory() -> None:
    """Collect unreferenced scan objects and return unused heap pages.

    Safe to call from the scan thread after checkpoint writes and after the
    worker drops price histories. It does not touch live signals or caches.
    """
    gc.collect()
    if os.name == "nt":
        _compact_windows_heap()
        return
    _malloc_trim()


def _read_proc_rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            parts = handle.read().split()
        if len(parts) < 2:
            return None
        return int(parts[1]) * int(_PAGE_SIZE)
    except (OSError, ValueError):
        return None


def _read_windows_rss_bytes() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
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

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if not ok:
            return None
        return int(counters.WorkingSetSize)
    except Exception:
        return None


def _malloc_trim() -> None:
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        return


def _compact_windows_heap() -> None:
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        heap = kernel.GetProcessHeap()
        kernel.HeapCompact(heap, 0)
    except Exception:
        return
