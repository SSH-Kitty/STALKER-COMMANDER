"""Regression tests for the full-app audit fixes."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from commander_gui import game_backup, keep_state, launch_options, romeo, settings
from commander_gui.diagnostics import redact


class AuditFixesTest(unittest.TestCase):
    def test_stale_lossless_dll_is_still_a_path(self):
        with mock.patch("commander_gui.gui_settings.load_gui_settings", return_value={"lossless_dll": "/gone/Lossless.dll"}), \
             mock.patch("commander_gui.launcher._steam_library_paths", return_value=[]):
            self.assertEqual(launch_options.lossless_dll(), Path("/gone/Lossless.dll"))

    def test_non_ascii_profile_names_get_their_own_backup_folder(self):
        self.assertNotEqual(game_backup._slug("Сталкер"), game_backup._slug("Зона"))
        self.assertEqual(game_backup._slug("GAMMA"), "GAMMA")

    def test_windows_paths_survive_toggle(self):
        out = launch_options.toggle(r"%command% -ini C:\games\x.ini", "-dbg", True)
        self.assertIn(r"C:\games\x.ini", launch_options.split(out))

    def test_redact_is_not_quadratic(self):
        start = time.monotonic()
        redact("://" + "a:" * 20000)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertNotIn("pw", redact("https://user:pw@host/x"))

    def test_read_only_settings_dir_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings.CliSettings, "save", side_effect=OSError("read-only")):
                self.assertTrue(settings.load_settings(path).profiles)

    def test_keep_state_that_is_not_an_object_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / keep_state._STATE_FILE).write_text(json.dumps([1, 2]))
            self.assertIsNone(keep_state._load(Path(tmp), ""))

    def test_romeo_update_keeps_the_players_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "profiles" / "ROMEO", root / "staging" / "ROMEO"
            (old / "saves").mkdir(parents=True)
            (old / "saves" / "a.scop").write_text("save")
            (old / "modlist.txt").write_text("old")
            new.mkdir(parents=True)
            (new / "modlist.txt").write_text("new")
            romeo._swap_profile(new, old)
            self.assertEqual((old / "modlist.txt").read_text(), "new")
            self.assertEqual((old / "saves" / "a.scop").read_text(), "save")
            self.assertFalse((root / "profiles" / ".ROMEO.keep").exists())

    def test_checksum_must_name_this_file(self):
        from commander_gui import self_update

        digest = "a" * 128
        text = f"{'b' * 128}  other.AppImage\n{digest}  mine.AppImage\n".encode()

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, size=-1):
                return text

        with mock.patch.object(self_update, "urlopen", return_value=Reply()):
            self.assertEqual(self_update._published_sha512("https://x/y/mine.AppImage"), digest)


if __name__ == "__main__":
    unittest.main()
