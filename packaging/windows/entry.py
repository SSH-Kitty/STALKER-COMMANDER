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

if __name__ == "__main__":
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
