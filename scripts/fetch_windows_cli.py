"""Download the Windows stalker-gamma CLI, 7-Zip and MinGit into cli/win-x64/.

The CLI is pinned to the same version the Linux build bundles, so both
builds parse identical CLI output. On a CLI bump, update CLI_VERSION and
CLI_SHA256 (the release page lists each asset's sha256 digest).

7-Zip (7z.exe + its own 7z.dll, in cli/win-x64/7zip/) is what Install Mod
unpacks archives with - the Windows twin of the Linux build's bundled 7zz.
It is taken from the official MSI: ``msiexec /a`` on Windows, or 7zz/7z
where this runs on Linux.

Usage: python scripts/fetch_windows_cli.py
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

CLI_VERSION = "1.41.1"
CLI_SHA256 = "e7fb059f564bb08da6dab0db98e3cb0f4ec5e47265a79794b4cbd6483c90b84e"
CLI_URL = (
    "https://github.com/FaithBeam/stalker-gamma-cli/releases/download/"
    f"{CLI_VERSION}/stalker-gamma%2Bwin.x64.zip"
)
SEVENZIP_VERSION = "26.05"
SEVENZIP_SHA256 = "3fe4ab99fa15629c652944309a2b2bc6e41fc933fc6b63f5df1ebcf3aea68263"
SEVENZIP_URL = (
    f"https://github.com/ip7z/7zip/releases/download/{SEVENZIP_VERSION}/"
    f"7z{SEVENZIP_VERSION.replace('.', '')}-x64.msi"
)
# MinGit: Git for Windows' minimal, portable build meant for bundling. The
# integrity repair, ROMEO and overlay restore shell out to git, and a fresh
# Windows has none. The sha256 is the digest GitHub lists for the release asset.
GIT_VERSION = "2.56.1"
GIT_TAG = f"v{GIT_VERSION}.windows.1"
GIT_SHA256 = "4a92850b6e917da4fd010669b614b55dccf5b0ec333a534cc4be4700dfefdafd"
GIT_URL = (
    f"https://github.com/git-for-windows/git/releases/download/{GIT_TAG}/"
    f"MinGit-{GIT_VERSION}-64-bit.zip"
)
ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "cli" / "win-x64"


def _download(url: str, sha256: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"SHA-256 mismatch for {url}: got {digest}, expected {sha256}")
    return data


def _fetch_cli() -> None:
    print(f"Downloading stalker-gamma {CLI_VERSION} (win-x64)")
    data = _download(CLI_URL, CLI_SHA256)
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not (DEST / name).resolve().is_relative_to(DEST):
                raise SystemExit(f"Refusing unsafe path in archive: {name}")
        archive.extractall(DEST)
    # Some releases nest everything in one top-level folder; flatten it.
    exe = next(DEST.rglob("stalker-gamma.exe"), None)
    if exe is None:
        raise SystemExit("stalker-gamma.exe not found in the archive")
    if exe.parent != DEST:
        for item in list(exe.parent.iterdir()):
            shutil.move(str(item), DEST / item.name)


def _fetch_7zip() -> None:
    print(f"Downloading 7-Zip {SEVENZIP_VERSION} (x64)")
    data = _download(SEVENZIP_URL, SEVENZIP_SHA256)
    target = DEST / "7zip"
    with tempfile.TemporaryDirectory() as tmp:
        msi = Path(tmp) / "7zip.msi"
        msi.write_bytes(data)
        out = Path(tmp) / "out"
        if os.name == "nt":
            subprocess.run(
                ["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={out}"], check=True
            )
        else:
            bundled = ROOT / "cli" / "usr" / "bin" / "resources" / "7zz"
            tool = shutil.which("7zz") or shutil.which("7z")
            tool = tool or (str(bundled) if bundled.is_file() else None)
            if tool is None:
                raise SystemExit("Need 7zz or 7z to unpack the 7-Zip MSI on Linux")
            subprocess.run(
                [tool, "x", "-y", f"-o{out}", str(msi)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        found = {}
        for path in out.rglob("*"):
            # msiexec keeps real names; 7z may show MSI file-table keys.
            name = path.name.lower().lstrip("_")
            if name in ("7z.exe", "7z.dll", "license.txt") and path.is_file():
                found[name] = path
        if not {"7z.exe", "7z.dll"} <= set(found):
            names = sorted(p.name for p in out.rglob("*"))
            raise SystemExit(f"7z.exe/7z.dll not found in the MSI; it holds {names}")
        target.mkdir(parents=True, exist_ok=True)
        for name, path in found.items():
            shutil.copyfile(path, target / name)


def _fetch_git() -> None:
    print(f"Downloading MinGit {GIT_VERSION} (x64)")
    data = _download(GIT_URL, GIT_SHA256)
    target = DEST / "git"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not (target / name).resolve().is_relative_to(target):
                raise SystemExit(f"Refusing unsafe path in archive: {name}")
        archive.extractall(target)
    if not (target / "cmd" / "git.exe").is_file():
        raise SystemExit("cmd/git.exe not found in the MinGit archive")


def main() -> int:
    _fetch_cli()
    _fetch_7zip()
    _fetch_git()
    print(f"Installed {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
