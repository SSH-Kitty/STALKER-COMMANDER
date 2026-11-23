"""Serialized atomic file writes used by GUI and background callbacks."""

from __future__ import annotations

import os
import re
import stat
import tempfile
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

_WRITE_LOCK = threading.RLock()


def remove_stale(folder: str | Path, pattern: str, max_age_s: float = 86400) -> None:
    """Delete *pattern* matches in *folder* older than *max_age_s*.

    For the hidden temp files and folders a download or unpack leaves when
    COMMANDER is killed partway (power loss, SIGKILL): no ``finally`` runs
    then, and they can be gigabytes. A day old, so one still in use by
    another COMMANDER window is never touched.
    """
    import shutil
    import time

    cutoff = time.time() - max_age_s
    try:
        stale = [p for p in Path(folder).glob(pattern) if p.lstat().st_mtime < cutoff]
    except OSError:
        return
    for path in stale:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def install_windows_rmtree() -> None:
    """Windows: make every ``shutil.rmtree`` delete read-only files too.

    Files unpacked from archives (and many mods) carry the read-only
    attribute, which Windows will not delete. ``rmtree`` then failed - or,
    with ``ignore_errors=True`` as most callers pass, silently left gigabytes
    behind (a Fresh Reset that stopped halfway, quarantine and ``.old``
    folders that never went). Patched once here like ``_hide_child_consoles``
    in main.py, rather than at the ~60 call sites. A file that is still open
    elsewhere stays an error for the caller, exactly as before.
    """
    import shutil

    if os.name != "nt" or getattr(shutil.rmtree, "_clears_read_only", False):
        return
    original = shutil.rmtree

    def rmtree(path, ignore_errors=False, onerror=None, *, onexc=None, dir_fd=None):
        def retry(func, failed, exc):
            if isinstance(exc, PermissionError):
                try:
                    os.chmod(failed, stat.S_IWRITE)
                    func(failed)
                    return
                except OSError as again:
                    exc = again
            if ignore_errors:
                return
            if onexc is not None:
                return onexc(func, failed, exc)
            if onerror is not None:
                return onerror(func, failed, (type(exc), exc, exc.__traceback__))
            raise exc

        return original(path, onexc=retry, dir_fd=dir_fd)

    rmtree._clears_read_only = True  # type: ignore[attr-defined]
    rmtree.avoids_symlink_attacks = original.avoids_symlink_attacks  # type: ignore[attr-defined]
    shutil.rmtree = rmtree


def split_lines(text: str) -> list[str]:
    """``text.splitlines()`` for files that only break on \\n, \\r\\n or \\r.

    ``str.splitlines()`` also breaks on U+2028, U+0085, \\x0b, \\x0c and
    \\x1c-\\x1e, so a mod name or INI value holding one was split in two
    and rewritten as two lines on the next save.
    """
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text through a unique sibling temporary file.

    Text read with ``errors="surrogateescape"`` keeps its non-UTF-8 bytes
    (a cp1251 name in an LTX/INI file) exactly as they were.
    """
    _write(path, text.encode("utf-8", errors="surrogateescape"), lock=True)


def write_bytes(path: Path, data: bytes, *, lock: bool = True) -> None:
    """Write ``data`` through a unique sibling temporary file.

    ``lock=False`` skips the sibling ``.<name>.lock`` file - for writing
    into a directory COMMANDER does not own (Steam's userdata), where a
    stray dotfile left behind would be litter in someone else's config.
    """
    _write(path, data, lock=lock)


def _replace(source: Path, target: Path, *, attempts: int = 12) -> None:
    """``os.replace``, retried briefly on Windows.

    Windows refuses to replace a file another handle has open (a concurrent
    read of settings.json, antivirus, the search indexer): ``PermissionError``
    ``[WinError 5]`` for a moment that passes by itself. Writes are serialized
    by ``_WRITE_LOCK`` but plain reads are not, so concurrent saves lost
    changes to this. POSIX replaces an open file without complaint.
    """
    if os.name != "nt":
        os.replace(source, target)
        return
    delay = 0.01
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def _replace_read_only(source: Path, target: Path, mode: int) -> None:
    """Windows: replace a read-only file and keep it read-only.

    Players mark user.ltx read-only so the game stops resetting it. POSIX
    replaces such a file (only the folder's permissions count) and the mode
    is copied onto the new one; Windows refuses with "Access is denied", so
    the flag comes off for the swap and goes back on after.
    """
    os.chmod(target, mode | stat.S_IWRITE)
    try:
        _replace(source, target)
    except BaseException:
        try:
            os.chmod(target, mode)
        except OSError:
            pass
        raise
    try:
        os.chmod(target, mode)
    except OSError:
        pass


def replace_file(source: Path, target: Path) -> None:
    """``os.replace(source, target)`` that also works on Windows when
    ``target`` is briefly open elsewhere or marked read-only (kept so)."""
    if os.name == "nt":
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:
            mode = None
        if mode is not None and not mode & stat.S_IWRITE:
            _replace_read_only(Path(source), Path(target), mode)
            return
    _replace(Path(source), Path(target))


def _write(path: Path, data: bytes, *, lock: bool) -> None:
    # A symlinked settings.json or modlist.txt (dotfile repos, profiles
    # shared between installs) is written *through*: os.replace() on the
    # link itself would swap the link for a plain file and silently cut it.
    if path.is_symlink():
        path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = None
    try:
        with _WRITE_LOCK:
            if fcntl is not None and lock:
                lock_fd = os.open(
                    path.parent / f".{path.name}.lock",
                    os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            fd, temporary = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            temporary_path = Path(temporary)
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                mode = None
            if mode is not None and os.name != "nt":
                # mkstemp creates 0o600; keep the replaced file's permissions
                # so shared files (e.g. the autostart .desktop) stay readable.
                try:
                    os.chmod(temporary_path, mode)
                except OSError:
                    pass
            try:
                stream = os.fdopen(fd, "wb")
            except BaseException:
                # fdopen failed before taking ownership of the descriptor -
                # close it directly or it leaks (the except block below only
                # unlinks the already-created temp file, not this fd).
                os.close(fd)
                raise
            with stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "nt" and mode is not None and not mode & stat.S_IWRITE:
                _replace_read_only(temporary_path, path, mode)
            else:
                _replace(temporary_path, path)
            try:  # make the rename itself survive a power cut
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                lock_fd = None
    except BaseException:
        if "temporary_path" in locals():
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass  # never hide the error that got us here
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        raise
