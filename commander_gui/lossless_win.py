"""Frame generation on Windows, through the Lossless Scaling app.

On Linux, frame generation is a Vulkan layer (MAKO) inside Wine/Proton. On
Windows the Game doesn't load anything: Lossless Scaling is its own app
that captures the game window. So COMMANDER writes a profile for the game
into Lossless Scaling's Settings.xml (from the same launch option variables
the Linux card keeps, see launch_options.FRAME_GEN) and starts the app
minimised with the game. Qt-free.

Lossless Scaling reads Settings.xml when it starts and writes it back as the
user changes things, so COMMANDER only writes while the app isn't running.
"""

from __future__ import annotations

import copy
import io
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from . import gui_settings, launch_options
from .atomic import write_bytes

EXE_NAME = "LosslessScaling.exe"
STEAM_URL = "https://store.steampowered.com/app/993090/Lossless_Scaling/"
#: Profiles COMMANDER writes are titled "COMMANDER <game exe>"; it only ever
#: touches those and never the user's own.
PROFILE_PREFIX = "COMMANDER "
#: Seconds Lossless Scaling waits after the game starts before it scales.
AUTOSCALE_DELAY = 3
_BACKUP_NAME = "Settings.commander-backup.xml"


def settings_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Lossless Scaling" / "Settings.xml"


def lossless_exe() -> Path | None:
    """LosslessScaling.exe: the one picked on the Play page, else one from a
    Steam library, else None."""
    from .launcher import _steam_library_paths

    chosen = gui_settings.load_gui_settings().get("lossless_exe") or ""
    if chosen and Path(chosen).is_file():
        return Path(chosen)
    for library in _steam_library_paths():
        exe = library / "steamapps" / "common" / "Lossless Scaling" / EXE_NAME
        if exe.is_file():
            return exe
    return None


def running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return EXE_NAME.lower() in out.lower()


def profile_values(text: str) -> dict[str, str]:
    """The Settings.xml profile fields for the options text (the frame
    generation variables in it)."""
    on = launch_options.is_on(text, launch_options.FRAME_GEN)
    flow = round(launch_options._flow_scale(text) * 100)
    perf = launch_options.var(text, launch_options.FG_PERFORMANCE) == "1"
    return {
        "FrameGeneration": "LSFG3" if on else "Off",
        "LSFG3Mode1": "FIXED",
        "LSFG3Multiplier": str(launch_options._multiplier(text)),
        "LSFGFlowScale": str(flow),
        "LSFGSize": "PERFORMANCE" if perf else "BALANCED",
        "AutoScale": "true",
        "AutoScaleDelay": str(AUTOSCALE_DELAY),
    }


def game_exes(anomaly: Path | None) -> list[Path]:
    """The game executables in Anomaly's bin folder, one profile each."""
    if anomaly is None:
        return []
    return sorted((Path(anomaly) / "bin").glob("AnomalyDX*.exe"))


def _set(profile: ET.Element, tag: str, value: str) -> None:
    node = profile.find(tag)
    if node is None:
        node = ET.SubElement(profile, tag)
    node.text = value


def write_profiles(text: str, anomaly: Path | None, path: Path | None = None) -> str:
    """Bring Lossless Scaling's Settings.xml in line with the options text.

    Returns "ok", "off" (frame generation is off: COMMANDER's profiles are
    taken out), or why nothing was written: "no-file" (never opened by the
    user, so no Settings.xml to build on), "running", "corrupt" or "no-game".
    The file is only ever edited, never started from scratch, and the user's
    own profiles stay as they are.
    """
    path = path or settings_path()
    on = launch_options.is_on(text, launch_options.FRAME_GEN)
    if not path.is_file():
        return "no-file"
    if running():
        return "running"
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return "corrupt"
    root = tree.getroot()
    profiles = root.find("GameProfiles")
    if profiles is None:
        return "corrupt"
    ours = [p for p in profiles.findall("Profile") if (p.findtext("Title") or "").startswith(PROFILE_PREFIX)]
    template = next((p for p in profiles.findall("Profile") if p.findtext("Title") == "Default"), None)
    exes = game_exes(anomaly)
    if on and not exes:
        return "no-game"
    if on and template is None:
        return "corrupt"
    if not on and not ours:
        return "off"  # nothing of COMMANDER's in the file: leave it untouched
    for profile in ours:
        profiles.remove(profile)
    if on:
        values = profile_values(text)
        for exe in exes:
            profile = copy.deepcopy(template)
            _set(profile, "Title", PROFILE_PREFIX + exe.name)
            # Where the app keeps it: right after Title.
            path_node = profile.find("Path")
            if path_node is None:
                path_node = ET.Element("Path")
                profile.insert(list(profile).index(profile.find("Title")) + 1, path_node)
            path_node.text = str(exe)
            for tag, value in values.items():
                _set(profile, tag, value)
            profiles.append(profile)
    ET.indent(tree, space="  ")
    out = io.BytesIO()
    tree.write(out, encoding="utf-8", xml_declaration=True)
    data = out.getvalue()
    current = path.read_bytes()
    if data.replace(b"\r\n", b"\n").strip() != current.replace(b"\r\n", b"\n").strip():
        backup = path.with_name(_BACKUP_NAME)
        if not backup.exists():
            backup.write_bytes(current)
        write_bytes(path, data, lock=False)
    return "ok" if on else "off"


def start(exe: Path | None = None) -> bool:
    """Start Lossless Scaling minimised, unless it already runs."""
    exe = exe or lossless_exe()
    if exe is None or running():
        return False
    try:
        subprocess.Popen(
            [str(exe), "-StartMinimized"], cwd=str(exe.parent), close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError:
        return False
    return True


#: What write_profiles' result means for the player, for the card.
MESSAGES = {
    "no-file": "Open Lossless Scaling once so it creates its settings, then come back.",
    "running": "Lossless Scaling is open, so changes apply after you close it. COMMANDER starts it with the game.",
    "corrupt": "Lossless Scaling's settings file couldn't be read. Open Lossless Scaling to repair it.",
    "no-game": "Anomaly's bin folder has no game executable to set up.",
}
