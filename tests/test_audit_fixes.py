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

    def test_second_romeo_removal_keeps_both_sets_of_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma, anomaly = Path(tmp) / "gamma", Path(tmp) / "anomaly"
            (gamma / "profiles" / romeo.GAMMA_PROFILE).mkdir(parents=True)
            (gamma / "profiles" / romeo.GAMMA_PROFILE / "modlist.txt").write_text("")
            (gamma / "saves from removed profile ROMEO").mkdir()
            (gamma / "profiles" / "ROMEO" / "saves").mkdir(parents=True)
            (gamma / "profiles" / "ROMEO" / "saves" / "new.scop").write_text("new")
            with mock.patch.object(romeo, "romeo_profiles", return_value=["ROMEO"]), \
                 mock.patch.object(romeo, "_restore_atmos", return_value=True), \
                 mock.patch("commander_gui.launcher.set_mo2_selected_profile"):
                romeo.uninstall(gamma, anomaly, Path(tmp) / "cache")
            self.assertEqual((gamma / "saves from removed profile ROMEO (2)" / "new.scop").read_text(), "new")

    def test_crashed_romeo_swap_puts_the_players_files_back(self):
        from commander_gui import coop

        with tempfile.TemporaryDirectory() as tmp:
            gamma = Path(tmp)
            keep = gamma / "profiles" / ".ROMEO.keep"
            (keep / "saves").mkdir(parents=True)
            (keep / "saves" / "a.scop").write_text("save")
            (gamma / "profiles" / "ROMEO").mkdir()
            coop.recover_swaps(gamma)
            self.assertEqual((gamma / "profiles" / "ROMEO" / "saves" / "a.scop").read_text(), "save")
            self.assertFalse(keep.exists())
            # A keep folder that could not go back is never deleted by an install.
            (keep / "saves").mkdir(parents=True)
            with self.assertRaises(romeo.ModInstallError):
                romeo._swap_profile(gamma / "new", gamma / "profiles" / "ROMEO")
            self.assertTrue((keep / "saves").is_dir())

    def test_swap_dir_never_leaves_half_a_copy_at_the_destination(self):
        from commander_gui import coop

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest, new = root / "mods" / "X", root / "staging" / "X"
            dest.mkdir(parents=True)
            (dest / "old.txt").write_text("old")
            new.mkdir(parents=True)
            (new / "new.txt").write_text("new")
            with mock.patch("shutil.move", side_effect=OSError("disk full")), self.assertRaises(OSError):
                coop._swap_dir(new, dest)
            self.assertEqual((dest / "old.txt").read_text(), "old")
            (root / "mods" / ("X" + coop._NEW_SUFFIX)).mkdir()  # a crash mid-copy
            coop.recover_swaps(root)
            self.assertFalse((root / "mods" / ("X" + coop._NEW_SUFFIX)).exists())
            coop._swap_dir(new, dest)
            self.assertEqual(sorted(p.name for p in (root / "mods").iterdir()), ["X"])
            self.assertEqual((dest / "new.txt").read_text(), "new")

    def test_windows_rd_never_sees_cmd_special_characters(self):
        from commander_gui.ui import utilities_page

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "S&G"
            path.mkdir()
            with mock.patch.object(utilities_page.os, "name", "nt"), \
                 mock.patch.object(utilities_page.subprocess, "run") as run:
                utilities_page._remove_tree(path)
            run.assert_not_called()
            self.assertFalse(path.exists())

    def test_unknown_engine_in_state_does_not_crash_the_card(self):
        from commander_gui.ui.exes_card import installed_text

        self.assertIn("future", installed_text("/x", {"engine": "future", "version": "1"}))

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
