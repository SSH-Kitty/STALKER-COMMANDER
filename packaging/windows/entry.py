"""PyInstaller entry point for the Windows build.

COMMANDER starts helper programs as ``sys.executable -m <module>`` (ASSISTANT,
for one). Frozen, ``sys.executable`` is COMMANDER.exe itself, so that form
is handled here by running the module, the way ``python -m`` would.
"""

import os
import runpy
import sys

if __name__ == "__main__":
    # Fresh Windows installs only hold the root certificates they have already
    # needed, so Python's TLS rejects github.com. Use the CA bundle that ships with the CLI.
    _ca = os.path.join(getattr(sys, "_MEIPASS", ""), "cli", "win-x64", "cacert.pem")
    if os.path.isfile(_ca):
        os.environ.setdefault("SSL_CERT_FILE", _ca)
    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        module = sys.argv[2]
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        sys.exit(0)
    from commander_gui.main import main

    sys.exit(main())
