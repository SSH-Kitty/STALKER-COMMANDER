"""Install and update progress on COMMANDER's taskbar button (Windows).

While a run is going, the taskbar button fills green the way a file copy's
does, so it shows from any other app. It turns yellow while paused. When a
run ends while COMMANDER isn't the active window, the button flashes, and
stays red after a failure until the user comes back to it.

Only the newest run started from a progress box (``ProgressArea``) is shown:
a progress box calls :func:`begin` when its run starts and :func:`end` when
it is over, and the bar it owns is read every half second in between, so
none of the places that move a bar need to know about the taskbar.

``ITaskbarList3`` is reached through ctypes (no pywin32 or comtypes in the
build). Everything here is a no-op off Windows, and every COM failure is
swallowed: the taskbar is a nicety and must never break an install.
"""

from __future__ import annotations

import os
import weakref

# TBPFLAG values.
NOPROGRESS = 0x0
INDETERMINATE = 0x1
NORMAL = 0x2
ERROR = 0x4
PAUSED = 0x8

_CLSID_TASKBARLIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
_CLSCTX_INPROC_SERVER = 0x1
# Vtable slots: IUnknown (0-2), ITaskbarList (3-7), ITaskbarList2 (8),
# then ITaskbarList3's SetProgressValue and SetProgressState.
_SLOT_HRINIT = 3
_SLOT_SET_PROGRESS_VALUE = 9
_SLOT_SET_PROGRESS_STATE = 10

_POLL_MS = 500


class _TaskbarList:
    """The ``ITaskbarList3`` COM object, or nothing where it can't be made."""

    def __init__(self) -> None:
        self._obj = None
        self._vtable = None
        if os.name != "nt":
            return
        try:
            self._create()
        except (AttributeError, OSError, ValueError):
            self._obj = None

    def _create(self) -> None:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        ole32 = ctypes.windll.ole32
        # Qt has already set COM up on the GUI thread; this only adds a
        # reference (S_FALSE) or reports a different model, which is fine.
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        clsid, iid = GUID(), GUID()
        if ole32.CLSIDFromString(_CLSID_TASKBARLIST, ctypes.byref(clsid)) != 0:
            return
        if ole32.IIDFromString(_IID_ITASKBARLIST3, ctypes.byref(iid)) != 0:
            return
        obj = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER, ctypes.byref(iid), ctypes.byref(obj)
        )
        if hr != 0 or not obj.value:
            return
        self._obj = obj
        self._vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        if self._call(_SLOT_HRINIT) != 0:
            self._obj = None

    def _call(self, slot: int, *args) -> int:
        import ctypes

        argtypes = [ctypes.c_void_p]
        for arg in args:
            argtypes.append(ctypes.c_ulonglong if isinstance(arg, _U64) else ctypes.c_void_p if isinstance(arg, _Hwnd) else ctypes.c_int)
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, *argtypes)
        method = proto(self._vtable[slot])
        return method(self._obj, *(int(a) for a in args))

    @property
    def ok(self) -> bool:
        return self._obj is not None

    def set_state(self, hwnd: int, state: int) -> None:
        if self.ok and hwnd:
            try:
                self._call(_SLOT_SET_PROGRESS_STATE, _Hwnd(hwnd), state)
            except OSError:
                pass

    def set_value(self, hwnd: int, done: int, total: int) -> None:
        if self.ok and hwnd and total > 0:
            try:
                self._call(_SLOT_SET_PROGRESS_VALUE, _Hwnd(hwnd), _U64(done), _U64(total))
            except OSError:
                pass


class _Hwnd(int):
    """A window handle argument (pointer-sized)."""


class _U64(int):
    """A ULONGLONG argument."""


def bar_state(minimum: int, maximum: int, value: int, paused: bool) -> tuple[int, int, int]:
    """(TBPFLAG, done, total) for a progress bar's range and value.

    A busy bar (range 0-0) is the taskbar's moving "indeterminate" fill.
    """
    if maximum <= minimum:
        return (PAUSED if paused else INDETERMINATE), 0, 0
    total = maximum - minimum
    done = min(max(value - minimum, 0), total)
    return (PAUSED if paused else NORMAL), done, total


def _top_window(widget):
    """The window that owns the taskbar button (a dialog's main window)."""
    window = widget.window()
    while window.parentWidget() is not None:
        window = window.parentWidget().window()
    return window


class _Controller:
    def __init__(self) -> None:
        self._taskbar: _TaskbarList | None = None
        self._owner = None  # weakref to the ProgressArea being shown
        self._hwnd = 0
        self._shown: tuple[int, int, int] | None = None
        self._timer = None
        self._clear_on_activate = False
        self._watching_activation = False

    def _list(self) -> _TaskbarList:
        if self._taskbar is None:
            self._taskbar = _TaskbarList()
        return self._taskbar

    def begin(self, area) -> None:
        if os.name != "nt" or not self._list().ok:
            return
        from PySide6.QtCore import QTimer

        try:
            hwnd = int(_top_window(area).winId())
        except RuntimeError:
            return
        if self._hwnd and self._hwnd != hwnd:
            self._list().set_state(self._hwnd, NOPROGRESS)
        self._owner = weakref.ref(area)
        self._hwnd = hwnd
        self._shown = None
        self._clear_on_activate = False
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setInterval(_POLL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    def _current(self):
        area = self._owner() if self._owner is not None else None
        return area

    def _poll(self) -> None:
        area = self._current()
        if area is None:
            self._stop(NOPROGRESS)
            return
        try:
            bar = area.bar
            state = bar_state(bar.minimum(), bar.maximum(), bar.value(), area.is_paused)
        except RuntimeError:  # the progress box was deleted
            self._stop(NOPROGRESS)
            return
        if state == self._shown:
            return
        self._shown = state
        flag, done, total = state
        taskbar = self._list()
        taskbar.set_state(self._hwnd, flag)
        if total:
            taskbar.set_value(self._hwnd, done, total)

    def _stop(self, flag: int) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._owner = None
        self._shown = None
        if self._hwnd:
            taskbar = self._list()
            if flag == ERROR:
                taskbar.set_value(self._hwnd, 1, 1)
            taskbar.set_state(self._hwnd, flag)

    def end(self, area, ok: bool | None) -> None:
        """The run of *area* is over: *ok* True (done), False (failed) or
        None (cancelled or reset, which needs no attention)."""
        if self._current() is not area or area is None:
            return
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        active = app is not None and app.applicationState() == Qt.ApplicationState.ApplicationActive
        if ok is None or active or ok:
            self._stop(NOPROGRESS)
        else:
            self._stop(ERROR)
            self._clear_on_activate = True
            self._watch_activation(app)
        if ok is not None and not active:
            try:
                QApplication.alert(_top_window(area), 0)  # flashes until clicked
            except RuntimeError:
                pass

    def _watch_activation(self, app) -> None:
        if self._watching_activation or app is None:
            return
        self._watching_activation = True
        app.applicationStateChanged.connect(self._on_app_state)

    def _on_app_state(self, state) -> None:
        from PySide6.QtCore import Qt

        if (
            state == Qt.ApplicationState.ApplicationActive
            and self._clear_on_activate
            and self._owner is None
        ):
            self._clear_on_activate = False
            if self._hwnd:
                self._list().set_state(self._hwnd, NOPROGRESS)


_controller = _Controller()


def begin(area) -> None:
    """Show *area*'s bar on the taskbar button from now on."""
    try:
        _controller.begin(area)
    except Exception:  # noqa: BLE001, S110 - never let the taskbar break a run
        pass


def end(area, ok: bool | None) -> None:
    """*area*'s run has ended (see ``_Controller.end``)."""
    try:
        _controller.end(area, ok)
    except Exception:  # noqa: BLE001, S110 - never let the taskbar break a run
        pass
