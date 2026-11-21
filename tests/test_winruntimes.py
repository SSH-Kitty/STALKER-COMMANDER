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

    def test_nothing_missing_off_windows(self):
        import os

        if os.name != "nt":
            self.assertEqual(winruntimes.missing(), [])


if __name__ == "__main__":
    unittest.main()
