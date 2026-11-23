import os
import tempfile
import unittest
from pathlib import Path

from commander_gui.launcher import (
    LaunchError,
    shortcut_slug,
    write_desktop_shortcut,
)


class ShortcutSlugTest(unittest.TestCase):
    def test_sanitizes_title(self):
        self.assertEqual(shortcut_slug("Anomaly (DX11-AVX)"), "anomaly-dx11-avx")

    def test_empty_title_falls_back(self):
        self.assertEqual(shortcut_slug("!!!"), "stalker-gamma")


class WriteDesktopShortcutTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", '.desktop files are Linux only')
    def test_writes_desktop_file_with_env_and_exec_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_desktop_shortcut(
                "Anomaly (DX11-AVX)",
                ["gamemoderun", "umu-run", "/games/GAMMA/ModOrganizer.exe", "run", "-e", "Anomaly (DX11-AVX)"],
                {"PROTONPATH": "/opt/GE-Proton with space", "WINEPREFIX": "/pfx"},
                "/games/GAMMA",
                icon="/icons/stalker-gamma.png",
                directory=Path(tmp),
            )
            self.assertEqual(path.name, "anomaly-dx11-avx.desktop")
            content = path.read_text(encoding="utf-8")
            self.assertIn("Name=Anomaly (DX11-AVX)", content)
            self.assertIn("Type=Application", content)
            self.assertIn("Path=/games/GAMMA", content)
            self.assertIn("Terminal=false", content)
            self.assertIn("Icon=/icons/stalker-gamma.png", content)
            exec_line = next(l for l in content.splitlines() if l.startswith("Exec="))
            # env vars inlined, sorted, quoted
            self.assertTrue(exec_line.startswith('Exec=env "PROTONPATH=/opt/GE-Proton with space" "WINEPREFIX=/pfx"'))
            self.assertIn('"Anomaly (DX11-AVX)"', exec_line)
            self.assertTrue(os.stat(path).st_mode & 0o111, "shortcut must be executable")

    @unittest.skipIf(os.name == "nt", '.desktop files are Linux only')
    def test_no_env_omits_env_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_desktop_shortcut(
                "Anomaly", ["wine", "game.exe"], {}, "/games", directory=Path(tmp)
            )
            exec_line = next(
                l for l in path.read_text().splitlines() if l.startswith("Exec=")
            )
            self.assertEqual(exec_line, 'Exec="wine" "game.exe"')

    def test_empty_command_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(LaunchError):
            write_desktop_shortcut("X", [], {}, ".", directory=Path(tmp))

    @unittest.skipIf(os.name == "nt", '.desktop files are Linux only')
    def test_quoting_escapes_special_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_desktop_shortcut(
                "X", ['C:\\dir with "quotes"\\app.exe'], {}, ".", directory=Path(tmp)
            )
            exec_line = next(
                l for l in path.read_text().splitlines() if l.startswith("Exec=")
            )
            # Quote-level escaping (\\ for a backslash, \" for a quote), then
            # every backslash doubled again for the string-value layer.
            self.assertIn('"C:\\\\\\\\dir with \\\\"quotes\\\\"\\\\\\\\app.exe"', exec_line)


@unittest.skipUnless(os.name == "nt", "Windows shortcuts")
class WindowsShortcutTest(unittest.TestCase):
    def test_writes_a_lnk_the_shell_reads_back(self):
        """A real .lnk, read back through the shell (WScript.Shell): the
        program, the quoted arguments, the folder and the icon all survive,
        and the file carries the installer's naming."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            mo2 = Path(tmp) / "GAMMA" / "ModOrganizer.exe"
            icon = Path(tmp) / "commander.ico"
            path = write_desktop_shortcut(
                "Anomaly (DX11-AVX)",
                [str(mo2), "-p", "G.A.M.M.A", "moshortcut://:Anomaly (DX11-AVX)"],
                {"IGNORED": "1"},
                str(mo2.parent),
                icon=str(icon),
                directory=Path(tmp),
            )
            self.assertEqual(path, Path(tmp) / "STALKER COMMANDER - Anomaly (DX11-AVX).lnk")
            script = (
                "$l = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
                "$l.TargetPath; $l.Arguments; $l.WorkingDirectory; $l.IconLocation"
            )
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                env={**os.environ, "LNK": str(path)},
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout.splitlines()
        self.assertEqual(out[0], str(mo2))
        self.assertEqual(out[1], '-p G.A.M.M.A "moshortcut://:Anomaly (DX11-AVX)"')
        self.assertEqual(out[2], str(mo2.parent))
        self.assertEqual(out[3], f"{icon},0")

    def test_names_follow_the_installers_format(self):
        from commander_gui.winshortcut import shortcut_name

        self.assertEqual(shortcut_name("Anomaly (DX11-AVX)"), "STALKER COMMANDER - Anomaly (DX11-AVX)")
        # Characters a Windows file name can't hold, and a trailing dot.
        self.assertEqual(shortcut_name('Mod: "Test"/X?.'), "STALKER COMMANDER - Mod Test X")
        self.assertEqual(shortcut_name("???"), "STALKER COMMANDER")

    def test_the_icon_is_commanders_own_as_an_ico(self):
        """Not COMMANDER.exe's or ModOrganizer.exe's: an .ico made from the
        app's PNG, with sharp small sizes, rewritten only when it changed."""
        import struct

        from commander_gui.steam_shortcuts import bundled_icon, install_ico

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "icons" / "commander.ico"
            self.assertEqual(install_ico(bundled_icon(), dest), str(dest))
            data = dest.read_bytes()
            reserved, kind, count = struct.unpack("<HHH", data[:6])
            self.assertEqual((reserved, kind), (0, 1))
            sizes = [data[6 + 16 * i] or 256 for i in range(count)]
            self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256])
            stamp = dest.stat().st_mtime_ns
            install_ico(bundled_icon(), dest)
            self.assertEqual(dest.stat().st_mtime_ns, stamp)
            self.assertEqual(install_ico(Path(tmp) / "missing.png", dest), "")

    def test_the_desktop_is_the_shells_own(self):
        from commander_gui.launcher import desktop_dir
        from commander_gui.winshortcut import desktop_dir as shell_desktop

        self.assertEqual(desktop_dir(), shell_desktop())
        self.assertTrue(desktop_dir().is_dir())


if __name__ == "__main__":
    unittest.main()
