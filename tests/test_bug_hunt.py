"""Regression tests for the first Windows bug hunt (v1.4.0 Windows).

Platform-neutral fixes live here; Windows-only ones in test_windows_port.py.
"""

import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

APP = QApplication.instance() or QApplication([])


class ReflowWatcherTests(unittest.TestCase):
    def test_a_deleted_page_leaves_the_window_spy(self):
        from commander_gui.ui import reflow

        top = QWidget()
        QVBoxLayout(top)
        old_page, new_page = QWidget(top), QWidget(top)
        reflow.watch(old_page)
        reflow.watch(new_page)
        spy = reflow._spy(top)
        self.assertEqual(len(spy.watchers), 2)
        old_page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(len(spy.watchers), 1)
        # A resize after the language switch rebuilt the pages must not raise.
        event = QResizeEvent(QSize(800, 600), QSize(700, 500))
        spy.eventFilter(object(), event)
        top.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_a_stale_watcher_is_dropped_instead_of_raising(self):
        from commander_gui.ui import reflow

        top = QWidget()
        spy = reflow._spy(top)

        class Dead:
            def window_resizing(self):
                raise RuntimeError("Internal C++ object already deleted.")

        live_calls = []

        class Live:
            def window_resizing(self):
                live_calls.append(1)

        dead, live = Dead(), Live()
        spy.watchers[:] = [dead, live]
        spy.eventFilter(object(), QResizeEvent(QSize(10, 10), QSize(5, 5)))
        self.assertEqual(live_calls, [1])
        self.assertNotIn(dead, spy.watchers)
        top.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class QtMessageHandlerTests(unittest.TestCase):
    def test_no_stderr_in_a_windowed_build_is_not_an_error(self):
        from unittest.mock import patch

        from PySide6.QtCore import QtMsgType

        from commander_gui import main

        with patch.object(main, "_PREVIOUS_QT_HANDLER", None), patch.object(main.sys, "stderr", None):
            main._quiet_qt_message_handler(QtMsgType.QtInfoMsg, None, "hello")


class AssistantPathTests(unittest.TestCase):
    """The Assistant must look where COMMANDER writes, on every platform
    (it used ~/.config on Windows too: no dumps, wrong theme)."""

    def test_dumps_and_settings_are_commanders_own(self):
        from assistant.dump import default_dumps_dir
        from assistant.ui.theme import config_path
        from commander_gui.config import gui_settings_path
        from commander_gui.log_dump import log_dumps_dir

        self.assertEqual(default_dumps_dir(), log_dumps_dir())
        self.assertEqual(config_path(), gui_settings_path())


if __name__ == "__main__":
    unittest.main()
