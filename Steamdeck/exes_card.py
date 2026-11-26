"""The Update screen's Game engine card: pick an engine and release, install, go back.

The desktop Updates page's ModdedExesCard (commander_gui/ui/exes_card.py)
with Deck rows and overlays in place of its combo box and message boxes.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt

from commander_gui import coop, modded_exes
from commander_gui.i18n import tr
from commander_gui.ui.common import (
    BackgroundTask,
    StreamTask,
    game_running,
    mo2_running,
)
from commander_gui.ui.exes_card import (
    _version_text,
    confirm_text,
    done_text,
    engine_hint,
    engines,
    installed_text,
    release_text,
    sync_mo2,
)

from .widgets import (
    DeckCard,
    DeckPicker,
    DeckRow,
    deck_button,
    deck_label,
    picker_overlay,
)


class DeckExesCard(QObject):
    """``card`` goes into the screen's layout."""

    def __init__(self, screen) -> None:
        super().__init__(screen)
        self.screen = screen
        self.window = screen.window
        self._cache: dict[str, list[dict]] = {}  # engine -> its releases
        self._failed: set[str] = set()  # engines whose list failed: no retry loop
        self._engine = modded_exes.MODDED
        self._shown_for = ""  # the Anomaly folder whose engine the picker shows
        self._releases: list[dict] = []
        self._release: dict | None = None
        self._task: StreamTask | None = None
        self._list_task: BackgroundTask | None = None

        self.card = DeckCard()
        body = self.card.body
        body.addWidget(deck_label(tr("Game engine"), role="rowTitle"))
        self.status = deck_label("", role="body", wrap=True)  # the engine in use
        body.addWidget(self.status)
        self.engine_row = DeckRow(tr("Engine"))
        self.engine_row.activated.connect(self._pick_engine)
        body.addWidget(self.engine_row)
        self.hint = deck_label(engine_hint(self._engine), role="caption", wrap=True)
        self.hint.setTextFormat(Qt.TextFormat.RichText)
        body.addWidget(self.hint)
        self.version_row = DeckRow(tr("Version"))
        self.version_row.activated.connect(self._pick_version)
        body.addWidget(self.version_row)
        self.install_button = deck_button(tr("Install"), role="primary", on_click=self._install_selected)
        self.file_button = deck_button(tr("From a file"), on_click=self._install_file)
        self.back_button = deck_button("", on_click=self._go_back)
        self.restore_button = deck_button(tr("Restore original exes"), on_click=self._restore)
        self.cancel_button = deck_button(tr("Cancel"), on_click=lambda: self._task and self._task.cancel())
        self.note = deck_label(tr("Co-op is on, so this is locked. Switch to singleplayer on the Multiplayer page, update the exes here, then switch co-op back on. Co-op keeps using its own engine."), role="caption", wrap=True)
        self.coop_button = deck_button(tr("Open Multiplayer"), on_click=lambda: self.window.set_page("multiplayer"))
        for widget in (self.install_button, self.file_button, self.back_button, self.restore_button, self.cancel_button, self.note, self.coop_button):
            body.addWidget(widget)

    def _anomaly(self) -> str:
        profile = self.screen.profile()
        return str(profile.anomaly).strip() if profile is not None else ""

    def _gamma(self) -> str:
        profile = self.screen.profile()
        return str(profile.gamma).strip() if profile is not None else ""

    def _set_engine(self, engine: str) -> None:
        self._engine = engine
        self.hint.setText(engine_hint(engine))
        self._releases = self._cache.get(engine, [])
        self._release = self._releases[0] if self._releases else None

    def _pick_engine(self) -> None:
        picker = DeckPicker("", [(name, engine) for name, engine in engines()], self._engine)

        def _chosen(engine: object) -> None:
            self.window.dismiss_overlay()
            self._set_engine(str(engine))
            self.refresh()

        picker.chosen.connect(_chosen)
        self.window.show_overlay(picker_overlay(self.window, tr("Engine"), picker))

    # --------------------------------------------------------------- state
    def refresh(self) -> None:
        anomaly = self._anomaly()
        state = modded_exes.load_state(anomaly) if anomaly else None
        if self._task is None and anomaly and anomaly != self._shown_for:
            self._shown_for = anomaly  # profile switched: show its engine
            self._set_engine(modded_exes.installed_engine(anomaly) or modded_exes.MODDED)
        self.engine_row.set_value({e: n for n, e in engines()}[self._engine])
        if self._task is None:
            self.status.setText(installed_text(anomaly, state))
        previous = (state or {}).get("previous")
        self.back_button.setText(tr("Go back to {version}", version=previous) if previous else "")
        self.back_button.setVisible(bool(previous) and not previous.startswith("file:"))
        self.restore_button.setVisible(bool(state))
        coop_on = bool(anomaly) and coop.is_active(anomaly)
        self.note.setVisible(coop_on)
        self.coop_button.setVisible(coop_on)
        busy = self._task is not None
        idle = not busy and not self.window.install_busy and bool(anomaly) and not coop_on
        for widget in (self.file_button, self.back_button, self.restore_button, self.version_row, self.engine_row):
            widget.setEnabled(idle)
        self.install_button.setEnabled(idle and self._release is not None)
        self.cancel_button.setVisible(busy)
        if self._release is not None:
            self.version_row.set_value(self._release_text(self._release))
        elif self._list_task is not None:
            self.version_row.set_value(tr("Loading releases"))
        if self._engine not in self._cache and self._engine not in self._failed and self._list_task is None:
            self._load_releases()

    def _release_text(self, release: dict) -> str:
        anomaly = self._anomaly()
        return release_text(release, modded_exes.load_state(anomaly) if anomaly else None, self._engine)

    def _load_releases(self) -> None:
        engine = self._engine
        self._failed.discard(engine)
        self.version_row.set_value(tr("Loading releases"))
        self._list_task = BackgroundTask(modded_exes.fetch_releases, engine=engine, parent=self)
        self._list_task.result.connect(lambda releases: self._on_releases(releases, engine))
        self._list_task.error.connect(lambda message: self._on_releases_error(message, engine))
        self._list_task.start()

    def _on_releases(self, releases: list[dict], engine: str) -> None:
        self._cache[engine] = releases
        self._list_task = None
        if engine == self._engine:
            self._set_engine(engine)
        self.refresh()

    def _on_releases_error(self, _message: str, engine: str) -> None:
        self._list_task = None
        self._failed.add(engine)  # the engine that was loading, not the one picked since
        if engine == self._engine:
            self.version_row.set_value(tr("Couldn't load releases. Check your connection."))
        self.refresh()

    def _pick_version(self) -> None:
        if not self._releases:
            self._load_releases()
            return
        picker = DeckPicker("", [(self._release_text(r), r["tag"]) for r in self._releases],
                            self._release["tag"] if self._release else None)

        def _chosen(tag: object) -> None:
            self.window.dismiss_overlay()
            self._release = next((r for r in self._releases if r["tag"] == tag), self._release)
            self.refresh()

        picker.chosen.connect(_chosen)
        self.window.show_overlay(picker_overlay(self.window, tr("Version"), picker))

    # ------------------------------------------------------------- actions
    def _blocked(self) -> bool:
        if mo2_running() or game_running():
            self.window.notify(tr("Close Mod Organizer and the game, then try again."), 6000)
            return True
        return False

    def _confirm(self, version: str, then, engine: str | None = None) -> None:
        title, message, info = confirm_text(self._anomaly(), engine or self._engine, version, self._gamma())
        if info:
            message += "\n\n" + info
        if version != modded_exes.RECOMMENDED:
            message += "\n\n" + tr("This version hasn't been tested with COMMANDER yet.")
        self.window.confirm(title, message, then, confirm_text=tr("Install"))

    def _install_selected(self) -> None:
        release = self._release
        if release and not self._blocked():
            self._confirm(release["tag"], lambda: self._run_install(release=release))

    def _go_back(self) -> None:
        anomaly = self._anomaly()
        previous = (modded_exes.load_state(anomaly) or {}).get("previous")
        engine = modded_exes.installed_engine(anomaly)
        release = next((r for r in self._cache.get(engine, []) if r["tag"] == previous), None)
        if release is None:
            self.window.notify(tr("{version} isn't in the release list right now. Check your connection and try again.", version=previous), 6000)
            return
        if not self._blocked():
            self._confirm(release["tag"], lambda: self._run_install(release=release), engine)

    def _install_file(self) -> None:
        from .mod_installer import pick_file

        downloads = Path.home() / "Downloads"

        def _picked(path: Path) -> None:
            if not self._blocked():
                self._confirm(path.name, lambda: self._run_install(zip_path=path))

        pick_file(self.window, tr("Pick an engine zip"), downloads if downloads.is_dir() else Path.home(), (".zip",), _picked)

    def _run_install(self, release: dict | None = None, zip_path: Path | None = None) -> None:
        anomaly = Path(self._anomaly())
        gamma = self._gamma()
        label = release["tag"] if release else f"file:{zip_path.name}"

        def worker(report):
            cancel_event = self._task.cancel_event  # set before the task starts
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
                try:
                    modded_exes.install(path, anomaly, label, cancel_event)
                finally:
                    sync_mo2(gamma, str(anomaly))  # a failed engine switch has already put the old exes back
            finally:
                if download_dir is not None:
                    shutil.rmtree(download_dir, ignore_errors=True)

        self._start(worker, done_text(release, label))

    def _restore(self) -> None:
        if self._blocked():
            return
        anomaly, gamma = self._anomaly(), self._gamma()

        def worker(report):
            modded_exes.restore_original(anomaly)
            sync_mo2(gamma, anomaly)

        self.window.confirm(
            tr("Restore the original exes?"),
            tr("COMMANDER puts back the exes and files that were there before it first installed modded exes."),
            lambda: self._start(worker, tr("The original exes are back.")),
            confirm_text=tr("Restore"),
        )

    def _start(self, worker, done: str) -> None:
        self.window.set_install_busy(True, "modded_exes")
        self._task = StreamTask(worker, parent=self)
        self._task.line.connect(self.status.setText)
        self._task.result.connect(lambda _r: self._end(done, ok=True))
        self._task.error.connect(lambda message: self._end(message, ok=False))
        self._task.start()
        self.refresh()

    def _end(self, message: str, *, ok: bool) -> None:
        self._task = None
        self._shown_for = ""  # the engine may have changed: show the installed one
        if self.window.install_operation == "modded_exes":
            self.window.set_install_busy(False)
        self.refresh()
        self.window.announce_finished(
            tr("Done") if ok else tr("The engine change didn't finish"), message, ok=ok
        )
