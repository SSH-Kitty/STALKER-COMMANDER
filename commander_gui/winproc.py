"""Running processes on Windows, without spawning anything. Qt-free.

The Linux side reads ``/proc``; Windows has no such thing, and ``tasklist``
costs a process start per check (hundreds of milliseconds on the GUI thread)
and prints in the console's OEM code page. A Toolhelp32 snapshot lists every
process with its image name in about a millisecond.

Only image names are available this way (a process's full command line needs
reading its memory), which is all the callers need: they look for
``ModOrganizer.exe``, ``Anomaly*.exe``, ``steam.exe`` and so on.
"""

from __future__ import annotations

import os

_TH32CS_SNAPPROCESS = 0x00000002


def processes() -> dict[int, str]:
    """pid -> image name (e.g. ``"ModOrganizer.exe"``) of every process.

    Empty on anything but Windows, or when the snapshot fails.
    """
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return {}
    found: dict[int, str] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            found[int(entry.th32ProcessID)] = entry.szExeFile
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def running(image_name: str) -> bool:
    """True when a process with this image name (any case) is running."""
    wanted = image_name.lower()
    own = os.getpid()
    return any(name.lower() == wanted for pid, name in processes().items() if pid != own)
