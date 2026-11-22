"""Windows runtimes the game needs on a native Windows install.

A fresh Windows lacks the Visual C++ runtime (``CONCRT140.dll``) and the legacy
DirectX 9-11 helper DLLs (``d3dx9_43.dll`` ...), and Anomaly fails to start
without them.  Linux gets the same libraries from winetricks instead, so this
module is Windows-only.
"""

from __future__ import annotations

import hashlib
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
    #: SHA-256 of the download when it never changes (the 2010 DirectX
    #: package); empty when the URL always serves the latest build
    #: (aka.ms's Visual C++ redist), which is then checked by its signature.
    sha256: str = ""


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
        sha256="053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b",
    ),
)

#: The organisation every runtime installer here is signed by.
_SIGNER = "Microsoft Corporation"
#: An installer that never returns (a stuck UAC session) is given up on.
_INSTALL_TIMEOUT_MS = 30 * 60 * 1000

# Exit codes of our own, next to the installers' real ones.
_DECLINED = 98  # the user declined UAC (or it could not start)
_BAD_SIGNATURE = 99  # not signed by Microsoft, or not the file we downloaded


def command_line(args: tuple[str, ...]) -> str:
    """``args`` as one Windows command line, quoting any that contain spaces.

    A temp folder under a profile like ``C:\\Users\\John Smith`` puts a space in
    the ``/T:`` extract path; splitting on spaces would cut it in two.
    """
    return subprocess.list2cmdline(list(args))


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
    if code in (3010, 1641):  # restart needed / the installer started one
        return True
    if code == _DECLINED:
        raise RuntimeError(tr("The install was cancelled: Windows asked for permission and it was not given."))
    if code == _BAD_SIGNATURE:
        raise RuntimeError(tr("The download is not signed by Microsoft, so it was not run."))
    raise RuntimeError(tr("The {name} installer failed (code {code}).", name=tr(runtime.label), code=code))


def _hold(path: str):
    """Open *path* so nobody can change, rename or delete it while held.

    Between checking an installer and Windows running it elevated, another
    program running as the user could otherwise swap the file (the temp
    folder is the user's): a way to get its own code run as administrator.
    Reading and running stay allowed, which is all the installer needs.
    Returns the handle, or None when it can't be opened like that.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    generic_read, share_read, open_existing = 0x80000000, 0x1, 3
    handle = kernel32.CreateFileW(path, generic_read, share_read, None, open_existing, 0, None)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None
    return handle


def _release(handle) -> None:
    if handle:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def signed_by_microsoft(path: str) -> bool:
    """True when *path* has a valid Authenticode signature whose signer's
    organisation is exactly Microsoft Corporation.

    WinVerifyTrust directly, as Windows itself checks a download: no hidden
    PowerShell (Get-AuthenticodeSignature), which antivirus heuristics flag
    and some machines block outright. Revocation is not fetched online, the
    same as Get-AuthenticodeSignature's default.
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR),
                    ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)), ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPWSTR),
            ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    class CRYPT_PROVIDER_CERT(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pCert", ctypes.c_void_p)]

    # WINTRUST_ACTION_GENERIC_VERIFY_V2
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    try:
        wintrust = ctypes.WinDLL("wintrust")
        crypt32 = ctypes.WinDLL("crypt32")
    except OSError:
        return False
    wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    wintrust.WTHelperProvDataFromStateData.argtypes = [wintypes.HANDLE]
    wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvCertFromChain.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    wintrust.WTHelperGetProvCertFromChain.restype = ctypes.POINTER(CRYPT_PROVIDER_CERT)
    crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                           wintypes.LPWSTR, wintypes.DWORD]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD

    file_info = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), path, None, None)
    data = WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    data.dwUIChoice = 2  # WTD_UI_NONE
    data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE
    data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = 1  # WTD_STATEACTION_VERIFY
    data.dwProvFlags = 0x10 | 0x1000  # REVOCATION_CHECK_NONE | CACHE_ONLY_URL_RETRIEVAL
    try:
        if wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data)) != 0:
            return False
        provider = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
        signer = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0) if provider else None
        cert = wintrust.WTHelperGetProvCertFromChain(signer, 0) if signer else None
        if not cert or not cert.contents.pCert:
            return False
        organisation = ctypes.create_string_buffer(b"2.5.4.10")  # szOID_ORGANIZATION_NAME
        name = ctypes.create_unicode_buffer(256)
        crypt32.CertGetNameStringW(cert.contents.pCert, 3, 0, organisation, name, 256)  # CERT_NAME_ATTR_TYPE
        return name.value == _SIGNER
    finally:
        data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))


def _run_elevated(exe: str, args: tuple[str, ...], *, verb: str = "runas", show: int = 1) -> int:
    """Run *exe* as administrator (one UAC prompt) and return its exit code.

    ShellExecuteEx's "runas" directly - the same prompt PowerShell's
    ``Start-Process -Verb RunAs`` showed, without a hidden PowerShell.
    *show* 0 (SW_HIDE) keeps a console program's window from flashing up.
    """
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = 0x00000040 | 0x00000100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = verb  # "open" only in the tests: no UAC prompt
    info.lpFile = exe
    info.lpParameters = command_line(args) or None
    info.lpDirectory = str(Path(exe).parent)
    info.nShow = show  # SW_SHOWNORMAL by default; the installers run quiet anyway
    if not shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return _DECLINED  # ERROR_CANCELLED (1223) when UAC was declined
    try:
        if kernel32.WaitForSingleObject(info.hProcess, _INSTALL_TIMEOUT_MS) != 0:
            raise RuntimeError(tr("The installer did not finish in time."))
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _checked(path: str, runtime: Runtime) -> bool:
    """The download is the one meant: its pinned hash, else Microsoft's signature."""
    if runtime.sha256:
        return _sha256(path) == runtime.sha256
    return signed_by_microsoft(path)


def _install_one(runtime: Runtime, tmp: str) -> bool:
    name = tr(runtime.label)
    exe = str(Path(tmp) / runtime.url.rsplit("/", 1)[1])
    try:
        with urllib.request.urlopen(runtime.url, timeout=60) as src, open(exe, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    except OSError as exc:
        raise RuntimeError(tr("Could not download {name}: {error}", name=name, error=exc)) from exc
    restart = False
    held = _hold(exe)  # the file checked is the file run (see _hold)
    try:
        if held is None or not _checked(exe, runtime):
            check_exit_code(_BAD_SIGNATURE, runtime)
        if runtime.extract:
            # Two elevated steps: unpack the archive, then run its real installer.
            extract_dir = Path(tmp) / "dxredist"
            check_exit_code(_run_elevated(exe, ("/Q", f"/T:{extract_dir}")), runtime)
        else:
            restart = check_exit_code(_run_elevated(exe, runtime.args), runtime)
    finally:
        _release(held)
    if runtime.extract:
        setup = extract_dir / "DXSETUP.exe"
        if not setup.is_file():
            raise RuntimeError(tr("{name} did not extract correctly.", name=name))
        held = _hold(str(setup))
        try:
            if held is None or not signed_by_microsoft(str(setup)):
                check_exit_code(_BAD_SIGNATURE, runtime)
            restart = check_exit_code(_run_elevated(str(setup), ("/silent",)), runtime)
        finally:
            _release(held)
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
