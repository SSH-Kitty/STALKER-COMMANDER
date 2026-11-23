"""PyInstaller entry point for the ModDB browser plugin on Windows.

Runs ``commander_gui.moddb_browser`` the way the Linux plugin's launcher
script runs ``python -m commander_gui.moddb_browser``, with the few things
only Windows needs set up first. Kept out of moddb_browser.py itself: a
change there means a new plugin PROTOCOL, and with it an update every
installed Linux plugin would ask for.

``--self-test`` (used by CI and the pack script): reads one line on stdin,
loads a page in a real QtWebEngine view, and reports both on stdout as one
JSON line. That proves what a windowless exe can't be assumed to do: talk
to COMMANDER over its pipes, and start Chromium's QtWebEngineProcess.
"""

import json
import sys

#: Taskbar identity: its own button, not grouped under COMMANDER's.
APP_USER_MODEL_ID = "SSH-Kitty.STALKER-COMMANDER.ModDBBrowser"


def _set_app_user_model_id() -> None:
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass  # only the taskbar grouping depends on it


def _self_test() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    import commander_gui.moddb_browser  # noqa: F401 - everything the browser imports is bundled

    line = sys.stdin.readline().strip() if sys.stdin else ""
    app = QApplication(sys.argv)
    view = QWebEngineView()
    result = {
        "event": "self-test",
        "stdin": line,
        "page": False,
        # The JSON lines carry file names in any script: UTF-8 mode must be
        # on (the spec's "X utf8" option), not the ANSI code page.
        "utf8_mode": sys.flags.utf8_mode,
    }

    def loaded(ok: bool) -> None:
        result["page"] = bool(ok)
        app.quit()

    view.loadFinished.connect(loaded)
    view.setHtml("<p>COMMANDER</p>")
    QTimer.singleShot(60_000, app.quit)
    app.exec()
    print(json.dumps(result), flush=True)
    return 0 if result["page"] and line else 1


def main() -> int:
    _set_app_user_model_id()
    if "--self-test" in sys.argv[1:]:
        return _self_test()

    from commander_gui import fonts, moddb_browser

    # The Linux .desktop entry (taskbar name and icon there): not a Windows
    # thing; the exe carries its own name and icon.
    moddb_browser._write_desktop_entry = lambda: None
    apply_theme = moddb_browser._apply_theme

    def apply_theme_with_font(app, config) -> None:
        # COMMANDER's stylesheet names Exo 2, which Windows doesn't have.
        fonts.load_bundled_font()
        apply_theme(app, config)

    moddb_browser._apply_theme = apply_theme_with_font
    return moddb_browser.main()


if __name__ == "__main__":
    sys.exit(main())
