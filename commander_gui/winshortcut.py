"""Windows shortcuts (.lnk), their icon and the real Desktop folder. Qt-free
except scaled_pngs.

A Linux desktop shortcut is a text file; a Windows one is a binary .lnk that
only the shell's own ShellLink object writes correctly. It is reached over COM
with plain ctypes (no pywin32 / comtypes: PySide6 stays the one dependency),
and ``QFile.link`` can't be used because it has no way to set arguments - the
MO2 command needs ``-p <profile> moshortcut://...``.

The Desktop comes from the shell too (``SHGetKnownFolderPath``): OneDrive
and a moved Desktop folder both put it somewhere other than ~/Desktop.
"""

from __future__ import annotations

import os
import re
import struct
import uuid
from pathlib import Path

#: ShellLink's class and the two interfaces this module calls on it.
_CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
_FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"

_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106 as a signed HRESULT

# Vtable slots (IUnknown takes 0-2 in both interfaces).
_QUERY_INTERFACE, _RELEASE = 0, 2
_SET_DESCRIPTION, _SET_WORKING_DIRECTORY, _SET_ARGUMENTS = 7, 9, 11
_SET_ICON_LOCATION, _SET_PATH = 17, 20
_PERSIST_SAVE = 6

#: IShellLink's own limits: SetArguments takes at most INFOTIPSIZE chars.
_MAX_ARGUMENTS = 1024

#: Every COMMANDER shortcut is named like the installer's own one
#: ("STALKER COMMANDER") and the Steam ones ("STALKER COMMANDER DECK").
APP_NAME = "STALKER COMMANDER"

#: Characters Windows doesn't allow in a file name.
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

#: Sizes in the generated .ico: Explorer's small, list, tile and large views.
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def shortcut_name(target: str) -> str:
    """The visible name of a desktop shortcut to *target*:
    ``STALKER COMMANDER - Anomaly (DX11-AVX)``. On Windows the file name is
    the name the Desktop shows, so it is kept readable, not slugged."""
    title = _INVALID_NAME_CHARS.sub(" ", target)
    title = re.sub(r"\s+", " ", title).strip(" .")
    return f"{APP_NAME} - {title}" if title else APP_NAME


def ico_bytes(images: list[bytes]) -> bytes:
    """An .ico holding each PNG in *images* (square, at most 256 px).

    PNG entries have been valid in .ico since Windows Vista, so the PNGs go
    in as they are - no bitmap conversion.
    """
    entries = []
    for png in images:
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG")
        width, height = struct.unpack(">II", png[16:24])
        if width != height or width > 256:
            raise ValueError(f"icon images must be square and at most 256 px, not {width}x{height}")
        entries.append((width, png))
    offset = 6 + 16 * len(entries)
    header = struct.pack("<HHH", 0, 1, len(entries))
    directory = b""
    for size, png in entries:
        # 0 stands for 256 in the one-byte width/height fields.
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    return header + directory + b"".join(png for _size, png in entries)


def scaled_pngs(source: bytes, sizes=_ICON_SIZES) -> list[bytes]:
    """*source* (a PNG) at each of *sizes*, smoothly scaled with Qt, so the
    small views get a sharp icon instead of Explorer shrinking the 256 px
    one. Just *source* when Qt can't read it."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage

    image = QImage.fromData(source)
    if image.isNull():
        return [source]
    out = []
    for size in sizes:
        if size > max(image.width(), image.height()):
            continue
        scaled = image.scaled(
            size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buffer, "PNG")
        buffer.close()
        out.append(bytes(data))
    return out or [source]


def _guid(text: str):
    import ctypes

    raw = uuid.UUID(text).bytes_le
    return (ctypes.c_byte * 16).from_buffer_copy(raw)


def desktop_dir() -> Path | None:
    """The user's Desktop folder as Windows has it, or None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    folder_id = _guid(_FOLDERID_DESKTOP)
    out = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value) if out.value else None
    finally:
        ole32.CoTaskMemFree(out)


def _method(pointer, slot: int, *argtypes):
    """Method *slot* of the COM object at *pointer*, as a callable."""
    import ctypes

    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    function = prototype(vtable[slot])
    return lambda *args: function(pointer, *args)


def _check(hresult: int, what: str) -> None:
    if hresult < 0:
        raise OSError(f"{what} failed (HRESULT 0x{hresult & 0xFFFFFFFF:08X})")


def write_lnk(
    path: Path,
    target: str,
    arguments: str = "",
    *,
    working_dir: str = "",
    icon: str = "",
    description: str = "",
) -> None:
    """Write a .lnk at *path* that runs *target* with *arguments*.

    *icon* is an .ico or .exe (its first icon is used). Raises OSError when
    the shell can't make or save the shortcut.
    """
    if os.name != "nt":
        raise OSError("Windows shortcuts can only be written on Windows")
    if len(arguments) >= _MAX_ARGUMENTS:
        raise OSError("The launch command is too long for a Windows shortcut")
    import ctypes

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    # Qt has already made the GUI thread an STA: S_FALSE then, and a worker
    # thread gets its own. Only a call that succeeded is paired with
    # CoUninitialize; RPC_E_CHANGED_MODE (an MTA thread) still lets COM work.
    init = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if init < 0 and init != _RPC_E_CHANGED_MODE:
        _check(init, "CoInitializeEx")
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        clsid, iid_link, iid_persist = (
            _guid(_CLSID_SHELL_LINK), _guid(_IID_SHELL_LINK_W), _guid(_IID_PERSIST_FILE),
        )
        _check(ole32.CoCreateInstance(
            ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER,
            ctypes.byref(iid_link), ctypes.byref(link),
        ), "Creating the shortcut")
        wstr = ctypes.c_wchar_p
        _check(_method(link, _SET_PATH, wstr)(target), "Setting the shortcut's program")
        _check(_method(link, _SET_ARGUMENTS, wstr)(arguments), "Setting the shortcut's arguments")
        if working_dir:
            _check(_method(link, _SET_WORKING_DIRECTORY, wstr)(working_dir), "Setting the shortcut's folder")
        if description:
            _check(_method(link, _SET_DESCRIPTION, wstr)(description[:259]), "Setting the shortcut's description")
        if icon:
            _check(_method(link, _SET_ICON_LOCATION, wstr, ctypes.c_int)(icon, 0), "Setting the shortcut's icon")
        _check(_method(link, _QUERY_INTERFACE, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(iid_persist), ctypes.byref(persist),
        ), "Opening the shortcut for saving")
        _check(_method(persist, _PERSIST_SAVE, wstr, ctypes.c_int)(str(path), 1), "Saving the shortcut")
    finally:
        for pointer in (persist, link):
            if pointer.value:
                _method(pointer, _RELEASE)()
        if init >= 0:
            ole32.CoUninitialize()
