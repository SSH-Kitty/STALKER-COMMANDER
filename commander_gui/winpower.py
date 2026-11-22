"""Keep Windows awake while COMMANDER works. Qt-free.

A GAMMA install or update downloads for hours, and Windows' default sleep
timer (often 15-30 minutes on a laptop) cut it off halfway. While a hold is
taken, Windows doesn't go to sleep on its own; the screen may still turn off,
and the user can still put the PC to sleep by hand.

Holds are counted, so two runs at once (an install and a verify) keep the
PC awake until both have ended. ``SetThreadExecutionState`` belongs to the
calling thread, so every hold and release must come from the same thread:
COMMANDER takes them on the GUI thread only.

A no-op everywhere but Windows.
"""

from __future__ import annotations

import os

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_holds = 0


def _set(flags: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except (AttributeError, OSError):
        return False


def hold() -> None:
    """Keep the PC from sleeping until the matching :func:`release`."""
    global _holds
    _holds += 1
    if _holds == 1:
        _set(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)


def release() -> None:
    """Drop one :func:`hold`; Windows may sleep again once none is left."""
    global _holds
    if _holds <= 0:
        return
    _holds -= 1
    if _holds == 0:
        _set(_ES_CONTINUOUS)


def holds() -> int:
    return _holds
