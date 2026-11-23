"""Windows settings that silently break GAMMA installs. Qt-free.

* **Smart App Control** (Windows 11) blocks programs that aren't code-signed
  and that Microsoft doesn't know yet, with no "Run anyway". COMMANDER, the
  GAMMA CLI, Mod Organizer 2 and the game are all unsigned, so a player who
  has it on can find any of them refusing to start.
* **OneDrive** backs up Desktop and Documents by default on new PCs, and
  "Files On-Demand" turns files it has uploaded into online-only
  placeholders. A GAMMA install in there is tens of gigabytes uploading, and
  the game and MO2 then fail on files that aren't on the disk.
"""

from __future__ import annotations

import os
from pathlib import PureWindowsPath

from .i18n import tr

SAC_OFF = 0
SAC_ON = 1
SAC_EVALUATION = 2

#: Win32 errors a blocked program start reports.
_APP_CONTROL_ERRORS = {
    4551,  # ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION (Smart App Control, WDAC)
    4580,  # ERROR_SYSTEM_INTEGRITY_REPUTATION_MALICIOUS
    4581,  # ERROR_SYSTEM_INTEGRITY_REPUTATION_PUA
    4582,  # ERROR_SYSTEM_INTEGRITY_REPUTATION_DANGEROUS_EXT
    4583,  # ERROR_SYSTEM_INTEGRITY_REPUTATION_OFFLINE
    1260,  # ERROR_ACCESS_DISABLED_BY_POLICY (AppLocker, software restriction)
}
_ANTIVIRUS_ERRORS = {
    225,  # ERROR_VIRUS_INFECTED
    226,  # ERROR_VIRUS_DELETED
}


def smart_app_control_state() -> int | None:
    """SAC_OFF, SAC_ON or SAC_EVALUATION, or None where Windows has no
    Smart App Control (Windows 10, or an upgraded Windows 11)."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")
    except OSError:
        return None
    return value if value in (SAC_OFF, SAC_ON, SAC_EVALUATION) else None


def onedrive_roots(environ: dict[str, str] | None = None) -> list[str]:
    """The folders OneDrive syncs on this PC (personal and work accounts)."""
    env = os.environ if environ is None else environ
    roots: list[str] = []
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = env.get(name, "").strip()
        if value and value not in roots:
            roots.append(value)
    return roots


def onedrive_folder(*paths: str, roots: list[str] | None = None) -> str | None:
    """The first of *paths* that is inside a OneDrive folder, else None.
    A folder that doesn't exist yet counts by where it would be made."""
    if roots is None:
        roots = onedrive_roots()
    parsed_roots = [PureWindowsPath(root) for root in roots if root]
    for path in paths:
        if not path:
            continue
        folder = PureWindowsPath(path)
        # PureWindowsPath compares without letter case, as Windows does.
        if any(folder.is_relative_to(root) for root in parsed_roots):
            return path
    return None


def start_blocked_hint(exc: BaseException) -> str:
    """A plain explanation when *exc* is Windows refusing to start a
    program; empty for any other error."""
    code = getattr(exc, "winerror", None)
    if code in _APP_CONTROL_ERRORS:
        return tr("Windows blocked this program. Smart App Control or an app control policy stops programs that aren't code-signed. Turn Smart App Control off in Windows Security > App & browser control, or ask whoever manages this PC to allow it.")
    if code in _ANTIVIRUS_ERRORS:
        return tr("Your antivirus blocked this program. Restore it from Windows Security > Protection history (or your antivirus' quarantine), then try again.")
    return ""
