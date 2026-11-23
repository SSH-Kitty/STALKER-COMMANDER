# PyInstaller spec for the ModDB browser plugin on Windows (one-folder, no
# console). The Windows counterpart of build-moddb-plugin.sh: COMMANDER.exe
# leaves QtWebEngine (a whole Chromium) out, so the browser ships on its own.
# Build from the repo root on Windows, then pack it:
#   pyinstaller --noconfirm packaging/windows/moddb-browser.spec
#   python packaging/windows/pack_moddb_plugin.py
# Output: dist/moddb-browser/moddb-browser.exe
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
    """The Details tab, as on COMMANDER.exe: a blank one is one more
    "unknown program" signal to antivirus heuristics."""
    text = (ROOT / "commander_gui" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE).group(1)
    numbers = [int(n) for n in re.findall(r"\d+", version)[:4]]
    numbers += [0] * (4 - len(numbers))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(numbers), prodvers=tuple(numbers)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "SSH-Kitty"),
                StringStruct("FileDescription", "STALKER COMMANDER ModDB Browser"),
                StringStruct("FileVersion", version),
                StringStruct("InternalName", "moddb-browser"),
                StringStruct("LegalCopyright", "Copyright (C) SSH-Kitty. GPL-3.0-or-later."),
                StringStruct("OriginalFilename", "moddb-browser.exe"),
                StringStruct("ProductName", "STALKER COMMANDER"),
                StringStruct("ProductVersion", version),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def submodules(package):
    """Every module under ``package``, from the files on disk (see commander.spec)."""
    for path in (ROOT / package).rglob("*.py"):
        parts = path.relative_to(ROOT).with_suffix("").parts
        yield ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "windows" / "moddb_entry.py")],
    pathex=[str(ROOT)],
    # The browser's icon (moddb_browser.py finds it next to itself) and the
    # Exo 2 font COMMANDER's stylesheet names. None of the app's artwork.
    datas=[
        (str(ROOT / "commander_gui" / "assets" / "moddb-browser.png"), "commander_gui/assets"),
        (str(ROOT / "commander_gui" / "fonts" / "Exo2-Variable.ttf"), "commander_gui/fonts"),
        (str(ROOT / "LICENSE"), "."),
        *([(str(ROOT / "licenses"), "licenses")] if (ROOT / "licenses").is_dir() else []),
    ],
    # The languages are loaded by name (i18n).
    hiddenimports=list(submodules("commander_gui/locales")),
    # Qt modules nothing here imports. QtWebEngine's own DLL dependencies
    # (Qt6Quick, Qt6Qml...) are still collected: this only drops the Python
    # bindings and what only they pull in.
    excludes=[
        "Steamdeck", "assistant", "tkinter",
        "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml",
        "PySide6.QtPdf", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtGraphs", "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtMultimedia",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    ],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    # UTF-8 mode, as COMMANDER.exe: the JSON lines on stdin/stdout carry
    # file names in any script.
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="moddb-browser",
    icon=str(ROOT / "commander_gui" / "assets" / "moddb-browser.png"),
    version=version_info(),
    console=False,
    upx=False,  # UPX-packed exes are a classic antivirus false positive
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="moddb-browser")  # noqa: F821
