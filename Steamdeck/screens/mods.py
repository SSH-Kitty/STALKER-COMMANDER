"""Turning mods on and off, and installing new ones.

Enable, disable, search, reorder, Install mod (an archive from
Downloads or an SD card, FOMOD installers included - see
``Steamdeck/mod_installer.py``), and Tools (the desktop's backups, New
category, Check for File Conflicts and the MO2 profile switcher, on the
same files). Reordering is the desktop's drag and drop
done with the pad: hold A on a mod to pick it up, D-pad up/down to carry it
(across category headers too), A to put it down, B to put it back. Nothing
is written until it is put down, and then once.

X opens the desktop's right-click menu: on a mod, Rename (a COMMANDER-only
nickname), Move to Category, Show Conflicts, Reinstall from Cache and
Update from Archive; on a category header, rename, move and (for one the
user made) delete it - its mods stay, moved to Uncategorized. Deleting mod
folders and MO2 profiles stays a desktop job - getting it subtly wrong on a
handheld costs the user their install.

Two implementation choices are worth knowing about:

* Rows are painted by a delegate rather than built from widgets. GAMMA ships
  500-900 mods; one widget per row would be hundreds of QWidgets and makes
  scrolling stutter on the Deck's APU.
* The list is the desktop Mod Manager's tree, flattened: every MO2
  separator is a category header row ("Weapons (42)") with its mods under
  it, in MO2's own on-screen order (modlist.txt is written bottom-up, so it
  is walked in reverse) with the same priority numbers. A on a header
  collapses or expands it; Collapse all folds the whole list down to its
  categories, which is the fastest way to reach one with a thumbstick.
  Search still exists, with Deck Mode's own keyboard on Y.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
)

from commander_gui import gui_settings
from commander_gui.i18n import tr
from commander_gui.launcher import select_mo2_profile
from commander_gui.mod_install import ModInstallError, sanitize_name
from commander_gui.modlist import (
    _backups_newest_first,
    _valid_name,
    add_category,
    custom_mod_names,
    delete_category,
    entries,
    find_enabled_mod_file_conflicts,
    grouped,
    mod_conflicts,
    modlist_path_for,
    move_category,
    move_mod,
    read_lines,
    rename_category,
    reorder_to_original,
    save_lines,
    seed_new_mo2_profile,
    set_status_at,
    snapshot_modlist_backup,
    summarize_mod_conflicts,
)
from commander_gui.themes import active_theme_tokens, token_color
from commander_gui.ui.common import BackgroundTask, mo2_running
from commander_gui.ui.mod_manager_page import cached_archive_for
from commander_gui.ui.profiles_page import mo2_profile_names

from .. import gamepad as pad
from ..focus import enable_kinetic_scroll
from ..modlist_io import ModlistWriteBlocked, backup_path, guard_reason, write_lines
from ..scale import px
from ..widgets import (
    MIN_TOUCH,
    DeckOverlay,
    DeckPicker,
    DeckTextPanel,
    deck_button,
    deck_label,
    picker_overlay,
)
from .base import DeckScreen

#: Row heights and the tappable check circle painted at the left of each mod.
_ROW = 68
_HEADER_ROW = 56
_CHECK = 38
_PAD = 18
#: Mod name, header and priority text sizes, in design pixels.
_NAME_PX = 21
_HEADER_PX = 19
_PRIORITY_PX = 15

#: Item data roles.
_LINE = Qt.ItemDataRole.UserRole  # mod: its line index in modlist.txt
_ENABLED = Qt.ItemDataRole.UserRole + 1  # mod: bool
_CATEGORY = Qt.ItemDataRole.UserRole + 2  # both: the category name
_KIND = Qt.ItemDataRole.UserRole + 3  # "header" or "mod"
_PRIORITY = Qt.ItemDataRole.UserRole + 4  # mod: MO2 priority number
_COUNT = Qt.ItemDataRole.UserRole + 5  # header: mods in the category
_COLLAPSED = Qt.ItemDataRole.UserRole + 6  # header: bool
_GRABBED = Qt.ItemDataRole.UserRole + 7  # mod: picked up to be moved
_NAME = Qt.ItemDataRole.UserRole + 8  # mod: its real (modlist) name

#: grouped()'s label for trailing mods with no separator - not a real
#: category, so it can't be renamed, moved or deleted.
_NO_CATEGORY = "Uncategorized"


def search_icon(color: QColor, size: int) -> QIcon:
    """A painted magnifying glass - no icon font or emoji font needed."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, max(2.0, size / 11))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        radius = size * 0.3
        centre = QPointF(size * 0.42, size * 0.42)
        painter.drawEllipse(centre, radius, radius)
        offset = radius * 0.72
        painter.drawLine(
            QPointF(centre.x() + offset, centre.y() + offset),
            QPointF(size * 0.88, size * 0.88),
        )
    finally:
        painter.end()
    return QIcon(pixmap)


class _ModRowDelegate(QStyledItemDelegate):
    """Paints a category header or a mod row.

    A delegate rather than row widgets - see this module's docstring for
    why that matters at GAMMA's modlist sizes.
    """

    def sizeHint(self, option, index) -> QSize:
        header = index.data(_KIND) == "header"
        return QSize(0, px(_HEADER_ROW if header else _ROW))

    def paint(self, painter, option, index) -> None:
        tokens = active_theme_tokens()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        pad = px(_PAD)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if index.data(_KIND) == "header":
            self._paint_header(painter, rect, index, tokens, selected, pad)
        else:
            self._paint_mod(painter, rect, index, tokens, selected, pad)
        painter.restore()

    @staticmethod
    def _selection(painter, rect, tokens, selected: bool, fill: QColor | None) -> None:
        inner = QRectF(rect.adjusted(2, 2, -2, -2))
        if fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(inner, px(10), px(10))
        if selected:
            painter.setPen(QPen(QColor(tokens.get("focus", "#9fe96f")), 2))
            painter.setBrush(token_color("btn_hover", "#222222"))
            painter.drawRoundedRect(inner, px(10), px(10))

    def _paint_header(self, painter, rect, index, tokens, selected, pad) -> None:
        # The same "header band" treatment the desktop tree gives category
        # rows: the card colour behind bold accent text.
        self._selection(painter, rect, tokens, selected, token_color("btn", "#222222"))
        collapsed = bool(index.data(_COLLAPSED))
        font = QFont(painter.font())
        font.setPixelSize(px(_HEADER_PX))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(tokens.get("accent_strong", "#9fe96f")))
        arrow = "▸" if collapsed else "▾"
        text = f"{arrow}   {index.data(_CATEGORY)}"
        painter.drawText(
            rect.adjusted(pad, 0, -pad, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        count = QFont(font)
        count.setPixelSize(px(_PRIORITY_PX))
        count.setWeight(QFont.Weight.Normal)
        painter.setFont(count)
        painter.setPen(QColor(tokens.get("text_dim", "#888888")))
        painter.drawText(
            rect.adjusted(pad, 0, -pad, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            tr("{count} mods", count=index.data(_COUNT)),
        )

    def _paint_mod(self, painter, rect, index, tokens, selected, pad) -> None:
        self._selection(painter, rect, tokens, selected, None)
        grabbed = bool(index.data(_GRABBED))
        if grabbed:
            # Picked up: a thick accent outline, so it reads as lifted off
            # the list rather than merely highlighted.
            painter.setPen(QPen(QColor(tokens.get("accent", "#9fe96f")), px(4)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect.adjusted(3, 3, -3, -3)), px(10), px(10))
        enabled = bool(index.data(_ENABLED))
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        # Hairline separator. Without it a fast scroll through GAMMA's 900
        # entries reads as one block of text rather than discrete rows.
        painter.setPen(QColor(tokens.get("gridline", "#222222")))
        painter.drawLine(
            rect.left() + pad, rect.bottom(), rect.right() - pad, rect.bottom()
        )
        indent = pad + px(12)
        box_size = px(_CHECK)
        box_y = rect.y() + (rect.height() - box_size) // 2
        box = rect.adjusted(indent, box_y - rect.y(), 0, 0)
        box.setWidth(box_size)
        box.setHeight(box_size)
        accent = QColor(tokens.get("accent", "#9fe96f"))
        border = QColor(tokens.get("border_strong", "#333333"))
        painter.setPen(border)
        painter.setBrush(accent if enabled else QColor(0, 0, 0, 0))
        painter.drawRoundedRect(box, box_size / 2, box_size / 2)
        if enabled:
            painter.setPen(QColor(tokens.get("accent_text", "#000000")))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "✓")

        # MO2 priority on the right, like the desktop tree's second column.
        small = QFont(painter.font())
        small.setPixelSize(px(_PRIORITY_PX))
        painter.setFont(small)
        painter.setPen(QColor(tokens.get("text_dim", "#888888")))
        priority_width = px(64)
        painter.drawText(
            rect.adjusted(rect.width() - pad - priority_width, 0, -pad, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "⇅" if grabbed else str(index.data(_PRIORITY) or ""),
        )

        text_x = box.right() + pad
        name_font = QFont(painter.font())
        name_font.setPixelSize(px(_NAME_PX))
        name_font.setWeight(QFont.Weight.DemiBold if enabled else QFont.Weight.Normal)
        painter.setFont(name_font)
        painter.setPen(
            QColor(tokens.get("text_bright" if enabled else "text_dim", "#dddddd"))
        )
        painter.drawText(
            rect.adjusted(text_x - rect.x(), 0, -(pad + priority_width + pad), 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )


class ModsScreen(DeckScreen):
    def build(self) -> None:
        self._lines: list[str] = []
        self._path: Path | None = None
        self._filter = "all"
        self._shown = 0
        self._snapshot_taken = False
        self._collapsed: set[str] = set()
        #: The mod row picked up with a held A, while it is being carried.
        self._grabbed: QListWidgetItem | None = None
        self._grab_warned = False

        # The list is the page: no search box eating a row above it. Search
        # is a magnifier button at the right of the filter chips (or Y), and
        # the text lives in a hidden field the on-screen keyboard types into.
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search mods"))
        self.search.textChanged.connect(self._on_search_changed)
        self.search.hide()

        # Nothing here needs the page to scroll - the list scrolls itself -
        # and a scrolling page around a scrolling list fights the finger.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        chips = QHBoxLayout()
        chips.setSpacing(px(8))
        self._chips = {}
        for key, label in (
            ("all", tr("All")),
            ("enabled", tr("Enabled")),
            ("disabled", tr("Disabled")),
        ):
            chip = deck_button(label, role="chip")
            chip.setCheckable(True)
            chip.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            chips.addWidget(chip, 1)
            self._chips[key] = chip
        self._chips["all"].setChecked(True)
        self.category_button = deck_button(
            "☰  " + tr("Categories"), role="chip", on_click=self._pick_category
        )
        chips.addWidget(self.category_button, 1)
        self.collapse_button = deck_button(
            tr("Collapse all"), role="chip", on_click=self._toggle_collapse_all
        )
        chips.addWidget(self.collapse_button, 1)
        self.search_button = deck_button("", role="chip", on_click=self.open_search)
        self.search_button.setObjectName("deckIconChip")
        self.search_button.setIcon(search_icon(QColor(active_theme_tokens().get("text", "#dddddd")), px(26)))
        self.search_button.setIconSize(QSize(px(26), px(26)))
        self.search_button.setFixedWidth(px(MIN_TOUCH + 32))
        self.search_button.setToolTip(tr("Search mods"))
        chips.addWidget(self.search_button)
        self.install_button = deck_button(
            "＋  " + tr("Install mod"), role="chip", on_click=self._install_mod
        )
        chips.addWidget(self.install_button, 1)
        self.backups_button = deck_button(
            "⚙  " + tr("Tools"), role="chip", on_click=self._show_tools
        )
        chips.addWidget(self.backups_button, 1)
        self.body.addLayout(chips)
        self._installer = None
        self._conflicts_task: BackgroundTask | None = None
        self._archive_task: BackgroundTask | None = None

        # Shown only while a search is active: what is being searched for,
        # and one tap (or B) to clear it.
        self.search_chip = deck_button("", role="chip", on_click=self._clear_search)
        self.search_chip.setObjectName("deckSearchChip")
        self.search_chip.hide()
        self.body.addWidget(self.search_chip)

        self.list = QListWidget()
        self.list.setItemDelegate(_ModRowDelegate(self.list))
        self.list.setUniformItemSizes(False)
        self.list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list.setMinimumHeight(px(_ROW) * 4)
        # itemClicked only - A reaches it too, through focus.activate(). With
        # itemActivated also connected, styles that activate on a single
        # click toggled every mod twice (i.e. not at all) and wrote twice.
        self.list.itemClicked.connect(self._on_item)
        enable_kinetic_scroll(self.list)
        self.body.addWidget(self.list, 1)

        self.status = deck_label("", role="caption", wrap=True)
        self.body.addWidget(self.status)

    # -- loading ----------------------------------------------------------
    def refresh(self) -> None:
        # Whatever was being carried belongs to the list about to be rebuilt.
        self._grabbed = None
        profile = self.profile()
        if profile is None:
            self._lines = []
            self.list.clear()
            self.status.setText(
                tr("Create or activate a profile first (Dashboard → Profiles).")
            )
            return
        self._path = modlist_path_for(profile.gamma, profile.mo2_profile)
        if self._path is None:
            self._lines = []
            self.list.clear()
            self.status.setText(tr("Not installed"))
            return
        try:
            self._lines = read_lines(self._path)
        except (OSError, ValueError) as exc:
            # A modlist we cannot parse is never written back - the desktop
            # page takes the same stance, because a partial understanding of
            # the file is how a load order gets destroyed.
            self._lines = []
            self.list.clear()
            self.status.setText(tr("Failed") + ": " + str(exc))
            return
        self._populate()

    def on_busy_changed(self, busy: bool) -> None:
        self._update_guard()

    def _update_guard(self) -> None:
        blocked = self.window.install_busy or mo2_running()
        self.list.setEnabled(not blocked)
        self.install_button.setEnabled(not blocked)
        self.backups_button.setEnabled(not blocked and self._path is not None)
        if blocked:
            self.status.setText(
                tr(
                    "Close Mod Organizer first - it would overwrite your "
                    "changes when it exits."
                )
                if mo2_running()
                else tr("An install is already running.")
            )

    # -- rendering --------------------------------------------------------
    def _set_filter(self, key: str) -> None:
        self._filter = key
        for chip_key, chip in self._chips.items():
            chip.setChecked(chip_key == key)
        self._populate()
        self.update_hints()

    def _populate(self) -> None:
        needle = self.search.text().strip().lower()
        current = self.list.currentItem()
        keep = None
        if current is not None:
            keep = (current.data(_KIND), current.data(_CATEGORY), current.data(_LINE))
        self.list.clear()
        shown = 0
        # Parsed once: this runs on every keystroke in the search box, over a
        # modlist that is 500-900 entries in a real GAMMA install.
        groups = grouped(self._lines)
        nicknames = self._nicknames()
        priority = 0
        restore_row = 0
        # Reversed, exactly like the desktop tree: MO2 writes modlist.txt
        # highest priority first but shows it at the bottom, so walking the
        # file backwards gives MO2's on-screen order and its numbering.
        for category, mods in reversed(groups):
            rows = []
            for status, name, line_index in reversed(mods):
                priority += 1
                enabled = status == "Enabled"
                if self._filter == "enabled" and not enabled:
                    continue
                if self._filter == "disabled" and enabled:
                    continue
                shown_name = nicknames.get(name, name)
                if needle and needle not in name.lower() and needle not in shown_name.lower():
                    continue
                rows.append((name, shown_name, line_index, enabled, priority))
            filtering = bool(needle) or self._filter != "all"
            if not rows and filtering:
                continue
            # A search shows every match, collapsed category or not.
            collapsed = category in self._collapsed and not needle
            header = QListWidgetItem(category)
            header.setData(_KIND, "header")
            header.setData(_CATEGORY, category)
            header.setData(_COUNT, len(rows))
            header.setData(_COLLAPSED, collapsed)
            self.list.addItem(header)
            if keep == ("header", category, None):
                restore_row = self.list.count() - 1
            if collapsed:
                continue
            for name, shown_name, line_index, enabled, number in rows:
                item = QListWidgetItem(shown_name)
                item.setData(_KIND, "mod")
                item.setData(_NAME, name)
                item.setData(_LINE, line_index)
                item.setData(_ENABLED, enabled)
                item.setData(_CATEGORY, category)
                item.setData(_PRIORITY, number)
                self.list.addItem(item)
                if keep == ("mod", category, line_index):
                    restore_row = self.list.count() - 1
                shown += 1
        if self.list.count():
            self.list.setCurrentRow(restore_row)
        self._shown = shown
        self._render_counts(groups)
        self._update_guard()

    def mod_items(self) -> list[QListWidgetItem]:
        """The mod rows currently in the list (headers excluded)."""
        return [
            self.list.item(row)
            for row in range(self.list.count())
            if self.list.item(row).data(_KIND) == "mod"
        ]

    def _on_item(self, item: QListWidgetItem) -> None:
        if self._grabbed is not None:
            # A (or a tap anywhere) puts the carried mod down where it is.
            self._drop()
            return
        if item.data(_KIND) == "header":
            self._toggle_category(str(item.data(_CATEGORY)))
        else:
            self._toggle(item)

    def _toggle_category(self, category: str) -> None:
        if category in self._collapsed:
            self._collapsed.discard(category)
        else:
            self._collapsed.add(category)
        self._populate()
        self._sync_collapse_button()

    def _toggle_collapse_all(self) -> None:
        categories = [name for name, _mods in grouped(self._lines)]
        if self._collapsed >= set(categories):
            self._collapsed.clear()
        else:
            self._collapsed = set(categories)
        self._populate()
        self._sync_collapse_button()
        self.list.setCurrentRow(0)

    def _sync_collapse_button(self) -> None:
        categories = {name for name, _mods in grouped(self._lines)}
        all_collapsed = bool(categories) and self._collapsed >= categories
        self.collapse_button.setText(tr("Expand all") if all_collapsed else tr("Collapse all"))

    # -- categories -------------------------------------------------------
    def _categories(self) -> list[str]:
        """Category headers in the list as currently shown, in order."""
        return [
            str(self.list.item(row).data(_CATEGORY) or "")
            for row in range(self.list.count())
            if self.list.item(row).data(_KIND) == "header"
        ]

    def _pick_category(self) -> None:
        categories = self._categories()
        if not categories:
            self.window.notify(tr("No mods to show."))
            return
        current = None
        item = self.list.currentItem()
        if item is not None:
            current = item.data(_CATEGORY)
        picker = DeckPicker(
            "",
            [(name or tr("Uncategorized"), name) for name in categories],
            current,
            searchable=len(categories) > 12,
        )
        picker.chosen.connect(self._jump_to_category)
        self.window.show_overlay(picker_overlay(self.window, tr("Jump to category"), picker))

    def _jump_to_category(self, category: object) -> None:
        self.window.dismiss_overlay(refocus=False)
        if category in self._collapsed:
            self._collapsed.discard(category)
            self._populate()
            self._sync_collapse_button()
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(_KIND) == "header" and item.data(_CATEGORY) == category:
                self.list.setCurrentRow(row)
                self.list.scrollToItem(
                    self.list.item(row), QListWidget.ScrollHint.PositionAtTop
                )
                break
        self.list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _category_actions(self) -> None:
        """X on a mod: act on its whole category at once."""
        item = self.list.currentItem()
        if item is None:
            return
        category = item.data(_CATEGORY)
        label = category or tr("Uncategorized")
        body = deck_label(
            tr("Enable or disable every mod in \"{category}\"?", category=label),
            role="body",
            wrap=True,
        )
        self.window.show_overlay(
            DeckOverlay(
                label,
                body,
                [
                    (tr("Enable all"), lambda: self._set_category(category, True), "primary"),
                    (tr("Disable all"), lambda: self._set_category(category, False), "normal"),
                    (tr("Cancel"), self.window.dismiss_overlay, "normal"),
                ],
                default_index=2,
            )
        )

    def _set_category(self, category: object, enabled: bool) -> None:
        self.window.dismiss_overlay()
        if self._path is None:
            return
        indexes = [
            line_index
            for name, mods in grouped(self._lines)
            if name == category
            for _status, _name, line_index in mods
        ]
        new_lines = list(self._lines)
        for line_index in indexes:
            new_lines = set_status_at(new_lines, line_index, enabled)
        if new_lines == self._lines:
            return
        if not self._write(new_lines):
            return
        row = self.list.currentRow()
        self._populate()
        self.list.setCurrentRow(min(row, self.list.count() - 1))

    # -- search -----------------------------------------------------------
    def open_search(self) -> None:
        self.window.open_keyboard(
            self.search, title=tr("Search mods"), on_done=lambda _t: self._focus_list()
        )

    def _focus_list(self) -> None:
        target = self.list if self.list.count() else self.search_button
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_search_changed(self, text: str) -> None:
        text = text.strip()
        self.search_chip.setVisible(bool(text))
        self.search_chip.setText("\u2715   " + tr("Search: {query}", query=text))
        self._populate()
        self.update_hints()

    def _clear_search(self) -> None:
        self.search.clear()
        # Back to the list the search was narrowing - the chip just pressed
        # is gone.
        self._focus_list()

    # -- reordering -------------------------------------------------------
    def wants_hold(self) -> bool:
        """Tell a held A from a tap on the list (see GamepadMonitor.hold_gate).

        Not while carrying a mod: then A puts it down, at once.
        """
        return self._grabbed is None and self.list.hasFocus() and self.list.isEnabled()

    def is_grabbing(self) -> bool:
        return self._grabbed is not None

    def _grab(self) -> bool:
        """Held A: pick up the highlighted mod. False on a header."""
        item = self.list.currentItem()
        if item is None or item.data(_KIND) != "mod":
            return False
        if self._path is None or self.window.install_busy or mo2_running():
            self._update_guard()
            return True
        if self.search.text().strip() or self._filter != "all":
            # With rows hidden, "just above this one" is not a place in the
            # load order the user can see.
            self.window.notify(tr("Clear the search and filter to move mods."), 5000)
            return True
        self._grabbed = item
        item.setData(_GRABBED, True)
        self.list.viewport().update()
        if not self._grab_warned:
            self._grab_warned = True
            self.window.notify(
                tr(
                    "Moving a mod changes the load order. D-pad moves it, "
                    "A drops it, B cancels."
                ),
                6000,
            )
        self.update_hints()
        return True

    def on_direction(self, action: str) -> bool:
        """D-pad while carrying a mod: up/down moves it, left/right do nothing."""
        item = self._grabbed
        if item is None:
            return False
        if action in (pad.UP, pad.DOWN):
            row = self.list.row(item)
            target = row + (-1 if action == pad.UP else 1)
            # Row 0 is the first category's header; nothing goes above it.
            if 1 <= target < self.list.count():
                self.list.takeItem(row)
                self.list.insertItem(target, item)
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item)
        return True

    def _drop(self) -> None:
        """Put the carried mod down: one move_mod(), one write."""
        item = self._grabbed
        if item is None:
            return
        self._grabbed = None
        item.setData(_GRABBED, False)
        name = item.data(_NAME)
        row = self.list.row(item)
        category = None
        for above in range(row - 1, -1, -1):
            if self.list.item(above).data(_KIND) == "header":
                category = str(self.list.item(above).data(_CATEGORY))
                break
        before = self.list.item(row - 1) if row > 0 else None
        after = self.list.item(row + 1) if row + 1 < self.list.count() else None
        # Screen order runs opposite to file order (see _populate), so
        # "below that mod on screen" is move_mod's before=True, the same
        # inversion the desktop tree's drop makes.
        if before is not None and before.data(_KIND) == "mod":
            new_lines = move_mod(self._lines, name, target_name=before.data(_NAME), before=True)
        elif after is not None and after.data(_KIND) == "mod":
            new_lines = move_mod(self._lines, name, target_name=after.data(_NAME), before=False)
        else:
            new_lines = move_mod(self._lines, name, category=category)
        if new_lines != self._lines:
            self._write(new_lines)
        self._populate()
        self._select_mod(name)
        self.update_hints()

    def _cancel_grab(self) -> None:
        """B: put the carried mod back where it was picked up."""
        item = self._grabbed
        if item is None:
            return
        self._grabbed = None
        name = item.data(_NAME)
        self._populate()
        self._select_mod(name)
        self.update_hints()

    def _select_mod(self, name: str) -> None:
        for mod in self.mod_items():
            if mod.data(_NAME) == name:
                self.list.setCurrentItem(mod)
                self.list.scrollToItem(mod)
                return

    def hideEvent(self, event) -> None:
        # Leaving the screen (L1/R1, a tab tap) with a mod still in hand
        # puts it back rather than leaving it half moved.
        self._cancel_grab()
        super().hideEvent(event)

    # -- input ------------------------------------------------------------
    def on_action(self, action: str) -> bool:
        if action == pad.ACCEPT_HOLD:
            if self._grabbed is not None:
                self._drop()
                return True
            return self._grab()
        if self._grabbed is not None and action in (pad.SEARCH, pad.CONTEXT):
            # Search or a category menu would rebuild the list under it.
            return True
        if action == pad.SEARCH:
            self.open_search()
            return True
        if action == pad.CONTEXT:
            item = self.list.currentItem()
            if self.list.hasFocus() and item is not None:
                if item.data(_KIND) == "header":
                    self._category_menu(item.data(_CATEGORY))
                else:
                    self._mod_menu(item)
            else:
                self._pick_category()
            return True
        return False

    def hints(self):
        # X does one of two things depending on where focus is, and B peels
        # off a search or filter before it leaves - so say which.
        if self._grabbed is not None:
            return [("↑↓", "Move"), ("A", "Drop"), ("B", "Cancel")]
        on_list = self.list.hasFocus()
        if self.search.text():
            back = "Clear search"
        elif self._filter != "all":
            back = "Clear filter"
        else:
            back = "Back"
        return [
            ("A", "Toggle / fold · hold: move" if on_list else "Select"),
            ("X", "Options" if on_list else "Jump to category"),
            ("Y", "Search"),
            ("B", back),
        ]

    def default_focus(self):
        return self.list if self.list.count() and self.list.isEnabled() else self.search_button

    # -- installing -------------------------------------------------------
    def _install_mod(self) -> None:
        from ..mod_installer import DeckModInstaller

        if self._installer is None:
            self._installer = DeckModInstaller(self)
        self._installer.start()

    def reveal_mod(self, name: str) -> None:
        """Show and select a mod by name - a fresh install, say - clearing
        whatever search, filter or folded category would hide it."""
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search_chip.hide()
        self._filter = "all"
        for chip_key, chip in self._chips.items():
            chip.setChecked(chip_key == "all")
        category = next(
            (c for c, mods in grouped(self._lines) for _s, n, _i in mods if n == name), None
        )
        if category is not None:
            self._collapsed.discard(category)
        self._populate()
        for item in self.mod_items():
            if item.data(_NAME) == name:
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                self.list.setFocus(Qt.FocusReason.OtherFocusReason)
                return

    # -- backups ----------------------------------------------------------
    # The desktop Mod Manager's three backup tools, on the same files: the
    # pristine .gammagui.bak and the timestamped modlist-*.bak snapshots.
    def _show_backups(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        if self._refuse_if_blocked():
            return
        pristine = backup_path(self._path)
        if pristine.is_file():
            stamp = datetime.fromtimestamp(pristine.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            status = tr("Backup: {name} ({stamp})", name=pristine.name, stamp=stamp)
        else:
            status = tr("No backup yet")
        self.window.show_overlay(
            DeckOverlay(
                tr("Backups"),
                deck_label(
                    tr("Save a backup of the current MO2 modlist.") + "\n\n" + status,
                    role="body",
                    wrap=True,
                ),
                [
                    (tr("Create backup"), self._create_backup, "primary"),
                    (tr("Restore backup"), self._pick_backup, "normal"),
                    (tr("Restore original order"), self._restore_original_order, "normal"),
                    (tr("Cancel"), self.window.dismiss_overlay, "normal"),
                ],
                panel_width=1100,
                default_index=3,
            )
        )

    def _refuse_if_blocked(self) -> bool:
        reason = guard_reason(self.window)
        if reason is not None:
            self.window.notify(reason, 6000)
            self._update_guard()
            return True
        return False

    def _create_backup(self) -> None:
        self.window.dismiss_overlay()
        if self._path is None or self._refuse_if_blocked():
            return
        try:
            snapshot_modlist_backup(self._path)
        except OSError as exc:
            self.window.notify(tr("Couldn't create the backup") + ": " + str(exc), 8000)
            return
        newest = _backups_newest_first(self._path)
        name = newest[0].name if newest else self._path.name
        self.window.notify(tr("Backup saved: {backup}", backup=name), 5000)

    def _pick_backup(self) -> None:
        self.window.dismiss_overlay()
        if self._path is None:
            return
        backups = _backups_newest_first(self._path)
        if not backups:
            self.window.notify(tr("No backup yet"))
            return
        options = []
        for bak in backups:
            try:
                stamp = datetime.fromtimestamp(bak.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                continue
            options.append((f"{stamp}   ·   {bak.name}", str(bak)))
        picker = DeckPicker("", options, 0)
        picker.chosen.connect(lambda value: self._confirm_restore(Path(str(value))))
        self.window.show_overlay(
            picker_overlay(self.window, tr("Restore backup"), picker, panel_width=1100)
        )

    def _confirm_restore(self, bak: Path) -> None:
        self.window.dismiss_overlay()

        def _restore() -> None:
            if self._path is None:
                return
            try:
                lines = read_lines(bak)
            except (OSError, ValueError) as exc:
                self.window.notify(tr("Couldn't restore the backup") + ": " + str(exc), 8000)
                return
            # A snapshot of the list being replaced goes first, so this
            # restore can itself be undone from the same picker.
            try:
                write_lines(self.window, self._path, lines, snapshot=True)
            except ModlistWriteBlocked as exc:
                self.window.notify(str(exc), 6000)
                self._update_guard()
                return
            self._snapshot_taken = True
            self.window.update_mod_counter()
            self.refresh()
            self.window.notify(tr("Restored from: {bak}", bak=bak.name), 5000)

        self.window.confirm(
            tr("Restore backup"),
            tr("Your mod list is replaced with this backup:\n{bak}", bak=bak.name)
            + "\n\n"
            + tr("Changes you made since then are lost."),
            _restore,
            confirm_text=tr("Restore"),
            confirm_role="danger",
        )

    def _restore_original_order(self) -> None:
        self.window.dismiss_overlay()
        if self._path is None or self._refuse_if_blocked():
            return
        pristine = backup_path(self._path)
        try:
            original = read_lines(pristine)
        except (OSError, ValueError):
            self.window.notify(tr("The original order isn't available"), 6000)
            return
        new_lines = reorder_to_original(self._lines, original)
        if new_lines == self._lines:
            self.window.notify(tr("The GAMMA mods are already in their original order."), 5000)
            return

        def _restore() -> None:
            if self._write(new_lines):
                self.refresh()
                self.window.notify(
                    tr("GAMMA mods restored to order from: {name}", name=pristine.name), 5000
                )

        self.window.confirm(
            tr("Put GAMMA back in its original order?"),
            tr("The GAMMA mods go back to their default load order.")
            + "\n\n•  " + tr("Mods you installed yourself are kept.")
            + "\n•  " + tr("Your categories are kept.")
            + "\n•  " + tr("Mods stay on or off as they are."),
            _restore,
            confirm_text=tr("Restore order"),
        )

    # -- menus ------------------------------------------------------------
    # The desktop Mod Manager's right-click menus and toolbar, as pickers:
    # each entry runs its action once the menu is down.
    def _menu(self, title: str, options: list[tuple[str, object]]) -> None:
        picker = DeckPicker("", options, None)

        def _run(action: object) -> None:
            self.window.dismiss_overlay()
            if callable(action):
                action()

        picker.chosen.connect(_run)
        self.window.show_overlay(picker_overlay(self.window, title, picker))

    def _ask_text(self, title: str, text: str, on_done) -> None:
        """The on-screen keyboard for one line of text; ``on_done(stripped)``."""
        edit = QLineEdit(text, self)
        edit.setMaxLength(120)
        edit.hide()

        def _done(value: str) -> None:
            edit.deleteLater()
            on_done(value.strip())

        self.window.open_keyboard(edit, title=title, on_done=_done)

    def _show_tools(self) -> None:
        if self._path is None or self._refuse_if_blocked():
            return
        profile = self.profile()
        self._menu(
            tr("Tools"),
            [
                (tr("Create backup"), self._create_backup),
                (tr("Restore backup"), self._pick_backup),
                (tr("Restore original order"), self._restore_original_order),
                (tr("New category"), self._new_category),
                (tr("Check for File Conflicts"), self._check_all_conflicts),
                (
                    tr("MO2 profile") + ":  " + (profile.mo2_profile if profile else "-"),
                    self._pick_mo2_profile,
                ),
            ],
        )

    def _mod_menu(self, item: QListWidgetItem) -> None:
        name = str(item.data(_NAME))
        options: list[tuple[str, object]] = [
            (tr("Rename"), lambda: self._rename_mod(name)),
        ]
        if name in self._nicknames():
            options.append((tr("Reset Name"), lambda: self._set_nickname(name, "")))
        options += [
            (tr("Enable / disable category"), self._category_actions),
            (tr("Move to Category"), lambda: self._pick_move_category(name)),
            (tr("Show Conflicts"), lambda: self._show_mod_conflicts(name)),
            (tr("Reinstall from Cache"), lambda: self._reinstall_from_cache(name)),
        ]
        # Only mods the user installed: a GAMMA mod's files are GAMMA's to
        # update, and replacing them by hand breaks the next update.
        if name in custom_mod_names(self._lines):
            options.append((tr("Update from Archive"), lambda: self._update_from_archive(name)))
        self._menu(item.text(), options)

    def _category_menu(self, category: object) -> None:
        category = str(category or _NO_CATEGORY)
        options: list[tuple[str, object]] = [
            (tr("Enable all"), lambda: self._set_category(category, True)),
            (tr("Disable all"), lambda: self._set_category(category, False)),
        ]
        if category != _NO_CATEGORY:
            options += [
                (tr("Rename Category"), lambda: self._rename_category(category)),
                (tr("Move Category Up"), lambda: self._move_category(category, 1)),
                (tr("Move Category Down"), lambda: self._move_category(category, -1)),
            ]
            # Only categories the user made: never offer to delete GAMMA's own.
            if category in self._tracked_categories()[2]:
                options.append((tr("Delete Category"), lambda: self._delete_category(category)))
        self._menu(category if category != _NO_CATEGORY else tr("Uncategorized"), options)

    # -- nicknames (COMMANDER-only names; the desktop's "mod_nicknames") ----
    def _nickname_key(self) -> str:
        profile = self.profile()
        return str(Path(profile.gamma)) if profile is not None and profile.gamma else ""

    def _nicknames(self) -> dict[str, str]:
        stored = gui_settings.load_gui_settings().get("mod_nicknames", {})
        return dict(stored.get(self._nickname_key(), {}))

    def _set_nickname(self, name: str, nickname: str) -> None:
        key = self._nickname_key()
        if not key:
            return
        stored = dict(gui_settings.load_gui_settings().get("mod_nicknames", {}))
        names = dict(stored.get(key, {}))
        nickname = nickname.strip()[:120]
        if nickname and nickname != name:
            names[name] = nickname
        else:
            names.pop(name, None)
        if names:
            stored[key] = names
        else:
            stored.pop(key, None)
        gui_settings.save_gui_settings(mod_nicknames=stored)
        self._populate()
        self._select_mod(name)

    def _rename_mod(self, name: str) -> None:
        # Only COMMANDER's settings change, so this works while MO2 runs.
        self._ask_text(
            tr("Rename mod"),
            self._nicknames().get(name, name),
            lambda text: self._set_nickname(name, text) if text else None,
        )

    # -- categories -------------------------------------------------------
    def _tracked_categories(self) -> tuple[dict, str | None, list[str]]:
        """The categories the user made, as the desktop page tracks them:
        ``(all tracked, this list's key, its names)``."""
        tracked = dict(gui_settings.load_gui_settings().get("user_created_categories", {}))
        profile = self.profile()
        if profile is None:
            return tracked, None, []
        key = f"{profile.profile_name}/{profile.mo2_profile}"
        # The older, profile-only key belongs to the profile's MO2 profile.
        names = tracked.get(key, tracked.get(profile.profile_name, []))
        return tracked, key, list(names)

    def _save_tracked(self, change) -> None:
        tracked, key, names = self._tracked_categories()
        if key is not None:
            tracked[key] = change(names)
            gui_settings.save_gui_settings(user_created_categories=tracked)

    def _new_category(self) -> None:
        def _add(text: str) -> None:
            if not text or self._refuse_if_blocked():
                return
            before = {name for name, _mods in grouped(self._lines)}
            try:
                new_lines = add_category(self._lines, sanitize_name(text))
            except (ModInstallError, ValueError) as exc:
                self.window.notify(tr("That category won't work") + ": " + str(exc), 6000)
                return
            if self._write(new_lines):
                added = [n for n, _m in grouped(new_lines) if n not in before]
                if added:
                    self._save_tracked(lambda names: names + [added[0]])
                self._populate()
                if added:
                    self._jump_to_category(added[0])

        self._ask_text(tr("New category"), "", _add)

    def _rename_category(self, old: str) -> None:
        def _rename(text: str) -> None:
            if not text or text == old or self._refuse_if_blocked():
                return
            before = {name for name, _mods in grouped(self._lines)}
            try:
                new_lines = rename_category(self._lines, old, text)
            except ValueError as exc:
                self.window.notify(tr("That name won't work") + ": " + str(exc), 6000)
                return
            if new_lines == self._lines or not self._write(new_lines):
                return
            # rename_category() may clean the name up: use what landed.
            final = next((n for n, _m in grouped(new_lines) if n not in before), text)
            self._save_tracked(lambda names: [final if n == old else n for n in names])
            self._collapsed.discard(old)
            self._populate()
            self._jump_to_category(final)

        self._ask_text(tr("New name for the category:"), old, _rename)

    def _move_category(self, category: str, delta: int) -> None:
        """+1 is Move Up on screen (the list shows the file bottom-up)."""
        if self._refuse_if_blocked():
            return
        categories = [n for n, _m in grouped(self._lines) if n != _NO_CATEGORY]
        if category not in categories:
            return
        neighbor = categories.index(category) + delta
        if not 0 <= neighbor < len(categories):
            return
        try:
            new_lines = move_category(self._lines, category, categories[neighbor], before=delta < 0)
        except ValueError as exc:
            self.window.notify(tr("Couldn't move the category") + ": " + str(exc), 6000)
            return
        if self._write(new_lines):
            self._populate()
            self._jump_to_category(category)

    def _delete_category(self, category: str) -> None:
        count = next((len(mods) for n, mods in grouped(self._lines) if n == category), 0)

        def _delete() -> None:
            if self._refuse_if_blocked():
                return
            try:
                new_lines = delete_category(self._lines, category, delete_members=False)
            except ValueError as exc:
                self.window.notify(tr("Couldn't delete the category") + ": " + str(exc), 6000)
                return
            # A backup first, always: Restore backup in Tools undoes this.
            if self._write(new_lines, snapshot=True):
                self._save_tracked(lambda names: [n for n in names if n != category])
                self._populate()

        self.window.confirm(
            tr('Delete the category "{category}"?', category=category),
            tr("Its {count} mod(s) move to Uncategorized and stay as they are.", count=count),
            _delete,
            confirm_text=tr("Delete Category"),
            confirm_role="danger",
        )

    def _pick_move_category(self, name: str) -> None:
        current = next(
            (c for c, mods in grouped(self._lines) for _s, n, _i in mods if n == name), None
        )
        options = [
            (category, category)
            for category in dict.fromkeys(c for c, _m in grouped(self._lines))
            if category != current
        ]
        if not options:
            return
        picker = DeckPicker("", options, None, searchable=len(options) > 12)
        picker.chosen.connect(lambda category: self._move_to_category(name, str(category)))
        self.window.show_overlay(picker_overlay(self.window, tr("Move to Category"), picker))

    def _move_to_category(self, name: str, category: str) -> None:
        self.window.dismiss_overlay()
        if self._refuse_if_blocked():
            return
        new_lines = move_mod(self._lines, name, category=category)
        if new_lines != self._lines and self._write(new_lines):
            self._collapsed.discard(category)
            self._populate()
            self._select_mod(name)

    # -- conflicts --------------------------------------------------------
    def _mods_dir(self) -> Path | None:
        profile = self.profile()
        if profile is None:
            return None
        mods_dir = Path(profile.gamma) / "mods"
        if mods_dir.is_symlink():
            self.window.notify(
                tr("The GAMMA mods folder is a link to another folder, which COMMANDER doesn't change for safety. Point the profile at the real folder instead."),
                8000,
            )
            return None
        return mods_dir

    def _run_conflicts(self, function, *args, on_done) -> None:
        """Scanning every enabled mod's files takes a while: off the GUI thread."""
        if self._conflicts_task is not None:
            return
        task = BackgroundTask(function, *args, parent=self)
        self._conflicts_task = task

        def _done(result) -> None:
            self._conflicts_task = None
            self.window.toast.hide()
            on_done(result)

        def _failed(message: str) -> None:
            self._conflicts_task = None
            self.window.notify(tr("Couldn't check for conflicts") + ": " + message, 8000)

        task.result.connect(_done)
        task.error.connect(_failed)
        task.start()

    def _show_mod_conflicts(self, name: str) -> None:
        mods_dir = self._mods_dir()
        if mods_dir is None:
            return
        lines = list(self._lines)
        enabled = any(mod == name and status == "Enabled" for status, mod in entries(lines))
        self.window.notify(tr("Checking conflicts for {name}", name=name), 0)

        def _show(result) -> None:
            overrides, overridden_by = result

            def _section(title: str, rows: dict[str, list[str]]) -> str:
                body = [
                    f"   {mod}  ({len(files)})"
                    for mod, files in sorted(rows.items(), key=lambda r: (-len(r[1]), r[0]))
                ]
                return title + "\n" + ("\n".join(body) if body else "   " + tr("None"))

            text = tr(
                "Files this mod shares with other enabled mods. Where two mods "
                "ship the same file, the one lower in the list wins."
            )
            if not enabled:
                text += "\n" + tr("This mod is disabled - this is what enabling it would do.")
            text += "\n\n" + _section(tr("Overrides ({count})", count=len(overrides)), overrides)
            text += "\n\n" + _section(
                tr("Overridden by ({count})", count=len(overridden_by)), overridden_by
            )
            self._show_text(tr("Conflicts - {name}", name=self._nicknames().get(name, name)), text)

        self._run_conflicts(mod_conflicts, lines, mods_dir, name, on_done=_show)

    def _check_all_conflicts(self) -> None:
        mods_dir = self._mods_dir()
        if mods_dir is None:
            return
        self.window.notify(tr("Scanning"), 0)
        custom = custom_mod_names(self._lines)

        def _show(conflicts, show_all: bool = False) -> None:
            if not conflicts:
                self.window.notify(tr("None of your turned-on mods change the same file."), 6000)
                return
            rows = summarize_mod_conflicts(conflicts)
            mine = [r for r in rows if r[0] in custom or r[1] in custom]
            shown = rows if show_all else mine
            if show_all:
                info = tr(
                    "Every mod pair that overrides each other's gamedata files, "
                    "including GAMMA's own mods overriding each other - mostly "
                    "intentional, curated by the pack itself."
                )
            elif not custom:
                info = tr(
                    "You haven't installed any mods of your own yet (nothing "
                    "is filed under \"Custom Mods\"), so there's nothing here "
                    "to check."
                )
            elif not mine:
                info = tr("None of your own installed mods conflict with anything.")
            else:
                info = tr(
                    "Conflicts touching a mod you installed yourself (Custom "
                    "Mods) - the ones actually worth checking."
                )
            text = info + "\n\n" + "\n".join(
                f"{winner}  >  {loser}  ({count})" for winner, loser, count in shown
            )
            buttons = []
            if not show_all and len(rows) > len(mine):
                buttons.append((
                    tr("Show all {total} conflicts (including GAMMA's own)", total=len(rows)),
                    lambda: _show(conflicts, True),
                ))
            self._show_text(
                tr("{count} Mod Conflict(s) Found", count=len(shown)), text, buttons
            )

        self._run_conflicts(
            find_enabled_mod_file_conflicts, list(self._lines), mods_dir, on_done=_show
        )

    def _show_text(self, title: str, text: str, buttons=()) -> None:
        body = DeckTextPanel(text, min_height=200, max_height=520)
        overlay = DeckOverlay(
            title,
            body,
            [*((label, action, "normal") for label, action in buttons),
             (tr("Close"), self.window.dismiss_overlay, "primary")],
            panel_width=1100,
        )
        overlay.default_button = body
        self.window.show_overlay(overlay)

    # -- reinstall / update -----------------------------------------------
    def _installer_for_job(self):
        from ..mod_installer import DeckModInstaller

        if self._installer is None:
            self._installer = DeckModInstaller(self)
        return self._installer

    def _reinstall_from_cache(self, name: str) -> None:
        if self._refuse_if_blocked():
            return
        profile = self.profile()
        if profile is None or self._archive_task is not None:
            return
        # The MD5 check reads the whole archive (some are GBs): off the GUI thread.
        self.window.notify(tr("Checking the cached copy of {name}", name=name), 0)
        task = BackgroundTask(cached_archive_for, profile, profile.mo2_profile, name, parent=self)
        self._archive_task = task

        def _done(archive) -> None:
            self._archive_task = None
            self.window.toast.hide()
            self._ask_reinstall_from_cache(name, archive)

        def _failed(_message: str) -> None:
            self._archive_task = None
            self.window.toast.hide()
            self._ask_reinstall_from_cache(name, None)

        task.result.connect(_done)
        task.error.connect(_failed)
        task.start()

    def _ask_reinstall_from_cache(self, name: str, archive: Path | None) -> None:
        if archive is None:
            self.window.show_overlay(
                DeckOverlay(
                    tr("Can't reinstall from the cache"),
                    deck_label(
                        tr(
                            "There's no downloaded copy of '{name}' in the cache. The "
                            "official GAMMA list may have changed since it was "
                            "installed, or it isn't part of GAMMA.",
                            name=name,
                        )
                        + "\n\n"
                        + tr("Use the reset tools on the Utilities page instead."),
                        role="body",
                        wrap=True,
                    ),
                    [(tr("Close"), self.window.dismiss_overlay, "primary")],
                )
            )
            return
        self.window.confirm(
            tr("Reinstall this mod?"),
            tr("'{name}' is installed again from its downloaded copy in the cache.", name=name)
            + "\n\n•  " + tr("Its files are replaced.")
            + "\n•  " + tr("It stays on or off, in the same place in the list."),
            lambda: self._installer_for_job().reinstall(
                archive, name, tr("'{name}' was reinstalled from its cached archive.", name=name)
            ),
            confirm_text=tr("Reinstall"),
        )

    def _update_from_archive(self, name: str) -> None:
        if self._refuse_if_blocked():
            return
        from ..mod_installer import ARCHIVE_SUFFIXES, pick_file

        def _chosen(archive: Path) -> None:
            installer = self._installer_for_job()
            if installer._refuse_xrrazom(archive):
                return
            self.window.confirm(
                tr("Update this mod?"),
                tr("The files of '{name}' are replaced with {archive}.", name=name, archive=archive.name)
                + "\n\n•  " + tr("It stays on or off, in the same place in the list.")
                + "\n•  " + tr("If the update fails, the current files are put back."),
                lambda: installer.reinstall(archive, name, tr("'{name}' was updated.", name=name)),
                confirm_text=tr("Update"),
            )

        downloads = Path.home() / "Downloads"
        pick_file(
            self.window,
            tr("Update {name} from archive", name=name),
            downloads if downloads.is_dir() else Path.home(),
            ARCHIVE_SUFFIXES,
            _chosen,
        )

    # -- MO2 profiles -----------------------------------------------------
    # Switch, New and Rename. Deleting one (its mod list, saves and
    # settings) stays a desktop job.
    def _pick_mo2_profile(self, select: str | None = None) -> None:
        profile = self.profile()
        if profile is None:
            return
        names = mo2_profile_names(profile.gamma)
        current = profile.mo2_profile
        picker = DeckPicker(
            "",
            [(("●  " if n == current else "○  ") + n, n) for n in names],
            select or current,
        )
        picker.chosen.connect(lambda name: self._switch_mo2_profile(str(name)))

        def _highlighted() -> str | None:
            item = picker.list.currentItem()
            return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

        overlay = DeckOverlay(
            tr("MO2 profile"),
            picker,
            [(tr("Cancel"), self.window.dismiss_overlay, "normal")],
            panel_width=1000,
            header_buttons=[
                (tr("Rename"), lambda: self._rename_mo2_profile(_highlighted()), "normal"),
                ("+  " + tr("New"), self._new_mo2_profile, "normal"),
            ],
        )
        overlay.default_button = picker.list
        overlay.hints = (("A", "Switch"), ("X", "Rename"), ("Y", "New"), ("B", "Cancel"))

        def _on_action(action: str) -> bool:
            if action == pad.CONTEXT:
                self._rename_mo2_profile(_highlighted())
                return True
            if action == pad.SEARCH:
                self._new_mo2_profile()
                return True
            return False

        overlay.action_handler = _on_action
        self.window.show_overlay(overlay)

    def _switch_mo2_profile(self, name: str) -> None:
        self.window.dismiss_overlay()
        profile = self.profile()
        if profile is None or name == profile.mo2_profile or self._refuse_if_blocked():
            return
        # Both MO2's own choice and the COMMANDER profile, so launching and
        # Mod Organizer itself agree on which mod list is in use.
        rc, out = select_mo2_profile(profile.gamma, name)
        if rc != 0:
            self.window.notify(tr("Couldn't change the MO2 profile") + ": " + out.strip(), 8000)
            return
        profile.mo2_profile = name
        try:
            self.window.settings.save()
        except OSError as exc:
            self.window.notify(tr("Could not write settings.json:\n{exc}", exc=exc), 8000)
        self._snapshot_taken = False
        self._collapsed.clear()
        self.window.profile_changed()
        self.window.notify(tr("MO2 profile") + ": " + name)

    def _new_mo2_profile(self) -> None:
        profile = self.profile()
        if profile is None or self._refuse_if_blocked():
            return
        existing = mo2_profile_names(profile.gamma)

        def _named(name: str) -> None:
            if not name:
                return
            if not _valid_name(name):
                self.window.notify(tr("Profile names can't be empty or contain / or \\."), 6000)
                return
            if name in existing:
                self.window.notify(tr('A profile named "{name}" already exists.', name=name), 6000)
                return
            empty = object()
            picker = DeckPicker(
                "",
                [(tr("(Empty profile)"), empty), *((n, n) for n in existing)],
                profile.mo2_profile,
            )
            picker.chosen.connect(lambda source: _create(name, None if source is empty else str(source)))
            self.window.show_overlay(
                picker_overlay(self.window, tr("Start with the mod list from:"), picker)
            )

        def _create(name: str, source: str | None) -> None:
            self.window.dismiss_overlay()
            if self._refuse_if_blocked():
                return
            try:
                if not (source and seed_new_mo2_profile(profile.gamma, name, source_profile=source)):
                    # Empty, or the source had no modlist.txt: still a profile.
                    save_lines(Path(profile.gamma) / "profiles" / name / "modlist.txt", [])
            except OSError as exc:
                self.window.notify(tr("Couldn't create the MO2 profile") + ": " + str(exc), 8000)
                return
            self._pick_mo2_profile(select=name)

        self._ask_text(tr("New MO2 profile"), "", _named)

    def _rename_mo2_profile(self, old: str | None) -> None:
        profile = self.profile()
        if not old or profile is None or self._refuse_if_blocked():
            return
        gamma = profile.gamma
        existing = mo2_profile_names(gamma)

        def _rename(new: str) -> None:
            if not new or new == old or self._refuse_if_blocked():
                return
            if not _valid_name(new):
                self.window.notify(tr("Profile names can't be empty or contain / or \\."), 6000)
                return
            if new in existing:
                self.window.notify(tr('A profile named "{name}" already exists.', name=new), 6000)
                return
            root = Path(gamma) / "profiles"
            try:
                (root / old).rename(root / new)
            except OSError as exc:
                self.window.notify(tr("Couldn't rename the MO2 profile") + ": " + str(exc), 8000)
                return
            settings = self.window.settings
            tracked = dict(gui_settings.load_gui_settings().get("user_created_categories", {}))
            for cli_profile in settings.profiles:
                if cli_profile.gamma != gamma or cli_profile.mo2_profile != old:
                    continue
                cli_profile.mo2_profile = new
                moved = tracked.pop(f"{cli_profile.profile_name}/{old}", None)
                if moved is not None:
                    tracked[f"{cli_profile.profile_name}/{new}"] = moved
            gui_settings.save_gui_settings(user_created_categories=tracked)
            if old == profile.mo2_profile or new == profile.mo2_profile:
                # MO2 must not be left pointing at a folder that is gone.
                select_mo2_profile(gamma, new)
            try:
                settings.save()
            except OSError as exc:
                self.window.notify(tr("Could not write settings.json:\n{exc}", exc=exc), 8000)
            self.window.profile_changed()
            self._pick_mo2_profile(select=new)

        self._ask_text(tr("New name for \"{name}\":", name=old), old, _rename)

    # -- editing ----------------------------------------------------------
    def _toggle(self, item: QListWidgetItem) -> None:
        if self._path is None or item.data(_KIND) != "mod":
            return
        line_index = item.data(_LINE)
        enabled = bool(item.data(_ENABLED))
        try:
            new_lines = set_status_at(self._lines, int(line_index), not enabled)
        except ValueError as exc:
            self.window.notify(tr("Failed") + ": " + str(exc), 6000)
            return
        if not self._write(new_lines):
            return
        item.setData(_ENABLED, not enabled)
        self.list.viewport().update()
        self._render_counts(grouped(self._lines))

    def _write(self, new_lines: list[str], *, snapshot: bool = False) -> bool:
        """Write through the guarded choke point; False if refused.

        ``snapshot`` forces a backup of the list first (a deleted
        category); otherwise one is taken before the session's first edit.
        """
        try:
            write_lines(
                self.window,
                self._path,
                new_lines,
                snapshot=snapshot or not self._snapshot_taken,
            )
        except ModlistWriteBlocked as exc:
            # Nothing was written, so rows must go on showing what the file
            # still says - leaving them flipped would misreport the load order.
            self.window.notify(str(exc), 6000)
            self._update_guard()
            return False
        self._snapshot_taken = True
        self._lines = new_lines
        self.window.update_mod_counter()
        return True

    def _render_counts(self, groups) -> None:
        enabled_count = sum(
            1
            for _c, mods in groups
            for status, _n, _i in mods
            if status == "Enabled"
        )
        total = sum(len(mods) for _c, mods in groups)
        filtered = bool(self.search.text().strip()) or self._filter != "all"
        self.status.setText(
            tr("{enabled} Mods", enabled=enabled_count)
            + f"  /  {total}"
            + (f"   ·   {self._shown} " + tr("shown") if filtered else "")
        )

    # -- back -------------------------------------------------------------
    def on_back(self) -> bool:
        """B puts a carried mod back, or clears a search, before it leaves."""
        if self._grabbed is not None:
            self._cancel_grab()
            return True
        if self.search.text():
            self.search.clear()
            return True
        if self._filter != "all":
            self._set_filter("all")
            return True
        return False
