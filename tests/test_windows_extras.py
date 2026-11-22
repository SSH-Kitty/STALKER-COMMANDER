"""Windows extras: keep-awake, taskbar progress, the laptop GPU choice,
Smart App Control and OneDrive warnings, and the oldest-Windows check."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from commander_gui import gui_settings, wingpu, winpower, winsecurity, wintaskbar

WINDOWS = os.name == "nt"
# Saved at import, before conftest stubs them out for every test.
_REAL_SYNC = wingpu.sync
_REAL_SAC_STATE = winsecurity.smart_app_control_state
_ENTRY = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "entry.py"


class KeepAwakeTest(unittest.TestCase):
    def setUp(self):
        winpower._holds = 0
        self.calls = []
        patcher = patch.object(winpower, "_set", side_effect=lambda flags: self.calls.append(flags) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, winpower, "_holds", 0)

    def test_two_holds_keep_the_pc_awake_until_both_are_released(self):
        winpower.hold()
        winpower.hold()
        winpower.release()
        self.assertEqual(self.calls, [winpower._ES_CONTINUOUS | winpower._ES_SYSTEM_REQUIRED])
        winpower.release()
        self.assertEqual(self.calls[-1], winpower._ES_CONTINUOUS)
        self.assertEqual(winpower.holds(), 0)

    def test_an_extra_release_does_nothing(self):
        winpower.release()
        self.assertEqual(self.calls, [])
        self.assertEqual(winpower.holds(), 0)

    def test_a_cli_run_holds_until_its_thread_has_ended(self):
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication

        from commander_gui.ui.common import CommandRunner

        QApplication.instance() or QApplication([])
        runner = CommandRunner([sys.executable, "-c", "pass"])
        runner.start()
        self.assertEqual(winpower.holds(), 1)
        for _ in range(500):
            QCoreApplication.processEvents()
            if not runner.is_running() and winpower.holds() == 0:
                break
            runner._thread and runner._thread.wait(10)
        self.assertEqual(winpower.holds(), 0)


class TaskbarStateTest(unittest.TestCase):
    def test_a_busy_bar_is_the_moving_fill(self):
        self.assertEqual(wintaskbar.bar_state(0, 0, 0, False), (wintaskbar.INDETERMINATE, 0, 0))

    def test_a_bar_shows_its_share_of_its_own_range(self):
        self.assertEqual(wintaskbar.bar_state(0, 100, 42, False), (wintaskbar.NORMAL, 42, 100))
        self.assertEqual(wintaskbar.bar_state(10, 20, 15, False), (wintaskbar.NORMAL, 5, 10))

    def test_a_value_out_of_range_is_clamped(self):
        self.assertEqual(wintaskbar.bar_state(0, 100, 150, False)[1], 100)
        self.assertEqual(wintaskbar.bar_state(0, 100, -5, False)[1], 0)

    def test_paused_is_yellow(self):
        self.assertEqual(wintaskbar.bar_state(0, 100, 30, True)[0], wintaskbar.PAUSED)

    def test_a_progress_box_run_never_breaks_on_the_taskbar(self):
        from PySide6.QtWidgets import QApplication

        from commander_gui.ui.common import ProgressArea

        QApplication.instance() or QApplication([])
        area = ProgressArea()
        area.on_started()
        area.bar.setRange(0, 100)
        area.bar.setValue(50)
        wintaskbar._controller._poll()
        area.on_finished(1, "failed")
        area.on_started()
        area.on_cancelled()
        area.reset()
        self.assertIsNone(wintaskbar._controller._owner)


class OneDriveTest(unittest.TestCase):
    ROOTS = (r"C:\Users\Player\OneDrive", r"C:\Users\Player\OneDrive - Work")

    def test_a_folder_inside_onedrive_is_found(self):
        self.assertEqual(
            winsecurity.onedrive_folder(r"C:\GAMMA", r"C:\Users\Player\OneDrive\Documents\gamma", roots=list(self.ROOTS)),
            r"C:\Users\Player\OneDrive\Documents\gamma",
        )

    def test_letter_case_does_not_matter(self):
        self.assertIsNotNone(winsecurity.onedrive_folder(r"c:\users\player\onedrive - work\G", roots=list(self.ROOTS)))

    def test_a_sibling_with_a_longer_name_is_not_inside(self):
        self.assertIsNone(winsecurity.onedrive_folder(r"C:\Users\Player\OneDriveBackup\G", roots=list(self.ROOTS[:1])))

    def test_no_onedrive_finds_nothing(self):
        self.assertIsNone(winsecurity.onedrive_folder(r"C:\Users\Player\OneDrive\G", roots=[]))

    def test_roots_come_from_every_account(self):
        env = {"OneDrive": "C:\\A", "OneDriveConsumer": "C:\\A", "OneDriveCommercial": "C:\\B"}
        self.assertEqual(winsecurity.onedrive_roots(env), ["C:\\A", "C:\\B"])

    @unittest.skipIf(not WINDOWS, "the OneDrive check is the Windows branch of windows_drive_ok")
    def test_picking_a_onedrive_folder_asks_first(self):
        from commander_gui.ui import common

        with (
            patch.object(winsecurity, "onedrive_roots", return_value=list(self.ROOTS)),
            patch.object(common.QMessageBox, "warning", return_value=common.QMessageBox.StandardButton.No) as box,
        ):
            self.assertFalse(common.windows_drive_ok(None, r"C:\Users\Player\OneDrive\GAMMA"))
            self.assertTrue(common.windows_drive_ok(None, r"C:\GAMMA"))
        self.assertEqual(box.call_count, 1)


class BlockedStartTest(unittest.TestCase):
    def _error(self, code):
        exc = OSError("blocked")
        exc.winerror = code
        return exc

    def test_smart_app_control_and_antivirus_blocks_are_explained(self):
        self.assertIn("Smart App Control", winsecurity.start_blocked_hint(self._error(4551)))
        self.assertIn("Protection history", winsecurity.start_blocked_hint(self._error(225)))

    def test_other_errors_get_no_hint(self):
        self.assertEqual(winsecurity.start_blocked_hint(self._error(2)), "")
        self.assertEqual(winsecurity.start_blocked_hint(ValueError("x")), "")

    def test_the_launch_error_carries_the_hint(self):
        from commander_gui import launcher

        with (
            patch.object(launcher.subprocess, "Popen", side_effect=self._error(4551)),
            self.assertRaises(launcher.LaunchError) as raised,
        ):
            launcher.launch_detached(["Anomaly.exe"], {}, tempfile.gettempdir())
        self.assertIn("Smart App Control", str(raised.exception))

    @unittest.skipIf(not WINDOWS, "reads the Windows registry")
    def test_the_smart_app_control_state_reads_without_error(self):
        self.assertIn(_REAL_SAC_STATE(), (None, winsecurity.SAC_OFF, winsecurity.SAC_ON, winsecurity.SAC_EVALUATION))


class SmartAppControlNoticeTest(unittest.TestCase):
    def _run(self, state):
        from commander_gui.ui import main_window

        window = SimpleNamespace()
        with (
            patch.object(winsecurity, "smart_app_control_state", return_value=state),
            patch.object(main_window.QMessageBox, "warning") as box,
        ):
            main_window.MainWindow._maybe_warn_smart_app_control(window)
        return box.call_count

    def test_shown_once_when_it_is_on(self):
        self.assertEqual(self._run(winsecurity.SAC_ON), 1)
        self.assertEqual(self._run(winsecurity.SAC_ON), 0)

    def test_not_shown_when_off_or_only_evaluating(self):
        self.assertEqual(self._run(winsecurity.SAC_OFF), 0)
        self.assertEqual(self._run(winsecurity.SAC_EVALUATION), 0)
        self.assertEqual(self._run(None), 0)
        self.assertFalse(gui_settings.load_gui_settings()["smart_app_control_notice_shown"])


class GameExesTest(unittest.TestCase):
    def test_only_the_engine_exes_in_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            for name in ("AnomalyDX11.exe", "AnomalyDX11AVX.exe", "anomalydx9.EXE", "crashrpt.exe", "Anomaly.dll"):
                (bin_dir / name).write_bytes(b"")
            names = sorted(Path(p).name.lower() for p in wingpu.game_exes(tmp))
        self.assertEqual(names, ["anomalydx11.exe", "anomalydx11avx.exe", "anomalydx9.exe"])

    def test_a_missing_folder_has_none(self):
        self.assertEqual(wingpu.game_exes(""), [])
        self.assertEqual(wingpu.game_exes(r"Z:\nowhere"), [])


@unittest.skipIf(not WINDOWS, "writes the Windows registry")
class GpuPreferenceTest(unittest.TestCase):
    """Against a throwaway registry key, never Windows' real one."""

    TEST_KEY = r"Software\STALKER-COMMANDER-tests\UserGpuPreferences"

    def setUp(self):
        import winreg

        patcher = patch.object(wingpu, "KEY", self.TEST_KEY)
        patcher.start()
        self.addCleanup(patcher.stop)

        def remove():
            for key in (self.TEST_KEY, r"Software\STALKER-COMMANDER-tests"):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                except OSError:
                    pass

        self.addCleanup(remove)
        self.exes = [r"C:\GAMMA\anomaly\bin\AnomalyDX11.exe", r"C:\GAMMA\anomaly\bin\AnomalyDX11AVX.exe"]

    def _set(self, exe, value):
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.TEST_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, exe, 0, winreg.REG_SZ, value)

    def test_sets_the_fast_card_but_keeps_the_users_own_choice(self):
        self._set(self.exes[0], "GpuPreference=1;")
        self.assertEqual(wingpu.prefer_high_performance(self.exes), [self.exes[1]])
        self.assertEqual(wingpu.preference(self.exes[0]), "GpuPreference=1;")
        self.assertEqual(wingpu.preference(self.exes[1]), wingpu.HIGH_PERFORMANCE)

    def test_undo_removes_only_what_is_still_commanders(self):
        wingpu.prefer_high_performance(self.exes)
        self._set(self.exes[0], "GpuPreference=1;")  # the user changed it since
        self.assertEqual(sorted(wingpu.undo(self.exes)), sorted(self.exes))
        self.assertEqual(wingpu.preference(self.exes[0]), "GpuPreference=1;")
        self.assertIsNone(wingpu.preference(self.exes[1]))

    def test_sync_remembers_and_takes_back_its_own_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bin").mkdir()
            (Path(tmp) / "bin" / "AnomalyDX11.exe").write_bytes(b"")
            exe = wingpu.game_exes(tmp)[0]
            _REAL_SYNC(tmp)
            self.assertEqual(gui_settings.load_gui_settings()["gpu_preferences_set"], [exe])
            self.assertEqual(wingpu.preference(exe), wingpu.HIGH_PERFORMANCE)
            gui_settings.save_gui_settings(high_performance_gpu=False)
            _REAL_SYNC(tmp)
            self.assertIsNone(wingpu.preference(exe))
            self.assertEqual(gui_settings.load_gui_settings()["gpu_preferences_set"], [])


@unittest.skipIf(not WINDOWS, "sys.getwindowsversion is Windows-only")
class OldestWindowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("commander_entry", _ENTRY)
        cls.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.entry)

    def _old(self, major, build):
        version = SimpleNamespace(major=major, minor=0, build=build, platform_version=(major, 0, build))
        with patch.object(sys, "getwindowsversion", return_value=version):
            return self.entry.windows_too_old()

    def test_windows_10_1809_and_newer_run(self):
        self.assertFalse(self._old(10, 17763))
        self.assertFalse(self._old(10, 26100))  # Windows 11 24H2

    def test_older_windows_is_refused(self):
        self.assertTrue(self._old(10, 17134))  # Windows 10 1803
        self.assertTrue(self._old(6, 9600))  # Windows 8.1
        self.assertTrue(self._old(6, 7601))  # Windows 7

    def test_this_pc_is_new_enough(self):
        self.assertFalse(self.entry.windows_too_old())


if __name__ == "__main__":
    unittest.main()
