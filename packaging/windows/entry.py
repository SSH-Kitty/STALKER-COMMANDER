"""PyInstaller entry point for the Windows build.

COMMANDER starts helper programs as ``sys.executable -m <module>`` (ASSISTANT,
for one). Frozen, ``sys.executable`` is COMMANDER.exe itself, so that form
is handled here by running the module, the way ``python -m`` would - for the
modules COMMANDER starts that way only, so COMMANDER.exe can't be used to
run any other code it happens to bundle.
"""

import os
import runpy
import sys

#: Every module COMMANDER runs as ``sys.executable -m <module>``.
HELPER_MODULES = frozenset({
    "assistant",  # ASSISTANT (assistant_launcher)
    "commander_gui",  # COMMANDER itself (autostart, relaunch)
    "commander_gui.ui.game_splash",
    "commander_gui.ui.graphics_window",
    "commander_gui.winjob",  # sends a CLI its Ctrl+C (cancel)
})

#: Windows 10 version 1809, the oldest Windows Qt 6 runs on. The installer
#: enforces the same (COMMANDER.iss MinVersion); this covers the portable zip.
MIN_WINDOWS_BUILD = 17763


def windows_too_old() -> bool:
    """True on a Windows older than :data:`MIN_WINDOWS_BUILD`.

    ``platform_version`` comes from kernel32.dll's own version, so a
    compatibility-mode shortcut ("Run this program for Windows 8") can't
    make a new Windows look old.
    """
    version = sys.getwindowsversion()
    major, _minor, build = getattr(version, "platform_version", (version.major, version.minor, version.build))
    return major < 10 or build < MIN_WINDOWS_BUILD


if __name__ == "__main__":
    if windows_too_old():
        # Before Qt is imported: on these Windows versions it fails with a
        # cryptic missing-function error instead.
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "STALKER COMMANDER needs Windows 10 version 1809 or newer, or Windows 11.\n\n"
            "Update Windows through Windows Update, then start COMMANDER again.",
            "STALKER COMMANDER",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)
    # Fresh Windows installs only hold the root certificates they have already
    # needed, so Python's TLS rejects github.com. Use the CA bundle that ships with the CLI.
    _ca = os.path.join(getattr(sys, "_MEIPASS", ""), "cli", "win-x64", "cacert.pem")
    if os.path.isfile(_ca) and "SSL_CERT_FILE" not in os.environ:
        os.environ["SSL_CERT_FILE"] = _ca
        # For COMMANDER's own Python only: config.child_environment() takes
        # it out again for git, the CLI, MO2 and the game, which have their
        # own certificate stores (a company's TLS proxy root, for one).
        os.environ["COMMANDER_BUNDLED_CA"] = _ca
    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        module = sys.argv[2]
        if module not in HELPER_MODULES:
            sys.exit(2)
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        sys.exit(0)
    from commander_gui.main import main

    sys.exit(main())
