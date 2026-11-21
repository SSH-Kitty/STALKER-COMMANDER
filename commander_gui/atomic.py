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
                # mkstemp creates 0o600; keep the replaced file's permissions
                # so shared files (e.g. the autostart .desktop) stay readable.
                os.chmod(
                    temporary_path,
                    stat.S_IMODE(path.stat().st_mode),
                )
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
            temporary_path.unlink(missing_ok=True)
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        raise
