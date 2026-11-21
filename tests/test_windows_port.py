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

    @unittest.skipUnless(os.name == "nt", "Windows paths ignore case")
    def test_a_differently_cased_old_path_is_still_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma = Path(tmp) / "new-gamma"
            gamma.mkdir()
            (gamma / "ModOrganizer.ini").write_text(
                "gamePath=c:/games/OLD/Anomaly\nbase_directory=C:\\GAMES\\old\\gamma\n", encoding="utf-8"
            )
            _rewrite_mo2_ini_paths(
                gamma,
                [(r"C:\Games\old\anomaly", r"D:\New\anomaly"), (r"C:\Games\old\gamma", r"D:\New\gamma")],
            )
            text = (gamma / "ModOrganizer.ini").read_text(encoding="utf-8")
            self.assertEqual(text, "gamePath=D:/New/anomaly\nbase_directory=D:\\New\\gamma\n")


@unittest.skipUnless(os.name == "nt", "Windows only")
class WindowsModNameTests(unittest.TestCase):
    def test_names_windows_cannot_create_are_cleaned(self):
        from commander_gui.mod_install import sanitize_name

        self.assertEqual(sanitize_name('Mod "HD"? <v2> | *'), "Mod HD v2")
        self.assertEqual(sanitize_name("CON"), "CON mod")
        self.assertEqual(sanitize_name("nul.txt"), "nul.txt mod")
        self.assertEqual(sanitize_name("Console"), "Console")
        name = sanitize_name("a" * 119 + " b")
        self.assertLessEqual(len(name), 120)
        self.assertFalse(name.endswith((" ", ".")))
        with tempfile.TemporaryDirectory() as tmp:
            for raw in ('Mod "HD"?', "CON", "x" * 300, "trailing. "):
                (Path(tmp) / sanitize_name(raw)).mkdir()


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



@unittest.skipUnless(os.name == "nt", "Windows only")
class ReadOnlyFileTests(unittest.TestCase):
    """Archives and players leave files read-only; Windows won't replace or delete those."""

    def _read_only(self, path: Path) -> None:
        import stat

        os.chmod(path, stat.S_IREAD)

    def _writable(self, path: Path) -> bool:
        import stat

        return bool(os.stat(path).st_mode & stat.S_IWRITE)

    def test_atomic_write_replaces_a_read_only_file_and_keeps_it_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "user.ltx"
            target.write_text("old")
            self._read_only(target)
            try:
                atomic.write_text(target, "new")
                self.assertEqual(target.read_text(), "new")
                self.assertFalse(self._writable(target))
                self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["user.ltx"])
            finally:
                os.chmod(target, 0o666)

    def test_rmtree_deletes_read_only_files_even_with_ignore_errors(self):
        import shutil

        atomic.install_windows_rmtree()
        for ignore in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp) / "mod"
                (tree / "gamedata").mkdir(parents=True)
                for name in ("a.ltx", "gamedata/b.xml"):
                    (tree / name).write_text("x")
                    self._read_only(tree / name)
                shutil.rmtree(tree, ignore_errors=ignore)
                self.assertFalse(tree.exists())

    def test_rmtree_still_reports_a_file_that_is_open(self):
        import shutil

        atomic.install_windows_rmtree()
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "mod"
            tree.mkdir()
            held = open(tree / "held.txt", "w")  # noqa: SIM115
            try:
                with self.assertRaises(PermissionError):
                    shutil.rmtree(tree)
                errors = []
                shutil.rmtree(tree, onexc=lambda *args: errors.append(args))
                self.assertTrue(errors)
            finally:
                held.close()



_CTRL_C_CHILD = """
import sys, time
try:
    for _ in range(300):
        time.sleep(0.1)
    open(sys.argv[1], "w").write("timeout")
except KeyboardInterrupt:
    open(sys.argv[1], "w").write("ctrl-c")
"""


@unittest.skipUnless(os.name == "nt", "Windows only")
class CliProcessTreeTests(unittest.TestCase):
    """Cancel is a Ctrl+C the CLI can clean up after; the tree dies with COMMANDER."""

    def _child(self, tmp: str, *extra: str):
        import subprocess
        import sys

        script = Path(tmp) / "child.py"
        script.write_text(_CTRL_C_CHILD)
        out = Path(tmp) / "out.txt"
        proc = subprocess.Popen(
            [sys.executable, str(script), str(out), *extra], creationflags=subprocess.CREATE_NO_WINDOW
        )
        return proc, out

    def test_ctrl_c_reaches_a_console_less_child_and_spares_us(self):
        import time

        from commander_gui import winjob

        winjob.allow_ctrl_c_for_children()  # pytest may inherit "ignore Ctrl+C"
        with tempfile.TemporaryDirectory() as tmp:
            proc, out = self._child(tmp)
            try:
                time.sleep(1.0)  # its KeyboardInterrupt handling is set up
                self.assertTrue(winjob.send_ctrl_c(proc.pid))
                proc.wait(15)
                self.assertEqual(out.read_text(), "ctrl-c")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_terminating_the_job_ends_the_process(self):
        from commander_gui import winjob

        with tempfile.TemporaryDirectory() as tmp:
            proc, _out = self._child(tmp)
            job = winjob.KillOnCloseJob()
            try:
                self.assertTrue(job.assign(proc._handle))
                self.assertTrue(job.terminate())
                self.assertIsNotNone(proc.wait(10))
            finally:
                job.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_closing_the_job_ends_what_is_left(self):
        from commander_gui import winjob

        with tempfile.TemporaryDirectory() as tmp:
            proc, _out = self._child(tmp)
            job = winjob.KillOnCloseJob()
            try:
                job.assign(proc._handle)
                job.close()  # what Windows does when COMMANDER exits or crashes
                self.assertIsNotNone(proc.wait(10))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()



@unittest.skipUnless(os.name == "nt", "Windows only")
class DiscordPipeTimeoutTest(unittest.TestCase):
    def test_a_pipe_that_never_answers_times_out(self):
        import ctypes
        import threading
        import time
        from ctypes import wintypes

        from commander_gui.discord_rpc import _PipeSocket

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        name = r"\\.\pipe\commander-test-silent-" + str(os.getpid())
        server = kernel32.CreateNamedPipeW(name, 3, 0, 1, 4096, 4096, 0, None)
        threading.Thread(target=lambda: kernel32.ConnectNamedPipe(wintypes.HANDLE(server), None), daemon=True).start()
        time.sleep(0.2)
        pipe = open(name, "r+b", buffering=0)  # noqa: SIM115
        try:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                _PipeSocket(pipe, timeout=0.3).recv(8)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            pipe.close()
            kernel32.CloseHandle(wintypes.HANDLE(server))


@unittest.skipUnless(os.name == "nt", "Windows only")
class FolderPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_typed_windows_paths_are_paths_not_filters(self):
        from commander_gui.ui.file_dialog import looks_like_path

        for path in (r"D:\Games", "d:/x", r"\\nas\share", "/x", "~"):
            self.assertTrue(looks_like_path(path), path)
        for text in ("gamma", "D:", r"mods\x"):
            self.assertFalse(looks_like_path(text), text)

    def test_drive_letters_are_listed(self):
        from commander_gui.ui.file_dialog import drives

        listed = drives()
        roots = [str(root) for _name, root, _used in listed]
        self.assertIn("C:\\", roots)
        self.assertEqual(roots, sorted(roots, key=str.lower))
        self.assertTrue(all(name.endswith(":") or name.endswith(":)") for name, _root, _used in listed))

    def test_hidden_and_system_entries_are_hidden(self):
        import ctypes

        from commander_gui.ui.file_dialog import _Picker

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Games").mkdir()
            secret = Path(tmp) / "$RECYCLE.BIN"
            secret.mkdir()
            ctypes.windll.kernel32.SetFileAttributesW(str(secret), 0x2 | 0x4)
            picker = _Picker(None, "Pick", tmp, "dir")
            try:
                shown = [picker.listing.item(i).text() for i in range(picker.listing.count())]
                self.assertEqual(shown, ["Games"])
                picker._set_hidden(True)
                shown = [picker.listing.item(i).text() for i in range(picker.listing.count())]
                self.assertEqual(sorted(shown), ["$RECYCLE.BIN", "Games"])
            finally:
                picker.deleteLater()
                ctypes.windll.kernel32.SetFileAttributesW(str(secret), 0x80)


if __name__ == "__main__":
    unittest.main()
