"""Windows process-tree control for the CLI: kill-on-close jobs and Ctrl+C. Qt-free.

Linux runs the CLI in its own session, so ``killpg`` reaches it and every
helper it started, and a SIGINT lets it cancel cleanly. Windows has neither:

* A **job object** with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` holds the CLI
  and everything it starts. COMMANDER keeps the only handle, so Windows ends
  the whole tree when COMMANDER exits - or crashes, or is ended from Task
  Manager - instead of leaving an install writing to the game folders with
  no window. ``terminate()`` is the hard kill for the whole tree.
* **Ctrl+C** is the CLI's cancel, as SIGINT is on Linux. A windowed program
  has no console to send it from, so COMMANDER borrows the CLI's own (hidden)
  console for the moment it takes.
"""

from __future__ import annotations

import os
import threading

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_CTRL_C_EVENT = 0

_console_lock = threading.Lock()


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.AttachConsole.restype = wintypes.BOOL
    kernel32.FreeConsole.argtypes = []
    kernel32.FreeConsole.restype = wintypes.BOOL
    kernel32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    return kernel32


class KillOnCloseJob:
    """One job object per CLI run. Every call is best-effort: a failure
    leaves the old behaviour (the process alone, ``Popen.kill``)."""

    def __init__(self) -> None:
        self._handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        try:
            kernel32 = _kernel32()
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
            ):
                kernel32.CloseHandle(handle)
                return
            self._handle = handle
        except (AttributeError, OSError):
            self._handle = None

    def assign(self, process_handle: int) -> bool:
        """Put the process (``Popen._handle``) in the job; its later children follow."""
        if not self._handle:
            return False
        try:
            return bool(_kernel32().AssignProcessToJobObject(self._handle, int(process_handle)))
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def terminate(self, exit_code: int = 1) -> bool:
        """End every process in the job at once."""
        if not self._handle:
            return False
        try:
            return bool(_kernel32().TerminateJobObject(self._handle, exit_code))
        except (AttributeError, OSError):
            return False

    def close(self) -> None:
        """Let go of the job. Anything still in it is ended (kill on close)."""
        handle, self._handle = self._handle, None
        if handle:
            try:
                _kernel32().CloseHandle(handle)
            except (AttributeError, OSError):
                pass


def send_ctrl_c(pid: int, *, timeout: float = 5.0) -> bool:
    """Deliver Ctrl+C to *pid* and every process on its console.

    Done by a short-lived helper (``-m commander_gui.winjob <pid>``), never
    from COMMANDER itself: borrowing the CLI's console puts the sender on
    it too, and Windows' default answer to the Ctrl+C there is to end the
    process - the GUI. The helper starts detached (no console to leave),
    ignores Ctrl+C itself, attaches to the CLI's console and sends it.

    *pid* must have a console of its own (a console program started with
    ``CREATE_NO_WINDOW``) and not be in a new process group (that turns
    Ctrl+C off for it). False when it could not be sent; the caller then
    ends the job instead.
    """
    if os.name != "nt":
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if _shares_our_console(pid):
        # Ctrl+C goes to every process on that console - this one too. Only
        # possible for a child started without CREATE_NO_WINDOW (COMMANDER
        # itself always adds it, see main._hide_child_consoles).
        return False
    import subprocess
    import sys
    from pathlib import Path

    env = None
    if not getattr(sys, "frozen", False):
        # From source: the helper's python must find this package wherever
        # COMMANDER's working directory is. Frozen, COMMANDER.exe -m does.
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    with _console_lock:
        try:
            done = subprocess.run(
                [sys.executable, "-m", "commander_gui.winjob", str(pid)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x8),
                timeout=timeout,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
    return done.returncode == 0


def _shares_our_console(pid: int) -> bool:
    """True when *pid* is attached to this process's console."""
    import ctypes

    members = (ctypes.c_uint * 256)()
    try:
        count = ctypes.windll.kernel32.GetConsoleProcessList(members, 256)
    except (AttributeError, OSError):
        return False
    return pid in members[: min(count, 256)]


def _send_ctrl_c_here(pid: int) -> bool:
    """The helper's side of send_ctrl_c(): run in a process with no console."""
    try:
        kernel32 = _kernel32()
    except (AttributeError, OSError):
        return False
    kernel32.FreeConsole()  # in case one was inherited after all
    if not kernel32.AttachConsole(pid):
        return False
    # The helper is on that console now too: it must not end on its own event.
    kernel32.SetConsoleCtrlHandler(None, True)
    return bool(kernel32.GenerateConsoleCtrlEvent(_CTRL_C_EVENT, 0))


def allow_ctrl_c_for_children() -> None:
    """Clear an inherited "ignore Ctrl+C" state, at startup.

    Windows passes that state from parent to child, and some launchers start
    programs with it set (Git Bash does). COMMANDER would then pass it on to
    every CLI it starts, and cancel()'s Ctrl+C would be ignored. COMMANDER
    itself never joins the CLI's console (see send_ctrl_c), so no Ctrl+C
    can reach it there.
    """
    if os.name != "nt":
        return
    try:
        _kernel32().SetConsoleCtrlHandler(None, False)
    except (AttributeError, OSError):
        pass


if __name__ == "__main__":
    import sys

    sys.exit(0 if len(sys.argv) == 2 and sys.argv[1].isdigit() and _send_ctrl_c_here(int(sys.argv[1])) else 1)
