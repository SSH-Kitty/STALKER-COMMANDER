"""The Updates page's Game engine box: pick an engine and release, install, go back."""

from __future__ import annotations

import html
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from .. import coop, modded_exes
from ..launcher import set_mo2_executable
from . import reflow
from .common import (
    WARN,
    BackgroundTask,
    NoWheelComboBox,
    StreamTask,
    game_running,
    mo2_running,
    tr,
)
from .dialogs import MessageBox as QMessageBox
from .file_dialog import FileDialog as QFileDialog
from .multiplayer_page import _hint, _section


def _version_text(label: str | None) -> str:
    if label and label.startswith("file:"):
        return label[5:]
    return label or ""


def engines() -> list[tuple[str, str]]:
    """(name, engine) for the engine picker."""
    return [(tr("Modded exes (demonized)"), modded_exes.MODDED), ("neXt-Ray", modded_exes.NEXT_RAY)]


def installed_text(anomaly: str, state: dict | None) -> str:
    """The card's status line: which engine and version the folder runs."""
    if not anomaly:
        return tr("Pick an Anomaly folder for this profile first.")
    if state:
        name = {e: n for n, e in engines()}[state.get("engine") or modded_exes.MODDED]
        version = state.get("version") or ""
        if version.startswith("file:"):
            return tr("In use: {engine} from {file}", engine=name, file=version[5:])
        return tr("In use: {engine} {version}", engine=name, version=version.removeprefix("neXt-Ray "))
    if built := modded_exes.build_date(anomaly):
        return tr("Installed: exes built {date} (GAMMA's or your own)", date=built)
    return tr("No engine exes found in the Anomaly folder.")


def release_text(release: dict, state: dict | None, engine: str) -> str:
    """One picker row: tag, date, and whether it is installed or was last used."""
    text = f"{release['tag']} · {release['published']}"
    if release["tag"] == modded_exes.RECOMMENDED:
        text += " · " + tr("recommended")
    state = state or {}
    clean = lambda v: (v or "").removeprefix("neXt-Ray ")
    if state.get("engine", modded_exes.MODDED) == engine and clean(state.get("version")) == release["tag"]:
        text += " · " + tr("installed")
    elif clean((state.get("last") or {}).get(engine)) == release["tag"]:
        text += " · " + tr("last used")
    return text


def engine_hint(engine: str) -> str:
    """Rich text: neXt-Ray's co-op and ROMEO limit in the theme's warning colour."""
    if engine == modded_exes.NEXT_RAY:
        about = html.escape(tr("neXt-Ray: a faster fork of the modded exes, with DX12, DLSS, FSR and XeSS. AVX CPUs only."))
        limit = html.escape(tr("Multiplayer and ROMEO don't run on it."))
        return f'{about} <span style="color:{WARN.name()}">{limit}</span>'
    return html.escape(tr("The game's engine (demonized's modded exes). GAMMA ships its own version; install a newer one here when a mod or modpack needs it."))


def confirm_text(anomaly: str, engine: str, version: str, gamma: str = "") -> tuple[str, str, str]:
    """(title, message, info) for the install question."""
    from .. import romeo

    installed = modded_exes.installed_engine(anomaly)
    if engine == modded_exes.NEXT_RAY:
        title = tr("Install {version}?", version=version)
        message = tr("COMMANDER replaces Anomaly's engine exes, clears the old engine's loose files from Anomaly's gamedata and db/mods folders, and adds Anomaly (DX12-AVX) to Mod Organizer.")
    else:
        title = tr("Install modded exes {version}?", version=version)
        message = tr("COMMANDER replaces Anomaly's engine exes and, as modpack guides say, clears the old version's loose files from Anomaly's gamedata and db/mods folders.")
    if installed is None:
        info = tr("The current exes and those files are saved first, so Restore original exes can put them back.")
    elif installed != engine:
        info = tr("COMMANDER first puts back the original exes, then installs this engine.")
    else:
        info = ""
    if engine == modded_exes.NEXT_RAY and gamma and romeo.installed(Path(gamma)):
        warning = tr("ROMEO is installed here. It needs modded exes 2026.9.12 or newer, so it won't run on neXt-Ray.")
        info = f"{info}\n\n{warning}" if info else warning
    return title, message, info


def romeo_launch_text() -> tuple[str, str]:
    """(title, message) asked before Play starts ROMEO on neXt-Ray."""
    return (
        tr("ROMEO doesn't run well on neXt-Ray"),
        tr("ROMEO needs modded exes 2026.9.12 or newer. neXt-Ray is based on an older build, so some of ROMEO's mods fail to load, such as Mags Reloaded and Better Stats Bars.")
        + "\n\n"
        + tr("Switch back to modded exes on the Updates page to play ROMEO. The G.A.M.M.A. profile runs fine on neXt-Ray."),
    )


def done_text(release: dict | None, label: str) -> str:
    if release is None or release.get("engine") == modded_exes.NEXT_RAY:
        return tr("{version} installed.", version=_version_text(label))
    return tr("Modded exes {version} installed.", version=_version_text(label))


def sync_mo2(gamma: str, anomaly: str) -> None:
    """Add or remove the DX12 entry in MO2 to match the installed engine;
    pick it when it was just added."""
    if not gamma:
        return
    want = modded_exes.installed_engine(anomaly) == modded_exes.NEXT_RAY
    if set_mo2_executable(gamma, modded_exes.DX12_TITLE, modded_exes.DX12_EXE, want) and want:
        from ..launcher import set_mo2_selected_title

        set_mo2_selected_title(gamma, modded_exes.DX12_TITLE)


class ModdedExesCard:
    """Builds the box; ``frame`` goes into the page's layout."""

    def __init__(self, window, on_installed) -> None:
        self.window = window
        self._on_installed = on_installed
        self._cache: dict[str, list[dict]] = {}  # engine -> its releases
        self._shown_for = ""  # the Anomaly folder whose engine the picker shows
        self._releases: list[dict] = []
        self._releases_failed = False  # stops refresh() re-fetching in a loop after an error
        self._task: StreamTask | None = None
        self._list_task: BackgroundTask | None = None

        self.frame, layout = _section(tr("Game engine"))
        # First under the title: the engine in use, not the one picked below.
        self.status = _hint()
        self.status.setObjectName("")
        layout.addWidget(self.status)
        self.engine = NoWheelComboBox()
        self.engine.setFixedHeight(38)
        for name, engine in engines():
            self.engine.addItem(name, engine)
        self.engine.currentIndexChanged.connect(self._engine_changed)
        layout.addWidget(self.engine)
        self.hint = _hint(engine_hint(modded_exes.MODDED))
        self.hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.hint)

        # The buttons go under the version list when the window is narrow.
        row = reflow.row(8)
        self.versions = NoWheelComboBox()
        self.versions.setMinimumWidth(260)
        self.versions.setFixedHeight(38)
        row.addWidget(self.versions, 1)
        self.install_button = self._button(tr("Install"), self._install_selected, "primary")
        self.file_button = self._button(tr("From a file"), self._install_file)
        self.back_button = self._button("", self._go_back)
        self.restore_button = self._button(tr("Restore original exes"), self._restore)
        self.cancel_button = self._button(tr("Cancel"), lambda: self._task and self._task.cancel())
        row.addWidget(
            reflow.group(
                self.install_button, self.file_button, self.back_button, self.restore_button, self.cancel_button
            )
        )
        layout.addLayout(row)
        note_row = QHBoxLayout()
        note_row.setSpacing(8)
        self.note = _hint()
        note_row.addWidget(self.note, 1)
        self.coop_button = self._button(tr("Open Multiplayer"), lambda: self.window.set_page("multiplayer"))
        note_row.addWidget(self.coop_button)
        layout.addLayout(note_row)
        self._show_installed_engine()
        self.refresh()

    def _show_installed_engine(self) -> None:
        anomaly = self._anomaly()
        engine = (modded_exes.installed_engine(anomaly) if anomaly else None) or modded_exes.MODDED
        self.engine.setCurrentIndex(max(0, self.engine.findData(engine)))

    def _engine_changed(self) -> None:
        engine = self.engine.currentData()
        self.hint.setText(engine_hint(engine))
        self._releases_failed = False
        if engine in self._cache:
            self._on_releases(self._cache[engine], engine)
        else:
            self._releases = []
            self.refresh()

    @staticmethod
    def _button(text: str, slot, name: str = "secondary") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        # Sits in a row with the primary Install: same height, no tall padding.
        button.setProperty("besidePrimary", True)
        button.setFixedHeight(38)
        button.setMinimumWidth(110)
        button.clicked.connect(slot)
        return button

    def _anomaly(self) -> str:
        profile = self.window.settings.active_profile
        return str(profile.anomaly).strip() if profile is not None else ""

    # --------------------------------------------------------------- state
    def refresh(self) -> None:
        anomaly = self._anomaly()
        state = modded_exes.load_state(anomaly) if anomaly else None
        if self._task is None and anomaly and anomaly != self._shown_for:
            self._shown_for = anomaly  # profile switched: show its engine
            self._show_installed_engine()
        self.status.setText(installed_text(anomaly, state))
        for i in range(self.versions.count()):  # tags change after an install
            if release := self.versions.itemData(i):
                self.versions.setItemText(i, release_text(release, state, self.engine.currentData()))
        previous = (state or {}).get("previous")
        self.back_button.setText(tr("Go back to {version}", version=previous) if previous else "")
        self.back_button.setVisible(bool(previous) and not previous.startswith("file:"))
        self.restore_button.setVisible(bool(state))
        coop_on = bool(anomaly) and coop.is_active(anomaly)
        self.note.setText(
            tr("Co-op is on, so this is locked. Switch to singleplayer on the Multiplayer page, update the exes here, then switch co-op back on. Co-op keeps using its own engine.") if coop_on else ""
        )
        self.note.setVisible(coop_on)
        self.coop_button.setVisible(coop_on)
        busy = self._task is not None
        idle = not busy and not self.window.install_busy and bool(anomaly) and not coop_on
        for button in (self.file_button, self.back_button, self.restore_button):
            button.setEnabled(idle)
        self.install_button.setEnabled(idle and self.versions.currentData() is not None)
        self.versions.setEnabled(idle)
        self.engine.setEnabled(idle)
        self.cancel_button.setVisible(busy)
        if self.engine.currentData() not in self._cache and self._list_task is None and not self._releases_failed:
            self._load_releases()

    def _load_releases(self) -> None:
        engine = self.engine.currentData()
        self.versions.clear()
        self.versions.addItem(tr("Loading releases"), None)
        self._list_task = BackgroundTask(lambda: modded_exes.fetch_releases(engine=engine))
        self._list_task.result.connect(lambda releases: self._on_releases(releases, engine))
        self._list_task.error.connect(self._on_releases_error)
        self._list_task.start()

    def _on_releases(self, releases: list[dict], engine: str) -> None:
        self._cache[engine] = releases
        self._list_task = None
        if engine != self.engine.currentData():
            self.refresh()  # picked another engine meanwhile: load that one
            return
        self._releases = releases
        anomaly = self._anomaly()
        state = modded_exes.load_state(anomaly) if anomaly else None
        self.versions.clear()
        for release in releases:
            self.versions.addItem(release_text(release, state, engine), release)
        self._list_task = None
        self.refresh()

    def _on_releases_error(self, message: str) -> None:
        self.versions.clear()
        self.versions.addItem(tr("Couldn't load releases. Check your connection."), None)
        self._list_task = None
        self._releases = []
        self._releases_failed = True
        self.refresh()

    # ------------------------------------------------------------- actions
    def _blocked(self) -> bool:
        if mo2_running() or game_running():
            QMessageBox.warning(
                self.frame,
                tr("Close the game first"),
                tr("Close Mod Organizer and the game, then try again."),
            )
            return True
        return False

    def _confirm(self, version: str, engine: str | None = None) -> bool:
        title, message, info = confirm_text(self._anomaly(), engine or self.engine.currentData(), version, self._gamma())
        answer = QMessageBox.question(
            self.frame,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            info=info,
            note="" if version == modded_exes.RECOMMENDED else tr("This version hasn't been tested with COMMANDER yet."),
            labels={QMessageBox.StandardButton.Yes: tr("Install"), QMessageBox.StandardButton.No: tr("Cancel")},
        )
        return answer == QMessageBox.StandardButton.Yes

    def _install_selected(self) -> None:
        release = self.versions.currentData()
        if release and not self._blocked() and self._confirm(release["tag"]):
            self._run_install(release=release)

    def _go_back(self) -> None:
        anomaly = self._anomaly()
        previous = (modded_exes.load_state(anomaly) or {}).get("previous")
        engine = modded_exes.installed_engine(anomaly)
        release = next((r for r in self._cache.get(engine, []) if r["tag"] == previous), None)
        if release is None:
            QMessageBox.warning(
                self.frame,
                tr("Can't find that version"),
                tr("{version} isn't in the release list right now. Check your connection and try again.", version=previous),
            )
            return
        if not self._blocked() and self._confirm(release["tag"], engine):
            self._run_install(release=release)

    def _install_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self.frame, tr("Pick an engine zip"), str(Path.home()), tr("Zip files") + " (*.zip)"
        )
        if name and not self._blocked() and self._confirm(Path(name).name):
            self._run_install(zip_path=Path(name))

    def _gamma(self) -> str:
        profile = self.window.settings.active_profile
        return str(profile.gamma).strip() if profile is not None else ""

    def _run_install(self, release: dict | None = None, zip_path: Path | None = None) -> None:
        anomaly = Path(self._anomaly())
        gamma = self._gamma()
        label = release["tag"] if release else f"file:{zip_path.name}"
        cancel_event = None

        def worker(report):
            download_dir = None
            try:
                path = zip_path
                if release is not None:
                    download_dir = Path(tempfile.mkdtemp(prefix=".exes-download-", dir=anomaly))
                    path = modded_exes.download(
                        release, download_dir,
                        lambda done, total: report(
                            tr("Downloading {version} {percent}%", version=label, percent=done * 100 // total)
                            if total else tr("Downloading {version}", version=label)
                        ),
                        cancel_event,
                    )
                report(tr("Installing {version}", version=_version_text(label)))
                modded_exes.install(path, anomaly, label, cancel_event)
                sync_mo2(gamma, str(anomaly))
            finally:
                if download_dir is not None:
                    shutil.rmtree(download_dir, ignore_errors=True)

        self._start(worker, done_text(release, label), installed=True)
        cancel_event = self._task.cancel_event

    def _restore(self) -> None:
        if self._blocked():
            return
        answer = QMessageBox.question(
            self.frame,
            tr("Restore the original exes?"),
            tr("COMMANDER puts back the exes and files that were there before it first installed modded exes."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            labels={QMessageBox.StandardButton.Yes: tr("Restore"), QMessageBox.StandardButton.No: tr("Cancel")},
        )
        if answer == QMessageBox.StandardButton.Yes:
            anomaly, gamma = self._anomaly(), self._gamma()

            def worker(report):
                modded_exes.restore_original(anomaly)
                sync_mo2(gamma, anomaly)

            self._start(worker, tr("The original exes are back."), installed=False)

    def _start(self, worker, done: str, installed: bool) -> None:
        self.window.set_install_busy(True, "modded_exes")
        self._task = StreamTask(worker)
        self._task.line.connect(self.status.setText)
        self._task.result.connect(lambda _r: self._finished(done, installed))
        self._task.error.connect(self._failed)
        self._task.start()
        self.refresh()

    def _finished(self, done: str, installed: bool) -> None:
        self._end()
        if installed:
            self._on_installed()
        QMessageBox.information(self.frame, tr("Done"), done)

    def _failed(self, message: str) -> None:
        self._end()
        QMessageBox.warning(self.frame, tr("The engine change didn't finish"), message)

    def _end(self) -> None:
        self._task = None
        self._shown_for = ""  # the engine may have changed: show the installed one
        if self.window.install_operation == "modded_exes":
            self.window.set_install_busy(False)
        self.refresh()
