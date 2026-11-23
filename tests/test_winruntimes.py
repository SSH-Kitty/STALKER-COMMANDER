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

    def test_restart_initiated_counts_as_restart_needed(self):
        self.assertTrue(winruntimes.check_exit_code(1641, winruntimes.RUNTIMES[0]))

    def test_directx_download_is_hash_pinned(self):
        dx = next(rt for rt in winruntimes.RUNTIMES if rt.extract)
        self.assertRegex(dx.sha256, r"^[0-9a-f]{64}$")

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_extract_into_folder_with_space(self):
        """The command line and exit code really pass through ShellExecuteEx
        (the "runas" path, with "open" so no UAC prompt appears)."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(prefix="dx test ") as tmp:
            out = Path(tmp) / "arg.txt"
            probe = Path(tmp) / "probe.py"
            probe.write_text("import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); sys.exit(7)")
            target = str(Path(tmp) / "dir with space")
            code = winruntimes._run_elevated(sys.executable, (str(probe), str(out), f"/T:{target}"), verb="open")
            self.assertEqual(code, 7)
            self.assertEqual(out.read_text(), f"/T:{target}")

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_signature_check_needs_microsoft_as_the_signer(self):
        import tempfile
        from pathlib import Path

        # python.exe is validly signed, by the Python Software Foundation.
        self.assertFalse(winruntimes.signed_by_microsoft(sys.executable))
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.exe"
            plain.write_bytes(b"MZ" + bytes(100))
            self.assertFalse(winruntimes.signed_by_microsoft(str(plain)))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_a_held_installer_cannot_be_swapped(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "setup.exe"
            exe.write_bytes(b"MZ")
            held = winruntimes._hold(str(exe))
            try:
                self.assertIsNotNone(held)
                with self.assertRaises(PermissionError):
                    exe.rename(exe.with_name("other.exe"))
                with self.assertRaises(PermissionError):
                    exe.write_bytes(b"evil")
                self.assertEqual(exe.read_bytes(), b"MZ")
            finally:
                winruntimes._release(held)

    def test_nothing_missing_off_windows(self):
        import os

        if os.name != "nt":
            self.assertEqual(winruntimes.missing(), [])


if __name__ == "__main__":
    unittest.main()
