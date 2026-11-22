"""Download and install COMMANDER's own update.

Two builds can update themselves:

* the AppImage (``commander_appimage_path()`` returns a path): the new
  AppImage is downloaded next to it and swapped in place;
* the Windows build installed by Setup (``commander_windows_install_dir()``
  returns a path): the new ``STALKER-COMMANDER-Setup-<version>.exe`` is
  downloaded to %TEMP%, and on restart COMMANDER quits and runs it silently
  over the install (``packaging/windows/installer/COMMANDER.iss`` reopens
  COMMANDER when it is done, see its ``/RELAUNCH=1`` entry).

A source checkout, the AUR package (which installs into ``/opt``/``/usr/bin``
via pacman, see ``packaging/aur/PKGBUILD``) and the portable Windows zip must
keep updating through their own normal channel instead, since replacing a
file pacman tracks would fight the package manager.

Nothing is swapped in unless the download proves itself first:

* it must be complete - exactly as many bytes as the server announced (a
  dropped connection otherwise ends the read quietly, and a truncated file
  used to replace the working AppImage);
* it must actually be an AppImage (ELF header plus the type-2 AppImage
  magic) or a Windows program (MZ/PE header), not an HTML error page or a
  truncated stub;
* it must match the release's ``<file>.sha512sum`` asset (``build-
  appimage.sh`` and the Windows CI build write one next to every build); a
  release without one is refused.

A checksum published in the same release protects against corruption, not
against someone who controls the release itself; that needs a signing key
held outside GitHub (minisign/GPG), which the release process does not have
yet.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import atomic
from .config import child_environment
from .network import urlopen_with_retry as urlopen
from .proton_installer import SHA512_RE, sha512_file
from .repair import USER_AGENT
from .updates import _COMMANDER_REPO

#: Generous but bounded - the real AppImage is a few hundred MB.
_MAX_APPIMAGE_BYTES = 2 * 1024**3
_MAX_CHECKSUM_BYTES = 64 * 1024

#: ELF magic, and the AppImage type-2 magic ("AI" + 0x02) that the AppImage
#: runtime stores in the ELF header's padding at offset 8.
_ELF_MAGIC = b"\x7fELF"
_APPIMAGE_MAGIC = b"AI\x02"



#: Setup's ``AppId`` (packaging/windows/installer/COMMANDER.iss). Inno Setup
#: records the install under ``Uninstall\<AppId>_is1``; never changes.
_INNO_APP_ID = "{67127C51-1884-40A2-9DD9-FC228192E4FD}"
#: Where the Windows build downloads the new Setup; emptied before each try.
_SETUP_DOWNLOAD_DIR_NAME = "STALKER-COMMANDER-update"


def verify_appimage_file(path: Path) -> None:
    """Raise ``CommanderSelfUpdateError`` unless *path* looks like an AppImage."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as exc:
        raise CommanderSelfUpdateError(f"Could not read the update: {exc}") from exc
    if not head.startswith(_ELF_MAGIC) or head[8:11] != _APPIMAGE_MAGIC:
        raise CommanderSelfUpdateError(
            "The downloaded update is not a valid AppImage - nothing was changed."
        )


def verify_setup_file(path: Path) -> None:
    """Raise ``CommanderSelfUpdateError`` unless *path* is a Windows program.

    The MZ header plus the PE signature its ``e_lfanew`` points at: enough to
    turn away an HTML error page or a stub. The published checksum is what
    proves it is the right program.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError as exc:
        raise CommanderSelfUpdateError(f"Could not read the update: {exc}") from exc
    valid = False
    if len(head) >= 64 and head[:2] == b"MZ":
        offset = int.from_bytes(head[60:64], "little")
        valid = offset + 4 <= len(head) and head[offset : offset + 4] == b"PE\0\0"
    if not valid:
        raise CommanderSelfUpdateError(
            "The downloaded update is not a valid Windows installer - nothing was changed."
        )


def _published_sha512(url: str) -> str | None:
    """The release's ``.sha512sum`` digest for *url*, or None if not published."""
    req = urllib.request.Request(url + ".sha512sum", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            text = resp.read(_MAX_CHECKSUM_BYTES).decode("ascii", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CommanderSelfUpdateError(f"Could not fetch the update checksum: {exc}") from exc
    except urllib.error.URLError as exc:
        raise CommanderSelfUpdateError(f"Could not fetch the update checksum: {exc}") from exc
    match = SHA512_RE.search(text)
    if match is None:
        raise CommanderSelfUpdateError("The update's checksum file is malformed")
    return match.group(1).lower()


class CommanderSelfUpdateError(Exception):
    """Raised when a self-update step fails."""


class CommanderUpdateAssetNotFoundError(CommanderSelfUpdateError):
    """Raised when the predicted release asset does not exist (a 404)."""


def commander_appimage_path() -> Path | None:
    """Return the running AppImage's own path, or None off that packaging."""
    appimage = os.environ.get("APPIMAGE")
    return Path(appimage) if appimage else None


def _same_dir(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def _inno_install_locations() -> list[str]:
    """Every ``InstallLocation`` Setup recorded: per user (HKCU) or all users (HKLM)."""
    import winreg

    subkey = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{_INNO_APP_ID}_is1"
    found = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return found


def commander_windows_install_dir() -> Path | None:
    """The folder Setup installed the running COMMANDER.exe into, or None.

    None for a source checkout, and for the portable zip: that one is
    unpacked wherever the user likes and has no Setup to update it with,
    so it keeps sending the user to the Releases page. Only an exe that
    really runs from the folder Setup recorded counts - a portable copy on
    a machine that also has the installed version must not run Setup over
    a different folder.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).parent
    try:
        locations = _inno_install_locations()
    except (ImportError, OSError):
        return None
    for location in locations:
        if _same_dir(location, exe_dir):
            return exe_dir
    return None


def commander_can_self_update() -> bool:
    """True when this build can download and install its own update."""
    return commander_appimage_path() is not None or commander_windows_install_dir() is not None


def _release_version(tag: str) -> str:
    # The tag comes from the release feed and lands in a URL path: only a
    # plain version-like tag, never "/" or ".." that could point elsewhere.
    if not re.fullmatch(r"v?[0-9A-Za-z][0-9A-Za-z._-]{0,63}", tag) or ".." in tag:
        raise CommanderSelfUpdateError(f"Unexpected release tag: {tag!r}")
    # The builds name their files from the lowercase __version__, while the
    # release tag may be cased "v1.4.0-Unstable2".
    return tag.removeprefix("v").lower()


def commander_update_asset_url(tag: str) -> str:
    """Predict the AppImage asset URL for release *tag*.

    Matches ``build-appimage.sh``'s own artifact naming:
    ``STALKER-GAMMA-COMMANDER-<version>-x86_64.AppImage``, version without
    a leading "v" even when the release tag has one.
    """
    filename = f"STALKER-GAMMA-COMMANDER-{_release_version(tag)}-x86_64.AppImage"
    return f"{_COMMANDER_REPO}/releases/download/{tag}/{filename}"


def commander_setup_filename(tag: str) -> str:
    """Setup's file name for *tag*, as COMMANDER.iss's ``OutputBaseFilename``."""
    return f"STALKER-COMMANDER-Setup-{_release_version(tag)}.exe"


def commander_setup_asset_url(tag: str) -> str:
    """Predict the Windows Setup asset URL for release *tag*.

    The Windows CI build names it from ``__version__`` (the same value as
    the release tag, minus the "v"); its ``.sha512sum`` goes next to it.
    """
    return f"{_COMMANDER_REPO}/releases/download/{tag}/{commander_setup_filename(tag)}"


def release_has_windows_setup(tag: str) -> bool:
    """True when release *tag* has a Windows Setup this build can install.

    Asked before offering an update: a release can be published for Linux
    before (or without) its Windows Setup, and the installed Windows build
    should keep saying "up to date" until there is something to install.
    Raises OSError when GitHub can't be reached, like the update check.
    """
    try:
        return _published_sha512(commander_setup_asset_url(tag)) is not None
    except CommanderSelfUpdateError as exc:
        raise OSError(str(exc)) from exc


def check_commander_update_for_this_build(
    current_version: str, channel: str = "stable"
) -> str | None:
    """``updates.check_commander_update``, minus releases this build can't install.

    Only the installed Windows build filters: everywhere else (AppImage,
    source, AUR, portable zip) a newer release is worth telling about.
    """
    from .updates import check_commander_update

    tag = check_commander_update(current_version, channel=channel)
    if tag and commander_windows_install_dir() is not None and not release_has_windows_setup(tag):
        return None
    return tag


def _download_verified(
    url: str,
    dest_dir: Path,
    name: str,
    verify: Callable[[Path], None],
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> Path:
    """Download *url* into a hidden ``.<name>.*.update`` file in *dest_dir*.

    Returns the file only once it is complete, passes *verify* and matches
    the release's published checksum; removes it on every failure. Raises
    ``CommanderUpdateAssetNotFoundError`` when the asset doesn't exist (a
    404), ``CommanderSelfUpdateError`` for every other failure.
    """
    # Asked for first: a release without a checksum is refused anyway, and
    # that should not cost the user the whole download.
    expected = _published_sha512(url)
    if expected is None:
        raise CommanderUpdateAssetNotFoundError(
            f"The update has no published checksum ({url}) - nothing was changed."
        )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        # Per-read stall limit, not a whole-download limit: 600 s left Cancel
        # stuck on "Stopping" for up to ten minutes when GitHub stalled.
        with urlopen(req, timeout=60) as resp:
            header_value = resp.headers.get("Content-Length")
            try:
                total = int(header_value) if header_value is not None else None
            except (TypeError, ValueError):
                total = None
            if total is not None and total <= 0:
                total = None
            if total is not None and total > _MAX_APPIMAGE_BYTES:
                raise CommanderSelfUpdateError(
                    "Update download exceeds the allowed size"
                )
            # A missing/malformed header must not silently skip the
            # preflight check - assume the worst case (the allowed cap).
            space_check_total = total if total is not None else _MAX_APPIMAGE_BYTES
            try:
                free = shutil.disk_usage(dest_dir).free
            except OSError as exc:
                raise CommanderSelfUpdateError(
                    f"Could not check free disk space: {exc}"
                ) from exc
            if free < space_check_total * 2:
                raise CommanderSelfUpdateError(
                    "Not enough free disk space to download the update"
                )
            atomic.remove_stale(dest_dir, f".{name}.*.update")
            try:
                fd, tmp_name = tempfile.mkstemp(
                    dir=dest_dir,
                    prefix=f".{name}.",
                    suffix=".update",
                )
            except OSError as exc:
                raise CommanderSelfUpdateError(
                    f"Could not create a temporary file in {dest_dir} - "
                    f"move COMMANDER to a writable folder and try again: {exc}"
                ) from exc
            tmp_path = Path(tmp_name)
            # Progress falls back to indeterminate (total=1) only for the
            # callback, independent of the size-cap/disk-space checks above.
            progress_total = total or 1
            downloaded = 0
            try:
                with os.fdopen(fd, "wb") as f:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise CommanderSelfUpdateError("Update cancelled")
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > _MAX_APPIMAGE_BYTES:
                            raise CommanderSelfUpdateError(
                                "Update download exceeds the allowed size"
                            )
                        if progress_cb is not None:
                            progress_cb(downloaded, progress_total)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CommanderUpdateAssetNotFoundError(
                f"No update file found at {url}"
            ) from exc
        raise CommanderSelfUpdateError(f"Update download failed: {exc}") from exc
    except urllib.error.URLError as exc:
        raise CommanderSelfUpdateError(f"Update download failed: {exc}") from exc
    try:
        if downloaded == 0:
            raise CommanderSelfUpdateError("Downloaded update file was empty")
        if total is not None and downloaded != total:
            # HTTPResponse.read() returns b"" on a dropped connection instead
            # of raising, so a short file is the only sign of it.
            raise CommanderSelfUpdateError(
                f"The update download was incomplete ({downloaded} of {total} bytes)"
                " - nothing was changed. Try again."
            )
        verify(tmp_path)
        if sha512_file(tmp_path) != expected:
            raise CommanderSelfUpdateError(
                "The update does not match its published checksum - nothing was changed."
            )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def download_commander_update(
    tag: str,
    running_path: Path,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> Path:
    """Download the AppImage asset for *tag* into ``running_path``'s own directory.

    Landing in the same directory as *running_path* keeps the later
    ``os.replace()`` swap on one filesystem, so it stays atomic. Raises
    ``CommanderUpdateAssetNotFoundError`` when the predicted asset doesn't
    exist (a 404), ``CommanderSelfUpdateError`` for every other failure.
    """
    return _download_verified(
        commander_update_asset_url(tag),
        running_path.parent,
        running_path.name,
        lambda path: verify_appimage_file(path),
        cancel_event=cancel_event,
        progress_cb=progress_cb,
    )


def _setup_download_dir() -> Path:
    """A fresh %TEMP% folder for the new Setup; earlier downloads are removed."""
    folder = Path(tempfile.gettempdir()) / _SETUP_DOWNLOAD_DIR_NAME
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CommanderSelfUpdateError(
            f"Could not create a folder for the update in {folder.parent}: {exc}"
        ) from exc
    return folder


def download_commander_setup(
    tag: str,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> Path:
    """Download and check the Windows Setup for *tag*. Returns its path.

    Not run here: Setup replaces COMMANDER's own files, so it runs once
    COMMANDER quits (``relaunch_commander``).
    """
    filename = commander_setup_filename(tag)
    folder = _setup_download_dir()
    try:
        downloaded = _download_verified(
            commander_setup_asset_url(tag),
            folder,
            filename,
            lambda path: verify_setup_file(path),
            cancel_event=cancel_event,
            progress_cb=progress_cb,
        )
    except CommanderUpdateAssetNotFoundError as exc:
        raise CommanderUpdateAssetNotFoundError(
            f"COMMANDER {tag} has no Windows installer yet - nothing was changed. "
            "Try again later, or check the releases page."
        ) from exc
    final = folder / filename
    try:
        os.replace(downloaded, final)
    except OSError as exc:
        downloaded.unlink(missing_ok=True)
        raise CommanderSelfUpdateError(f"Could not save the update: {exc}") from exc
    return final


def install_commander_update(downloaded: Path, running_path: Path) -> None:
    """Atomically replace *running_path* with *downloaded* (same filesystem)."""
    verify_appimage_file(downloaded)
    # An AppImage must be executable; the file was verified just above.
    try:
        os.chmod(downloaded, 0o755)  # nosec B103
        os.replace(downloaded, running_path)
    except OSError:
        # Don't leave a few hundred MB of hidden ".update" file behind.
        downloaded.unlink(missing_ok=True)
        raise


def setup_command(setup: Path) -> list[str]:
    """The command line that runs *setup* as an update over this install.

    * ``/SILENT``: a progress window only, no wizard pages. Not
      ``/VERYSILENT`` - the user should see that something is happening
      after COMMANDER closed - and not ``/SUPPRESSMSGBOXES``, whose default
      answers would quietly cancel the update if a question did come up.
    * ``/CLOSEAPPLICATIONS``: anything still holding COMMANDER's files (an
      ASSISTANT window) is closed instead of failing the copy.
    * ``/MERGETASKS=!runtimes``: no Visual C++/DirectX install (and its
      administrator prompts) in the middle of an update; the desktop
      shortcut keeps its earlier choice.
    * ``/RELAUNCH=1``: COMMANDER.iss's [Run] entry that reopens COMMANDER
      once the update is in (the wizard's "Launch" box is skipped silently).
    * ``/LOG``: Setup's log next to it in %TEMP%, for a failed update.
    """
    return [
        str(setup),
        "/SILENT",
        "/SP-",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/MERGETASKS=!runtimes",
        "/RELAUNCH=1",
        f"/LOG={setup.with_suffix('.log')}",
    ]


def _setup_environment() -> dict[str, str]:
    """COMMANDER's environment minus the PyInstaller bootloader's own.

    Setup reopens COMMANDER.exe, which would inherit them through it: a
    ``_PYI_*`` variable left from this process makes the new bootloader
    take itself for a child of the old, gone one.
    """
    env = child_environment()
    for key in list(env):
        if key.upper().startswith("_PYI_") or key.upper() == "_MEIPASS2":
            env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def relaunch_commander(
    path: Path, release_lock: Callable[[], None] | None = None
) -> None:
    """Start *path* as a new, independent process.

    *path* is the AppImage, or on Windows the downloaded Setup - which then
    installs the update and reopens COMMANDER itself.

    *release_lock* must drop the single-instance lock before the new
    process starts - same requirement as ``deck_launch.relaunch_exec``'s own
    *release_lock*, and for the same reason: the new process reaches its own
    ``_acquire_instance_lock()`` almost immediately, typically before this
    (still-running) process has actually exited, and a lock still held at
    that moment makes the new process conclude another instance is running
    and exit with no window at all. On Windows it also drops the mutex
    Setup's ``AppMutex`` waits on.
    """
    if release_lock is not None:
        release_lock()
    if os.name == "nt" and Path(path).suffix.lower() == ".exe":
        verify_setup_file(path)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            setup_command(path),
            env=_setup_environment(),
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    subprocess.Popen(
        [str(path)],
        # Not this process's environment as-is: the old AppRun pointed
        # SSL_CERT_FILE into this AppImage's mount, which vanishes when we
        # quit, and the new AppRun only sets it when unset.
        env=child_environment(),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def download_and_install_commander_update(
    tag: str,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> Path:
    """Get *tag* ready to run. Returns the path ``relaunch_commander`` starts.

    The AppImage is downloaded over the running one (its path is returned);
    the installed Windows build downloads Setup, which installs on restart.
    """
    running_path = commander_appimage_path()
    if running_path is None:
        if commander_windows_install_dir() is not None:
            return download_commander_setup(
                tag, cancel_event=cancel_event, progress_cb=progress_cb
            )
        raise CommanderSelfUpdateError(
            "This copy of COMMANDER cannot update itself (only the AppImage and "
            "the installed Windows version can)."
        )
    downloaded = download_commander_update(
        tag, running_path, cancel_event=cancel_event, progress_cb=progress_cb
    )
    if cancel_event is not None and cancel_event.is_set():
        downloaded.unlink(missing_ok=True)
        raise CommanderSelfUpdateError("Update cancelled")
    install_commander_update(downloaded, running_path)
    return running_path


def switch_commander_build(
    target: str,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[Path, str]:
    """Install the newest ``"unstable"`` or ``"stable"`` build over this one.

    Unlike an update, this may go *down* a version: reverting from an
    unstable build installs the latest stable release even when it is older.
    Returns the path ``relaunch_commander`` starts and the tag installed.
    """
    from . import __version__
    from .updates import latest_stable_tag, newer_unstable_tag

    if not commander_can_self_update():
        raise CommanderSelfUpdateError(
            "This copy of COMMANDER cannot switch builds itself (only the AppImage "
            "and the installed Windows version can)."
        )
    if target == "unstable":
        tag = newer_unstable_tag(__version__)
        if tag is None:
            raise CommanderSelfUpdateError(
                "There is no unstable build newer than the stable one right now "
                "(or the release list could not be read)."
            )
    elif target == "stable":
        tag = latest_stable_tag()
        if tag is None:
            raise CommanderSelfUpdateError(
                "Could not find the latest stable release - check your connection "
                "and try again."
            )
    else:
        raise CommanderSelfUpdateError(f"Unknown build channel: {target}")
    path = download_and_install_commander_update(
        tag, cancel_event=cancel_event, progress_cb=progress_cb
    )
    return path, tag
