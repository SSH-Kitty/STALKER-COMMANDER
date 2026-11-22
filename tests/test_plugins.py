"""The ModDB browser plugin: install from an archive, update checks, download events."""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from commander_gui import plugins


def _archive(path: Path, files: dict[str, str]) -> str:
    with tarfile.open(path, "w:xz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return hashlib.sha512(path.read_bytes()).hexdigest()


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        plugins.data_root().mkdir(parents=True)

    def _plugin(self, version: str = "1") -> tuple[Path, str]:
        meta = json.dumps({"protocol": plugins.PROTOCOL, "qt": "6.11.1", "exec": "run"})
        archive = self.tmp / f"plugin-{version}.tar.xz"
        digest = _archive(archive, {"moddb-browser/plugin.json": meta, "moddb-browser/run": version})
        return archive, digest

    def test_install_then_update_replaces_it(self) -> None:
        for version in ("1", "2"):
            archive, digest = self._plugin(version)
            plugins.install_archive(archive, digest)
            info = plugins.installed()
            self.assertEqual(plugins.launcher(info).read_text(), version)
        self.assertEqual(sorted(p.name for p in plugins.data_root().iterdir()), ["moddb-browser"])

    def test_checksum_mismatch_leaves_installed_plugin(self) -> None:
        archive, digest = self._plugin("1")
        plugins.install_archive(archive, digest)
        bad, _digest = self._plugin("2")
        with self.assertRaises(plugins.PluginError):
            plugins.install_archive(bad, "0" * 128)
        self.assertEqual(plugins.launcher(plugins.installed()).read_text(), "1")

    def test_failed_swap_puts_the_installed_plugin_back(self) -> None:
        archive, digest = self._plugin("1")
        plugins.install_archive(archive, digest)
        new, digest = self._plugin("2")
        real_replace = os.replace

        def replace(src, dst):
            if Path(src).parent.name.startswith(".plugin-staging-"):
                raise OSError("disk full")
            real_replace(src, dst)

        with mock.patch.object(plugins.os, "replace", replace), self.assertRaises(OSError):
            plugins.install_archive(new, digest)
        self.assertEqual(plugins.launcher(plugins.installed()).read_text(), "1")

    def test_install_from_a_file_uses_a_checksum_saved_next_to_it(self) -> None:
        archive, digest = self._plugin("1")
        self.assertIsNone(plugins.local_checksum(archive))
        Path(f"{archive}.sha512sum").write_text(f"{digest}  {archive.name}\n")
        self.assertEqual(plugins.local_checksum(archive), digest)
        plugins.install_archive(archive, plugins.local_checksum(archive))
        self.assertEqual(plugins.launcher(plugins.installed()).read_text(), "1")

    def test_archive_without_plugin_json_is_refused(self) -> None:
        archive = self.tmp / "other.tar.xz"
        digest = _archive(archive, {"something/else": "x"})
        with self.assertRaises(plugins.PluginError):
            plugins.install_archive(archive, digest)
        self.assertIsNone(plugins.installed())

    def test_launcher_outside_plugin_is_refused(self) -> None:
        archive, digest = self._plugin()
        plugins.install_archive(archive, digest)
        with self.assertRaises(plugins.PluginError):
            plugins.launcher({"exec": "../../../bin/sh"})

    def test_needs_update(self) -> None:
        ok = {"protocol": plugins.PROTOCOL, "qt": "6.11.1"}
        self.assertFalse(plugins.needs_update(ok, "6.11.1"))
        self.assertTrue(plugins.needs_update(ok, "6.12.0"))
        self.assertFalse(plugins.needs_update(ok, "6.10.2"))
        self.assertTrue(plugins.needs_update({**ok, "protocol": plugins.PROTOCOL + 1}, "6.11.1"))
        self.assertTrue(plugins.needs_update({}, "6.11.1"))
        with mock.patch.dict(os.environ, {"APPIMAGE": ""}):
            self.assertFalse(plugins.needs_update({**ok, "qt": "6.0.0"}))

    @unittest.skipUnless(os.name == "nt", "the Windows build")
    def test_the_windows_build_asks_for_a_plugin_built_with_its_qt(self) -> None:
        """COMMANDER.exe and the plugin come from the same pinned Qt (CI), so
        an older plugin Qt means a newer plugin exists - as in the AppImage.
        A source checkout runs any Qt, so it never compares."""
        import PySide6

        old = {"protocol": plugins.PROTOCOL, "qt": "6.0.0"}
        with mock.patch.dict(os.environ, {"APPIMAGE": ""}):
            self.assertFalse(plugins.needs_update(old))
            with mock.patch.object(plugins.sys, "frozen", True, create=True):
                self.assertTrue(plugins.needs_update(old))
                self.assertFalse(plugins.needs_update({**old, "qt": PySide6.__version__}))

    def test_parse_event_only_trusts_the_downloads_folder(self) -> None:
        plugins.downloads_dir().mkdir(parents=True)
        inside = plugins.downloads_dir() / "mod.7z"
        inside.write_text("x")
        outside = self.tmp / "elsewhere.7z"
        outside.write_text("x")

        def line(path) -> str:
            return json.dumps({"event": "downloaded", "file": str(path), "url": "https://x"})

        self.assertEqual(plugins.parse_event(line(inside)), inside.resolve())
        self.assertIsNone(plugins.parse_event(line(outside)))
        self.assertIsNone(plugins.parse_event(line(plugins.downloads_dir() / "missing.7z")))
        self.assertIsNone(plugins.parse_event("Qt: some warning"))
        self.assertIsNone(plugins.parse_event("[1, 2]"))

    def test_delete_downloads_in_a_shared_folder_only_deletes_the_browsers_files(self) -> None:
        shared = self.tmp / "Downloads"
        shared.mkdir()
        ours, theirs, replaced = shared / "mod.7z", shared / "photo.jpg", shared / "other.7z"
        for path in (ours, theirs, replaced):
            path.write_text("x")
        with mock.patch("commander_gui.gui_settings.load_gui_settings", return_value={"moddb_downloads": str(shared)}):
            self.assertEqual(plugins.downloads_dir(), shared)
            plugins.remember_download(ours)
            plugins.remember_download(replaced)
            replaced.write_text("the player's own file, same name")
            self.assertEqual(plugins.downloaded_files(), [ours])
            plugins.remember_download(ours)  # drops the stale entry
            self.assertEqual(set(plugins._load_download_record()), {str(ours)})
            plugins.delete_downloads()
        self.assertFalse(ours.exists())
        self.assertTrue(theirs.exists())
        self.assertTrue(replaced.exists())

    def test_release_candidates_own_release_first_and_stable_skips_prereleases(self) -> None:
        feed = ["v1.5.0-Unstable1", "v1.4.1", "v1.4.0", "v1.4.0-Unstable2"]
        self.assertEqual(plugins.release_candidates(feed, "1.4.0"), ["v1.4.0", "v1.4.1"])
        self.assertEqual(
            plugins.release_candidates(feed, "1.4.0-unstable2"),
            ["v1.4.0-Unstable2", "v1.5.0-Unstable1", "v1.4.1", "v1.4.0"],
        )

    def test_asset_name_names_system_and_arch(self) -> None:
        self.assertRegex(plugins.asset_name(), r"^COMMANDER-ModDB-Browser-Plugin-(linux|windows|macos)-\w+\.tar\.xz$")



class AdBlockTests(unittest.TestCase):
    def test_ad_hosts_and_their_subdomains_are_blocked_but_moddb_is_not(self) -> None:
        try:
            from commander_gui.moddb_browser import is_ad_host
        except ImportError:
            self.skipTest("QtWebEngine isn't installed")
        self.assertTrue(is_ad_host("securepubads.g.doubleclick.net"))
        self.assertTrue(is_ad_host("cdn.intergient.com"))
        self.assertTrue(is_ad_host("HTML-LOAD.CC"))
        self.assertFalse(is_ad_host("www.moddb.com"))
        self.assertFalse(is_ad_host("sjc3.dl.dbolical.com"))
        self.assertFalse(is_ad_host("challenges.cloudflare.com"))
        self.assertFalse(is_ad_host("notdoubleclick.net"))


class BrowserShutdownTests(unittest.TestCase):
    """The browser closes with COMMANDER: left running, a download it then
    finished reached nobody, and the next COMMANDER let a second one start on
    the same cookie and cache folder."""

    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication

        from commander_gui.ui import moddb_plugin

        QApplication.instance() or QApplication([])
        self.module = moddb_plugin
        self.plugin = moddb_plugin.ModDBPlugin(window=None)
        patcher = mock.patch.object(moddb_plugin, "_CONTROLLER", self.plugin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _process(self, finishes: bool) -> mock.Mock:
        process = mock.Mock()
        process.waitForFinished.return_value = finishes
        self.plugin._process = process
        return process

    def test_nothing_open_is_nothing_to_do(self) -> None:
        self.module.shutdown()
        self.assertFalse(self.plugin.running)

    def test_the_browser_is_asked_to_close_and_no_crash_box_follows(self) -> None:
        process = self._process(finishes=True)
        self.module.shutdown()
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        # Its own exit must not raise "closed unexpectedly" during the quit.
        process.finished.disconnect.assert_called_once_with(self.plugin._finished)
        process.errorOccurred.disconnect.assert_called_once_with(self.plugin._failed)
        self.assertFalse(self.plugin.running)

    def test_a_browser_that_ignores_the_close_is_killed(self) -> None:
        process = self._process(finishes=False)
        self.module.shutdown()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertFalse(self.plugin.running)

    def test_the_quit_handler_closes_it_before_anything_can_exit(self) -> None:
        """main's aboutToQuit handler may os._exit(): the browser goes first."""
        source = (Path(__file__).resolve().parents[1] / "commander_gui" / "main.py").read_text(encoding="utf-8")
        handler = source[source.index("def _shutdown() -> None:"):]
        self.assertLess(handler.index("moddb_plugin.shutdown()"), handler.index("os._exit(0)"))


class WindowsSpecTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1] / "packaging" / "windows"

    def test_both_exes_turn_utf8_mode_on(self) -> None:
        """PyInstaller's bootloader takes Python's own -X key, "utf8": the
        "utf8_mode=1" both specs had was silently ignored, so COMMANDER.exe
        ran on the ANSI code page and the plugin read its stdin with it."""
        for spec in ("commander.spec", "moddb-browser.spec"):
            with self.subTest(spec=spec):
                text = (self.ROOT / spec).read_text(encoding="utf-8")
                self.assertIn('("X utf8", None, "OPTION")', text)
                self.assertNotIn("utf8_mode", text)

    def test_the_plugin_launches_its_own_exe(self) -> None:
        """plugin.json's exec, the spec's exe name and the pack script agree."""
        spec = (self.ROOT / "moddb-browser.spec").read_text(encoding="utf-8")
        pack = (self.ROOT / "pack_moddb_plugin.py").read_text(encoding="utf-8")
        self.assertIn('name="moddb-browser"', spec)
        self.assertIn('EXE = "moddb-browser.exe"', pack)


if __name__ == "__main__":
    unittest.main()
