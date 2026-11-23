"""Windows extras: keep-awake, taskbar progress, the laptop GPU choice,
Smart App Control and OneDrive warnings, the oldest-Windows check, the
page file check and the uninstaller's cleanup."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from commander_gui import (
    gui_settings,
    wincleanup,
    wingpu,
    winpagefile,
    winpower,
    winsecurity,
    wintaskbar,
)

WINDOWS = os.name == "nt"
# Saved at import, before conftest stubs them out for every test.
_REAL_SYNC = wingpu.sync
_REAL_SAC_STATE = winsecurity.smart_app_control_state
_REAL_PAGE_FILE = winpagefile.page_file
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

    def test_the_uninstaller_may_run_the_cleanup(self):
        self.assertIn("commander_gui.wincleanup", self.entry.HELPER_MODULES)


class PageFileTest(unittest.TestCase):
    def test_reading_the_setting(self):
        self.assertTrue(winpagefile.parse(["?:\\pagefile.sys"]).managed)
        self.assertTrue(winpagefile.parse(["C:\\pagefile.sys 0 0"]).managed)
        self.assertTrue(winpagefile.parse(["C:\\pagefile.sys"]).managed)
        fixed = winpagefile.parse(["C:\\pagefile.sys 2048 4096", "D:\\pagefile.sys 1024 1024"])
        self.assertEqual((fixed.managed, fixed.max_mb), (False, 5120))
        self.assertTrue(winpagefile.parse([""]).off)
        self.assertTrue(winpagefile.parse([]).off)

    def test_too_small_only_when_fixed_or_off_and_short_of_32_gb(self):
        managed = winpagefile.PageFile(True, 0)
        off = winpagefile.PageFile(False, 0)
        self.assertFalse(winpagefile.too_small(managed, 8192))
        self.assertTrue(winpagefile.too_small(off, 16384))
        self.assertFalse(winpagefile.too_small(off, 32768))
        self.assertFalse(winpagefile.too_small(winpagefile.PageFile(False, 16384), 16384))
        self.assertTrue(winpagefile.too_small(winpagefile.PageFile(False, 4096), 16384))
        self.assertFalse(winpagefile.too_small(None, 8192))
        self.assertFalse(winpagefile.too_small(off, 0))

    @unittest.skipIf(not WINDOWS, "reads the Windows registry")
    def test_this_pcs_setting_and_ram_read_without_error(self):
        self.assertIsInstance(_REAL_PAGE_FILE(), (winpagefile.PageFile, type(None)))
        self.assertGreater(winpagefile.ram_mb(), 0)


@unittest.skipIf(not WINDOWS, "the page file check is Windows-only")
class PageFileWarningTest(unittest.TestCase):
    def _ask(self, setting, answer):
        from commander_gui.ui import common

        with (
            patch.object(winpagefile, "page_file", return_value=setting),
            patch.object(winpagefile, "ram_mb", return_value=16384),
            patch.object(winpagefile, "open_settings") as opened,
            patch.object(common.QMessageBox, "warning", return_value=answer) as box,
        ):
            ok = common.page_file_ok(None)
        return ok, box, opened

    def test_play_anyway_is_remembered_until_the_setting_changes(self):
        from commander_gui.ui.common import QMessageBox

        off = winpagefile.PageFile(False, 0)
        ok, box, _opened = self._ask(off, QMessageBox.StandardButton.No)
        self.assertTrue(ok)
        self.assertIn("no page file", box.call_args.args[2])
        self.assertIn("16 GB", box.call_args.args[2])
        self.assertEqual(self._ask(off, QMessageBox.StandardButton.No)[1].call_count, 0)
        ok, box, _opened = self._ask(winpagefile.PageFile(False, 4096), QMessageBox.StandardButton.No)
        self.assertEqual(box.call_count, 1)
        self.assertIn("4 GB", box.call_args.args[2])

    def test_open_settings_stops_the_launch(self):
        from commander_gui.ui.common import QMessageBox

        ok, _box, opened = self._ask(winpagefile.PageFile(False, 0), QMessageBox.StandardButton.Yes)
        self.assertFalse(ok)
        opened.assert_called_once()

    def test_a_managed_page_file_asks_nothing(self):
        ok, box, _opened = self._ask(winpagefile.PageFile(True, 0), None)
        self.assertTrue(ok)
        self.assertEqual(box.call_count, 0)


class OutOfMemoryCrashTest(unittest.TestCase):
    def test_the_engine_out_of_memory_crash_points_at_the_page_file(self):
        from assistant.analyzers.xray import analyze_xray

        lines = [
            "FATAL ERROR",
            "",
            "[error]Expression    : fatal error",
            "[error]Function      : xrMemory::mem_alloc",
            "[error]Arguments     : Out of memory. Memory request: 262144 K",
        ]
        findings = analyze_xray("xray.log", "game log", lines)
        self.assertTrue(any("ran out of memory" in f.title for f in findings), [f.title for f in findings])
        self.assertTrue(any("page file" in f.suggestion for f in findings))

    def test_the_out_of_memory_exit_codes_are_explained(self):
        from assistant import knowledge

        for code in ("0xC0000017", "-1073741523"):  # STATUS_NO_MEMORY, STATUS_COMMITMENT_LIMIT
            self.assertIn("page file", knowledge.launch_exit_code(code)[1])


class UninstallCleanupTest(unittest.TestCase):
    APP = r"C:\Users\Player\AppData\Local\Programs\STALKER COMMANDER"

    def _entry(self, key, exe):
        return (key, 0x00, [("appname", 0x01, "x"), ("Exe", 0x01, f'"{exe}"')])

    def test_only_shortcuts_into_the_install_folder_are_removed(self):
        shortcuts = [
            self._entry("0", self.APP + r"\COMMANDER.exe"),
            self._entry("1", r"C:\Games\Other\game.exe"),
            self._entry("2", self.APP.lower() + r"\commander.exe"),
            self._entry("3", self.APP + r" 2\COMMANDER.exe"),  # a different folder
        ]
        kept = wincleanup.without_shortcuts_into(shortcuts, self.APP)
        self.assertEqual([key for key, _t, _v in kept], ["1", "3"])

    def test_the_defender_script_removes_exactly_the_saved_folders(self):
        script = wincleanup.removal_script([r"C:\GAMMA\anomaly", r"C:\Users\O'Brien\gamma"])
        self.assertIn("Remove-MpPreference -ExclusionPath @('C:\\GAMMA\\anomaly','C:\\Users\\O''Brien\\gamma')", script)
        self.assertNotIn("Add-MpPreference", script)

    @unittest.skipIf(not WINDOWS, "wincleanup only runs on Windows")
    def test_the_count_step_reports_the_saved_exclusions(self):
        gui_settings.save_gui_settings(defender_excluded=[r"C:\GAMMA\a", r"C:\GAMMA\b"])
        self.assertEqual(wincleanup.main(["defender-count"]), 2)
        self.assertEqual(wincleanup.main(["no-such-step"]), wincleanup.FAILED)

    @unittest.skipIf(not WINDOWS, "wincleanup only runs on Windows")
    def test_a_silent_uninstall_never_asks_for_admin_rights(self):
        gui_settings.save_gui_settings(defender_excluded=[r"C:\GAMMA\a"])
        with (
            patch.object(wincleanup, "_is_admin", return_value=False),
            patch("commander_gui.winruntimes._run_elevated") as elevated,
        ):
            self.assertEqual(wincleanup.main(["defender", "--only-if-admin"]), wincleanup.DECLINED)
        elevated.assert_not_called()

    @unittest.skipIf(not WINDOWS, "wincleanup only runs on Windows")
    def test_removed_exclusions_are_forgotten_and_declined_ones_kept(self):
        gui_settings.save_gui_settings(defender_excluded=[r"C:\GAMMA\a"])
        with patch("commander_gui.winruntimes._run_elevated", return_value=98):  # UAC declined
            self.assertEqual(wincleanup.main(["defender"]), wincleanup.DECLINED)
        self.assertEqual(wincleanup.excluded_folders(), [r"C:\GAMMA\a"])
        with patch("commander_gui.winruntimes._run_elevated", return_value=0):
            self.assertEqual(wincleanup.main(["defender"]), 0)
        self.assertEqual(wincleanup.excluded_folders(), [])

    @unittest.skipIf(not WINDOWS, "wincleanup only runs on Windows")
    def test_steam_open_leaves_the_shortcuts_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            from commander_gui import steam_shortcuts as st

            vdf = Path(tmp) / "shortcuts.vdf"
            st.write_shortcuts(vdf, st.set_shortcuts_map([], [self._entry("0", self.APP + r"\COMMANDER.exe")]))
            with (
                patch.object(st, "find_userdata_config_dirs", return_value=[Path(tmp)]),
                patch.object(st, "steam_is_running", return_value=True),
                patch.object(wincleanup, "undo_gpu_preferences"),
            ):
                self.assertEqual(wincleanup.main(["windows-changes", "--app", self.APP]), wincleanup.STEAM_RUNNING)
            self.assertEqual(len(st.get_shortcuts_map(st.read_shortcuts(vdf))), 1)
            with (
                patch.object(st, "find_userdata_config_dirs", return_value=[Path(tmp)]),
                patch.object(st, "steam_is_running", return_value=False),
                patch.object(wincleanup, "undo_gpu_preferences"),
            ):
                self.assertEqual(wincleanup.main(["windows-changes", "--app", self.APP]), 0)
            self.assertEqual(st.get_shortcuts_map(st.read_shortcuts(vdf)), [])


if __name__ == "__main__":
    unittest.main()
