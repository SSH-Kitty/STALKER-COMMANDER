"""Analyzer for the crashes Windows itself recorded (windows/crash-reports.txt).

COMMANDER's Windows log dump boils every Windows Error Reporting report and
Event Log crash of the game's programs down to one line
(commander_gui/wincrash.py)::

    2026-11-20 21:03  crash   app=AnomalyDX11AVX.exe  module=nvwgf2umx.dll  code=c0000005  source=WER

The module a program crashed in says most about the cause: the graphics
driver, an overlay hooked into the game, or the engine itself (then a mod
or script, and the engine log usually names it).
"""

from __future__ import annotations

import re

from commander_gui.i18n import tr

from .. import knowledge
from ..findings import (
    CATEGORY_GAME,
    CATEGORY_INFO,
    CATEGORY_INSTALL,
    CATEGORY_MO2,
    CATEGORY_SYSTEM,
    Finding,
    Severity,
)
from .common import FindingFactory

_LINE_RE = re.compile(
    r"^(?P<when>\S+ \S+)\s+(?P<kind>crash|hang|dotnet)\s+app=(?P<app>\S+)\s+"
    r"module=(?P<module>\S+)\s+code=(?P<code>\S+)\s+source=(?P<source>.+)$"
)

#: Graphics driver and Direct3D modules (lower-case prefixes).
_GPU_MODULES = (
    "nvwgf2um", "nvd3dum", "nvldumd", "nvoglv", "nvapi",  # NVIDIA
    "atidxx", "atiumd", "amdxx", "aticfx", "amdihk",  # AMD
    "igd10", "igd11", "igd12", "igdumd", "igc64", "igdgmm",  # Intel
    "d3d11.dll", "d3d9.dll", "d3d10", "dxgi.dll", "d3dcompiler",
)
#: Overlays and capture tools that inject into the game (any part of the name).
_OVERLAY_MODULES = (
    "rtsshooks", "rtsshooksloader", "gameoverlayrenderer", "discordhook", "nvspcap",
    "graphics-hook", "obs-", "fraps", "mirillis", "action_x64", "bdcamvk", "overlay",
    "reshade", "ow-graphics", "medal-hook",
)
#: The X-Ray engine's own modules and its Lua.
_ENGINE_MODULES = ("xrgame", "xrcore", "xrrender", "xrengine", "xr_3da", "xrphysics", "xrapi", "xrscripts", "xrcdb", "xrsound", "luajit", "lua51", "anomaly")


def _starts(module: str, prefixes: tuple[str, ...]) -> bool:
    return module.lower().startswith(prefixes)


def _contains(module: str, names: tuple[str, ...]) -> bool:
    lowered = module.lower()
    return any(name in lowered for name in names)


def _category(app: str) -> str:
    lowered = app.lower()
    if lowered.startswith("modorganizer"):
        return CATEGORY_MO2
    if lowered.startswith("stalker-gamma"):
        return CATEGORY_INSTALL
    if lowered.startswith(("anomaly", "xr_3da")):
        return CATEGORY_GAME
    return CATEGORY_SYSTEM


def analyze_windows_crashes(arcname: str, where: str, lines: list[str]) -> list[Finding]:
    """One finding per kind of crash (program, module, code), counted."""
    factory = FindingFactory(arcname, where)
    groups: dict[tuple[str, str, str, str], list[tuple[int, str]]] = {}
    for index, line in enumerate(lines):
        match = _LINE_RE.match(line.strip())
        if match:
            key = (match["kind"], match["app"], match["module"], match["code"])
            when = match["when"]
            # One crash listed by both sources counts once.
            if all(seen_when != when for _index, seen_when in groups.get(key, [])):
                groups.setdefault(key, []).append((index, when))
    if not groups:
        return [
            factory.make(
                Severity.OK,
                CATEGORY_INFO,
                tr("Windows recorded no crashes of the game's programs."),
                None,
                detail=tr("Windows keeps a report for every program that crashes or stops responding; none of them is the game, Mod Organizer, COMMANDER or the installer."),
                suggestion=tr("Nothing to do. This is normal."),
            )
        ]
    findings = []
    for (kind, app, module, code), seen in groups.items():
        first_index = seen[0][0]
        newest = max(when for _index, when in seen)
        count_text = tr("Windows recorded this {count} time(s), last on {when} (UTC).", count=len(seen), when=newest)
        meaning = knowledge.windows_crash_code(code)
        module = "" if module == "-" else module
        code = "" if code == "-" else code
        category = _category(app)
        severity = Severity.ERROR
        if kind == "hang":
            severity = Severity.WARNING
            title = tr("{app} stopped responding.", app=app)
            suggestion = tr("If it happens while loading, give it a few minutes: GAMMA loads slowly. If it freezes during play, check the game engine log in this dump.")
        elif kind == "dotnet":
            title = tr("{app} crashed with a .NET error ({code}).", app=app, code=code or "?")
            suggestion = tr("Check the logs in this dump from the same time. If it keeps happening, share this dump on the GAMMA or COMMANDER Discord.")
        elif module and _starts(module, _GPU_MODULES):
            category = CATEGORY_SYSTEM
            title = tr("{app} crashed inside the graphics driver ({module}).", app=app, module=module)
            suggestion = tr("Update your graphics driver, or reinstall it cleanly, and undo any graphics card overclock. If it keeps happening, lower the graphics settings.")
        elif module and _contains(module, _OVERLAY_MODULES):
            title = tr("{app} crashed inside an overlay ({module}).", app=app, module=module)
            suggestion = tr("Turn that overlay off (MSI Afterburner / RivaTuner, Steam, Discord, ReShade or a screen recorder) and play again.")
        elif module and _starts(module, _ENGINE_MODULES):
            title = tr("{app} crashed inside the game engine ({module}).", app=app, module=module)
            suggestion = tr("Engine crashes usually come from a mod, a script or a broken save. The game engine log in this dump often names the cause just before the crash. COMMANDER's Install page has Verify Integrity, which can repair broken game files.")
        else:
            title = tr("{app} crashed ({module}, code {code}).", app=app, module=module or "?", code=code or "?")
            suggestion = tr("Check the logs in this dump from the same time. If it keeps happening, share this dump on the GAMMA or COMMANDER Discord.")
        if knowledge.is_out_of_memory(code):
            suggestion = knowledge.out_of_memory_fix()
        findings.append(
            factory.make(
                severity,
                category,
                title,
                first_index + 1,
                detail=" ".join(part for part in (meaning, count_text) if part),
                suggestion=suggestion,
                excerpt_text="\n".join(lines[index] for index, _when in seen[:5]),
                technical=f"app={app} module={module or '-'} code={code or '-'}",
                dedup_key=f"wincrash:{kind}:{app}:{module}:{code}",
            )
        )
    return findings
