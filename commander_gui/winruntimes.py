"""Windows runtimes the game needs on a native Windows install.

A fresh Windows lacks the Visual C++ runtime (``CONCRT140.dll``) and the legacy
DirectX 9-11 helper DLLs (``d3dx9_43.dll`` ...), and Anomaly fails to start
without them.  Linux gets the same libraries from winetricks instead, so this
module is Windows-only.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .i18n import tr


@dataclass(frozen=True)
class Runtime:
    key: str  # the Install page's dependency row for it
    label: str
    dlls: tuple[str, ...]  # all must be in System32
    url: str
    args: tuple[str, ...]  # silent-install switches
    #: True for the DirectX redist: a self-extracting archive (``/Q /T:dir``)
    #: holding ``DXSETUP.exe /silent``, not a single silent-install exe like
    #: the others. ``dxwebsetup.exe`` (the small "web installer") looks like
    #: the obvious choice but phones home for its legacy D3DX/XAudio2.7/
    #: XInput component list, which no longer answers - this offline package
    #: (the same one winetricks' d3dx9/d3dcompiler_43 verbs use) has every
    #: file inside already.
    extract: bool = False


RUNTIMES = (
    Runtime(
        "vcrun2022",
        "Visual C++ Runtime",
        ("concrt140.dll",),
        "https://aka.ms/vs/17/release/vc_redist.x64.exe",
        ("/install", "/quiet", "/norestart"),
    ),
    Runtime(
        "d3dx9",
        "DirectX Runtime",
        (
            "d3dx9_43.dll",
            "d3dx10_43.dll",
            "d3dx11_43.dll",
            "d3dcompiler_43.dll",
            "d3dcsx_43.dll",
            "xinput1_3.dll",
            "xaudio2_7.dll",
        ),
        "https://download.microsoft.com/download/8/4/A/84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/directx_Jun2010_redist.exe",
        (),
        extract=True,
    ),
)

# Checks the download is Microsoft-signed, then runs it elevated (UAC prompt).
# 98: the user declined UAC (or it could not start); 99: bad signature.
_SCRIPT = r"""
$f = $env:RUNTIME_EXE
$s = Get-AuthenticodeSignature -LiteralPath $f
if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*Microsoft Corporation*') { exit 99 }
try {
  $p = Start-Process -FilePath $f -ArgumentList ($env:RUNTIME_ARGS -split ' ') -Verb RunAs -Wait -PassThru
} catch { exit 98 }
exit $p.ExitCode
"""


def missing_dlls(runtime: Runtime) -> list[str]:
    """DLLs of ``runtime`` not in System32 (none off Windows)."""
    if os.name != "nt":
        return []
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return [dll for dll in runtime.dlls if not (system32 / dll).is_file()]


def missing() -> list[Runtime]:
    return [rt for rt in RUNTIMES if missing_dlls(rt)]


def check_exit_code(code: int, runtime: Runtime) -> bool:
    """True when a restart may be needed; raises RuntimeError on failure."""
    if code in (0, 1638):  # 1638: a newer version is already installed
        return False
    if code == 3010:
        return True
    if code == 98:
        raise RuntimeError(tr("The install was cancelled: Windows asked for permission and it was not given."))
    if code == 99:
        raise RuntimeError(tr("The download is not signed by Microsoft, so it was not run."))
    raise RuntimeError(tr("The {name} installer failed (code {code}).", name=tr(runtime.label), code=code))


def _run_elevated(exe: str, args: tuple[str, ...]) -> int:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
        env={**os.environ, "RUNTIME_EXE": exe, "RUNTIME_ARGS": " ".join(args)},
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    return result.returncode


def _install_one(runtime: Runtime, tmp: str) -> bool:
    name = tr(runtime.label)
    exe = str(Path(tmp) / runtime.url.rsplit("/", 1)[1])
    try:
        with urllib.request.urlopen(runtime.url, timeout=60) as src, open(exe, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    except OSError as exc:
        raise RuntimeError(tr("Could not download {name}: {error}", name=name, error=exc)) from exc
    if runtime.extract:
        # Two elevated steps: unpack the archive, then run its real installer.
        extract_dir = Path(tmp) / "dxredist"
        check_exit_code(_run_elevated(exe, ("/Q", f"/T:{extract_dir}")), runtime)
        setup = extract_dir / "DXSETUP.exe"
        if not setup.is_file():
            raise RuntimeError(tr("{name} did not extract correctly.", name=name))
        restart = check_exit_code(_run_elevated(str(setup), ("/silent",)), runtime)
    else:
        restart = check_exit_code(_run_elevated(exe, runtime.args), runtime)
    if missing_dlls(runtime):
        raise RuntimeError(tr("The installer finished but {name} is still missing.", name=name))
    return restart


def install() -> str:
    """Download and install every missing runtime. Blocking: run off the GUI thread."""
    if os.name != "nt":
        raise RuntimeError(tr("The Windows runtimes are only needed on Windows."))
    restart = False
    with tempfile.TemporaryDirectory() as tmp:
        for runtime in missing():
            restart |= _install_one(runtime, tmp)
    if restart:
        return tr("The Windows runtimes are installed. Restart Windows if the game still does not start.")
    return tr("The Windows runtimes are installed.")
