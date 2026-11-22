"""The installed Windows build updates itself through Setup (self_update)."""

import hashlib
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from commander_gui import self_update
from commander_gui.self_update import (
    CommanderSelfUpdateError,
    CommanderUpdateAssetNotFoundError,
)

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "windows" / "installer" / "COMMANDER.iss"

#: The smallest file verify_setup_file takes for a Windows program.
_FAKE_PE = b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little") + b"PE\0\0"


class _FakeResponse:
    def __init__(self, data, headers):
        self._data = data
        self.headers = headers

    def read(self, size=-1):
        if size < 0 or size >= len(self._data):
            chunk, self._data = self._data, b""
            return chunk
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _serve(body, checksum=True):
    """Fake GitHub: the Setup and (unless *checksum* is False) its .sha512sum."""
    digest = hashlib.sha512(body).hexdigest().encode() + b"  STALKER-COMMANDER-Setup-9.9.9.exe"
    urls = []

    def fake_urlopen(request, timeout=None):
        urls.append(request.full_url)
        if request.full_url.endswith(".sha512sum"):
            if not checksum:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            return _FakeResponse(digest, {})
        return _FakeResponse(body, {"Content-Length": str(len(body))})

    return patch("commander_gui.self_update.urllib.request.urlopen", side_effect=fake_urlopen), urls


class SetupFileTests(unittest.TestCase):
    def test_a_windows_program_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup.exe"
            path.write_bytes(_FAKE_PE + b"payload")
            self_update.verify_setup_file(path)

    def test_an_error_page_or_a_stub_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup.exe"
            for body in (b"<html>rate limited</html>", b"MZ" + b"\0" * 100, b""):
                path.write_bytes(body)
                with self.assertRaisesRegex(CommanderSelfUpdateError, "not a valid Windows installer"):
                    self_update.verify_setup_file(path)

    def test_asset_names_match_the_installer_build(self):
        # COMMANDER.iss: OutputBaseFilename=STALKER-COMMANDER-Setup-{#AppVersion},
        # AppVersion = __version__ (lowercase), the tag without its "v".
        self.assertIn("OutputBaseFilename=STALKER-COMMANDER-Setup-{#AppVersion}", ISS.read_text())
        url = self_update.commander_setup_asset_url("v1.4.0-Unstable2")
        self.assertTrue(url.endswith("/releases/download/v1.4.0-Unstable2/STALKER-COMMANDER-Setup-1.4.0-unstable2.exe"))
        for bad in ("../x", "v1/../2", "a/b", ""):
            with self.assertRaises(CommanderSelfUpdateError):
                self_update.commander_setup_asset_url(bad)


class InstallerScriptTests(unittest.TestCase):
    def test_the_app_id_is_the_one_setup_records(self):
        self.assertIn("AppId={" + self_update._INNO_APP_ID, ISS.read_text())

    def test_a_silent_update_reopens_commander_as_the_user(self):
        text = ISS.read_text()
        entry = next(line for line in text.splitlines() if "Check: RelaunchAfterUpdate" in line)
        self.assertIn("skipifnotsilent", entry)
        self.assertIn("runasoriginaluser", entry)
        self.assertIn("{param:relaunch|0}') = '1'", text)
        command = self_update.setup_command(Path("C:/t/STALKER-COMMANDER-Setup-9.9.9.exe"))
        self.assertIn("/RELAUNCH=1", command)
        self.assertIn("/SILENT", command)
        # The mutex COMMANDER holds is the one Setup waits on.
        mutex = re.search(r"^AppMutex=(.+)$", text, re.MULTILINE).group(1).strip()
        self.assertIn(f'"{mutex}"', (ROOT / "commander_gui" / "main.py").read_text(encoding="utf-8"))

    def test_ci_publishes_the_checksum_the_update_needs(self):
        workflow = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text()
        self.assertIn("-Algorithm SHA512", workflow)
        self.assertIn("dist/STALKER-COMMANDER-Setup-*.exe.sha512sum", workflow)


@unittest.skipUnless(os.name == "nt", "Windows only")
class InstallDirTests(unittest.TestCase):
    def _frozen(self, exe_dir: Path, locations):
        return (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(exe_dir / "COMMANDER.exe")),
            patch.object(self_update, "_inno_install_locations", return_value=locations),
        )

    def test_only_the_folder_setup_recorded_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            a, b, c = self._frozen(exe_dir, [str(exe_dir) + "\\"])
            with a, b, c:
                self.assertEqual(self_update.commander_windows_install_dir(), exe_dir)
                self.assertTrue(self_update.commander_can_self_update())
            # A portable copy beside an installed one.
            a, b, c = self._frozen(exe_dir, [r"C:\Program Files\STALKER COMMANDER"])
            with a, b, c:
                self.assertIsNone(self_update.commander_windows_install_dir())
            a, b, c = self._frozen(exe_dir, [])
            with a, b, c:
                self.assertIsNone(self_update.commander_windows_install_dir())

    def test_a_source_checkout_is_not_an_install(self):
        with patch.object(self_update, "_inno_install_locations", return_value=[str(ROOT)]):
            self.assertFalse(getattr(sys, "frozen", False))
            self.assertIsNone(self_update.commander_windows_install_dir())


@unittest.skipUnless(os.name == "nt", "Windows only")
class SetupDownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.install = self.tmp / "install"
        self.install.mkdir()
        (self.install / "COMMANDER.exe").write_bytes(b"old")
        patches = [
            patch.object(self_update.tempfile, "gettempdir", return_value=str(self.tmp)),
            patch.object(self_update, "commander_windows_install_dir", return_value=self.install),
            patch.dict(os.environ, {"APPIMAGE": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_update_downloads_setup_and_leaves_the_install_alone(self):
        body = _FAKE_PE + b"new"
        served, urls = _serve(body)
        with served:
            path = self_update.download_and_install_commander_update("v9.9.9")
        self.assertEqual(path, self.tmp / "STALKER-COMMANDER-update" / "STALKER-COMMANDER-Setup-9.9.9.exe")
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name])
        self.assertEqual((self.install / "COMMANDER.exe").read_bytes(), b"old")
        self.assertTrue(urls[0].endswith("STALKER-COMMANDER-Setup-9.9.9.exe.sha512sum"))

    def test_a_release_without_a_windows_setup_says_so(self):
        served, _urls = _serve(_FAKE_PE, checksum=False)
        with served, self.assertRaisesRegex(CommanderUpdateAssetNotFoundError, "no Windows installer yet"):
            self_update.download_and_install_commander_update("v9.9.9")

    def test_a_wrong_checksum_or_non_program_is_refused(self):
        served, _urls = _serve(_FAKE_PE + b"new")
        with served, patch.object(self_update, "sha512_file", return_value="0" * 128), self.assertRaisesRegex(
            CommanderSelfUpdateError, "checksum"
        ):
            self_update.download_commander_setup("v9.9.9")
        served, _urls = _serve(b"<html>nope</html>")
        with served, self.assertRaisesRegex(CommanderSelfUpdateError, "not a valid Windows installer"):
            self_update.download_commander_setup("v9.9.9")
        self.assertEqual(list((self.tmp / "STALKER-COMMANDER-update").iterdir()), [])

    def test_the_update_check_skips_a_release_with_no_windows_setup_yet(self):
        with patch("commander_gui.updates.check_commander_update", return_value="v9.9.9"):
            served, _urls = _serve(_FAKE_PE, checksum=False)
            with served:
                self.assertIsNone(self_update.check_commander_update_for_this_build("1.4.0"))
            served, _urls = _serve(_FAKE_PE)
            with served:
                self.assertEqual(self_update.check_commander_update_for_this_build("1.4.0"), "v9.9.9")

    def test_restart_runs_setup_after_dropping_the_locks(self):
        setup = self.tmp / "STALKER-COMMANDER-Setup-9.9.9.exe"
        setup.write_bytes(_FAKE_PE)
        order = []
        with (
            patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": r"C:\old", "_PYI_PARENT_PROCESS_LEVEL": "1"}),
            patch.object(self_update.subprocess, "Popen", side_effect=lambda *a, **k: order.append(("popen", a, k))),
        ):
            self_update.relaunch_commander(setup, release_lock=lambda: order.append(("lock",)))
        self.assertEqual(order[0], ("lock",))
        _name, args, kwargs = order[1]
        self.assertEqual(args[0][0], str(setup))
        self.assertIn("/RELAUNCH=1", args[0])
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", kwargs["env"])
        self.assertNotIn("_PYI_PARENT_PROCESS_LEVEL", kwargs["env"])
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_switching_builds_goes_through_setup_too(self):
        with (
            patch("commander_gui.updates.latest_stable_tag", return_value="v9.9.9"),
            patch.object(self_update, "download_commander_setup", return_value=Path("s.exe")) as download,
        ):
            self.assertEqual(self_update.switch_commander_build("stable"), (Path("s.exe"), "v9.9.9"))
        download.assert_called_once()


@unittest.skipUnless(os.name == "nt", "Windows only")
class RunningMutexTests(unittest.TestCase):
    def test_handing_over_to_setup_releases_the_mutex(self):
        import ctypes

        from commander_gui import main

        main._announce_running()
        self.assertTrue(main._RUNNING_MUTEX)
        main.release_instance_lock()
        self.assertIsNone(main._RUNNING_MUTEX)
        # Nothing holds it now: opening it by name finds no such mutex.
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenMutexW(SYNCHRONIZE, False, "STALKER-COMMANDER-running")
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        self.assertFalse(handle)


if __name__ == "__main__":
    unittest.main()
