"""Undo COMMANDER's changes to Windows when it is uninstalled. Qt-free.

Setup's uninstaller (packaging/windows/installer/COMMANDER.iss) runs this
as ``COMMANDER.exe -m commander_gui.wincleanup <step>`` while COMMANDER.exe
is still on disk, because only COMMANDER knows what it changed: the lists
are in its settings. Each step's exit code is what the uninstaller reads.

``windows-changes --app <install folder>``
    Takes back the per-game graphics card choices COMMANDER set (wingpu.py)
    and removes the Steam shortcuts that start COMMANDER from that folder.
    Exit code: 0, or STEAM_RUNNING when shortcuts are left because Steam
    is open (Steam rewrites shortcuts.vdf from memory when it exits, so an
    edit made now would be undone).

``defender-count``
    Exit code: how many folders COMMANDER added to Microsoft Defender's
    exclusions (at most 99), or COUNT_UNKNOWN (100) when the list can't
    be read.

``defender [--only-if-admin]``
    Removes those exclusions in one elevated PowerShell (one UAC prompt,
    none when already elevated). ``--only-if-admin`` is for a silent
    uninstall, where a UAC prompt out of nowhere must not appear.
    Exit code: 0 done, DECLINED when UAC was declined or skipped, FAILED.

Nothing here touches the game folders, saves or COMMANDER's settings files;
deleting COMMANDER's own data is the uninstaller's question to ask.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import PureWindowsPath

STEAM_RUNNING = 3
DECLINED = 2
FAILED = 1
#: defender-count's exit code when the list can't be read (outside 0-99).
COUNT_UNKNOWN = 100


# ----------------------------------------------------------- GPU and Steam
def undo_gpu_preferences() -> None:
    from . import gui_settings, wingpu

    ours = list(gui_settings.load_gui_settings().get("gpu_preferences_set") or [])
    if ours:
        gone = set(wingpu.undo(ours))
        gui_settings.save_gui_settings(gpu_preferences_set=[exe for exe in ours if exe not in gone])


def _starts_from(exe_field: str, folder: str) -> bool:
    """True when a shortcut's Exe (quoted, as Steam stores it) is inside *folder*."""
    exe = exe_field.strip().strip('"')
    if not exe or not folder:
        return False
    return PureWindowsPath(exe).is_relative_to(PureWindowsPath(folder))


def without_shortcuts_into(shortcuts: list, folder: str) -> list:
    """*shortcuts* (a parsed "shortcuts" map) minus every entry whose Exe
    is inside *folder*."""
    kept = []
    for key, type_byte, value in shortcuts:
        if isinstance(value, list):
            fields = {k: v for k, _t, v in value}
            if isinstance(fields.get("Exe"), str) and _starts_from(fields["Exe"], folder):
                continue
        kept.append((key, type_byte, value))
    return kept


def remove_steam_shortcuts(app_dir: str) -> bool:
    """Remove COMMANDER's Steam shortcuts from every Steam account. False
    when some were left because Steam is running."""
    from . import steam_shortcuts as st

    left = False
    for config in st.find_userdata_config_dirs():
        vdf = config / "shortcuts.vdf"
        try:
            root = st.read_shortcuts(vdf)
        except (OSError, st.ShortcutsFileError):
            continue  # a file this version doesn't understand stays as it is
        shortcuts = st.get_shortcuts_map(root)
        kept = without_shortcuts_into(shortcuts, app_dir)
        if len(kept) == len(shortcuts):
            continue
        if st.steam_is_running():
            left = True
            continue
        try:
            st.write_shortcuts(vdf, st.set_shortcuts_map(root, kept))
        except OSError:
            continue
    return not left


def windows_changes(app_dir: str) -> int:
    undo_gpu_preferences()
    return 0 if remove_steam_shortcuts(app_dir) else STEAM_RUNNING


# ---------------------------------------------------------------- Defender
def excluded_folders() -> list[str]:
    from . import gui_settings

    return [p for p in gui_settings.load_gui_settings().get("defender_excluded") or [] if isinstance(p, str)]


def removal_script(folders: list[str]) -> str:
    """PowerShell that takes *folders* off Defender's exclusion list.
    Exit 0 when it worked or Defender isn't there, else 1."""
    from .winprep import _quote

    paths = ",".join(_quote(p) for p in folders)
    return "\n".join([
        "try { Get-MpComputerStatus -ErrorAction Stop | Out-Null } catch { exit 0 }",
        f"try {{ Remove-MpPreference -ExclusionPath @({paths}) -ErrorAction Stop }} catch {{ exit 1 }}",
        "exit 0",
    ])


def _is_admin() -> bool:
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def remove_defender_exclusions(*, only_if_admin: bool = False) -> int:
    from . import gui_settings
    from .winprep import powershell_exe
    from .winruntimes import _DECLINED, _run_elevated

    folders = excluded_folders()
    if not folders:
        return 0
    if only_if_admin and not _is_admin():
        return DECLINED
    encoded = base64.b64encode(removal_script(folders).encode("utf-16-le")).decode("ascii")
    args = ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded)
    code = _run_elevated(powershell_exe(), args, show=0)
    if code == _DECLINED:
        return DECLINED
    if code != 0:
        return FAILED
    gui_settings.save_gui_settings(defender_excluded=[])
    return 0


# -------------------------------------------------------------------- main
def main(argv: list[str]) -> int:
    if os.name != "nt" or not argv:
        return FAILED
    step, rest = argv[0], argv[1:]
    try:
        if step == "windows-changes" and len(rest) == 2 and rest[0] == "--app":
            return windows_changes(rest[1])
        if step == "defender-count":
            try:
                return min(len(excluded_folders()), 99)
            except Exception:  # noqa: BLE001 - FAILED (1) would read as "1 folder"
                return COUNT_UNKNOWN
        if step == "defender":
            return remove_defender_exclusions(only_if_admin="--only-if-admin" in rest)
    except Exception:  # noqa: BLE001 - an uninstall must never stop on this
        return FAILED
    return FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
