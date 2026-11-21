# PyInstaller spec for the Windows build (one-folder, no console).
# Build from the repo root on Windows:
#   python scripts/fetch_windows_cli.py
#   pyinstaller packaging/windows/commander.spec
# Output: dist/COMMANDER/COMMANDER.exe
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent.parent  # noqa: F821 - defined by PyInstaller

# project_root() is <bundle>/_internal, so data keeps its repo-relative path.
datas = [
    (str(ROOT / "commander_gui" / "assets"), "commander_gui/assets"),
    (str(ROOT / "commander_gui" / "fonts"), "commander_gui/fonts"),
    (str(ROOT / "cli" / "win-x64"), "cli/win-x64"),
    (str(ROOT / "cli" / "stalker-gamma.png"), "cli"),
    (str(ROOT / "assistant"), "assistant"),
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
        for name in collect_submodules("commander_gui") + collect_submodules("assistant")
        if name != "commander_gui.moddb_browser"
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
    [("X utf8_mode=1", None, "OPTION")],
    exclude_binaries=True,
    name="COMMANDER",
    icon=str(ROOT / "cli" / "stalker-gamma.png"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="COMMANDER")  # noqa: F821
