"""Run the game on the fast graphics card of a two-GPU laptop (Windows).

Laptops with a gaming card next to the processor's own graphics often start
Anomaly on the weak one, and the game then runs at a fraction of its frame
rate. Windows keeps a per-program choice for this (Settings > System >
Display > Graphics), stored per exe path under
``HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences`` as
``GpuPreference=2;`` (high performance). COMMANDER sets that for the game's
exes when the user has made no choice of their own for them, and remembers
which ones it set so turning the option off takes exactly those back out.

On a PC with one graphics card the value does nothing. Qt-free.
"""

from __future__ import annotations

import os
from pathlib import Path

KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
HIGH_PERFORMANCE = "GpuPreference=2;"


def game_exes(anomaly: str | os.PathLike) -> list[str]:
    """Every engine exe in ``<anomaly>/bin`` (AnomalyDX11.exe, the AVX and
    DX9/10 builds, modded exes), as full Windows paths."""
    if not anomaly:
        return []
    folder = Path(anomaly) / "bin"
    try:
        return sorted(
            str(path.resolve())
            for path in folder.iterdir()
            if path.suffix.lower() == ".exe" and path.name.lower().startswith("anomaly")
        )
    except OSError:
        return []


def _open(write: bool):
    import winreg

    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
    if write:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, access)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, access)


def preference(exe: str) -> str | None:
    """The saved choice for *exe*, or None when there is none."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with _open(False) as key:
            value, _kind = winreg.QueryValueEx(key, exe)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def prefer_high_performance(exes: list[str]) -> list[str]:
    """Set the high-performance card for each of *exes* that has no choice
    yet. Returns the exes it set (to remember for :func:`undo`)."""
    if os.name != "nt" or not exes:
        return []
    import winreg

    added: list[str] = []
    try:
        with _open(True) as key:
            for exe in exes:
                if preference(exe) is not None:
                    continue  # the user's own choice, or one already set
                try:
                    winreg.SetValueEx(key, exe, 0, winreg.REG_SZ, HIGH_PERFORMANCE)
                except OSError:
                    continue
                added.append(exe)
    except OSError:
        return added
    return added


def undo(exes: list[str]) -> list[str]:
    """Remove the choice from each of *exes* still set to what COMMANDER
    wrote. Returns the exes whose value is gone (or never was there)."""
    if os.name != "nt" or not exes:
        return list(exes)
    import winreg

    removed: list[str] = []
    try:
        with _open(True) as key:
            for exe in exes:
                current = preference(exe)
                if current is not None and current != HIGH_PERFORMANCE:
                    removed.append(exe)  # changed since: the user's now, not ours
                    continue
                try:
                    if current is not None:
                        winreg.DeleteValue(key, exe)
                except OSError:
                    continue
                removed.append(exe)
    except OSError:
        return removed
    return removed


def sync(anomaly: str | os.PathLike) -> None:
    """Apply the "high-performance graphics card" setting to *anomaly*'s
    exes, or take COMMANDER's own choices back out when it is off."""
    if os.name != "nt":
        return
    from . import gui_settings

    state = gui_settings.load_gui_settings()
    ours = list(state.get("gpu_preferences_set") or [])
    if state.get("high_performance_gpu", True):
        added = prefer_high_performance(game_exes(anomaly))
        if added:
            gui_settings.save_gui_settings(gpu_preferences_set=sorted({*ours, *added}))
    elif ours:
        gone = set(undo(ours))
        gui_settings.save_gui_settings(gpu_preferences_set=[exe for exe in ours if exe not in gone])
