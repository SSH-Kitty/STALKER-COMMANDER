"""The Frame generation card: on or off and its settings, kept as
variables in the launch options text. On the Play page, and in the graphics
overlay, where COMMANDER passes the text back and forth."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import gui_settings, launch_options, lossless_win
from .common import exe_pids, info_label, make_card, open_url, section_label, tr
from .file_dialog import FileDialog as QFileDialog


class FrameGenCard(QWidget):
    """get_text and set_text read and write the launch options text."""

    dll_changed = Signal()

    def __init__(self, get_text: Callable[[], str], set_text: Callable[[str], None]) -> None:
        super().__init__()
        self._get_text = get_text
        self._set_text = set_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build())

    #: Each settings row: (row label, variable, ((button text, value), ...)).
    #: A None value leaves the variable out, for the default.
    _ROWS = (
        ("Model", launch_options.FG_MODEL, (("MAKO FG", launch_options.MODEL_MAKO), ("Lossless Scaling", launch_options.MODEL_LOSSLESS))),
        ("Multiplier", launch_options.FG_MULTIPLIER, (("2x", "2"), ("3x", "3"), ("4x", "4"), ("5x", "5"))),
        ("Detail", launch_options.FG_FLOW_SCALE, (("100%", None), ("75%", "0.75"), ("50%", "0.5"), ("25%", "0.25"))),
        ("Mode", launch_options.FG_PERFORMANCE, (("Quality", None), ("Performance", "1"))),
    )

    def _button(self, text: str, key: str | None, value) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("segment")
        button.setCheckable(True)
        button.setFixedHeight(34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False: self._set(key, value))
        return button

    def _build(self) -> QFrame:
        """Frame generation: on/off and its settings, all kept as variables
        in the launch options text, all changing a running game too. Locked,
        with what's missing, until there's a Lossless.dll."""
        card, layout = make_card()
        layout.setSpacing(12)
        # (row's buttons with their values, variable or None for on/off)
        self._rows: list[tuple[list[tuple[QPushButton, object]], str | None]] = []

        header = QHBoxLayout()
        header.addWidget(section_label(tr("Frame generation"), level=2))
        header.addStretch(1)
        toggle = []
        for text, value in ((tr("On"), True), (tr("Off"), False)):
            button = self._button(text, None, value)
            button.setMinimumWidth(72)
            header.addWidget(button)
            toggle.append((button, value))
        self._rows.append((toggle, None))
        layout.addLayout(header)
        self._about = info_label("")
        layout.addWidget(self._about)
        self._missing = info_label("")
        self._missing.setObjectName("warn")
        self._missing.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._missing)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        for row, (label, key, choices) in enumerate(self._ROWS):
            grid.addWidget(info_label(tr(label), wrap=False), row, 0)
            buttons = QHBoxLayout()
            buttons.setSpacing(8)
            row_buttons = []
            for text, value in choices:
                button = self._button(tr(text), key, value)
                buttons.addWidget(button, 1)
                row_buttons.append((button, value))
            grid.addLayout(buttons, row, 1)
            self._rows.append((row_buttons, key))
        layout.addLayout(grid)
        layout.addStretch(1)
        # Confirms what the running game uses.
        self._running = info_label("")
        self._running.setObjectName("accent")
        layout.addWidget(self._running)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._dll_label = info_label("", wrap=False)
        self._dll_label.setObjectName("dim")
        footer.addWidget(self._dll_label, 1)
        self._get_lossless_button = QPushButton(tr("Get Lossless Scaling"))
        self._get_lossless_button.setObjectName("secondary")
        self._get_lossless_button.setProperty("compact", True)
        self._get_lossless_button.clicked.connect(
            lambda: open_url("https://store.steampowered.com/app/993090/Lossless_Scaling/")
        )
        footer.addWidget(self._get_lossless_button)
        self._choose_dll_button = QPushButton(tr("Choose DLL"))
        self._choose_dll_button.setObjectName("secondary")
        self._choose_dll_button.setProperty("compact", True)
        self._choose_dll_button.setToolTip(tr("Pick Lossless Scaling's Lossless.dll yourself."))
        self._choose_dll_button.clicked.connect(self._choose_dll)
        footer.addWidget(self._choose_dll_button)
        layout.addLayout(footer)
        return card

    def _choose_dll(self) -> None:
        dll = launch_options.lossless_dll()
        start = str(dll.parent if dll else Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select Lossless Scaling's DLL"), start, "DLL (*.dll)"
        )
        if path:
            gui_settings.save_gui_settings(lossless_dll=path)
            self.sync()
            self.dll_changed.emit()

    def _set(self, key: str | None, value) -> None:
        text = self._get_text()
        if key is None:
            text = launch_options.frame_gen(text, value)
        else:
            text = launch_options.set_var(text, key, value)
        self._set_text(text)
        # Clicking the picked button again unticks it and leaves the text
        # as it was, so no textChanged: tick it back here.
        self.sync()

    def sync(self) -> bool:
        """Show the card's state from the options text, and lock it with what
        to get until there's a Lossless.dll. True when there is."""
        text = self._get_text()
        on = launch_options.is_on(text, launch_options.FRAME_GEN)
        model = launch_options.frame_gen_model(text)
        mako = model == launch_options.MODEL_MAKO
        steps = []
        dll = launch_options.lossless_dll()
        if mako:
            if not launch_options.mako_fg_ready():
                steps.append(tr("MAKO FG is missing from this COMMANDER install. Reinstall COMMANDER, or pick Lossless Scaling."))
        elif dll is None:
            steps.append(tr("Frame generation needs a file from Lossless Scaling (paid, on Steam). Set its Properties > Compatibility to a Proton version and install it: COMMANDER finds the file there. Have the file already? Use Choose DLL."))
        elif not launch_options.frame_gen_ready(dll, model) and dll.name.lower() == launch_options.BETA_DLL.lower():
            steps.append(tr("lsfg-vk.dll is from Lossless Scaling's lsfg-vk beta. In Steam, switch Lossless Scaling back to its usual version (Properties > Betas > None), or pick its Lossless.dll."))
        elif not launch_options.frame_gen_ready(dll, model):
            steps.append(tr("That file isn't one frame generation can use. Pick Lossless.dll from Lossless Scaling."))
        self._about.setText(tr(
            "MAKO FG frame generation for smoother gameplay. Plays best above 40 FPS but adds a little input lag." if mako
            else "Lossless Scaling Frame Generation for smoother gameplay. Plays best above 40 FPS but adds a little input lag."))
        label = launch_options.mako_fg_label() if mako else launch_options.dll_label(dll) if dll else ""
        self._dll_label.setText(label if mako or dll else tr("No DLL found"))
        self._dll_label.setToolTip("" if mako else str(dll or ""))
        lines = list(steps)
        # Changes reach a running game only when frame generation was loaded
        # at its start; say so rather than let the buttons seem to do nothing.
        loaded = game_has_frame_gen()
        if not steps and loaded is False:
            lines = [tr("Frame generation wasn't on when the game started, so changes apply from the next launch. In co-op, let COMMANDER restart Steam when it asks.")]
        self._running.setText(tr("Loaded in the running game, with {dll}.", dll=label) if loaded and label else "")
        self._running.setVisible(bool(loaded and label))
        self._missing.setText("\n".join(lines))
        self._missing.setVisible(bool(lines))
        self._get_lossless_button.setVisible(not mako and dll is None)
        self._choose_dll_button.setVisible(not mako)
        for buttons, key in self._rows:
            current = on if key is None else model if key == launch_options.FG_MODEL else launch_options.var(text, key)
            if key == launch_options.FG_MULTIPLIER:
                current = current or "2"
            for button, value in buttons:
                # Off, the settings show no pick: they'd look live. The model
                # is a choice, not a setting: always shown, always open.
                button.setChecked(value == current and (on or key in (None, launch_options.FG_MODEL)))
                # Left on (typed in, or set before an uninstall): Off stays
                # clickable so it can still be switched off.
                button.setEnabled(True if key == launch_options.FG_MODEL else on and not steps if key else not steps or (on and value is False))
        return not steps


class WindowsFrameGenCard(FrameGenCard):
    """The same card for Windows, where frame generation is the Lossless
    Scaling app: COMMANDER writes a profile for the game into its settings
    and starts it with the game (see lossless_win). Needs the app, not a DLL;
    changes apply from the next launch."""

    _ROWS = FrameGenCard._ROWS[1:]  # no model to pick: it is Lossless Scaling's

    def __init__(self, get_text, set_text) -> None:
        super().__init__(get_text, set_text)
        self._get_lossless_button.setText(tr("Get Lossless Scaling"))
        self._choose_dll_button.setText(tr("Choose app"))
        self._choose_dll_button.setToolTip(tr("Pick LosslessScaling.exe yourself."))

    def _choose_dll(self) -> None:
        exe = lossless_win.lossless_exe()
        start = str(exe.parent if exe else Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select Lossless Scaling"), start, "LosslessScaling.exe (LosslessScaling.exe);;EXE (*.exe)"
        )
        if path:
            gui_settings.save_gui_settings(lossless_exe=path)
            self.sync()
            self.dll_changed.emit()

    def sync(self) -> bool:
        text = self._get_text()
        on = launch_options.is_on(text, launch_options.FRAME_GEN)
        exe = lossless_win.lossless_exe()
        steps = []
        if exe is None:
            steps.append(tr("Frame generation needs the Lossless Scaling app (paid, on Steam). Install it and COMMANDER finds it there. Have it already somewhere else? Use Choose app."))
        elif not lossless_win.settings_path().is_file():
            steps.append(tr(lossless_win.MESSAGES["no-file"]))
        self._about.setText(tr("Lossless Scaling Frame Generation for smoother gameplay. Plays best above 40 FPS but adds a little input lag."))
        self._dll_label.setText(f"{exe.parent.name}/{exe.name}" if exe else tr("Lossless Scaling not found"))
        self._dll_label.setToolTip(str(exe or ""))
        lines = list(steps)
        if not steps and on:
            lines.append(tr("COMMANDER starts Lossless Scaling with the game. Changes apply from the next launch."))
        self._running.setVisible(False)
        self._missing.setText("\n".join(lines))
        self._missing.setVisible(bool(lines))
        self._get_lossless_button.setVisible(exe is None)
        for buttons, key in self._rows:
            current = on if key is None else launch_options.var(text, key)
            if key == launch_options.FG_MULTIPLIER:
                current = current or "2"
            for button, value in buttons:
                button.setChecked(value == current and (on or key is None))
                button.setEnabled(on and not steps if key else not steps or (on and value is False))
        return not steps


def game_has_frame_gen() -> bool | None:
    """Whether the running game has frame generation loaded (its library
    mapped in): None when no game runs."""
    pids = exe_pids("AnomalyDX")
    if not pids:
        return None
    for pid in pids:
        try:
            maps = Path(f"/proc/{pid}/maps").read_bytes()
        except OSError:
            continue
        if b"libmako-render" in maps:
            return True
    return False
