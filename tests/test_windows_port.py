"""Regression tests for Windows-only behaviour fixed in the Windows port."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commander_gui import atomic, config
from commander_gui.cli_runner import signed_exit_code
from commander_gui.ui.utilities_page import _rewrite_mo2_ini_paths


class SignedExitCodeTest(unittest.TestCase):
    def test_windows_crash_codes_fit_a_qt_int_signal(self):
        # 0xC0000142 (STATUS_DLL_INIT_FAILED) is 3221225794 unsigned and used to
        # overflow Signal(int, str).
        self.assertEqual(signed_exit_code(3221225794), -1073741502)
        self.assertEqual(signed_exit_code(0xC0000135), -1073741515)  # DLL not found
        for rc in range(-1, -4, -1):
            self.assertEqual(signed_exit_code(rc), rc)

    def test_ordinary_codes_are_unchanged(self):
        for rc in (0, 1, 2, 124, 126, 255, 0x7FFFFFFF, -9):
            self.assertEqual(signed_exit_code(rc), rc)


@unittest.skipUnless(os.name == "nt", "Windows only")
class ReplaceRetryTest(unittest.TestCase):
    def test_a_briefly_locked_target_is_retried(self):
        calls = []

        def flaky(source, target):
            calls.append((source, target))
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")

        with patch("commander_gui.atomic.os.replace", side_effect=flaky), patch("commander_gui.atomic.time.sleep"):
            atomic._replace(Path("a.tmp"), Path("a"))
        self.assertEqual(len(calls), 3)

    def test_a_target_that_stays_locked_still_raises(self):
        with (
            patch("commander_gui.atomic.os.replace", side_effect=PermissionError(5, "Access is denied")) as replace,
            patch("commander_gui.atomic.time.sleep"),
            self.assertRaises(PermissionError),
        ):
            atomic._replace(Path("a.tmp"), Path("a"), attempts=4)
        self.assertEqual(replace.call_count, 4)

    def test_concurrent_reads_do_not_lose_a_save(self):
        """A reader holding the file open (no FILE_SHARE_DELETE) blocks os.replace for a moment."""
        import threading
        import time

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old")
            reader = open(target)  # noqa: SIM115 - held open on purpose
            threading.Timer(0.15, reader.close).start()
            started = time.monotonic()
            atomic.write_text(target, "new")
            self.assertEqual(target.read_text(), "new")
            self.assertGreaterEqual(time.monotonic() - started, 0.1)


@unittest.skipUnless(os.name == "nt", "Windows only")
class GitBinaryTest(unittest.TestCase):
    def test_bundled_mingit_comes_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "cli" / "win-x64" / "git" / "cmd" / "git.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            with patch("commander_gui.config.project_root", return_value=Path(tmp)), patch(
                "shutil.which", return_value=r"C:\elsewhere\git.exe"
            ):
                self.assertEqual(config.git_binary(), str(exe))

    def test_falls_back_to_path_then_none(self):
        with tempfile.TemporaryDirectory() as tmp, patch("commander_gui.config.project_root", return_value=Path(tmp)):
            with patch("shutil.which", return_value=r"C:\Program Files\Git\cmd\git.exe"):
                self.assertEqual(config.git_binary(), r"C:\Program Files\Git\cmd\git.exe")
            with patch("shutil.which", return_value=None):
                self.assertIsNone(config.git_binary())


class Mo2IniRewriteTest(unittest.TestCase):
    """Move install: MO2 spells paths ``C:/...``, ``C:\\...`` and ``C:\\\\...`` in one file."""

    def test_every_spelling_of_the_old_path_is_rewritten_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma = Path(tmp) / "new-gamma"
            gamma.mkdir()
            (gamma / "ModOrganizer.ini").write_text(
                "[General]\n"
                "gamePath=C:/Games/old/anomaly\n"
                "base_directory=C:\\Games\\old\\gamma\n"
                "downloads=@ByteArray(C:\\\\Games\\\\old\\\\gamma\\\\downloads)\n",
                encoding="utf-8",
            )
            _rewrite_mo2_ini_paths(
                gamma,
                [(r"C:\Games\old\anomaly", r"D:\New\anomaly"), (r"C:\Games\old\gamma", r"D:\New\gamma")],
            )
            text = (gamma / "ModOrganizer.ini").read_text(encoding="utf-8")
            self.assertIn("gamePath=D:/New/anomaly\n", text)
            self.assertIn("base_directory=D:\\New\\gamma\n", text)
            self.assertIn("D:\\\\New\\\\gamma\\\\downloads", text)
            self.assertNotIn("old", text)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.name == "nt", "Windows only")
class WindowsProcessTests(unittest.TestCase):
    """Process checks read a Toolhelp32 snapshot: /proc and pgrep don't exist."""

    def test_snapshot_sees_a_child_by_image_name(self):
        import subprocess
        import sys

        from commander_gui import winproc

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            names = winproc.processes()
            self.assertIn(child.pid, names)
            self.assertTrue(names[child.pid].lower().endswith(".exe"))
            self.assertTrue(winproc.running(Path(sys.executable).name.upper()))
        finally:
            child.kill()
            child.wait()
        self.assertFalse(winproc.running("no-such-process-0123456789.exe"))

    def test_mo2_and_game_checks_use_image_names(self):
        from commander_gui.ui import common

        snapshot = {101: "ModOrganizer.exe", 202: "AnomalyDX11AVX.exe", 303: "steam.exe", os.getpid(): "python.exe"}
        with patch("commander_gui.winproc.processes", return_value=snapshot):
            common._CMDLINES = (0.0, {})
            self.assertTrue(common.process_running(r"ModOrganizer\.exe"))
            common._CMDLINES = (0.0, {})
            self.assertEqual(common.exe_pids("AnomalyDX"), {202})
            common._CMDLINES = (0.0, {})
            self.assertEqual(common.mo2_pids(), {101})
            self.assertTrue(common.steam_running())
        with patch("commander_gui.winproc.processes", return_value={}):
            common._CMDLINES = (0.0, {})
            self.assertFalse(common.process_running(r"ModOrganizer\.exe"))
            self.assertFalse(common.steam_running())

    def test_lossless_running_needs_no_tasklist(self):
        from commander_gui import lossless_win

        with patch("commander_gui.winproc.processes", return_value={5: "LosslessScaling.exe"}), patch(
            "subprocess.run", side_effect=AssertionError("tasklist spawned")
        ):
            self.assertTrue(lossless_win.running())
