"""Windows prep before an install: the downloads junction, the Defender
exclusion and long paths (commander_gui/winprep.py and its Install page gate)."""

import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from commander_gui import gui_settings, winprep
from commander_gui.ui.install_page import InstallPage

WINDOWS = os.name == "nt"
# Saved at import, before conftest's autouse fixture stubs it out for every
# other test; winprep.apply stays stubbed (it must never run for real).
_REAL_GATE = InstallPage._windows_prep_first
_REAL_APPLY = winprep.apply


class PlanTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_only_folders_not_excluded_yet_are_planned(self):
        step = winprep.plan(
            [r"C:\GAMMA\anomaly", r"C:\GAMMA\gamma", r"C:\GAMMA\cache"],
            [r"c:\gamma\ANOMALY"],
            wanted=True,
            long_paths_on=True,
        )
        self.assertEqual([Path(p).name for p in step.exclude], ["gamma", "cache"])
        self.assertFalse(step.long_paths)

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_the_same_folder_twice_is_excluded_once(self):
        step = winprep.plan([r"C:\GAMMA\x", r"C:\GAMMA\X"], [], wanted=True, long_paths_on=True)
        self.assertEqual(len(step.exclude), 1)

    def test_option_off_plans_nothing_but_a_needed_link(self):
        self.assertFalse(winprep.plan([r"C:\GAMMA\gamma"], [], wanted=False, long_paths_on=False))
        step = winprep.plan([r"C:\GAMMA\gamma"], [], wanted=False, long_paths_on=False, link=("l", "t"))
        self.assertTrue(step)
        self.assertEqual((step.exclude, step.long_paths), ((), False))

    def test_long_paths_only_when_off(self):
        self.assertTrue(winprep.plan([], [], wanted=True, long_paths_on=False).long_paths)
        self.assertFalse(winprep.plan([], [], wanted=True, long_paths_on=True))


class ScriptTest(unittest.TestCase):
    def test_quotes_are_doubled_including_typographic_ones(self):
        self.assertEqual(winprep._quote("O'Brien"), "'O''Brien'")
        self.assertEqual(winprep._quote("a\u2019b"), "'a\u2019\u2019b'")

    def test_script_has_only_the_planned_steps(self):
        text = winprep.script(winprep.Plan(exclude=(r"C:\G\a",)))
        self.assertIn("Add-MpPreference -ExclusionPath @('C:\\G\\a')", text)
        self.assertNotIn("LongPathsEnabled", text)
        self.assertNotIn("SymbolicLink", text)
        text = winprep.script(winprep.Plan(long_paths=True, link=(r"C:\G\gamma\downloads", r"\\nas\cache")))
        self.assertNotIn("Add-MpPreference", text)
        self.assertIn("LongPathsEnabled", text)
        self.assertIn("-Target '\\\\nas\\cache'", text)
        self.assertTrue(text.endswith("exit $r"))

    def test_arguments_carry_the_script_whole(self):
        step = winprep.Plan(exclude=(r"C:\Games\O'Brien\GAMMA\cache",), long_paths=True)
        args = winprep.powershell_args(step)
        self.assertEqual(args[-2], "-EncodedCommand")
        self.assertEqual(base64.b64decode(args[-1]).decode("utf-16-le"), winprep.script(step))

    @unittest.skipUnless(WINDOWS, "needs Windows PowerShell")
    def test_script_parses_in_powershell(self):
        """Parsed only, never run: running it needs admin rights."""
        step = winprep.Plan(
            exclude=(r"C:\Games\O'Brien\GAMMA\anomaly", "C:\\G\u2019s\\cache"),
            long_paths=True,
            link=(r"C:\Games\GAMMA\gamma\downloads", r"\\nas\share\cache"),
        )
        encoded = base64.b64encode(winprep.script(step).encode("utf-16-le")).decode("ascii")
        check = (
            "$s = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded + "'));"
            "$e = $null; [void][Management.Automation.Language.Parser]::ParseInput($s, [ref]$null, [ref]$e);"
            "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }"
        )
        done = subprocess.run(
            [winprep.powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", check],
            capture_output=True, text=True, timeout=120, check=False,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_powershell_is_found_by_full_path(self):
        exe = Path(winprep.powershell_exe())
        self.assertTrue(exe.is_absolute())
        self.assertEqual(exe.name, "powershell.exe")


class ResultTest(unittest.TestCase):
    ALL = winprep.Plan(exclude=("a",), long_paths=True, link=("l", "t"))

    def test_declined(self):
        from commander_gui.winruntimes import _DECLINED

        self.assertEqual(winprep.result_for(_DECLINED, self.ALL), winprep.Result(declined=True))

    def test_bits(self):
        self.assertEqual(winprep.result_for(0, self.ALL), winprep.Result())
        result = winprep.result_for(winprep.NO_DEFENDER | winprep.LINK_FAILED, self.ALL)
        self.assertTrue(result.no_defender and result.link_failed)
        self.assertFalse(result.exclusion_failed or result.long_paths_failed)

    def test_bits_of_steps_not_planned_are_ignored(self):
        result = winprep.result_for(15, winprep.Plan(long_paths=True))
        self.assertEqual(result, winprep.Result(long_paths_failed=True))

    def test_powershell_failing_counts_as_every_step_failing(self):
        result = winprep.result_for(-196608, self.ALL)
        self.assertTrue(result.exclusion_failed and result.long_paths_failed and result.link_failed)
        self.assertFalse(result.declined)

    def test_apply_runs_hidden_and_elevated(self):
        with patch("commander_gui.winruntimes._run_elevated", return_value=0) as run:
            self.assertEqual(_REAL_APPLY(self.ALL), winprep.Result())
        exe, args = run.call_args.args
        self.assertEqual(exe, winprep.powershell_exe())
        self.assertEqual(args, winprep.powershell_args(self.ALL))
        self.assertEqual(run.call_args.kwargs, {"show": 0})


class LinkTest(unittest.TestCase):
    def test_existing_folder_or_old_layout_needs_no_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma = Path(tmp) / "gamma"
            (gamma / "downloads").mkdir(parents=True)
            self.assertFalse(winprep.link_needed(gamma, Path(tmp) / "cache"))
            shutil.rmtree(gamma / "downloads")
            self.assertTrue(winprep.link_needed(gamma, Path(tmp) / "cache"))
            self.assertFalse(winprep.link_needed(gamma, gamma / "downloads"))

    @unittest.skipUnless(WINDOWS, "junctions are Windows-only")
    def test_junction_is_made_without_admin_and_deleting_gamma_keeps_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma, cache = Path(tmp) / "gamma", Path(tmp) / "cache"
            gamma.mkdir()
            cache.mkdir()
            (cache / "mod.7z").write_bytes(b"x")
            self.assertTrue(winprep.make_downloads_link(gamma, cache))
            link = gamma / "downloads"
            self.assertTrue(link.is_junction())
            self.assertTrue(winprep.is_link(link))
            self.assertTrue((link / "mod.7z").is_file())
            self.assertTrue(winprep.make_downloads_link(gamma, cache))  # already there
            shutil.rmtree(gamma)
            self.assertTrue((cache / "mod.7z").is_file())

    @unittest.skipUnless(WINDOWS, "junctions are Windows-only")
    def test_no_junction_and_no_symlink_leaves_it_to_the_elevated_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma = Path(tmp) / "gamma"
            gamma.mkdir()
            with (
                patch("_winapi.CreateJunction", side_effect=OSError),
                patch("commander_gui.winprep.os.symlink", side_effect=OSError),
            ):
                self.assertFalse(winprep.make_downloads_link(gamma, r"\\nas\cache"))


@unittest.skipUnless(WINDOWS, "junctions are Windows-only")
class WalkersStepOverJunctionsTest(unittest.TestCase):
    """gamma\\downloads is a junction to the whole download cache: nothing
    that walks the GAMMA folder may go into it."""

    def _install(self, tmp):
        gamma, cache = Path(tmp) / "gamma", Path(tmp) / "cache"
        (gamma / "mods").mkdir(parents=True)
        (gamma / "mods" / "a.txt").write_bytes(b"12")
        cache.mkdir()
        (cache / "big.7z").write_bytes(b"x" * 1000)
        winprep.make_downloads_link(gamma, cache)
        return gamma, cache

    def test_dir_size(self):
        from commander_gui.ui.common import dir_size

        with tempfile.TemporaryDirectory() as tmp:
            gamma, _cache = self._install(tmp)
            self.assertEqual(dir_size(gamma), 2)

    def test_move_refuses_it_like_a_symlink(self):
        from commander_gui.ui.utilities_page import _copy_dir_tree

        with tempfile.TemporaryDirectory() as tmp:
            gamma, _cache = self._install(tmp)
            with self.assertRaises(ValueError):
                _copy_dir_tree(gamma, Path(tmp) / "moved", lambda _m: None)
            self.assertFalse((Path(tmp) / "moved" / "downloads" / "big.7z").exists())

    def test_copy_install_keeps_it_a_junction(self):
        from commander_gui.game_installs import _copy_tree

        with tempfile.TemporaryDirectory() as tmp:
            gamma, cache = self._install(tmp)
            copy = Path(tmp) / "copy"
            _copy_tree(gamma, copy, [0], lambda: None)
            self.assertTrue((copy / "downloads").is_junction())
            self.assertEqual((copy / "downloads").resolve(), cache.resolve())
            self.assertTrue((copy / "mods" / "a.txt").is_file())


class SavedExclusionsTest(unittest.TestCase):
    def test_bad_values_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gui.json"
            path.write_text('{"defender_excluded": ["C:\\\\GAMMA\\\\cache", 3, null]}', encoding="utf-8")
            with patch("commander_gui.gui_settings.gui_settings_path", return_value=path):
                self.assertEqual(gui_settings.load_gui_settings()["defender_excluded"], ["C:\\GAMMA\\cache"])
                path.write_text('{"defender_excluded": "C:\\\\GAMMA"}', encoding="utf-8")
                self.assertEqual(gui_settings.load_gui_settings()["defender_excluded"], [])


@unittest.skipUnless(WINDOWS, "the gate only runs on Windows")
class InstallGateTest(unittest.TestCase):
    """InstallPage._windows_prep_first / _on_windows_prep_done."""

    def _page(self, tmp, checked=True):
        base = Path(tmp)
        for name in ("anomaly", "gamma", "cache"):
            (base / name).mkdir(exist_ok=True)
        page = InstallPage.__new__(InstallPage)
        page.window = Mock()
        page.install_button = Mock()
        page.anomaly_button = Mock()
        page.checkboxes = {"win_prep": Mock(isChecked=Mock(return_value=checked))}
        page._update_button_states = Mock()
        profile = SimpleNamespace(anomaly=str(base / "anomaly"), gamma=str(base / "gamma"), cache=str(base / "cache"))
        return page, profile

    def _gate(self, page, profile, then, *, excluded=(), long_paths=False, gamma_step=True):
        with (
            patch("commander_gui.ui.install_page.gui_settings.load_gui_settings", return_value={"defender_excluded": list(excluded)}),
            patch("commander_gui.ui.install_page.winprep.long_paths_enabled", return_value=long_paths),
            patch("commander_gui.ui.install_page.BackgroundTask") as task_cls,
        ):
            waiting = _REAL_GATE(page, profile, then, gamma_step=gamma_step)
        return waiting, task_cls

    def test_nothing_to_do_goes_straight_on_but_makes_the_junction(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, profile = self._page(tmp)
            folders = [profile.anomaly, profile.gamma, profile.cache]
            waiting, task_cls = self._gate(page, profile, Mock(), excluded=folders, long_paths=True)
            self.assertFalse(waiting)
            task_cls.assert_not_called()
            self.assertTrue((Path(profile.gamma) / "downloads").is_junction())

    def test_anomaly_step_makes_no_junction(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, profile = self._page(tmp, checked=False)
            waiting, _task = self._gate(page, profile, Mock(), gamma_step=False)
            self.assertFalse(waiting)
            self.assertFalse(os.path.lexists(Path(profile.gamma) / "downloads"))

    def test_asks_once_per_folder_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, profile = self._page(tmp)
            waiting, task_cls = self._gate(page, profile, Mock())
            self.assertTrue(waiting)
            step = task_cls.call_args.args[1]
            self.assertEqual(len(step.exclude), 3)
            self.assertTrue(step.long_paths)
            self.assertIsNone(step.link)
            page.window.set_install_busy.assert_called_with(True, "gamma")
            # Declined or not, the retry that follows doesn't ask again.
            waiting, task_cls = self._gate(page, profile, Mock())
            self.assertFalse(waiting)

    def test_option_off_never_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, profile = self._page(tmp, checked=False)
            waiting, task_cls = self._gate(page, profile, Mock())
            self.assertFalse(waiting)
            task_cls.assert_not_called()

    def test_bare_page_without_the_option_goes_straight_on(self):
        """Other code (and tests) start installs on pages built without it."""
        with tempfile.TemporaryDirectory() as tmp:
            page, profile = self._page(tmp)
            del page.checkboxes
            waiting, _task = self._gate(page, profile, Mock())
            self.assertFalse(waiting)

    def _done(self, page, step, result, saved=()):
        then = Mock()
        with (
            patch("commander_gui.ui.install_page.gui_settings.load_gui_settings", return_value={"defender_excluded": list(saved)}),
            patch("commander_gui.ui.install_page.gui_settings.save_gui_settings") as save,
            patch("commander_gui.ui.install_page.QMessageBox.warning") as warning,
        ):
            page._on_windows_prep_done(step, result, then)
        return then, save, warning

    def test_success_saves_the_exclusions_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, _profile = self._page(tmp)
            step = winprep.Plan(exclude=(r"C:\G\a", r"C:\G\b"), long_paths=True)
            then, save, warning = self._done(page, step, winprep.Result(), saved=[r"c:\g\a"])
            then.assert_called_once_with()
            save.assert_called_once_with(defender_excluded=[r"c:\g\a", r"C:\G\b"])
            warning.assert_not_called()

    def test_declined_warns_saves_nothing_and_still_installs(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, _profile = self._page(tmp)
            then, save, warning = self._done(page, winprep.Plan(exclude=(r"C:\G\a",)), winprep.Result(declined=True))
            then.assert_called_once_with()
            save.assert_not_called()
            warning.assert_called_once()

    def test_other_antivirus_lists_the_folders_and_is_not_asked_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, _profile = self._page(tmp)
            step = winprep.Plan(exclude=(r"C:\G\a",))
            then, save, warning = self._done(page, step, winprep.Result(no_defender=True))
            then.assert_called_once_with()
            save.assert_called_once()
            self.assertIn(r"C:\G\a", warning.call_args.args[2])

    def test_missing_link_stops_the_gamma_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, _profile = self._page(tmp)
            step = winprep.Plan(link=(r"C:\G\gamma\downloads", r"\\nas\cache"))
            for result in (winprep.Result(declined=True), winprep.Result(link_failed=True)):
                then, _save, warning = self._done(page, step, result)
                then.assert_not_called()
                warning.assert_called_once()
                page.window.set_install_busy.assert_called_with(False)


@unittest.skipUnless(WINDOWS, "junctions are Windows-only")
class UpdateApplyLinkTest(unittest.TestCase):
    """update apply makes the same admin-only symlink when it is missing."""

    def _apply(self, tmp, *, link_ok=True):
        from PySide6.QtWidgets import QApplication

        from commander_gui.parsers import UpdateDiff
        from commander_gui.settings import CliProfile, CliSettings
        from commander_gui.ui.dialogs import MessageBox as QMessageBox
        from commander_gui.ui.update_page import UpdatePage
        from commander_gui.updates import UpdateStatus

        QApplication.instance() or QApplication([])
        gamma, cache = Path(tmp) / "gamma", Path(tmp) / "cache"
        gamma.mkdir()
        profile = CliProfile(active=True, profile_name="Test", gamma=str(gamma), cache=str(cache))

        class FakeWindow:
            settings = CliSettings(profiles=[profile])
            install_busy = False

            def refresh_settings(self):
                pass

            def set_install_busy(self, *a, **kw):
                pass

        page = UpdatePage(FakeWindow())
        page._render(UpdateStatus(diffs=[UpdateDiff(status="Modified", text="X")]))
        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            patch.object(QMessageBox, "warning") as warning,
            patch("commander_gui.ui.update_page.free_space_bytes", return_value=None),
            patch("commander_gui.ui.update_page.CommandRunner") as runner_cls,
            patch("commander_gui.ui.update_page.winprep.make_downloads_link", wraps=winprep.make_downloads_link)
            if link_ok
            else patch("commander_gui.ui.update_page.winprep.make_downloads_link", return_value=False),
        ):
            page._apply()
        return gamma, cache, runner_cls, warning

    def test_junction_is_made_before_the_cli_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamma, cache, runner_cls, warning = self._apply(tmp)
            self.assertTrue((gamma / "downloads").is_junction())
            self.assertTrue(cache.is_dir())
            runner_cls.assert_called_once()
            warning.assert_not_called()

    def test_no_link_stops_before_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            _gamma, _cache, runner_cls, warning = self._apply(tmp, link_ok=False)
            runner_cls.assert_not_called()
            warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
