"""Profiles, as a panel on the Dashboard.

There used to be a Profile tab. What it held - switch to another profile,
edit this one, start a new one - is a handful of rows, and the Dashboard
already had an "Active profile" row pointing at it, so it now lives there
instead: one less tab, and the profile you are about to play with is on the
screen you look at first.

The active profile is one row; A on it opens a list of every profile to
switch to (a dropdown, rather than every profile stacked on the Dashboard).
Edit profile opens the editor, where the name and the three install folders - Anomaly,
GAMMA, cache - are changed (or the profile deleted) with the on-screen keyboard and Deck Mode's own
folder browser. That is what the desktop Profiles page's Browse buttons are
for: pointing COMMANDER at an install on another drive, or at a second copy
of the game. The editor also holds the desktop page's note, colour tag,
Compare profiles and Export Profile; Import is on the New profile screen.
Repository URLs and branches stay a desktop job.

Saving goes through the same ``save_profile`` the desktop page uses
(``commander_gui/ui/profiles_page.py``), so the two can never disagree about
what an edited profile looks like on disk.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from commander_gui.i18n import tr
from commander_gui.profile_bundle import ProfileBundleError, export_profile_bundle
from commander_gui.settings import cli_ok, run_config_command
from commander_gui.ui.common import (
    BackgroundTask,
    activate_profile,
    game_running,
    mo2_running,
)
from commander_gui.ui.profiles_page import (
    NOTE_MAX_LENGTH,
    PROFILE_COLORS,
    check_cli_values,
    compare_modlists,
    forget_profile_extras,
    profile_color,
    profile_mod_lines,
    profile_note,
    save_profile,
    set_profile_color,
    set_profile_note,
)

from . import gamepad as pad
from .folder_picker import show_folder_picker
from .scale import px
from .widgets import (
    ROW_GAP,
    DeckOverlay,
    DeckPicker,
    DeckRow,
    DeckTextPanel,
    deck_label,
    picker_overlay,
    repolish,
    side_by_side,
)


def _shorten(path: str, limit: int = 46) -> str:
    """Keep the end of a long path - the part that differs between installs."""
    return path if len(path) <= limit else "…" + path[-(limit - 1):]


class ProfileEditor(QWidget):
    """The editor overlay's body: a name field and three folder rows."""

    FIELDS = (
        ("anomaly", "STALKER Anomaly folder"),
        ("gamma", "GAMMA folder"),
        ("cache", "Download cache folder"),
    )

    def __init__(self, window, profile) -> None:
        super().__init__()
        self.window = window
        self.profile = profile
        self.paths = {key: getattr(profile, key) or "" for key, _ in self.FIELDS}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(px(10))
        layout.addWidget(deck_label(tr("Profile name"), role="caption"))
        self.name_edit = QLineEdit(profile.profile_name or "")
        self.name_edit.setPlaceholderText(tr("Profile name"))
        self.name_edit.setMaxLength(40)
        layout.addWidget(self.name_edit)
        self.rows: dict[str, DeckRow] = {}
        for key, label in self.FIELDS:
            row = DeckRow(tr(label), _shorten(self.paths[key]) or tr("Not set"))
            row.activated.connect(lambda k=key: self._browse(k))
            layout.addWidget(row)
            self.rows[key] = row
        layout.addWidget(
            deck_label(
                tr(
                    "Point these at an existing install to use it, or at empty "
                    "folders to install a fresh copy there."
                ),
                role="caption",
                wrap=True,
            )
        )
        # Saved at once, like the desktop page: they belong to the saved
        # profile, not to the edit in progress.
        # Two to a line: four more full-width rows overflow the Deck screen.
        self.note_row = DeckRow(tr("Note"))
        self.note_row.activated.connect(self._edit_note)
        self.color_row = DeckRow(tr("Color"))
        self.color_row.activated.connect(self._pick_color)
        layout.addLayout(side_by_side(self.note_row, self.color_row))
        compare_row = DeckRow(tr("Compare profiles"))
        compare_row.activated.connect(self._pick_compare)
        export_row = DeckRow(tr("Export Profile"))
        export_row.activated.connect(self._export)
        layout.addLayout(side_by_side(compare_row, export_row))
        self._render_extras()

    def _render_extras(self) -> None:
        name = self.profile.profile_name
        note = profile_note(name).replace("\n", " ")
        # The start of it; the row is half width.
        self.note_row.set_value(note.strip()[:20])
        key = profile_color(name)
        self.color_row.set_value(tr(PROFILE_COLORS[key][1]) if key else tr("No color"))

    def _edit_note(self) -> None:
        # One line on the keyboard; a desktop note's line breaks are only
        # lost if the note is actually changed here.
        shown = profile_note(self.profile.profile_name).replace("\n", " ")
        edit = QLineEdit(shown, self)
        edit.setMaxLength(NOTE_MAX_LENGTH)
        edit.hide()

        def _done(text: str) -> None:
            edit.deleteLater()
            if text == shown:
                return
            try:
                set_profile_note(self.profile.profile_name, text)
            except OSError as exc:
                self.window.notify(tr("Couldn't save the note") + ": " + str(exc), 8000)
            self._render_extras()

        self.window.open_keyboard(edit, title=tr("Note"), on_done=_done)

    def _pick_color(self) -> None:
        options = [(tr("No color"), "")] + [
            ("●  " + tr(label), key) for key, (_fill, label) in PROFILE_COLORS.items()
        ]
        picker = DeckPicker("", options, profile_color(self.profile.profile_name) or "")

        def _chosen(key: object) -> None:
            self.window.dismiss_overlay()
            try:
                set_profile_color(self.profile.profile_name, str(key) or None)
            except OSError as exc:
                self.window.notify(tr("Couldn't save the colour") + ": " + str(exc), 8000)
            self._render_extras()

        picker.chosen.connect(_chosen)
        self.window.show_overlay(picker_overlay(self.window, tr("Color"), picker), stacked=True)

    def _pick_compare(self) -> None:
        others = [
            p for p in self.window.settings.profiles
            if p.profile_name != self.profile.profile_name
        ]
        if not others:
            self.window.notify(tr("Create another profile to compare mod lists."), 6000)
            return
        picker = DeckPicker("", [(p.profile_name, p.profile_name) for p in others])

        def _chosen(name: object) -> None:
            self.window.dismiss_overlay()
            other = next((p for p in others if p.profile_name == name), None)
            if other is not None:
                self._show_compare(other)

        picker.chosen.connect(_chosen)
        self.window.show_overlay(
            picker_overlay(self.window, tr("Compare profiles"), picker), stacked=True
        )

    def _show_compare(self, other) -> None:
        """The desktop Compare view's "Differences only", as text."""
        left_lines, right_lines = profile_mod_lines(self.profile), profile_mod_lines(other)
        if left_lines is None or right_lines is None:
            missing = self.profile if left_lines is None else other
            self.window.notify(
                tr("'{name}' has no mod list yet - install GAMMA for it first.", name=missing.profile_name),
                6000,
            )
            return
        rows = [row for row in reversed(compare_modlists(left_lines, right_lines)) if row.differs]
        states = {True: "✓ " + tr("Enabled"), False: "✗ " + tr("Disabled"), None: "— " + tr("Not in list")}
        left, right = self.profile.profile_name, other.profile_name
        text = tr("Same mod list") if not rows else "\n\n".join(
            f"{row.name}\n   {left}: {states[row.left]}   ·   {right}: {states[row.right]}"
            for row in rows
        )
        body = DeckTextPanel(text, min_height=200, max_height=520)
        overlay = DeckOverlay(
            f"{left}  ·  {right}" + (f"  ·  {tr('{count} differences', count=len(rows))}" if rows else ""),
            body,
            [(tr("Close"), self.window.dismiss_overlay, "primary")],
            panel_width=1100,
        )
        overlay.default_button = body
        self.window.show_overlay(overlay, stacked=True)

    def _export(self) -> None:
        def _chosen(folder: Path) -> None:
            name = self.profile.profile_name
            target = folder / f"{name}.commander-profile.zip"
            number = 2
            while target.exists() or target.is_symlink():
                target = folder / f"{name} {number}.commander-profile.zip"
                number += 1
            try:
                export_profile_bundle(self.profile, target)
            except ProfileBundleError as exc:
                self.window.notify(tr("Couldn't export the profile") + ": " + str(exc), 8000)
                return
            self.window.notify(tr("Exported to {path}", path=str(target)), 8000)

        downloads = Path.home() / "Downloads"
        show_folder_picker(
            self.window,
            title=tr("Export Profile"),
            start=downloads if downloads.is_dir() else None,
            on_choose=_chosen,
            stacked=True,
        )

    def _browse(self, key: str) -> None:
        current = Path(self.paths[key]).expanduser() if self.paths[key] else None
        start = None
        if current is not None:
            start = current if current.is_dir() else current.parent
            if not start.is_dir():
                start = None
        show_folder_picker(
            self.window,
            title=tr(dict(self.FIELDS)[key]),
            start=start,
            on_choose=lambda path, k=key: self.set_path(k, str(path)),
            stacked=True,
        )

    def set_path(self, key: str, path: str) -> None:
        self.paths[key] = path
        self.rows[key].set_value(_shorten(path))


class ProfilePanel(QWidget):
    """The active profile as one row; A opens the switcher.

    A plain row-height line like the Dashboard's other rows. Edit and New
    live in the switcher's title bar rather than beside this row: they are
    profile management, which is what that panel is for.
    """

    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self._task = None
        self._editor: ProfileEditor | None = None
        self._editing_name: str | None = None
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(px(ROW_GAP))
        self.chooser = DeckRow(tr("Active profile"))
        self.chooser.activated.connect(self.choose_profile)
        line.addWidget(self.chooser, 1)

    # -- rendering --------------------------------------------------------
    def refresh(self) -> None:
        busy = self.window.install_busy
        name = self._active_name()
        self.chooser.set_value((name or tr("No Profile")) + "   ▾")
        self.chooser.value_label.setObjectName("deckRowTitleAccent" if name else "deckRowValue")
        repolish(self.chooser.value_label)
        # With no profiles, A on the row starts a new one (choose_profile).
        self.chooser.setEnabled(not busy)

    def _active_name(self) -> str | None:
        active = self.window.settings.active_profile
        return active.profile_name if active is not None else None

    def choose_profile(self) -> None:
        """The dropdown: every profile, the active one pre-selected."""
        profiles = self.window.settings.profiles
        if not profiles:
            self.new_profile()
            return
        active = self._active_name()
        picker = DeckPicker(
            "",
            [
                (("●  " if p.profile_name == active else "○  ") + p.profile_name, p.profile_name)
                for p in profiles
            ],
            active,
        )
        picker.chosen.connect(self._on_chosen)
        overlay = DeckOverlay(
            tr("Switch profile"),
            picker,
            [(tr("Cancel"), self.window.dismiss_overlay, "normal")],
            panel_width=1000,
            header_buttons=[
                (tr("Edit"), self._edit_from_picker, "normal"),
                ("+  " + tr("New"), self._new_from_picker, "normal"),
            ],
        )
        # Straight onto the list, on the active profile; Edit and New sit
        # up in the title row, so they get face buttons of their own.
        overlay.default_button = picker.list
        overlay.hints = (("A", "Switch"), ("X", "Edit"), ("Y", "New"), ("B", "Cancel"))

        def _on_action(action: str) -> bool:
            if action == pad.CONTEXT:
                self._edit_from_picker()
                return True
            if action == pad.SEARCH:
                self._new_from_picker()
                return True
            return False

        overlay.action_handler = _on_action
        self.window.show_overlay(overlay)

    def _edit_from_picker(self) -> None:
        self.window.dismiss_overlay(refocus=False)
        self.open_editor(self._active_name())

    def _new_from_picker(self) -> None:
        self.window.dismiss_overlay()
        self.new_profile()

    def _on_chosen(self, name: object) -> None:
        self.window.dismiss_overlay()
        if name and name != self._active_name():
            self._confirm_switch(str(name))

    def new_profile(self) -> None:
        from .window import SETUP_KEY

        if self.window.install_busy:
            self.window.notify(tr("An install is already running."))
            return
        self.window.set_page(SETUP_KEY)

    # -- editing ----------------------------------------------------------
    def open_editor(self, name: str | None) -> None:
        profile = next(
            (p for p in self.window.settings.profiles if p.profile_name == name), None
        )
        if profile is None:
            return
        if self.window.install_busy:
            self.window.notify(tr("An install is already running."))
            return
        self._editing_name = name
        self._editor = ProfileEditor(self.window, profile)
        overlay = DeckOverlay(
            tr("Edit profile"),
            self._editor,
            [
                (tr("Save"), self._save, "primary"),
                (tr("Delete profile"), self._confirm_delete, "danger"),
                (tr("Cancel"), self.window.dismiss_overlay, "normal"),
            ],
            panel_width=1100,
            default_index=2,
        )
        overlay.default_button = self._editor.name_edit
        self.window.show_overlay(overlay)

    def _save(self) -> None:
        editor, original = self._editor, self._editing_name
        if editor is None or original is None:
            return
        name = editor.name_edit.text().strip()
        if not name:
            self.window.notify(tr("A profile name is required."))
            return
        if any(
            p.profile_name == name and p.profile_name != original
            for p in self.window.settings.profiles
        ):
            self.window.notify(tr("A profile named '{name}' already exists.", name=name))
            return
        if not all(editor.paths.values()):
            self.window.notify(tr("Anomaly, GAMMA, and cache folders are required."))
            return
        if self.window.install_busy:
            self.window.notify(tr("An install is already running."))
            return
        if mo2_running(force=True):
            self.window.notify(
                tr("Mod Organizer / the game is currently running. Close it first.")
            )
            return
        existing = next(
            (p for p in self.window.settings.profiles if p.profile_name == original), None
        )
        if existing is None:
            return
        edited = dataclasses.replace(
            existing,
            profile_name=name,
            anomaly=editor.paths["anomaly"],
            gamma=editor.paths["gamma"],
            cache=editor.paths["cache"],
            extra=dict(existing.extra),
        )
        try:
            check_cli_values(edited)
        except ValueError as exc:
            self.window.notify(str(exc), 6000)
            return
        try:
            save_profile(self.window.settings, original, edited)
        except OSError as exc:
            self.window.notify(
                tr("Could not write settings.json:\n{exc}", exc=exc), 8000
            )
            return
        self.window.dismiss_overlay()
        self._editor = None
        self.window.refresh_settings()
        # Every screen reads the profile's folders; bring them all up to date.
        self.window.profile_changed()
        self.window.notify(tr("Profile '{profile_name}' saved.", profile_name=name))

    # -- deleting ---------------------------------------------------------
    def _confirm_delete(self) -> None:
        name = self._editing_name
        profile = next(
            (p for p in self.window.settings.profiles if p.profile_name == name), None
        )
        if profile is None:
            return
        if self.window.install_busy or self._task is not None:
            self.window.notify(tr("An install is already running."))
            return
        folders = tr(
            "Anomaly: {anomaly}\nGAMMA: {gamma}\nCache: {cache}",
            anomaly=profile.anomaly or "-",
            gamma=profile.gamma or "-",
            cache=profile.cache or "-",
        )
        message = (
            tr("Only the profile is removed from COMMANDER. Your game files stay on disk:")
            + "\n\n"
            + folders
        )
        if profile.active and game_running(force=True):
            message += "\n\n" + tr(
                "Mod Organizer or the game is running on this profile. Deleting the profile won't close it, but COMMANDER will stop tracking it."
            )
        self._editor = None
        self.window.confirm(
            tr("Delete the profile '{name}'?", name=name),
            message,
            lambda: self._delete(name),
            confirm_text=tr("Delete profile"),
            confirm_role="danger",
        )

    def _delete(self, name: str) -> None:
        if self.window.install_busy or self._task is not None:
            self.window.notify(tr("An install is already running."))
            return
        # The CLI owns settings.json's profile list, as on the desktop page.
        self._task = BackgroundTask(run_config_command, ["delete", name], timeout=300, parent=self)
        self._task.result.connect(lambda result: self._deleted(name, *result))
        self._task.error.connect(lambda message: self._delete_failed(message))
        self._task.start()
        self.chooser.setEnabled(False)

    def _delete_failed(self, message: str) -> None:
        self._task = None
        self.chooser.setEnabled(True)
        self.window.notify(tr("Couldn't delete the profile") + ": " + message, 8000)

    def _deleted(self, name: str, rc: int, out: str, err: str) -> None:
        self._task = None
        self.chooser.setEnabled(True)
        if not cli_ok(rc, out, err):
            self._delete_failed((out + "\n" + err).strip() or tr("The profile couldn't be deleted."))
            return
        self.window.refresh_settings()
        if any(p.profile_name == name for p in self.window.settings.profiles):
            self._delete_failed(
                tr("The profile '{name}' couldn't be deleted. Try again, or restart COMMANDER.", name=name)
            )
            return
        forget_profile_extras(name)
        self.window.profile_changed()
        self.window.notify(tr("Profile '{name}' deleted.", name=name))

    # -- switching --------------------------------------------------------
    def _confirm_switch(self, name: str) -> None:
        if self._task is not None:
            # One switch at a time: two overlapping `config use` runs made the
            # first to finish report failure against the other's result.
            self.window.notify(tr("Switching profile"))
            return
        if self.window.install_busy:
            self.window.notify(tr("An install is already running."))
            return
        active = self.window.settings.active_profile
        if game_running(force=True):
            # MO2 or a game started without it (Play Anomaly): the same
            # question the desktop Dashboard asks.
            self.window.confirm(
                tr("Game Running"),
                tr(
                    "Mod Organizer / the game appears to be running under the current active profile ('{active_name}').\n\nSwitching the active profile now will not stop it, but COMMANDER's other pages will stop reflecting its state.\n\nSwitch anyway?",
                    active_name=active.profile_name if active is not None else "",
                ),
                lambda: self._switch(name),
            )
            return
        self._switch(name)

    def _switch(self, name: str) -> None:
        self.window.notify(tr("Activating {name}", name=name), 0)
        # activate_profile only needs window.refresh_settings() and
        # window.settings, both of which DeckWindow provides - so the
        # desktop helper is reused exactly as-is, CLI call and all.
        self._task = activate_profile(self.window, self, name, on_done=self._done)
        self.chooser.setEnabled(False)

    def _done(self, ok: bool) -> None:
        self._task = None
        self.chooser.setEnabled(True)
        self.window.toast.hide()
        if ok:
            active = self.window.settings.active_profile
            self.window.notify(
                tr(
                    "Profile '{name}' is now active.",
                    name=active.profile_name if active is not None else "",
                )
            )
        # A different profile means different folders, mods and updates -
        # and results cached for the old one must not be shown for it.
        self.window.profile_changed()
