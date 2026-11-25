"""Install demonized's modded exes (xray-monolith) into the Anomaly folder.

GAMMA pins its own build: the exes go in ``bin/`` and the gamedata that build
needs is copied loose into ``gamedata/`` (configs, scripts, shaders,
materials). A release zip instead carries that gamedata packed in
``db/mods/00_modded_exes_gamedata.db0``, and loose files win over a ``.db0``,
so installing one follows the guide modpacks give (ROMEO's included): empty
``db/mods``, clear ``gamedata/configs`` except ``axr_options.ltx`` and
``cache_dbg.ltx``, drop ``gamedata/materials``, ``scripts`` and ``shaders``,
then unpack the zip over Anomaly.

Nothing is deleted the first time: what the clean-up removes, and the exes it
replaces, move into ``commander-exes/original`` so ``restore_original()`` can
put the folder back exactly as it was. A GAMMA update copies its loose files
and exes back; ``after_gamma_update()`` cleans up again when the player kept
their exes.

Two engines install this way: demonized's modded exes and neXt-Ray
(Ozicom23's fork of them, AVX exes only, with a DX12 renderer). Their zips
share the layout and the ``.db0`` name; the engine is told apart by
``bin/AnomalyDX12AVX.exe``. Switching from one engine to the other first
restores the originals, so no exe of the old engine is left behind.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from . import __version__, atomic, network
from .mod_install import ModInstallError, extract_archive
from .network import read_response_bytes

_USER_AGENT = f"CommanderGUI/{__version__}"
_TAG_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$")

MODDED = "modded"
NEXT_RAY = "nextray"
#: Engine -> (GitHub releases API, release zip name, release tag).
_SOURCES = {
    MODDED: (
        "https://api.github.com/repos/themrdemonized/xray-monolith/releases",
        re.compile(r"^STALKER-Anomaly-modded-exes-MT-TEST_[\d.]+\.zip$"),
        _TAG_RE,
    ),
    NEXT_RAY: (
        "https://api.github.com/repos/Ozicom23/next-ray-engine/releases",
        re.compile(r"^neXt-Ray-[\d.]+\.zip$"),
        re.compile(r"^v\d+(\.\d+)*$"),
    ),
}
#: neXt-Ray's DX12 exe and the MO2 executable COMMANDER adds for it.
DX12_EXE = "AnomalyDX12AVX.exe"
DX12_TITLE = "Anomaly (DX12-AVX)"
_MAX_API_BYTES = 4 * 1024 * 1024
_MAX_ZIP_BYTES = 1024**3

#: The release COMMANDER marks as tested. None until one has been played on.
RECOMMENDED: str | None = None

GAMEDATA_DB = "00_modded_exes_gamedata.db0"
_KEEP_CONFIGS = {"axr_options.ltx", "cache_dbg.ltx"}
_DROP_FOLDERS = ("materials", "scripts", "shaders")
_STATE_DIR = "commander-exes"


class ExesError(RuntimeError):
    pass


# -- versions -----------------------------------------------------------------
def fetch_releases(count: int = 30, engine: str = MODDED) -> list[dict]:
    """Recent releases of *engine* with an engine zip, newest first.

    Each has ``tag``, ``published`` (ISO date), ``url``, ``size`` and
    ``sha256`` (GitHub's digest of the zip, "" when it has none). A
    neXt-Ray tag reads ``neXt-Ray 0.2.2``.
    """
    api, asset_re, tag_re = _SOURCES[engine]
    req = urllib.request.Request(
        f"{api}?per_page={min(100, max(1, count))}",
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with network.urlopen_with_retry(req, timeout=30) as resp:
        data = json.loads(read_response_bytes(resp, _MAX_API_BYTES))
    out = []
    for release in data:
        tag = release.get("tag_name", "")
        asset = next((a for a in release.get("assets", []) if asset_re.match(a.get("name", ""))), None)
        if not tag_re.match(tag) or asset is None or release.get("draft"):
            continue
        digest = asset.get("digest") or ""
        out.append({
            "engine": engine,
            "tag": f"neXt-Ray {tag[1:]}" if engine == NEXT_RAY else tag,
            "published": (release.get("published_at") or "")[:10],
            "url": asset.get("browser_download_url", ""),
            "size": int(asset.get("size") or 0),
            "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
        })
    return out


def build_date(anomaly: str | Path) -> str | None:
    """Build date of ``bin/AnomalyDX11.exe`` (from its PE header), or None."""
    try:
        with open(Path(anomaly) / "bin" / "AnomalyDX11.exe", "rb") as exe:
            head = exe.read(4096)
        offset = struct.unpack_from("<I", head, 0x3C)[0]
        if head[offset:offset + 4] != b"PE\0\0":
            return None
        stamp = struct.unpack_from("<I", head, offset + 8)[0]
    except (OSError, struct.error):
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()


def at_least(anomaly: str | Path, tag: str) -> bool:
    """True if the installed exes are release ``tag`` (``YYYY.M.D``) or newer.

    Uses COMMANDER's record when it installed a release, else the exe's build
    date (the 2026.9.12 exe is stamped 2026-09-12, the release day)."""
    def _key(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.split(r"[.-]", text))

    state = load_state(anomaly) or {}
    if state.get("engine") == NEXT_RAY:
        return False  # forked from the 2026.5.15 modded exes; not a newer one
    version = state.get("version")
    if not (isinstance(version, str) and _TAG_RE.match(version)):
        version = build_date(anomaly)
    return version is not None and _key(version) >= _key(tag)


def load_state(anomaly: str | Path) -> dict | None:
    """``{"version", "previous", "engine"}`` when COMMANDER installed the exes.

    ``engine`` is ``MODDED`` or ``NEXT_RAY`` (missing in older records:
    ``MODDED``, the only engine then).

    ``version``/``previous`` are a release tag, ``"file:<zip name>"``, or
    None for "what was there before COMMANDER" (``previous`` only).
    """
    try:
        state = json.loads((Path(anomaly) / _STATE_DIR / "state.json").read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _save_state(anomaly: Path, state: dict) -> None:
    atomic.write_text(anomaly / _STATE_DIR / "state.json", json.dumps(state))


# -- download -----------------------------------------------------------------
def download(release: dict, dest_dir: Path, progress=None, cancel_event: Event | None = None) -> Path:
    """Download a release zip into ``dest_dir`` and check its SHA-256."""
    url = release["url"]
    if not url.startswith("https://github.com/"):
        raise ExesError(f"Unexpected download address: {url}")
    if not release.get("sha256"):
        raise ExesError("GitHub lists no checksum for this release, so it can't be checked.")
    target = dest_dir / url.rsplit("/", 1)[-1]
    digest = hashlib.sha256()
    done = 0
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with network.urlopen_with_retry(req, timeout=60) as resp, open(target, "wb") as out:
        while chunk := resp.read(1 << 20):
            if cancel_event is not None and cancel_event.is_set():
                raise ExesError("Download cancelled")
            done += len(chunk)
            if done > _MAX_ZIP_BYTES:
                raise ExesError("The download is larger than a modded exes release can be.")
            digest.update(chunk)
            out.write(chunk)
            if progress:
                progress(done, release.get("size") or 0)
    if digest.hexdigest() != release["sha256"].lower():
        raise ExesError("The download doesn't match GitHub's checksum. Try again.")
    return target


# -- install ------------------------------------------------------------------
def _clean_targets(anomaly: Path) -> list[Path]:
    """What the guide's clean-up removes from the Anomaly folder."""
    found = []
    db = anomaly / "db" / "mods"
    if db.is_dir():
        found += sorted(db.iterdir())
    configs = anomaly / "gamedata" / "configs"
    if configs.is_dir():
        found += sorted(p for p in configs.iterdir() if p.name.lower() not in _KEEP_CONFIGS)
    found += [anomaly / "gamedata" / name for name in _DROP_FOLDERS if (anomaly / "gamedata" / name).exists()]
    return found


def _move(path: Path, anomaly: Path, backup: Path) -> None:
    target = backup / path.relative_to(anomaly)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), target)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _put_back(original: Path, anomaly: Path) -> None:
    """Move every file in ``original`` back over the Anomaly folder and
    delete ``original``."""
    if not original.is_dir():
        return
    for path in sorted(original.rglob("*"), reverse=True):
        if path.is_file():
            target = anomaly / path.relative_to(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), target)
    shutil.rmtree(original)


def installed_engine(anomaly: str | Path) -> str | None:
    """The engine COMMANDER installed here, or None (GAMMA's own)."""
    state = load_state(anomaly)
    return state.get("engine", MODDED) if state else None


def runs_installed_engine(anomaly: str | Path, binary: str) -> bool:
    """False for a launch target that would start another engine than the
    one installed: with neXt-Ray (AVX exes only), GAMMA's non-AVX and DX8-10
    exes are still in ``bin`` but its loose gamedata is gone. Also False for
    a DX12 target whose exe is gone (an Anomaly reinstall or a GAMMA update
    removed neXt-Ray but left the MO2 entry)."""
    name = Path(binary.replace("\\", "/")).name.lower()
    if name == DX12_EXE.lower() and not Path(binary).is_file():
        return False
    if not name.startswith("anomalydx") or installed_engine(anomaly) != NEXT_RAY:
        return True
    return name in ("anomalydx11avx.exe", DX12_EXE.lower())


def _check_layout(staging: Path) -> str:
    """Check an unpacked zip; return its engine."""
    exes = staging / "bin"
    if not ((exes / "AnomalyDX11.exe").is_file() or (exes / "AnomalyDX11AVX.exe").is_file()) \
            or not (staging / "db" / "mods" / GAMEDATA_DB).is_file():
        raise ExesError("This zip isn't a modded exes release: it needs bin/AnomalyDX11.exe and db/mods/" + GAMEDATA_DB + ".")
    extra = {p.name for p in staging.iterdir()} - {"bin", "db"}
    if extra:
        raise ExesError(f"This zip has more than modded exes in it: {', '.join(sorted(extra))}")
    return NEXT_RAY if (exes / DX12_EXE).is_file() else MODDED


def install(zip_path: Path, anomaly: str | Path, label: str, cancel_event: Event | None = None) -> None:
    """Install a modded exes zip. ``label`` is its release tag or ``file:<name>``."""
    from . import coop

    anomaly = Path(anomaly)
    if not (anomaly / "bin").is_dir():
        raise ExesError("The Anomaly folder has no bin folder.")
    if coop.is_active(anomaly):
        # Co-op's engine is in place and its backups hold the singleplayer
        # files. Switched off is fine: switching on backs up afresh.
        raise ExesError("Co-op is on. Switch to singleplayer on the Multiplayer page first.")
    state = load_state(anomaly)
    last = dict((state or {}).get("last") or {})  # engine -> version last used
    if state is not None:
        last[state.get("engine", MODDED)] = state.get("version")
    root = anomaly / _STATE_DIR
    root.mkdir(exist_ok=True)
    # Beside ``root``, not in it: switching engines deletes ``root``.
    staging = Path(tempfile.mkdtemp(prefix=".exes-", dir=anomaly))
    try:
        extract_archive(zip_path, staging / "zip", cancel_event)
        engine = _check_layout(staging / "zip")
        if cancel_event is not None and cancel_event.is_set():
            raise ModInstallError("Mod installation cancelled")
        if state is not None and state.get("engine", MODDED) != engine:
            # Other engine: back to the originals, then a first install.
            # A failure from here on leaves GAMMA's own engine in place.
            restore_original(anomaly)
            root.mkdir(exist_ok=True)
            state = None
        original = root / "original"
        # No state means no finished install: an ``original`` folder is
        # what an interrupted first install moved out, so it goes back.
        first = state is None
        if first and original.exists():
            _put_back(original, anomaly)
        added = set(state.get("added", [])) if state else set()
        placed: list[Path] = []
        try:
            if first:
                # Everything the zip replaces or the clean-up removes, so
                # restore_original() can put the folder back as it was.
                original.mkdir()
                (original / "bin").mkdir()
            for exe in (staging / "zip" / "bin").iterdir():
                current = anomaly / "bin" / exe.name
                if first and current.is_file():
                    shutil.copy2(current, original / "bin" / exe.name)
                elif not (original / "bin" / exe.name).exists():
                    added.add(exe.name)  # no original: restore deletes it
            for path in _clean_targets(anomaly):
                if first:
                    _move(path, anomaly, original)
                else:
                    # Only what a GAMMA update copied back since: the
                    # originals are already saved.
                    _remove(path)
            for path in sorted((staging / "zip").rglob("*")):
                if path.is_file():
                    target = anomaly / path.relative_to(staging / "zip")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), target)
                    placed.append(target.relative_to(anomaly))
            _save_state(anomaly, {
                "version": label,
                "previous": state.get("version") if state else None,
                "added": sorted(added),
                "engine": engine,
                "last": last,
            })
        except BaseException as exc:
            if first:
                try:
                    # Files the zip brought that had no original are deleted;
                    # the rest are overwritten again by _put_back().
                    for rel in placed:
                        if not (original / rel).exists():
                            (anomaly / rel).unlink(missing_ok=True)
                    _put_back(original, anomaly)
                except OSError as rollback:
                    # Usually the lock that stopped the install (the game
                    # still has an exe open): keep the first error visible.
                    raise ExesError(
                        f"Installing the modded exes failed ({exc}), and putting the original files back "
                        f"failed too ({rollback}). Close the game and Mod Organizer 2, then try again."
                    ) from exc
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_original(anomaly: str | Path) -> None:
    """Put back what was there before COMMANDER installed modded exes."""
    from . import coop

    anomaly = Path(anomaly)
    if coop.is_active(anomaly):
        raise ExesError("Co-op is on. Switch to singleplayer on the Multiplayer page first.")
    root = anomaly / _STATE_DIR
    original = root / "original"
    if not original.is_dir():
        raise ExesError("There is no saved copy of the original exes.")
    for path in _clean_targets(anomaly):
        _remove(path)
    for name in (load_state(anomaly) or {}).get("added", []):
        if Path(name).name == name:
            (anomaly / "bin" / name).unlink(missing_ok=True)
    _put_back(original, anomaly)
    shutil.rmtree(root)


def after_gamma_update(anomaly: str | Path, kept_exes: bool) -> None:
    """Fix up the folder after a GAMMA update that may have copied its own
    exes and loose gamedata back.

    Kept exes: redo the clean-up, and keep the ``.db0`` (saved by the caller
    across the update). Not kept: GAMMA's exes are back, so COMMANDER's
    record and backup no longer describe the folder; drop them, the
    ``.db0``, whose loose counterparts GAMMA just reinstalled, and the
    files the engine added that GAMMA has no copy of (neXt-Ray's DX12 exe
    and DLLs would otherwise run against GAMMA's gamedata).
    """
    anomaly = Path(anomaly)
    state = load_state(anomaly)
    if state is None:
        return
    if kept_exes:
        for path in _clean_targets(anomaly):
            if path.name != GAMEDATA_DB:
                _remove(path)
    else:
        (anomaly / "db" / "mods" / GAMEDATA_DB).unlink(missing_ok=True)
        for name in state.get("added", []):
            if Path(name).name == name:
                (anomaly / "bin" / name).unlink(missing_ok=True)
        shutil.rmtree(anomaly / _STATE_DIR, ignore_errors=True)
