"""Is Windows' page file big enough for GAMMA? Qt-free.

The page file lets Windows hand out more memory than the RAM there is.
GAMMA's hundreds of mods need a lot of it, and with the page file turned
off or capped at a small fixed size (common "save SSD space" advice) the
game crashes with out-of-memory errors once RAM is full.

The setting is read from the registry (``PagingFiles``, readable without
administrator rights). Windows' own default, "system managed", grows the
page file as needed, so only a missing or fixed-size page file can be too
small: then RAM plus the largest the page file may grow to is compared with
:data:`NEEDED_MB`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: RAM plus page file GAMMA is given room for (32 GB).
NEEDED_MB = 32 * 1024

_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"


@dataclass(frozen=True)
class PageFile:
    #: Windows sizes it itself ("system managed" or "automatic for all drives").
    managed: bool
    #: The largest the fixed-size page files may grow to, in MB.
    max_mb: int

    @property
    def off(self) -> bool:
        return not self.managed and self.max_mb == 0


def parse(entries: list[str]) -> PageFile:
    """The ``PagingFiles`` value's lines: ``?:\\pagefile.sys`` (automatic
    for all drives), ``C:\\pagefile.sys 0 0`` or no sizes (system managed),
    ``C:\\pagefile.sys 4096 8192`` (fixed: initial and maximum MB)."""
    managed = False
    max_mb = 0
    for entry in entries:
        parts = entry.strip().split()
        if not parts:
            continue
        if parts[0].startswith("?:"):
            managed = True
            continue
        sizes = [int(p) for p in parts[1:3] if p.isdigit()]
        if len(sizes) < 2 or sizes == [0, 0]:
            managed = True
        else:
            max_mb += max(sizes)
    return PageFile(managed, max_mb)


def page_file() -> PageFile | None:
    """This PC's page file setting, or None when it can't be read."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _KEY) as key:
            value, kind = winreg.QueryValueEx(key, "PagingFiles")
    except OSError:
        return None
    if kind != winreg.REG_MULTI_SZ or not isinstance(value, list):
        return None
    return parse(value)


def ram_mb() -> int:
    """Installed RAM in MB, 0 when unknown.

    The installed amount (16384 for 16 GB), not what Windows reports as
    usable (a little less, after the firmware's and graphics' share), so a
    16 GB PC with a 16 GB page file counts as the 32 GB it is.
    """
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    installed_kb = ctypes.c_ulonglong()
    try:
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(installed_kb)):
            return installed_kb.value // 1024
    except (AttributeError, OSError):
        pass

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0
    except (AttributeError, OSError):
        return 0
    return status.ullTotalPhys // (1024 * 1024)


def too_small(setting: PageFile | None, ram: int) -> bool:
    """True when RAM plus the page file at its largest is under NEEDED_MB."""
    if setting is None or setting.managed or ram <= 0:
        return False
    return ram + setting.max_mb < NEEDED_MB


def signature(setting: PageFile, ram: int) -> str:
    """Identifies this setup, so the warning comes back only after it changes."""
    return f"{int(setting.managed)}:{setting.max_mb}:{ram // 1024}"


def open_settings() -> bool:
    """Open Performance Options, whose Advanced tab holds Virtual memory.

    Through the shell, not CreateProcess: Windows starts this one as
    administrator (a UAC prompt for standard users), which CreateProcess
    refuses with "requires elevation"."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    try:
        os.startfile(os.path.join(root, "System32", "SystemPropertiesPerformance.exe"))
    except OSError:
        return False
    return True
