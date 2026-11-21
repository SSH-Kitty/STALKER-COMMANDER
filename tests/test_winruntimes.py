import os
import sys
import unittest

from commander_gui import winruntimes


class WinRuntimesTest(unittest.TestCase):
    def test_exit_codes(self):
        rt = winruntimes.RUNTIMES[0]
        self.assertFalse(winruntimes.check_exit_code(0, rt))
        self.assertFalse(winruntimes.check_exit_code(1638, rt))
        self.assertTrue(winruntimes.check_exit_code(3010, rt))
        for bad in (98, 99, 1603):
            with self.assertRaises(RuntimeError):
                winruntimes.check_exit_code(bad, rt)

    def test_command_line_quotes_paths_with_spaces(self):
        self.assertEqual(winruntimes.command_line(()), "")
        self.assertEqual(winruntimes.command_line(("/install", "/quiet")), "/install /quiet")
        line = winruntimes.command_line(("/Q", r"/T:C:\Users\John Smith\AppData\Local\Temp\dxredist"))
        self.assertEqual(line, r'/Q "/T:C:\Users\John Smith\AppData\Local\Temp\dxredist"')

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_extract_into_folder_with_space(self):
        """The command line really reaches the process intact (PowerShell round trip)."""
        import subprocess
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(prefix="dx test ") as tmp:
            out = Path(tmp) / "arg.txt"
            probe = Path(tmp) / "probe.py"
            probe.write_text("import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])")
            target = str(Path(tmp) / "dir with space")
            # Same shape as winruntimes._SCRIPT's Start-Process, minus the UAC elevation.
            script = "Start-Process -FilePath $env:PY -ArgumentList $env:ARGS -Wait -NoNewWindow"
            args = winruntimes.command_line((str(probe), str(out), f"/T:{target}"))
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                env={**os.environ, "PY": sys.executable, "ARGS": args},
                check=True,
            )
            self.assertEqual(out.read_text(), f"/T:{target}")

    def test_nothing_missing_off_windows(self):
        import os

        if os.name != "nt":
            self.assertEqual(winruntimes.missing(), [])


if __name__ == "__main__":
    unittest.main()
