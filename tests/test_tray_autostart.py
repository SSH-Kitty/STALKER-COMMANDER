"""The login entry's --autostart flag: added once, never carried into a relaunch."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from commander_gui import autostart
from commander_gui.deck_launch import relaunch_argv


class AutostartFlagTests(unittest.TestCase):
    def test_old_entry_gets_the_flag_once(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
            path = Path(home) / "autostart" / "stalker-gamma-commander.desktop"
            path.parent.mkdir()
            path.write_text("[Desktop Entry]\nExec=\"/some where/COMMANDER.AppImage\"\nTerminal=false\n")
            autostart.add_autostart_flag()
            autostart.add_autostart_flag()
            self.assertIn('Exec="/some where/COMMANDER.AppImage" --autostart\n', path.read_text())
            self.assertEqual(path.read_text().count("--autostart"), 1)

    def test_relaunch_drops_the_flag(self):
        argv = relaunch_argv(deck=True, orig_argv=["/x", "--autostart"], executable="/x")
        self.assertNotIn("--autostart", argv)


if __name__ == "__main__":
    unittest.main()
