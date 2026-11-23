"""What Windows itself recorded about game crashes, for the log dump. Qt-free.

The Linux dump takes the Wine prefix's logs; on Windows the same evidence is
kept by Windows:

* **Windows Error Reporting** writes a ``Report.wer`` for every program that
  crashed or hung (``%LOCALAPPDATA%`` and ``%ProgramData%\\Microsoft\\Windows\\
  WER``), sometimes with a small ``.mdmp`` minidump next to it.
* **The Event Log** keeps an "Application Error" (1000), "Application Hang"
  (1002) or ".NET Runtime" (1026) event for each.
* **CrashDumps** (``%LOCALAPPDATA%\\CrashDumps``) holds full dumps when they
  are turned on.

Only reports for the programs a GAMMA player runs are taken (the game,
Mod Organizer, COMMANDER, the CLI, Lossless Scaling), never other apps'.
Both sources are read by position, not by their field names, which Windows
translates into the system language.

:func:`crash_lines` boils every report and event down to one line in the
same plain format, which the ASSISTANT reads (assistant/analyzers/
windows_crashes.py); :func:`windows_facts` is the Windows section of
diagnostics.txt.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Image names (lower case, prefixes) whose crashes belong in a dump.
PROGRAMS = ("anomaly", "xr_3da", "modorganizer", "commander", "stalker-gamma", "losslessscaling", "lossless")
#: Reports older than this are left out: they predate the problem.
MAX_AGE_DAYS = 60
MAX_REPORTS = 12
MAX_DUMPS = 3
#: Larger minidumps are listed, not packed (a heap dump is gigabytes).
MAX_DUMP_BYTES = 64_000_000

_EVENT_RE = re.compile(r"<Event\b.*?</Event>", re.DOTALL)
_DATA_RE = re.compile(r"<Data(?:\s[^>]*)?>(.*?)</Data>|<Data(?:\s[^>]*)?/>", re.DOTALL)
_EVENT_ID_RE = re.compile(r"<EventID[^>]*>(\d+)</EventID>")
_TIME_RE = re.compile(r"<TimeCreated SystemTime='([^']+)'")


def is_game_program(name: str) -> bool:
    """True for an image name (``AnomalyDX11AVX.exe``) COMMANDER cares about."""
    return name.strip().lower().startswith(PROGRAMS)


@dataclass(frozen=True)
class Crash:
    when: str  # "2026-11-20 21:03" (UTC)
    kind: str  # "crash", "hang" or "dotnet"
    app: str
    module: str
    code: str  # "c0000005" (lower case, no 0x)
    source: str  # "WER" or "Event Log"

    def line(self) -> str:
        return (
            f"{self.when}  {self.kind:<6}  app={self.app}  module={self.module or '-'}  "
            f"code={self.code or '-'}  source={self.source}"
        )


def _normal_code(value: str) -> str:
    """Windows writes exception codes in hex, with or without 0x; an
    all-digit code such as 80000003 is still hex. Only a negative number
    is a decimal (a signed NTSTATUS)."""
    value = value.strip().lower().removeprefix("0x")
    if value.startswith("-") and value[1:].isdigit():
        value = f"{int(value) & 0xFFFFFFFF:08x}"
    return value


# ------------------------------------------------------------ WER reports
def _wer_roots() -> list[Path]:
    roots = []
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramData")):
        if base:
            for name in ("ReportArchive", "ReportQueue"):
                roots.append(Path(base) / "Microsoft" / "Windows" / "WER" / name)
    return roots


def _report_program(folder_name: str) -> str:
    """``AppCrash_AnomalyDX11AVX._1f2e..._id`` -> ``AnomalyDX11AVX.``"""
    parts = folder_name.split("_")
    return parts[1] if len(parts) > 2 else ""


def wer_report_folders(roots: list[Path] | None = None, now: float | None = None) -> list[Path]:
    """The newest crash/hang report folders for the game's programs."""
    cutoff = (now or time.time()) - MAX_AGE_DAYS * 86400
    found: list[tuple[float, Path]] = []
    for root in _wer_roots() if roots is None else roots:
        try:
            folders = list(root.iterdir())
        except OSError:
            continue
        for folder in folders:
            if not folder.name.lower().startswith(("appcrash_", "apphang_", "clr20r3_")):
                continue
            if not is_game_program(_report_program(folder.name)):
                continue
            try:
                mtime = folder.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff and (folder / "Report.wer").is_file():
                found.append((mtime, folder))
    found.sort(key=lambda item: item[0], reverse=True)
    return [folder for _mtime, folder in found[:MAX_REPORTS]]


def read_report(path: Path) -> str:
    """A Report.wer as text (UTF-16 with a BOM, or UTF-8 in older ones)."""
    data = path.read_bytes()
    for encoding in ("utf-16", "utf-8-sig"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "=" in text:
            return text
    return data.decode("utf-8", errors="replace")


def _filetime(value: str) -> str:
    try:
        stamp = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(value) // 10)
    except (ValueError, OverflowError):
        return "?"
    return stamp.strftime("%Y-%m-%d %H:%M")


def parse_report(text: str) -> Crash | None:
    """The crash a Report.wer describes. Sig[n] are read by position (their
    names are translated): 0 app, 3 fault module and 6 the exception code
    for a crash; a hang has no module or code."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    event = values.get("EventType", "").upper()
    app = values.get("Sig[0].Value", "")
    if not app:
        return None
    if event.startswith("APPCRASH"):
        kind, module, code = "crash", values.get("Sig[3].Value", ""), values.get("Sig[6].Value", "")
    elif event in ("BEX", "BEX64"):  # a blocked buffer overrun: Sig[6] is the offset there
        kind, module, code = "crash", values.get("Sig[3].Value", ""), values.get("Sig[7].Value", "")
    elif event.startswith("APPHANG"):
        kind, module, code = "hang", "", ""
    elif event.startswith("CLR20R3"):  # .NET (the CLI): Sig[7] is the exception type
        kind, module, code = "dotnet", values.get("Sig[3].Value", ""), values.get("Sig[7].Value", "")
    else:
        return None
    return Crash(_filetime(values.get("EventTime", "")), kind, app, module, _normal_code(code), "WER")


# -------------------------------------------------------------- Event Log
def _events_xml(days: int = MAX_AGE_DAYS) -> str:
    """Recent Application Error / Hang / .NET Runtime events as XML."""
    if os.name != "nt":
        return ""
    query = (
        "*[System[(EventID=1000 or EventID=1002 or EventID=1026) and "
        f"TimeCreated[timediff(@SystemTime) <= {days * 86_400_000}]]]"
    )
    root = os.environ.get("SystemRoot", r"C:\Windows")
    try:
        result = subprocess.run(
            [os.path.join(root, "System32", "wevtutil.exe"), "qe", "Application", f"/q:{query}",
             "/f:xml", "/rd:true", "/c:300"],
            capture_output=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def _unescape(value: str) -> str:
    return value.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def parse_events(xml: str) -> list[Crash]:
    """Game-program crashes in wevtutil's XML. EventData is positional:
    1000 is app, version, timestamp, module, version, timestamp, code...;
    1002 starts with the app; 1026 is one block of text naming the app."""
    crashes = []
    for block in _EVENT_RE.findall(xml):
        event_id = _EVENT_ID_RE.search(block)
        when = _TIME_RE.search(block)
        data = [_unescape(match or "") for match in _DATA_RE.findall(block)]
        if not event_id or not data:
            continue
        stamp = when.group(1)[:16].replace("T", " ") if when else "?"
        number = event_id.group(1)
        if number == "1000" and len(data) > 6 and is_game_program(data[0]):
            crashes.append(Crash(stamp, "crash", data[0], data[3], _normal_code(data[6]), "Event Log"))
        elif number == "1002" and is_game_program(data[0]):
            crashes.append(Crash(stamp, "hang", data[0], "", "", "Event Log"))
        elif number == "1026":
            text = data[0]
            app = re.search(r"([\w.-]+\.exe)", text)
            code = re.search(r"\b(?:0x)?(e0434352|c0000005|c0000409|[0-9a-f]{8})\b", text, re.IGNORECASE)
            if app and is_game_program(app.group(1)):
                crashes.append(Crash(stamp, "dotnet", app.group(1), "", _normal_code(code.group(1)) if code else "", "Event Log"))
    return crashes


# -------------------------------------------------------------- the dump
def crash_dump_files(folder: Path | None = None, now: float | None = None) -> list[Path]:
    """Newest dumps in %LOCALAPPDATA%\\CrashDumps for the game's programs."""
    if folder is None:
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            return []
        folder = Path(base) / "CrashDumps"
    cutoff = (now or time.time()) - MAX_AGE_DAYS * 86400
    try:
        dumps = [p for p in folder.iterdir() if p.suffix.lower() == ".dmp" and is_game_program(p.name)]
    except OSError:
        return []
    dated = []
    for path in dumps:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            dated.append((mtime, path))
    dated.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in dated[:MAX_DUMPS]]


def dump_entries() -> tuple[dict[str, str], dict[str, Path]]:
    """(texts, files) for the log dump's ``windows/`` folder.

    ``crash-reports.txt`` is one line per crash, newest first, which the
    ASSISTANT reads; ``wer/`` holds each Report.wer as text for a human.
    Files are the minidumps and CrashDumps, packed as they are.
    """
    texts: dict[str, str] = {}
    files: dict[str, Path] = {}
    crashes: list[Crash] = []
    for folder in wer_report_folders():
        try:
            text = read_report(folder / "Report.wer")
        except OSError:
            continue
        texts[f"wer/{folder.name}.txt"] = text
        if crash := parse_report(text):
            crashes.append(crash)
        try:
            minidumps = [p for p in folder.iterdir() if p.suffix.lower() == ".mdmp"]
        except OSError:
            minidumps = []
        for dump in minidumps:
            files[f"wer/{folder.name}-{dump.name}"] = dump
    # The Event Log lists most crashes WER reported too: keep one of each.
    seen = {(c.when, c.kind, c.app.lower(), c.module.lower(), c.code) for c in crashes}
    for crash in parse_events(_events_xml()):
        key = (crash.when, crash.kind, crash.app.lower(), crash.module.lower(), crash.code)
        if key not in seen:
            seen.add(key)
            crashes.append(crash)
    for path in crash_dump_files():
        files[f"crashdumps/{path.name}"] = path
    crashes.sort(key=lambda crash: crash.when, reverse=True)
    header = [
        "Crashes and hangs Windows recorded for the game's programs",
        f"(last {MAX_AGE_DAYS} days, from Windows Error Reporting and the Event Log)",
        "",
    ]
    texts["crash-reports.txt"] = "\n".join(header + ([c.line() for c in crashes] or ["none"])) + "\n"
    return texts, files


# ----------------------------------------------------------- diagnostics
def _registry(root, key: str, name: str):
    import winreg

    try:
        with winreg.OpenKey(root, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return None


def _windows_version() -> str:
    import winreg

    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    get = lambda name: _registry(winreg.HKEY_LOCAL_MACHINE, key, name)
    build = str(get("CurrentBuild") or "?")
    product = str(get("ProductName") or "Windows")
    # ProductName still says "Windows 10" on Windows 11.
    if build.isdigit() and int(build) >= 22000:
        product = product.replace("Windows 10", "Windows 11")
    ubr = get("UBR")
    return f"{product} {get('DisplayVersion') or get('ReleaseId') or ''} (build {build}{f'.{ubr}' if ubr is not None else ''})".replace("  ", " ")


def _drive_facts(path: str) -> str:
    import ctypes
    import shutil

    drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    fs = ctypes.create_unicode_buffer(32)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(drive, None, 0, None, None, None, fs, 32)
    kinds = {2: "removable", 3: "local", 4: "network", 5: "CD", 6: "RAM disk"}
    kind = kinds.get(ctypes.windll.kernel32.GetDriveTypeW(drive), "unknown")
    try:
        free = f"{shutil.disk_usage(drive).free / 2**30:.0f} GB free"
    except OSError:
        free = "free space unknown"
    return f"{drive} {fs.value if ok else '?'}, {kind}, {free}"


def _compat_flags(exes: list[str]) -> list[str]:
    """Compatibility-mode settings ("Run as administrator", "Windows 8"...)
    on the game's or MO2's exe: MO2 and its virtual file system break when
    only one of them runs elevated or in a compatibility mode."""
    import winreg

    found = []
    key = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
    for root, label in ((winreg.HKEY_CURRENT_USER, "user"), (winreg.HKEY_LOCAL_MACHINE, "all users")):
        for exe in exes:
            value = _registry(root, key, exe)
            if value:
                found.append(f"{Path(exe).name} ({label}): {value}")
    return found


def windows_facts(anomaly: str = "", gamma: str = "", cache: str = "") -> list[str]:
    """The Windows section of diagnostics.txt: the settings that decide
    whether GAMMA installs and runs."""
    if os.name != "nt":
        return []
    from . import gui_settings, wingpu, winpagefile, winprep, winruntimes, winsecurity

    lines = []

    def add(label: str, compute) -> None:
        try:
            lines.append(f"{label}: {compute()}")
        except Exception as exc:  # noqa: BLE001 - one unreadable fact must not lose the rest
            lines.append(f"{label}: ? ({exc.__class__.__name__})")

    import winreg

    add("Windows", _windows_version)
    add("Running as administrator", lambda: "yes" if _is_admin() else "no")
    add("UAC", lambda: "off" if _registry(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "EnableLUA") == 0 else "on")

    def page_file() -> str:
        setting = winpagefile.page_file()
        ram = winpagefile.ram_mb()
        if setting is None:
            text = "unknown"
        elif setting.managed:
            text = "system managed"
        elif setting.off:
            text = "OFF"
        else:
            text = f"fixed, up to {setting.max_mb} MB"
        warn = "  << too small for GAMMA" if winpagefile.too_small(setting, ram) else ""
        return f"{text}, RAM {ram} MB installed{warn}"

    add("Page file", page_file)
    sac = {None: "not present", 0: "off", 1: "ON (blocks unsigned programs)", 2: "evaluation"}
    add("Smart App Control", lambda: sac.get(winsecurity.smart_app_control_state(), "?"))
    add("Long paths", lambda: "on" if winprep.long_paths_enabled() else "off")
    add("Game folders in OneDrive", lambda: winsecurity.onedrive_folder(anomaly, gamma, cache) or "no")
    state = gui_settings.load_gui_settings()
    add("Defender exclusions COMMANDER added", lambda: len(state.get("defender_excluded") or []))
    add("Missing runtimes", lambda: ", ".join(r.label for r in winruntimes.missing()) or "none")
    for label, folder in (("Anomaly drive", anomaly), ("GAMMA drive", gamma)):
        if folder:
            add(label, lambda folder=folder: _drive_facts(folder))
    exes = wingpu.game_exes(anomaly)
    add("Graphics card choice", lambda: ", ".join(f"{Path(e).name}={wingpu.preference(e) or 'Windows default'}" for e in exes) or "no game exes found")
    mo2 = str(Path(gamma) / "ModOrganizer.exe") if gamma else ""
    add("Compatibility settings", lambda: "; ".join(_compat_flags([*exes, *([mo2] if mo2 else [])])) or "none")
    return lines


def _is_admin() -> bool:
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False
