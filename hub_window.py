"""Minimal Polish games launcher with optional shortcut management."""
from __future__ import annotations

import os
import ctypes
from ctypes import wintypes
from pathlib import Path
from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut, QCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QStackedWidget, QVBoxLayout, QWidget,
)
from desktop_model import DesktopDelegate, DesktopModel, DesktopProxyModel, DesktopView, FAVORITE_ROLE, ID_ROLE, ITEM_ROLE

STYLE = """
QWidget { color: #eaf1fc; font-family: 'Segoe UI'; font-size: 13px; }
QLabel { background: transparent; }
QLineEdit { background: rgba(12, 21, 37, 155); border: 1px solid rgba(149, 174, 206, 50);
    border-radius: 12px; padding: 11px 15px; color: #f4f7fc; selection-background-color: #427fc2; }
QLineEdit:focus { border-color: #77b4f5; background: rgba(16, 28, 47, 205); }
QPushButton { background: rgba(47, 64, 90, 130); border: 1px solid rgba(163, 186, 218, 35);
    border-radius: 9px; padding: 8px 12px; color: #dce7f8; }
QPushButton:hover { background: rgba(62, 84, 117, 190); border-color: rgba(159, 198, 248, 110); }
QPushButton:checked { background: rgba(60, 112, 177, 160); border-color: #659bd8; color: white; }
QPushButton:disabled { color: #697b93; background: rgba(33, 46, 66, 80); border-color: rgba(140, 160, 185, 15); }
QListView { background: transparent; border: 0; outline: 0; }
QListView::item { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 3px; }
QScrollBar::handle:vertical { background: rgba(160, 181, 211, 105); border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #86a8d4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #17253c; border: 1px solid #3b5272; padding: 6px; }
QMenu::item { padding: 8px 25px 8px 13px; border-radius: 6px; }
QMenu::item:selected { background: #34577e; }
QMenu::item:disabled { color: #75869e; }
QMenu::separator { height: 1px; background: #34465f; margin: 5px 8px; }
QMessageBox, QInputDialog, QFileDialog { background: #17243a; }
"""


class GameHubWindow(QWidget):
    open_requested = Signal(object)
    operation_requested = Signal(object)
    location_requested = Signal(object)
    properties_requested = Signal(object)
    refresh_requested = Signal()
    favorite_changed = Signal(str, bool)
    extra_source_changed = Signal(bool)
    settings_changed = Signal(str, object)
    game_added = Signal(str)
    game_removed = Signal(str)
    games_restored = Signal()

    def __init__(self, base_dir: str, config_mgr):
        super().__init__()
        self.base_dir, self.config_mgr = base_dir, config_mgr
        self._selection_mode = self._operation_busy = self._acrylic = False
        self._filter, self._selection_anchor, self._shortcuts = "all", None, []
        self._pending_id_mapping = {}
        self._preferred_size = QSize(int(config_mgr.get("hub_width", 1600)), int(config_mgr.get("hub_height", 860)))
        self._panel_target_rect = None
        self._chrome_setup = False
        self._interactive_start_size = None
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("GameDock — Gry")
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(STYLE)
        self.resize(self._preferred_size)
        self.model = DesktopModel(config_mgr.get("favorites", []), self)
        self.proxy = DesktopProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.delegate = DesktopDelegate(self)
        self._build_ui()
        self._build_shortcuts()
        for signal in (self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset, self.proxy.layoutChanged):
            signal.connect(self._update_counts)
        self._set_view_mode(config_mgr.get("view_mode", "grid"), False)
        self.proxy.set_sort_mode(config_mgr.get("sort_mode", "name_asc"))
        self._update_counts()
        self.set_sync_status("Ładowanie gier…")

    @property
    def desktop_items(self):
        return self.model.items

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)
        self.header = QWidget()
        self.header.setFixedHeight(44)
        self.header.installEventFilter(self)
        self.header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header.customContextMenuRequested.connect(lambda position: self._show_header_menu(self.header.mapToGlobal(position)))
        layout.addWidget(self.header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Szukaj gry…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Szukaj gry")
        self.search.textChanged.connect(self._search_changed)
        self.search.hide()
        layout.addWidget(self.search)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self.view = DesktopView()
        self.view.setItemDelegate(self.delegate)
        self.view.setModel(self.proxy)
        self.view.item_clicked.connect(self._item_clicked)
        self.view.control_clicked.connect(self._control_clicked)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.stack.addWidget(self.view)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        self.empty_title = QLabel("Brak gier")
        self.empty_title.setStyleSheet("font-size: 16px; color: white;")
        self.empty_description = QLabel("Gry wykryjemy automatycznie. Użyj prawego przycisku i wybierz „Dodaj grę”.")
        self.empty_description.setStyleSheet("color: #c3cddd;")
        for label in (self.empty_title, self.empty_description):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(label)
        empty_layout.addStretch()
        self.stack.addWidget(empty)
        layout.addWidget(self.stack, 1)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setMaximumHeight(100)
        self.message_label.hide()
        layout.addWidget(self.message_label)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.message_label.hide)

    def eventFilter(self, watched, event):
        if watched is self.header and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                handle = self.windowHandle()
                if handle:
                    handle.startSystemMove()
                return True
            if event.button() == Qt.MouseButton.RightButton:
                self._show_header_menu(event.globalPosition().toPoint())
                return True
        return super().eventFilter(watched, event)

    def nativeEvent(self, event_type, message):
        if os.name == "nt":
            native = wintypes.MSG.from_address(int(message))
            if native.message == 0x0231 and not self._chrome_setup:  # WM_ENTERSIZEMOVE
                self._interactive_start_size = QSize(self.size())
            elif native.message == 0x0232:  # WM_EXITSIZEMOVE
                QTimer.singleShot(0, self._finish_user_resize)
        try:
            from native_windows import handle_panel_native_event
            handled, result = handle_panel_native_event(message)
            if handled:
                return True, result
        except (ImportError, OSError, AttributeError):
            pass
        return super().nativeEvent(event_type, message)

    def _show_search(self):
        self.search.show()
        self.search.setFocus()
        self.search.selectAll()

    def _show_header_menu(self, position=None):
        self._make_header_menu().exec(position or QCursor.pos())

    def _make_header_menu(self):
        menu = QMenu(self)
        menu.addAction("Dodaj grę…", self._add_game).setEnabled(not self._operation_busy)
        menu.addAction("Szukaj…    Ctrl+F", self._show_search)
        menu.addAction("Zakończ zaznaczanie" if self._selection_mode else "Zaznacz gry", lambda: self.set_selection_mode(not self._selection_mode))
        menu.addAction("Zaznacz wszystko    Ctrl+A", self._select_all)
        if self.model.checked_items:
            items = self.model.checked_items
            menu.addAction("Usuń wybrane z listy gier", self._remove_games).setEnabled(not self._operation_busy)
            if all(x.kind == "shortcut" for x in items):
                self._add_shortcut_menu(menu, items)
        menu.addSeparator()
        filters = menu.addMenu("Pokaż")
        for kind, label in (("all", "Wszystkie gry"), ("favorites", "Ulubione")):
            action = filters.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._filter == kind)
            action.triggered.connect(lambda checked=False, key=kind: self._set_filter(key))
        sorting = menu.addMenu("Sortowanie")
        for mode, label in (("name_asc", "Nazwa A–Z"), ("name_desc", "Nazwa Z–A"), ("modified", "Ostatnio zmienione")):
            action = sorting.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.proxy.sort_mode == mode)
            action.triggered.connect(lambda checked=False, key=mode: self._set_sort_mode(key))
        views = menu.addMenu("Widok")
        for mode, label in (("grid", "Ikony"), ("list", "Lista")):
            action = views.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.delegate.list_mode == (mode == "list"))
            action.triggered.connect(lambda checked=False, key=mode: self._set_view_mode(key))
        menu.addSeparator()
        menu.addAction("Odśwież    F5", self.refresh_requested.emit)
        menu.addAction("Ustawienia…", self._show_settings)
        return menu

    def _build_shortcuts(self):
        callbacks = {"Ctrl+F": self._show_search,
            "F5": self.refresh_requested.emit, "F2": self._rename, "Delete": self._remove_games,
            "Ctrl+A": self._select_all, "Space": self._toggle_current,
            "Return": self._open_current, "Enter": self._open_current,
            "Escape": self._escape, "Shift+F10": self._keyboard_context_menu}
        for sequence, callback in callbacks.items():
            shortcut = QShortcut(QKeySequence(sequence), self.view if sequence == "Space" else self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut if sequence == "Space" else Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def apply_snapshot(self, items):
        focused_id = self.view.currentIndex().data(ID_ROLE)
        top_index = self.view.indexAt(QPoint(15, 15))
        if not top_index.isValid():
            top_index = self.view.indexAt(QPoint(75, 35))
        top_id = top_index.data(ID_ROLE)
        focused_id = self._pending_id_mapping.get(focused_id, focused_id)
        top_id = self._pending_id_mapping.get(top_id, top_id)
        self._pending_id_mapping.clear()
        top_offset = self.view.visualRect(top_index).top() if top_index.isValid() else 0
        previous_scroll = self.view.verticalScrollBar().value()
        self.model.apply_snapshot(items)
        self.model.set_favorites(self.config_mgr.get("favorites", []))
        self.view.doItemsLayout()
        restored = self.proxy.mapFromSource(self.model.index_for_id(focused_id))
        if restored.isValid():
            self.view.setCurrentIndex(restored)
        top_restored = self.proxy.mapFromSource(self.model.index_for_id(top_id))
        if top_restored.isValid():
            self.view.scrollTo(top_restored, DesktopView.ScrollHint.PositionAtTop)
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().value()-top_offset)
        else:
            self.view.verticalScrollBar().setValue(previous_scroll)
        self._update_counts()

    def remap_item_ids(self, mapping):
        # An operation can rename paths before the next desktop snapshot arrives.
        self._pending_id_mapping.update(mapping)
        self.model.remap_item_ids(mapping)
        self._selection_anchor = mapping.get(self._selection_anchor, self._selection_anchor)

    def set_favorites(self, favorites):
        self.model.set_favorites(favorites)
        self._update_counts()

    def apply_icon(self, item_id: str, png: bytes):
        self.model.apply_icon(item_id, png)

    def set_sync_status(self, text: str, is_error: bool = False):
        self.header.setToolTip(text)
        if is_error:
            self._show_message(text, True)

    def set_operation_busy(self, busy: bool):
        self._operation_busy = busy

    def show_operation_result(self, result):
        self.set_operation_busy(False)
        read = result.get if isinstance(result, dict) else lambda key, default=None: getattr(result, key, default)
        cancelled = read("cancelled", False)
        completed = len(read("successful_ids", ()))
        errors = list(read("errors", ()))
        success = read("success", read("ok", completed > 0 and not errors and not cancelled))
        message = read("message", "") or read("error", "")
        if not message:
            if errors:
                message = (f"Wykonane elementy: {completed}. " if completed else "") + "Nie udało się zakończyć wszystkich czynności.\n"
                message += "\n".join(str(error) for error in errors[:3])
                if len(errors) > 3:
                    message += f"\nPozostałe błędy: {len(errors)-3}."
            elif cancelled:
                message = (f"Wykonane elementy: {completed}. " if completed else "") + "Operacja została anulowana."
            elif success:
                message = f"Gotowe. Wykonane elementy: {completed}." if completed else "Gotowe."
            else:
                message = "Nie udało się zakończyć operacji."
        self._show_message(str(message), bool(errors) or (not success and not cancelled))
        self.message_label.setToolTip("\n".join(str(error) for error in errors) or str(message))

    def _show_message(self, text, error=False):
        self.message_label.setText(text)
        color = "#ffccd1" if error else "#c3e7d7"
        self.message_label.setStyleSheet(f"color: {color}; background: rgba(50, 68, 87, 170); padding: 8px 12px; border-radius: 8px;")
        self.message_label.setVisible(error)
        if error:
            self._message_timer.start(12000)

    def show_panel(self):
        self._chrome_setup = True
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            width = min(max(640, self._preferred_size.width()), max(320, int(available.width()*.9)))
            height = min(max(440, self._preferred_size.height()), max(240, int(available.height()*.9)))
            self.setMinimumSize(min(640, width), min(440, height))
            self.resize(width, height)
            self.move(available.center()-QPoint(width//2, height//2))
        self._panel_target_rect = self._physical_panel_rect()
        self.showNormal()
        self._apply_backdrop()
        QTimer.singleShot(0, self._apply_backdrop)
        QTimer.singleShot(100, self._finish_chrome_setup)
        self.raise_()
        self.activateWindow()
        (self.search if self.search.isVisible() else self.view).setFocus()

    def _physical_panel_rect(self):
        try:
            from native_windows import cursor_position, monitor_for_point, monitor_dpi
            point = cursor_position()
            monitor = monitor_for_point(*point) if point else None
            if monitor:
                scale = monitor_dpi(*point) / 96
                work_width = monitor.work_right-monitor.work_left
                work_height = monitor.work_bottom-monitor.work_top
                width = min(round(self._preferred_size.width()*scale), round(work_width*.9))
                height = min(round(self._preferred_size.height()*scale), round(work_height*.9))
                return (monitor.work_left+(work_width-width)//2, monitor.work_top+(work_height-height)//2, width, height)
        except (ImportError, OSError, AttributeError):
            pass
        return None

    def _finish_chrome_setup(self):
        self._apply_backdrop()
        self._chrome_setup = False
        self._panel_target_rect = None

    def _finish_user_resize(self):
        previous, self._interactive_start_size = self._interactive_start_size, None
        if previous is not None and previous != self.size() and not self._chrome_setup and not self.isMaximized():
            self._preferred_size = QSize(self.size())
            self.settings_changed.emit("hub_width", self._preferred_size.width())
            self.settings_changed.emit("hub_height", self._preferred_size.height())

    def _apply_backdrop(self):
        try:
            from native_windows import apply_acrylic, configure_panel_chrome, place_panel_window, uses_per_pixel_transparency
            configure_panel_chrome(int(self.winId()))
            native_backdrop = bool(apply_acrylic(int(self.winId())))
            self._acrylic = uses_per_pixel_transparency(int(self.winId())) or native_backdrop
            if self._chrome_setup and self._panel_target_rect:
                place_panel_window(int(self.winId()), *self._panel_target_rect)
        except (ImportError, OSError, AttributeError):
            self._acrylic = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        try:
            opacity = max(0.0, min(1.0, float(self.config_mgr.get("glass_opacity", .28))))
        except (TypeError, ValueError):
            opacity = .28
        painter.setBrush(QColor(0, 0, 0, max(1, round(opacity*255)) if self._acrylic else 255))
        plate = self.rect().adjusted(4, 4, -4, -4)
        painter.drawRoundedRect(plate, 34, 34)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(plate, 34, 34)
        painter.end()

    def closeEvent(self, event):
        event.ignore()
        if not self._operation_busy:
            self.hide()

    def _search_changed(self, text):
        self.proxy.set_query(text)
        self._update_counts()

    def _set_filter(self, kind):
        self._filter = kind
        self.proxy.set_kind(kind)
        self._update_counts()

    def _set_sort_mode(self, mode):
        self.proxy.set_sort_mode(mode)
        self.settings_changed.emit("sort_mode", mode)

    def _set_view_mode(self, mode, persist=True):
        mode = "list" if mode == "list" else "grid"
        self.view.set_list_mode(mode == "list")
        if persist:
            self.settings_changed.emit("view_mode", mode)

    def _update_counts(self, *args):
        shown = self.proxy.rowCount()
        self.stack.setCurrentIndex(0 if shown else 1)
        filtered = bool(self.search.text()) or self._filter != "all"
        self.empty_title.setText("Brak pasujących gier" if filtered else "Brak gier")
        self.empty_description.setText("Zmień wyszukiwanie lub wybierz „Wszystkie gry”." if filtered else "Gry wykryjemy automatycznie. Użyj prawego przycisku i wybierz „Dodaj grę”.")

    def set_selection_mode(self, enabled):
        self._selection_mode = enabled
        self.delegate.selection_mode = enabled
        if not enabled:
            self.model.clear_checked()
        self.view.viewport().update()

    def _item_clicked(self, index, modifiers=0):
        if self._operation_busy:
            return
        item = index.data(ITEM_ROLE)
        if not item:
            return
        modifying = modifiers & int((Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier).value)
        if self._selection_mode or modifying:
            self.set_selection_mode(True)
            if modifiers & int(Qt.KeyboardModifier.ShiftModifier.value) and self._selection_anchor is not None:
                anchor = self.proxy.mapFromSource(self.model.index_for_id(self._selection_anchor))
                start = anchor.row() if anchor.isValid() else index.row()
                self.model.set_checked_ids(self.proxy.index(row, 0).data(ID_ROLE)
                    for row in range(min(start, index.row()), max(start, index.row())+1))
            else:
                self.model.set_checked(item.id, index.data(Qt.ItemDataRole.CheckStateRole) != Qt.CheckState.Checked)
            self._selection_anchor = item.id
        else:
            self.open_requested.emit(item)

    def _control_clicked(self, index, control):
        if self._operation_busy:
            return
        item = index.data(ITEM_ROLE)
        if control == "check":
            self.set_selection_mode(True)
            self.model.set_checked(item.id, index.data(Qt.ItemDataRole.CheckStateRole) != Qt.CheckState.Checked)
            self.view.setCurrentIndex(index)
            self._selection_anchor = item.id
        else:
            favorite = not bool(index.data(FAVORITE_ROLE))
            self.model.set_favorite(item.id, favorite)
            self.favorite_changed.emit(item.id, favorite)
            self._update_counts()

    def _targets(self):
        if self.model.checked_items:
            return self.model.checked_items
        item = self.view.currentIndex().data(ITEM_ROLE)
        return [item] if item else []

    def _open_current(self):
        if self._operation_busy:
            return
        index = self.view.currentIndex()
        if not index.isValid() and self.proxy.rowCount():
            index = self.proxy.index(0, 0)
        item = index.data(ITEM_ROLE)
        if item:
            self.open_requested.emit(item)

    def _toggle_current(self):
        if self.search.hasFocus():
            self.search.insert(" ")
        elif self.view.currentIndex().isValid():
            self._control_clicked(self.view.currentIndex(), "check")

    def _select_all(self):
        if self.search.hasFocus():
            self.search.selectAll()
            return
        self.set_selection_mode(True)
        self.model.set_checked_ids(self.proxy.index(row, 0).data(ID_ROLE) for row in range(self.proxy.rowCount()))

    def _escape(self):
        if self._operation_busy:
            return
        if self.search.isVisible():
            self.search.clear()
            self.search.hide()
            self.view.setFocus()
        elif self._selection_mode:
            self.set_selection_mode(False)
        else:
            self.hide()

    def _rename(self, items=None):
        if self._operation_busy:
            return
        items = items or self._targets()
        if len(items) != 1 or items[0].kind != "shortcut" or not items[0].can_rename:
            return
        # Like Explorer: edit the visible name and keep the shortcut's extension.
        path = Path(items[0].path) if items[0].path else None
        suffix = path.suffix if path else ""
        original = path.stem if path else items[0].display_name
        name, accepted = QInputDialog.getText(self, "Zmień nazwę", "Nowa nazwa:", text=original)
        name = name.strip()
        if not accepted or not name:
            return
        if suffix and not name.casefold().endswith(suffix.casefold()):
            name += suffix
        if name != (path.name if path else original):
            self.operation_requested.emit({"action": "rename", "items": items, "new_name": name})

    def _choose_destination(self, action, items=None):
        if self._operation_busy:
            return
        items = items or self._targets()
        capability = "can_move" if action == "move" else "can_copy"
        if not items or not all(item.kind == "shortcut" and getattr(item, capability) for item in items):
            return
        destination = QFileDialog.getExistingDirectory(self, "Przenieś do folderu" if action == "move" else "Kopiuj do folderu", os.path.expanduser("~"))
        if destination:
            self.operation_requested.emit({"action": action, "items": items, "destination": destination})

    def _delete(self, items=None):
        if self.search.hasFocus() and items is None:
            self.search.del_()
            return
        if self._operation_busy:
            return
        items = items or self._targets()
        if not items or not all(item.kind == "shortcut" and item.can_delete for item in items):
            return
        summary = f"skrót „{items[0].display_name}”" if len(items) == 1 else f"wybrane skróty ({len(items)})"
        answer = QMessageBox.question(self, "Przenieść skrót do Kosza?", f"Przenieść {summary} do Kosza?\nGra pozostanie zainstalowana.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.operation_requested.emit({"action": "delete", "items": items, "recycle": True})

    def _add_game(self):
        if self._operation_busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Dodaj grę", os.path.expanduser("~"), "Gry i skróty (*.lnk *.url *.exe *.website)")
        if path:
            self.game_added.emit(path)

    def _remove_games(self, items=None):
        if self.search.hasFocus() and items is None:
            self.search.del_()
            return
        if not self._operation_busy:
            for item in items or self._targets():
                self.game_removed.emit(item.id)

    def _add_shortcut_menu(self, menu, items):
        submenu = menu.addMenu("Zarządzaj skrótem" if len(items) == 1 else "Zarządzaj skrótami")
        ready = not self._operation_busy
        submenu.addAction("Zmień nazwę…", lambda: self._rename(items)).setEnabled(ready and len(items) == 1 and items[0].can_rename)
        submenu.addAction("Przenieś do…", lambda: self._choose_destination("move", items)).setEnabled(ready and all(x.can_move for x in items))
        submenu.addAction("Kopiuj do…", lambda: self._choose_destination("copy", items)).setEnabled(ready and all(x.can_copy for x in items))
        submenu.addAction("Przenieś skrót do Kosza…", lambda: self._delete(items)).setEnabled(ready and all(x.can_delete for x in items))
        return submenu

    def _context_menu(self, position):
        index = self.view.indexAt(position)
        if not index.isValid():
            self._show_header_menu(self.view.viewport().mapToGlobal(position))
            return
        else:
            self.view.setCurrentIndex(index)
            item = index.data(ITEM_ROLE)
            items = self.model.checked_items if item in self.model.checked_items else [item]
            menu = self._make_item_menu(item, items)
        menu.exec(self.view.viewport().mapToGlobal(position))

    def _make_item_menu(self, item, items=None):
        items, ready, menu = items or [item], not self._operation_busy, QMenu(self)
        menu.addAction("Otwórz", lambda: self.open_requested.emit(item)).setEnabled(ready)
        favorite = self.model.index_for_id(item.id).data(FAVORITE_ROLE)
        def toggle_favorite():
            self.model.set_favorite(item.id, not favorite)
            self.favorite_changed.emit(item.id, not favorite)
            self._update_counts()
        menu.addAction("Usuń z ulubionych" if favorite else "Dodaj do ulubionych", toggle_favorite)
        def select_item():
            self.set_selection_mode(True)
            self.model.set_checked(item.id, True)
        menu.addAction("Zaznacz", select_item).setEnabled(ready)
        menu.addSeparator()
        menu.addAction("Pokaż w folderze", lambda: self.location_requested.emit(item)).setEnabled(bool(item.path) and ready)
        menu.addAction("Właściwości", lambda: self.properties_requested.emit(item)).setEnabled(ready)
        menu.addSeparator()
        menu.addAction("Usuń z listy gier", lambda: self._remove_games(items)).setEnabled(ready)
        if all(x.kind == "shortcut" for x in items):
            self._add_shortcut_menu(menu, items)
        return menu

    def _keyboard_context_menu(self):
        index = self.view.currentIndex()
        if index.isValid():
            self._context_menu(self.view.visualRect(index).center())

    def _make_settings_menu(self):
        menu = QMenu(self)
        for key, label, default in (("autostart", "Uruchamiaj z Windows", False),
                                    ("dock_visible", "Przycisk na pulpicie", True),
                                    ("dock_locked", "Zablokuj pozycję przycisku", False)):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(self.config_mgr.get(key, default)))
            action.triggered.connect(lambda checked, setting=key: self.settings_changed.emit(setting, checked))
        menu.addSeparator()
        extra = menu.addAction("Dołącz skróty z folderu GameDock")
        extra.setCheckable(True)
        extra.setChecked(bool(self.config_mgr.get("include_shortcuts", False)))
        extra.triggered.connect(self.extra_source_changed.emit)
        extra.setToolTip(os.path.join(self.base_dir, "Shortcuts"))
        overrides = self.config_mgr.get("game_overrides", {})
        if isinstance(overrides, dict) and any(value is False for value in overrides.values()):
            menu.addSeparator()
            menu.addAction("Przywróć gry usunięte z listy", self.games_restored.emit).setEnabled(not self._operation_busy)
        return menu

    def _show_settings(self):
        self._make_settings_menu().exec(QCursor.pos())
