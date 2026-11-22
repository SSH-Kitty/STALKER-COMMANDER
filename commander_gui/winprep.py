"""Windows: get the install folders ready before the CLI touches them.

The CLI's Windows setup (``--add-folders-to-win-defender-exclusion``,
``--enable-long-paths`` and the ``gamma\\downloads`` -> cache symlink it
always makes) runs as one PowerShell call that needs administrator rights.
COMMANDER runs as the user, so that call fails and the whole install stops
before anything is downloaded. So COMMANDER does that part itself first:

* the ``downloads`` link as a directory junction, which needs no admin
  rights; the CLI finds it there and skips its own symlink step;
* the Defender exclusion and long paths in one elevated PowerShell (one UAC
  prompt), only for what isn't done yet. The two flags are never passed to
  the CLI: without admin rights they make it stop.

A junction only reaches a local drive. A cache on a network share gets a
real symlink instead, made elevated with the rest.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

#: The folder in GAMMA the CLI expects to lead to the download cache.
DOWNLOADS = "downloads"

_LONG_PATHS_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"

# Bits of the elevated script's exit code, one per step that failed.
EXCLUSION_FAILED = 1
LONG_PATHS_FAILED = 2
NO_DEFENDER = 4  # Defender isn't the active antivirus
LINK_FAILED = 8


def is_link(path: str | Path) -> bool:
    """A symlink or a junction: a folder to step over, never walk into."""
    path = Path(path)
    return path.is_symlink() or path.is_junction()


def norm(path: str | Path) -> str:
    """*path* as compared and saved: absolute, Windows case-folded."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def link_needed(gamma: str | Path, cache: str | Path) -> bool:
    """``gamma\\downloads`` is missing and the cache is somewhere else.

    Anything already there (a folder, a link, even a broken one) is left
    alone, as the CLI does. A cache that *is* ``gamma\\downloads`` (the old
    Windows layout) needs no link."""
    link = Path(gamma).expanduser() / DOWNLOADS
    return not os.path.lexists(link) and norm(cache) != norm(link)


def make_downloads_link(gamma: str | Path, cache: str | Path) -> bool:
    """Link ``gamma\\downloads`` to the cache without admin rights.

    A junction first; a symlink when that fails (Developer Mode allows one
    without admin rights). False when neither works, so the elevated step
    has to make it. Both folders must exist already."""
    if not link_needed(gamma, cache):
        return True
    link = str(Path(gamma).expanduser() / DOWNLOADS)
    target = os.path.abspath(os.path.expanduser(str(cache)))
    import _winapi

    try:
        _winapi.CreateJunction(target, link)
        return True
    except OSError:
        pass
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def long_paths_enabled() -> bool:
    """Windows' long path support is on (readable without admin rights)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _LONG_PATHS_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False
    return value == 1


@dataclass(frozen=True)
class Plan:
    """What the elevated step does; each part only when it is still needed."""

    exclude: tuple[str, ...] = ()  # folders for Defender's exclusion list
    long_paths: bool = False
    link: tuple[str, str] | None = None  # (link, target) symlink to make

    def __bool__(self) -> bool:
        return bool(self.exclude or self.long_paths or self.link)


def plan(
    folders: list[str],
    excluded: list[str],
    *,
    wanted: bool,
    long_paths_on: bool,
    link: tuple[str, str] | None = None,
) -> Plan:
    """The elevated step for *folders*, leaving out what is done already.

    *wanted* is the Install page's Defender/long paths option. *excluded* is
    what an earlier run saved as excluded (Windows only shows the exclusion
    list to administrators). *link* is a symlink no junction could replace."""
    exclude: list[str] = []
    if wanted:
        seen = {norm(p) for p in excluded}
        for folder in folders:
            if norm(folder) not in seen:
                seen.add(norm(folder))
                exclude.append(os.path.abspath(os.path.expanduser(folder)))
    return Plan(tuple(exclude), wanted and not long_paths_on, link)


def _quote(text: str) -> str:
    """A PowerShell single-quoted string. PowerShell also ends one at the
    typographic single quotes, so those are doubled like the plain one."""
    for mark in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        text = text.replace(mark, mark * 2)
    return f"'{text}'"


def script(step: Plan) -> str:
    """The PowerShell for *step*. Its exit code holds one bit per failure."""
    lines = ["$r = 0"]
    if step.exclude:
        paths = ",".join(_quote(p) for p in step.exclude)
        lines += [
            "$mode = $null",
            f"try {{ $mode = (Get-MpComputerStatus -ErrorAction Stop).AMRunningMode }} catch {{ $r = $r -bor {NO_DEFENDER} }}",
            # Passive or EDR mode: another antivirus does the scanning.
            f"if (-not ($r -band {NO_DEFENDER}) -and $mode -and $mode -ne 'Normal') {{ $r = $r -bor {NO_DEFENDER} }}",
            f"if (-not ($r -band {NO_DEFENDER})) {{ try {{ Add-MpPreference -ExclusionPath @({paths}) -ErrorAction Stop }} catch {{ $r = $r -bor {EXCLUSION_FAILED} }} }}",
        ]
    if step.long_paths:
        lines.append(
            f"try {{ New-ItemProperty -Path {_quote('HKLM:' + chr(92) + _LONG_PATHS_KEY)} -Name LongPathsEnabled"
            f" -Value 1 -PropertyType DWord -Force -ErrorAction Stop | Out-Null }} catch {{ $r = $r -bor {LONG_PATHS_FAILED} }}"
        )
    if step.link:
        link, target = step.link
        lines.append(
            f"try {{ New-Item -ItemType SymbolicLink -Path {_quote(link)} -Target {_quote(target)}"
            f" -ErrorAction Stop | Out-Null }} catch {{ $r = $r -bor {LINK_FAILED} }}"
        )
    lines.append("exit $r")
    return "\n".join(lines)


def powershell_args(step: Plan) -> tuple[str, ...]:
    """Arguments that run *step*'s script, passed whole on the command line:
    a script file in the user's temp folder could be swapped before the
    elevated PowerShell reads it."""
    encoded = base64.b64encode(script(step).encode("utf-16-le")).decode("ascii")
    return ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded)


def powershell_exe() -> str:
    """Windows PowerShell by its full path, never found through PATH."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


@dataclass(frozen=True)
class Result:
    declined: bool = False  # UAC was declined, so nothing ran
    exclusion_failed: bool = False
    no_defender: bool = False
    long_paths_failed: bool = False
    link_failed: bool = False


def result_for(code: int, step: Plan) -> Result:
    """Read the elevated PowerShell's exit code for *step*."""
    from .winruntimes import _DECLINED

    if code == _DECLINED:
        return Result(declined=True)
    if code < 0 or code > 15:
        # PowerShell itself failed: nothing can be trusted to have run.
        code = EXCLUSION_FAILED | LONG_PATHS_FAILED | LINK_FAILED
    return Result(
        exclusion_failed=bool(step.exclude and code & EXCLUSION_FAILED),
        no_defender=bool(step.exclude and code & NO_DEFENDER),
        long_paths_failed=bool(step.long_paths and code & LONG_PATHS_FAILED),
        link_failed=bool(step.link and code & LINK_FAILED),
    )


def apply(step: Plan) -> Result:
    """Run *step* as administrator: one UAC prompt, no window."""
    from .winruntimes import _run_elevated

    return result_for(_run_elevated(powershell_exe(), powershell_args(step), show=0), step)
