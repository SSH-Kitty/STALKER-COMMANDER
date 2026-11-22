"""Pack the Windows ModDB browser plugin for a GitHub release.

The Windows half of build-moddb-plugin.sh, run after
``pyinstaller packaging/windows/moddb-browser.spec``:

1. prunes what the browser never loads (as the Linux build does),
2. runs the plugin's self-test - pipes and Chromium both work - as a gate,
3. writes ``plugin.json``,
4. packs ``COMMANDER-ModDB-Browser-Plugin-windows-x86_64.tar.xz`` and its
   ``.sha512sum``, the format commander_gui/plugins.py downloads and checks.

Usage (repo root):  python packaging/windows/pack_moddb_plugin.py [output-dir]
"""

from __future__ import annotations

import hashlib
import json
import lzma
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "dist" / "moddb-browser"
EXE = "moddb-browser.exe"
#: Chromium's own UI languages kept: COMMANDER's languages, as on Linux.
KEEP_LOCALES = {
    "en-US", "en-GB", "de", "es", "es-419", "fr", "pl", "pt-BR", "pt-PT", "ro", "ru", "tr", "uk",
}

sys.path.insert(0, str(ROOT))
from commander_gui import __version__
from commander_gui.plugins import PROTOCOL, asset_name


def die(message: str) -> None:
    sys.exit(f"ERROR: {message}")


def prune(qt: Path) -> None:
    removed = 0

    def drop(path: Path) -> None:
        nonlocal removed
        removed += path.stat().st_size
        path.unlink()

    # Debug builds of Chromium's packs (only a debug Qt loads them), and the
    # developer tools (never opened here).
    for path in (qt / "resources").glob("*.debug.*"):
        drop(path)
    devtools = qt / "resources" / "qtwebengine_devtools_resources.pak"
    if devtools.is_file():
        drop(devtools)
    # Qt's own translations aren't loaded; Chromium's only for COMMANDER's languages.
    for path in (qt / "translations").glob("*.qm"):
        drop(path)
    for path in (qt / "translations" / "qtwebengine_locales").glob("*.pak"):
        if path.stem not in KEEP_LOCALES:
            drop(path)
    print(f"==> pruned {removed / 2**20:.0f} MB")


def self_test(exe: Path) -> None:
    """Fails the build unless the windowless exe reads stdin (UTF-8), loads a
    page through QtWebEngineProcess and answers on stdout."""
    probe = json.dumps({"probe": "Δ"}, ensure_ascii=False)
    try:
        done = subprocess.run(
            [str(exe), "--self-test"],
            input=(probe + "\n").encode("utf-8"),
            capture_output=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        die("the plugin's self-test hung")
    out = done.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        result = json.loads(out[-1])
    except (IndexError, ValueError):
        die(f"the plugin's self-test printed no result (exit {done.returncode}): {done.stderr[-2000:]!r}")
    print(f"==> self-test: {json.dumps(result)}")  # ASCII: CI consoles are cp1252
    if done.returncode != 0 or not result.get("page"):
        die("the plugin couldn't load a page")
    if result.get("stdin") != probe or result.get("utf8_mode") != 1:
        die("the plugin's stdin isn't read as UTF-8")


def qt_version() -> str:
    import PySide6

    return PySide6.__version__


def pack(output: Path) -> None:
    def tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if "__pycache__" in info.name.split("/"):
            return None
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mode = 0o755 if info.isdir() or info.name.endswith((".exe", ".dll", ".pyd")) else 0o644
        return info

    output.parent.mkdir(parents=True, exist_ok=True)
    # xz -9, as the Linux plugin: one 400 MB download per player outweighs
    # a few minutes of CI.
    with lzma.open(output, "wb", preset=9) as xz, tarfile.open(fileobj=xz, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for path in sorted(BUILD.rglob("*")):
            arcname = "moddb-browser/" + path.relative_to(BUILD).as_posix()
            tf.add(path, arcname=arcname, recursive=False, filter=tar_filter)
    digest = hashlib.sha512()
    with output.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    Path(f"{output}.sha512sum").write_text(f"{digest.hexdigest()}  {output.name}\n", encoding="ascii")
    print(f"==> {output} ({output.stat().st_size / 2**20:.0f} MB)")


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    exe = BUILD / EXE
    if not exe.is_file():
        die(f"{exe} not found - run pyinstaller packaging/windows/moddb-browser.spec first")
    if not re.fullmatch(r"COMMANDER-ModDB-Browser-Plugin-windows-[a-z0-9_]+\.tar\.xz", asset_name()):
        die(f"run this on Windows: the asset would be named {asset_name()}")
    prune(BUILD / "_internal" / "PySide6")
    self_test(exe)
    info = {"protocol": PROTOCOL, "qt": qt_version(), "version": __version__, "exec": EXE}
    (BUILD / "plugin.json").write_text(json.dumps(info) + "\n", encoding="utf-8")
    print(f"==> plugin.json: {info}")
    pack(out_dir / asset_name())


if __name__ == "__main__":
    main()
