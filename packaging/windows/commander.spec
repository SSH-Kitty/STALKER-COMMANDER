# PyInstaller spec for the Windows build (one-folder, no console).
# Build from the repo root on Windows:
#   python scripts/fetch_windows_cli.py
#   pyinstaller packaging/windows/commander.spec
# Output: dist/COMMANDER/COMMANDER.exe
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent.parent  # noqa: F821 - defined by PyInstaller


def version_info():
    """COMMANDER.exe's Details tab (company, product, version...).

    An exe without one is one more "unknown program" signal to antivirus
    heuristics, and Windows shows a blank Details tab for it.
    """
    text = (ROOT / "commander_gui" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE).group(1)
    numbers = [int(n) for n in re.findall(r"\d+", version)[:4]]
    numbers += [0] * (4 - len(numbers))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(numbers), prodvers=tuple(numbers)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "SSH-Kitty"),
                StringStruct("FileDescription", "STALKER COMMANDER"),
                StringStruct("FileVersion", version),
                StringStruct("InternalName", "COMMANDER"),
                StringStruct("LegalCopyright", "Copyright (C) SSH-Kitty. GPL-3.0-or-later."),
                StringStruct("OriginalFilename", "COMMANDER.exe"),
                StringStruct("ProductName", "STALKER COMMANDER"),
                StringStruct("ProductVersion", version),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def submodules(package):
    """Every module under ``package``, from the files on disk.

    Not PyInstaller's collect_submodules(): it imports the package in a
    separate process where the repo is not on sys.path, so it silently
    returned nothing and the dynamically loaded locales were left out.
    """
    for path in (ROOT / package).rglob("*.py"):
        parts = path.relative_to(ROOT).with_suffix("").parts
        yield ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


#: Modules never imported on Windows (Proton/Wine/Steam Deck), plus the ModDB
#: browser, which runs in the plugin's own interpreter (it brings QtWebEngine).
LINUX_ONLY = {
    "commander_gui.moddb_browser",
    "commander_gui.proton_installer",
    "commander_gui.ui.proton_manager",
    "commander_gui.winetricks",
}

# project_root() is <bundle>/_internal, so data keeps its repo-relative path.
datas = [
    (str(ROOT / "commander_gui" / "assets"), "commander_gui/assets"),
    (str(ROOT / "commander_gui" / "fonts"), "commander_gui/fonts"),
    (str(ROOT / "cli" / "win-x64"), "cli/win-x64"),
    (str(ROOT / "cli" / "stalker-gamma.png"), "cli"),
    # The whole package: assistant_launcher looks for assistant/__main__.py.
    # Bytecode caches from a local run stay out.
    *[
        (str(path), str(path.parent.relative_to(ROOT)).replace("\\", "/"))
        for path in (ROOT / "assistant").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ],
    (str(ROOT / "LICENSE"), "."),
]
if (ROOT / "licenses").is_dir():
    datas.append((str(ROOT / "licenses"), "licenses"))

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "windows" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # Loaded by name at runtime (i18n locales, ``-m assistant``).
    # moddb_browser runs in the ModDB plugin's own interpreter (it brings
    # QtWebEngine), never in COMMANDER's.
    hiddenimports=[
        name
        for name in [*submodules("commander_gui"), *submodules("assistant")]
        # moddb_browser: see above. The rest is Linux only (Proton, Wine,
        # the Steam Deck), never imported on Windows.
        if name not in LINUX_ONLY
    ],
    # Deck Mode is SteamOS-only; the Qt modules COMMANDER does not use add
    # ~250 MB (QtWebEngine alone is 150 MB).
    excludes=[
        "Steamdeck",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.QtOpenGL",
        "PySide6.QtPdf", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtGraphs", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    ],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    # UTF-8 mode: open() and pipes default to UTF-8 like on Linux, not the
    # ANSI code page (which cannot write names such as "Δ").
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="COMMANDER",
    icon=str(ROOT / "cli" / "stalker-gamma.png"),
    version=version_info(),
    console=False,
    upx=False,  # UPX-packed exes are a classic antivirus false positive
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="COMMANDER")  # noqa: F821
